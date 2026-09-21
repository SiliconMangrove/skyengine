"""Local orchestration for compiled algorithm experiments.

The orchestrator binds trusted registry factories to runtime drivers, gives
every run an isolated workspace, and publishes platform-owned event and result
artifacts.  Scheduling is deliberately sequential in this first stage; a
distributed scheduler can consume the same ``ExecutionPlan`` later.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from .artifacts import ArtifactManifest, LocalArtifactStore, RunArtifactPublisher
from .comparison import ComparisonEngine, ComparisonReport
from .events import EventRecorder, JsonlEventSink
from .models import (
    AlgorithmInterface,
    ArtifactKind,
    ArtifactRef,
    EventType,
    ExecutionPlan,
    RunContext,
    RunFailure,
    RunPurpose,
    RunResult,
    RunSpec,
    RunStatus,
    StateSnapshot,
    TrialResult,
    TrialStatus,
)
from .objectives import ObjectiveEngine
from .protocols import RuntimeDriver
from .registry import PlatformRegistry, default_registry
from .runtimes import (
    BatchRuntimeDriver,
    IterativeRuntimeDriver,
    OnlineRuntimeDriver,
    TrainableRuntimeDriver,
)
from .serialization import to_jsonable


RuntimeFactory = Callable[
    [EventRecorder],
    RuntimeDriver[object, object],
]


class AlgorithmExecutionCancelled(RuntimeError):
    """Raised after a cooperative cancellation request reaches a safe boundary."""


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Terminal record of one concrete execution of a compiled plan."""

    execution_id: str
    plan: ExecutionPlan
    run_results: tuple[RunResult, ...]
    artifact_manifests: tuple[ArtifactManifest, ...]
    plan_artifact: ArtifactRef
    report_artifact: ArtifactRef
    comparison_report: ComparisonReport | None
    comparison_artifact: ArtifactRef | None
    execution_manifest: ArtifactManifest
    started_at: datetime
    finished_at: datetime


class LocalOrchestrator:
    """Execute a compiled plan in-process with isolated per-run workspaces."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        registry: PlatformRegistry = default_registry,
        artifact_store: LocalArtifactStore | None = None,
        runtime_factories: Mapping[AlgorithmInterface, RuntimeFactory] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._executions_root = self._workspace_root / "executions"
        self._registry = registry
        self._artifact_store = artifact_store or LocalArtifactStore(
            self._workspace_root / "artifacts"
        )
        self._runtime_factories: dict[AlgorithmInterface, RuntimeFactory] = {
            AlgorithmInterface.ONLINE: OnlineRuntimeDriver,
            AlgorithmInterface.BATCH: BatchRuntimeDriver,
            AlgorithmInterface.ITERATIVE: IterativeRuntimeDriver,
            AlgorithmInterface.TRAINABLE: TrainableRuntimeDriver,
        }
        if runtime_factories:
            self._runtime_factories.update(runtime_factories)

    @property
    def artifact_store(self) -> LocalArtifactStore:
        """Content-addressed store shared by executions and replay workflows."""

        return self._artifact_store

    def execute_run(
        self,
        run: RunSpec,
        *,
        execution_id: str,
        initial_snapshot: StateSnapshot | None = None,
        cancel_event: Event | None = None,
    ) -> tuple[RunResult, ArtifactManifest]:
        """Execute one run for deterministic or branched replay."""

        _validate_execution_id(execution_id)
        _validate_local_budget(run)
        active_cancel_event = cancel_event if cancel_event is not None else Event()
        _raise_if_cancelled(active_cancel_event)
        execution_directory = self._executions_root / execution_id
        if execution_directory.exists():
            raise FileExistsError(
                f"execution workspace already exists: {execution_id}"
            )
        execution_directory.mkdir(parents=True)
        result = self._execute_run(
            execution_id,
            execution_directory,
            run,
            initial_snapshot=initial_snapshot,
            cancel_event=active_cancel_event,
        )
        if result[0].status is RunStatus.CANCELLED or active_cancel_event.is_set():
            raise AlgorithmExecutionCancelled(
                f"execution {execution_id!r} was cancelled"
            )
        return result

    def execute(
        self,
        plan: ExecutionPlan,
        *,
        execution_id: str,
        cancel_event: Event | None = None,
    ) -> ExecutionReport:
        """Execute every run once in stable plan order."""

        _validate_execution_id(execution_id)
        active_cancel_event = cancel_event if cancel_event is not None else Event()
        _raise_if_cancelled(active_cancel_event)
        for run in plan.runs:
            _validate_local_budget(run)
        unavailable_interfaces = tuple(
            sorted(
                {
                    run.algorithm.interface.value
                    for run in plan.runs
                    if run.algorithm.interface not in self._runtime_factories
                    and self._registry.algorithm_manifest(
                        run.algorithm.algorithm_id,
                        run.algorithm.version,
                    ).runtime
                    is None
                }
            )
        )
        if unavailable_interfaces:
            raise ValueError(
                "execution plan requires runtime drivers that are not registered: "
                f"{unavailable_interfaces}"
            )
        execution_directory = self._executions_root / execution_id
        if execution_directory.exists():
            raise FileExistsError(f"execution workspace already exists: {execution_id}")
        execution_directory.mkdir(parents=True)

        started_at = datetime.now(timezone.utc)
        plan_artifact = self._artifact_store.put_json(
            to_jsonable(plan),
            kind=ArtifactKind.EXECUTION_PLAN,
            metadata={
                "execution_id": execution_id,
                "experiment_id": plan.experiment.experiment_id,
                "plan_digest": plan.plan_digest,
            },
        )
        results: list[RunResult] = []
        manifests: list[ArtifactManifest] = []
        for run in plan.runs:
            _raise_if_cancelled(active_cancel_event)
            result, manifest = self._execute_run(
                execution_id,
                execution_directory,
                run,
                cancel_event=active_cancel_event,
            )
            results.append(result)
            manifests.append(manifest)
            if (
                result.status is RunStatus.CANCELLED
                or active_cancel_event.is_set()
            ):
                raise AlgorithmExecutionCancelled(
                    f"execution {execution_id!r} was cancelled"
                )

        comparison_report: ComparisonReport | None = None
        comparison_artifact: ArtifactRef | None = None
        if plan.experiment.purpose is RunPurpose.COMPARE:
            comparison_report = ComparisonEngine().build(
                plan,
                results,
                execution_id=execution_id,
            )
            comparison_artifact = self._artifact_store.put_json(
                to_jsonable(comparison_report),
                kind=ArtifactKind.REPORT,
                metadata={
                    "execution_id": execution_id,
                    "experiment_id": plan.experiment.experiment_id,
                    "report_type": "algorithm_comparison",
                },
            )

        finished_at = datetime.now(timezone.utc)
        report_artifact = self._artifact_store.put_json(
            {
                "execution_id": execution_id,
                "experiment_id": plan.experiment.experiment_id,
                "plan_digest": plan.plan_digest,
                "started_at": started_at,
                "finished_at": finished_at,
                "run_results": tuple(results),
                "run_manifest_ids": tuple(
                    manifest.manifest_id for manifest in manifests
                ),
                "comparison_report": comparison_report,
            },
            kind=ArtifactKind.REPORT,
            metadata={
                "execution_id": execution_id,
                "experiment_id": plan.experiment.experiment_id,
                "report_type": "execution",
            },
        )
        execution_artifacts = (
            plan_artifact,
            report_artifact,
            *((comparison_artifact,) if comparison_artifact is not None else ()),
            *(
                artifact
                for manifest in manifests
                for artifact in manifest.artifacts
            ),
        )
        execution_manifest = self._artifact_store.write_manifest(
            execution_id,
            execution_artifacts,
            metadata={
                "execution_id": execution_id,
                "experiment_id": plan.experiment.experiment_id,
                "plan_digest": plan.plan_digest,
                "run_count": len(results),
                "comparison_report": comparison_artifact is not None,
            },
        )

        return ExecutionReport(
            execution_id=execution_id,
            plan=plan,
            run_results=tuple(results),
            artifact_manifests=tuple(manifests),
            plan_artifact=plan_artifact,
            report_artifact=report_artifact,
            comparison_report=comparison_report,
            comparison_artifact=comparison_artifact,
            execution_manifest=execution_manifest,
            started_at=started_at,
            finished_at=finished_at,
        )

    def aggregate_trial(
        self,
        report: ExecutionReport,
        trial_id: str,
    ) -> TrialResult:
        """Aggregate one fully evaluated trial in deterministic run order."""

        trial = next(
            (item for item in report.plan.trials if item.trial_id == trial_id),
            None,
        )
        if trial is None:
            raise KeyError(f"unknown trial: {trial_id}")

        expected_runs = tuple(
            run for run in report.plan.runs if run.trial_id == trial_id
        )
        results_by_id = {result.run_id: result for result in report.run_results}
        missing = tuple(
            run.run_id for run in expected_runs if run.run_id not in results_by_id
        )
        if missing:
            raise ValueError(f"trial has missing run results: {missing}")

        ordered_results = tuple(
            results_by_id[run.run_id] for run in expected_runs
        )
        unavailable = tuple(
            result.run_id
            for result in ordered_results
            if result.status not in {RunStatus.SUCCEEDED, RunStatus.INFEASIBLE}
            or not result.metrics
        )
        if unavailable:
            status = (
                TrialStatus.CANCELLED
                if any(
                    result.status is RunStatus.CANCELLED
                    for result in ordered_results
                )
                else TrialStatus.FAILED
            )
            failure = next(
                (
                    result.failure
                    for result in ordered_results
                    if result.failure is not None
                ),
                RunFailure(
                    code="trial_run_unavailable",
                    message=(
                        "trial contains runs without authoritative objective "
                        f"metrics: {unavailable}"
                    ),
                    details={"run_ids": unavailable},
                ),
            )
            return TrialResult(
                trial_id=trial.trial_id,
                candidate_id=None,
                parameters=trial.algorithm.parameters,
                objective_values=(),
                feasible=False,
                run_results=ordered_results,
                status=status,
                failure=failure,
            )

        return ObjectiveEngine(trial.objective).aggregate_trial(
            trial.trial_id,
            trial.algorithm.parameters,
            ordered_results,
        )

    def aggregate_trials(
        self,
        report: ExecutionReport,
    ) -> tuple[TrialResult, ...]:
        """Aggregate every trial in stable plan order."""

        return tuple(
            self.aggregate_trial(report, trial.trial_id)
            for trial in report.plan.trials
        )

    def _execute_run(
        self,
        execution_id: str,
        execution_directory: Path,
        run: RunSpec,
        *,
        initial_snapshot: StateSnapshot | None = None,
        cancel_event: Event,
    ) -> tuple[RunResult, ArtifactManifest]:
        run_id = run.run_id
        _validate_component_id(run_id, "run_id")
        run_directory = execution_directory / run_id
        run_directory.mkdir()
        event_path = run_directory / "events.jsonl"

        algorithm_manifest = self._registry.algorithm_manifest(
            run.algorithm.algorithm_id,
            run.algorithm.version,
        )
        domain_manifest = self._registry.domain_manifest(
            run.domain.domain_id,
            run.domain.version,
        )
        for artifact in run.input_artifacts:
            if not self._artifact_store.verify(artifact):
                raise OSError(
                    f"input artifact failed integrity verification: {artifact.digest}"
                )
        artifact_publisher = RunArtifactPublisher(
            self._artifact_store,
            {
                "execution_id": execution_id,
                "experiment_id": run.experiment_id,
                "trial_id": run.trial_id,
                "run_id": run_id,
            },
        )
        algorithm = self._registry.create_algorithm(run.algorithm)
        domain = self._registry.create_domain(run.domain)

        runtime_factory = self._runtime_factories.get(run.algorithm.interface)
        if runtime_factory is None and algorithm_manifest.runtime is None:
            raise ValueError(
                "no runtime driver registered for interface "
                f"{run.algorithm.interface.value!r}"
            )

        with JsonlEventSink(event_path) as sink:
            recorder = EventRecorder(execution_id, run_id, sink)
            context = RunContext(
                run=run,
                execution_id=execution_id,
                algorithm_manifest=algorithm_manifest,
                domain_manifest=domain_manifest,
                workspace_uri=run_directory.resolve().as_uri(),
                event_log_uri=event_path.resolve().as_uri(),
                artifact_publisher=artifact_publisher,
                artifact_resolver=self._artifact_store,
                event_publisher=recorder,
                cancel_event=cancel_event,
            )
            if algorithm_manifest.runtime is not None:
                driver = self._registry.create_runtime(
                    algorithm_manifest.runtime,
                    recorder,
                )
            else:
                if runtime_factory is None:
                    raise ValueError(
                        "algorithm manifest does not select a runtime plugin"
                    )
                driver = runtime_factory(recorder)
            if driver.interface is not run.algorithm.interface:
                raise ValueError(
                    f"runtime driver {driver.interface.value!r} cannot execute "
                    f"interface {run.algorithm.interface.value!r}"
                )
            if initial_snapshot is not None and not isinstance(
                driver,
                OnlineRuntimeDriver,
            ):
                raise ValueError(
                    "branch replay requires the standard online runtime"
                )
            driver_started_at = datetime.now(timezone.utc)
            try:
                if initial_snapshot is not None:
                    result = driver.execute(
                        context,
                        algorithm,
                        domain,
                        initial_snapshot=initial_snapshot,
                    )
                else:
                    result = driver.execute(context, algorithm, domain)
            except Exception as error:
                finished_at = datetime.now(timezone.utc)
                cancelled = (
                    isinstance(error, AlgorithmExecutionCancelled)
                    or cancel_event.is_set()
                )
                status = (
                    RunStatus.CANCELLED if cancelled else RunStatus.FAILED
                )
                failure = RunFailure(
                    code=(
                        "execution_cancelled"
                        if cancelled
                        else type(error).__name__
                    ),
                    message=str(error) or type(error).__name__,
                    details={"exception_type": type(error).__name__},
                )
                result = RunResult(
                    run_id=run_id,
                    status=status,
                    metrics={},
                    objective_values=(),
                    constraint_values={},
                    constraint_violations={},
                    feasible=False,
                    started_at=driver_started_at,
                    finished_at=finished_at,
                    failure=failure,
                )
                recorder.emit(
                    EventType.RUN_FINISHED,
                    {
                        "status": status.value,
                        "metrics": {},
                        "objective_values": (),
                        "constraint_values": {},
                        "constraint_violations": {},
                        "feasible": False,
                        "failure_code": failure.code,
                    },
                    wall_time=finished_at,
                )

        unsupported_outputs = tuple(
            artifact.kind
            for artifact in result.artifacts
            if artifact.kind not in algorithm_manifest.output_artifact_kinds
            and artifact.kind is not ArtifactKind.STATE_SNAPSHOT
        )
        if unsupported_outputs:
            raise ValueError(
                f"algorithm {run.algorithm.algorithm_id!r} published undeclared "
                f"artifact kinds: {unsupported_outputs}"
            )
        foreign_outputs = tuple(
            artifact.digest
            for artifact in result.artifacts
            if artifact.metadata.get("execution_id") != execution_id
            or artifact.metadata.get("run_id") != run_id
        )
        if foreign_outputs:
            raise ValueError(
                "run outputs must be published through its ArtifactPublisher: "
                f"{foreign_outputs}"
            )

        event_artifact = artifact_publisher.publish_file(
            event_path,
            kind=ArtifactKind.EVENT_LOG,
            media_type="application/x-ndjson",
        )
        result_with_events = replace(
            result,
            artifacts=(*result.artifacts, event_artifact),
            event_log=event_artifact,
        )
        result_artifact = artifact_publisher.publish_json(
            to_jsonable(result_with_events),
            kind=ArtifactKind.REPORT,
        )
        final_result = replace(
            result_with_events,
            artifacts=(*result_with_events.artifacts, result_artifact),
        )
        manifest = self._artifact_store.write_manifest(
            f"{execution_id}.{run_id}",
            final_result.artifacts,
            metadata={
                "execution_id": execution_id,
                "experiment_id": run.experiment_id,
                "trial_id": run.trial_id,
                "run_id": run_id,
                "run_status": final_result.status.value,
                "algorithm_artifact_count": len(result.artifacts),
            },
        )
        return final_result, manifest


def _validate_component_id(value: str, name: str) -> None:
    if value in {"", ".", ".."} or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        for character in value
    ):
        raise ValueError(
            f"{name} may contain only letters, digits, dash, underscore, and dot"
        )


def _validate_execution_id(execution_id: str) -> None:
    _validate_component_id(execution_id, "execution_id")


def _raise_if_cancelled(cancel_event: Event) -> None:
    if cancel_event.is_set():
        raise AlgorithmExecutionCancelled("algorithm execution was cancelled")


def _validate_local_budget(run: RunSpec) -> None:
    budget = run.budget
    unsupported_resource_limits = tuple(
        name
        for name, value in {
            "cpu_cores": budget.cpu_cores,
            "gpu_count": budget.gpu_count,
            "memory_bytes": budget.memory_bytes,
        }.items()
        if value is not None
    )
    if unsupported_resource_limits:
        raise ValueError(
            "the in-process local orchestrator cannot enforce resource "
            f"limits: {unsupported_resource_limits}"
        )
    if (
        budget.decision_time_seconds is not None
        and run.algorithm.interface is not AlgorithmInterface.ONLINE
    ):
        raise ValueError(
            "decision_time_seconds is only valid for online algorithms"
        )

    work_limits = {
        "max_iterations": budget.max_iterations,
        "max_evaluations": budget.max_evaluations,
        "max_steps": budget.max_steps,
    }
    supported_by_interface = {
        AlgorithmInterface.ONLINE: {"max_steps"},
        AlgorithmInterface.BATCH: set(),
        AlgorithmInterface.ITERATIVE: {"max_iterations", "max_evaluations"},
        AlgorithmInterface.TRAINABLE: {
            "max_iterations",
            "max_evaluations",
            "max_steps",
        },
    }
    supported = supported_by_interface.get(run.algorithm.interface)
    if supported is None:
        return
    ignored_work_limits = tuple(
        name
        for name, value in work_limits.items()
        if value is not None and name not in supported
    )
    if ignored_work_limits:
        raise ValueError(
            f"{run.algorithm.interface.value} runtime does not enforce work limits: "
            f"{ignored_work_limits}"
        )
