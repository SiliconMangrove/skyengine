"""Experiment-level parameter search with sealed benchmark evaluation."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import cast

from .artifacts import ArtifactManifest, LocalArtifactStore, RunArtifactPublisher
from .compiler import ExperimentCompiler
from .events import EventRecorder, JsonlEventSink
from .models import (
    AlgorithmInterface,
    AlgorithmRef,
    ArtifactKind,
    ArtifactRef,
    Budget,
    ComparisonMode,
    EventType,
    ExperimentSpec,
    ParameterSet,
    RunPurpose,
    ScenarioRef,
    TrialResult,
    TrialStatus,
)
from .objectives import ObjectiveEngine
from .orchestrator import (
    AlgorithmExecutionCancelled,
    ExecutionReport,
    LocalOrchestrator,
    RuntimeFactory,
)
from .protocols import SearchOptimizer
from .registry import PlatformRegistry, default_registry
from .serialization import to_jsonable


@dataclass(frozen=True, slots=True)
class TuningReport:
    """Terminal provenance for optimization and sealed benchmark runs."""

    execution_id: str
    experiment: ExperimentSpec
    candidate_results: tuple[TrialResult, ...]
    incumbents: tuple[TrialResult, ...]
    benchmark_results: tuple[TrialResult, ...]
    training_reports: tuple[ExecutionReport, ...]
    optimization_reports: tuple[ExecutionReport, ...]
    benchmark_reports: tuple[ExecutionReport, ...]
    optimizer_checkpoint: ArtifactRef
    event_log: ArtifactRef
    report_artifact: ArtifactRef
    artifact_manifest: ArtifactManifest
    started_at: datetime
    finished_at: datetime


class TuningOrchestrator:
    """Drive ask/tell search and isolate benchmark data from candidate choice."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        registry: PlatformRegistry = default_registry,
        artifact_store: LocalArtifactStore | None = None,
        runtime_factories: Mapping[AlgorithmInterface, RuntimeFactory] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._registry = registry
        self._artifact_store = artifact_store or LocalArtifactStore(
            self._workspace_root / "artifacts"
        )
        self._compiler = ExperimentCompiler(registry)
        self._orchestrator = LocalOrchestrator(
            self._workspace_root,
            registry=registry,
            artifact_store=self._artifact_store,
            runtime_factories=runtime_factories,
        )

    def execute(
        self,
        experiment: ExperimentSpec,
        *,
        execution_id: str,
        cancel_event: Event | None = None,
    ) -> TuningReport:
        """Optimize on tuning scenarios, then evaluate incumbents on benchmark."""

        _validate_execution_id(execution_id)
        active_cancel_event = cancel_event if cancel_event is not None else Event()
        _raise_if_cancelled(active_cancel_event)
        self._compiler.validate(experiment)
        if experiment.purpose is not RunPurpose.TUNE or experiment.tuning is None:
            raise ValueError("TuningOrchestrator requires a tune experiment")

        tuning_directory = self._workspace_root / "tuning" / execution_id
        if tuning_directory.exists():
            raise FileExistsError(
                f"tuning workspace already exists: {execution_id}"
            )
        tuning_directory.mkdir(parents=True)
        event_path = tuning_directory / "events.jsonl"
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        tuning = experiment.tuning
        target = experiment.algorithms[0]
        publisher = RunArtifactPublisher(
            self._artifact_store,
            {
                "execution_id": execution_id,
                "experiment_id": experiment.experiment_id,
                "experiment": experiment,
                "scope": "tuning",
            },
        )
        optimizer = cast(
            SearchOptimizer,
            self._registry.create_algorithm(tuning.optimizer),
        )
        declared_optimizer_budget = tuning.optimizer.budget or Budget()
        optimizer_budget = replace(
            declared_optimizer_budget,
            max_evaluations=(
                tuning.max_trials
                if declared_optimizer_budget.max_evaluations is None
                else min(
                    tuning.max_trials,
                    declared_optimizer_budget.max_evaluations,
                )
            ),
        )
        optimizer.initialize(
            tuning.search_space,
            experiment.objective,
            optimizer_budget,
            experiment.base_seed,
            publisher,
        )

        candidate_results: list[TrialResult] = []
        optimization_reports: list[ExecutionReport] = []
        training_reports: list[ExecutionReport] = []
        benchmark_results: list[TrialResult] = []
        benchmark_reports: list[ExecutionReport] = []
        candidate_ids: set[str] = set()
        parameter_keys: set[str] = set()
        frozen_artifacts_by_candidate: dict[str, tuple[ArtifactRef, ...]] = {}
        stop_reason = "optimizer"

        with JsonlEventSink(event_path) as sink:
            events = EventRecorder(execution_id, "tuning", sink)
            events.emit(
                EventType.TUNING_STARTED,
                {
                    "optimizer_id": tuning.optimizer.algorithm_id,
                    "target_algorithm_id": target.algorithm_id,
                    "max_trials": tuning.max_trials,
                    "batch_size": tuning.batch_size,
                    "tuning_scenarios": tuple(
                        scenario.scenario_id for scenario in experiment.scenarios
                    ),
                    "training_scenarios": tuple(
                        scenario.scenario_id
                        for scenario in tuning.training_scenarios
                    ),
                    "benchmark_scenarios": tuple(
                        scenario.scenario_id
                        for scenario in tuning.benchmark_scenarios
                    ),
                },
            )

            while (
                len(candidate_results) < tuning.max_trials
                and not optimizer.should_stop()
            ):
                _raise_if_cancelled(active_cancel_event)
                if (
                    optimizer_budget.wall_time_seconds is not None
                    and time.monotonic() - started_monotonic
                    >= optimizer_budget.wall_time_seconds
                ):
                    stop_reason = "wall_time"
                    break
                request_count = min(
                    tuning.batch_size,
                    tuning.max_trials - len(candidate_results),
                )
                proposed = optimizer.ask(request_count)
                if not proposed:
                    if optimizer.should_stop():
                        stop_reason = "optimizer"
                        break
                    raise ValueError(
                        "search optimizer stopped producing candidates before "
                        "reporting should_stop"
                    )
                if len(proposed) > request_count:
                    raise ValueError(
                        "search optimizer returned more candidates than requested"
                    )

                assessed_batch: list[TrialResult] = []
                for candidate in proposed:
                    _raise_if_cancelled(active_cancel_event)
                    if not candidate.candidate_id:
                        raise ValueError("candidate_id must not be empty")
                    if candidate.candidate_id in candidate_ids:
                        raise ValueError(
                            "search optimizer repeated candidate_id "
                            f"{candidate.candidate_id!r}"
                        )
                    parameter_key = _parameter_key(candidate.value)
                    if parameter_key in parameter_keys:
                        raise ValueError(
                            "search optimizer repeated an evaluated parameter set"
                        )
                    candidate_ids.add(candidate.candidate_id)
                    parameter_keys.add(parameter_key)
                    candidate_index = len(candidate_results) + len(assessed_batch)
                    events.emit(
                        EventType.CANDIDATE_PROPOSED,
                        {
                            "candidate_id": candidate.candidate_id,
                            "parameters": candidate.value,
                            "candidate_index": candidate_index,
                        },
                    )

                    candidate_target = replace(
                        target,
                        parameters={**target.parameters, **candidate.value},
                    )
                    training_execution_id: str | None = None
                    if target.interface is AlgorithmInterface.TRAINABLE:
                        training_experiment = _derived_experiment(
                            experiment,
                            candidate_target,
                            scenarios=tuning.training_scenarios,
                            purpose=RunPurpose.TRAIN,
                            candidate_id=candidate.candidate_id,
                        )
                        training_execution_id = (
                            f"{execution_id}.candidate-{candidate_index:06d}.train"
                        )
                        training_report = self._orchestrator.execute(
                            self._compiler.compile(training_experiment),
                            execution_id=training_execution_id,
                            cancel_event=active_cancel_event,
                        )
                        training_reports.append(training_report)
                        training_result = self._orchestrator.aggregate_trials(
                            training_report
                        )[0]
                        if training_result.status is not TrialStatus.SUCCEEDED:
                            assessed = replace(
                                training_result,
                                candidate_id=candidate.candidate_id,
                                parameters=candidate.value,
                            )
                            assessed_batch.append(assessed)
                            events.emit(
                                EventType.CANDIDATE_EVALUATED,
                                {
                                    "candidate_id": candidate.candidate_id,
                                    "status": assessed.status.value,
                                    "objective_values": (),
                                    "constraint_values": {},
                                    "constraint_violations": {},
                                    "feasible": False,
                                    "failure_code": (
                                        None
                                        if assessed.failure is None
                                        else assessed.failure.code
                                    ),
                                    "child_execution_id": None,
                                    "training_execution_id": training_execution_id,
                                },
                            )
                            continue
                        frozen_artifacts = _frozen_artifacts(
                            target,
                            training_report,
                            self._registry,
                        )
                        frozen_artifacts_by_candidate[
                            candidate.candidate_id
                        ] = frozen_artifacts
                        candidate_target = replace(
                            candidate_target,
                            input_artifacts=frozen_artifacts,
                        )
                    evaluation_experiment = _derived_experiment(
                        experiment,
                        candidate_target,
                        scenarios=experiment.scenarios,
                        purpose=RunPurpose.VALIDATE,
                        candidate_id=candidate.candidate_id,
                    )
                    child_execution_id = (
                        f"{execution_id}.candidate-{candidate_index:06d}.evaluate"
                    )
                    child_report = self._orchestrator.execute(
                        self._compiler.compile(evaluation_experiment),
                        execution_id=child_execution_id,
                        cancel_event=active_cancel_event,
                    )
                    optimization_reports.append(child_report)
                    trial_result = self._orchestrator.aggregate_trials(
                        child_report
                    )[0]
                    assessed = replace(
                        trial_result,
                        candidate_id=candidate.candidate_id,
                        parameters=candidate.value,
                    )
                    assessed_batch.append(assessed)
                    events.emit(
                        EventType.CANDIDATE_EVALUATED,
                        {
                            "candidate_id": candidate.candidate_id,
                            "status": assessed.status.value,
                            "objective_values": assessed.objective_values,
                            "constraint_values": assessed.constraint_values,
                            "constraint_violations": (
                                assessed.constraint_violations
                            ),
                            "feasible": assessed.feasible,
                            "failure_code": (
                                None
                                if assessed.failure is None
                                else assessed.failure.code
                            ),
                            "child_execution_id": child_execution_id,
                            "training_execution_id": training_execution_id,
                        },
                    )

                optimizer.tell(tuple(assessed_batch))
                candidate_results.extend(assessed_batch)

            if len(candidate_results) >= tuning.max_trials:
                stop_reason = "max_trials"

            _raise_if_cancelled(active_cancel_event)
            if not candidate_results:
                raise ValueError("search optimizer evaluated no candidates")
            if not any(
                result.status is TrialStatus.SUCCEEDED
                for result in candidate_results
            ):
                failures = tuple(
                    (
                        result.candidate_id,
                        None if result.failure is None else result.failure.code,
                    )
                    for result in candidate_results
                )
                raise ValueError(
                    "all tuning candidates failed; no incumbent can be selected: "
                    f"{failures}"
                )
            incumbents = _select_incumbents(
                experiment,
                tuple(candidate_results),
            )

            if tuning.benchmark_scenarios:
                events.emit(
                    EventType.BENCHMARK_STARTED,
                    {
                        "incumbent_ids": tuple(
                            result.candidate_id for result in incumbents
                        ),
                        "scenario_ids": tuple(
                            scenario.scenario_id
                            for scenario in tuning.benchmark_scenarios
                        ),
                    },
                )
                for benchmark_index, incumbent in enumerate(incumbents):
                    _raise_if_cancelled(active_cancel_event)
                    candidate_id = incumbent.candidate_id
                    if candidate_id is None:
                        raise ValueError("tuning incumbent lacks candidate_id")
                    frozen_artifacts: tuple[ArtifactRef, ...] = ()
                    if target.interface is AlgorithmInterface.TRAINABLE:
                        frozen_artifacts = frozen_artifacts_by_candidate[
                            candidate_id
                        ]
                    benchmark_experiment = _benchmark_experiment(
                        experiment,
                        target,
                        incumbent,
                        frozen_artifacts,
                    )
                    benchmark_plan = self._compiler.compile(
                        benchmark_experiment
                    )
                    benchmark_execution_id = (
                        f"{execution_id}.benchmark-{benchmark_index:06d}"
                    )
                    benchmark_report = self._orchestrator.execute(
                        benchmark_plan,
                        execution_id=benchmark_execution_id,
                        cancel_event=active_cancel_event,
                    )
                    benchmark_reports.append(benchmark_report)
                    benchmark_result = replace(
                        self._orchestrator.aggregate_trials(
                            benchmark_report
                        )[0],
                        candidate_id=candidate_id,
                    )
                    benchmark_results.append(benchmark_result)
                events.emit(
                    EventType.BENCHMARK_FINISHED,
                    {
                        "results": tuple(
                            {
                                "candidate_id": result.candidate_id,
                                "status": result.status.value,
                                "objective_values": result.objective_values,
                                "feasible": result.feasible,
                                "failure_code": (
                                    None
                                    if result.failure is None
                                    else result.failure.code
                                ),
                            }
                            for result in benchmark_results
                        )
                    },
                )

            _raise_if_cancelled(active_cancel_event)
            checkpoint = optimizer.snapshot()
            if checkpoint.kind is not ArtifactKind.CHECKPOINT:
                raise ValueError(
                    "search optimizer snapshot must be a checkpoint artifact"
                )
            if checkpoint.metadata.get("execution_id") != execution_id:
                raise ValueError(
                    "search optimizer snapshot must use the tuning artifact publisher"
                )
            if not self._artifact_store.verify(checkpoint):
                raise OSError("search optimizer checkpoint failed integrity verification")
            events.emit(
                EventType.TUNING_FINISHED,
                {
                    "evaluated_candidates": len(candidate_results),
                    "stop_reason": stop_reason,
                    "incumbent_ids": tuple(
                        result.candidate_id for result in incumbents
                    ),
                    "optimizer_checkpoint": checkpoint.digest,
                },
            )

        _raise_if_cancelled(active_cancel_event)
        event_log = publisher.publish_file(
            event_path,
            kind=ArtifactKind.EVENT_LOG,
            media_type="application/x-ndjson",
        )
        finished_at = datetime.now(timezone.utc)
        report_artifact = publisher.publish_json(
            {
                "execution_id": execution_id,
                "experiment_id": experiment.experiment_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "candidate_results": tuple(candidate_results),
                "incumbents": incumbents,
                "benchmark_results": tuple(benchmark_results),
                "optimization_execution_ids": tuple(
                    report.execution_id for report in optimization_reports
                ),
                "training_execution_ids": tuple(
                    report.execution_id for report in training_reports
                ),
                "benchmark_execution_ids": tuple(
                    report.execution_id for report in benchmark_reports
                ),
                "optimizer_checkpoint": checkpoint,
                "event_log": event_log,
            },
            kind=ArtifactKind.REPORT,
        )
        manifest = self._artifact_store.write_manifest(
            execution_id,
            (checkpoint, event_log, report_artifact),
            metadata={
                "execution_id": execution_id,
                "experiment_id": experiment.experiment_id,
                "candidate_count": len(candidate_results),
                "incumbent_count": len(incumbents),
                "training_execution_count": len(training_reports),
                "benchmark_run_count": len(benchmark_reports),
            },
        )
        return TuningReport(
            execution_id=execution_id,
            experiment=experiment,
            candidate_results=tuple(candidate_results),
            incumbents=incumbents,
            benchmark_results=tuple(benchmark_results),
            training_reports=tuple(training_reports),
            optimization_reports=tuple(optimization_reports),
            benchmark_reports=tuple(benchmark_reports),
            optimizer_checkpoint=checkpoint,
            event_log=event_log,
            report_artifact=report_artifact,
            artifact_manifest=manifest,
            started_at=started_at,
            finished_at=finished_at,
        )


def _derived_experiment(
    experiment: ExperimentSpec,
    target: AlgorithmRef,
    *,
    scenarios: tuple[ScenarioRef, ...],
    purpose: RunPurpose,
    candidate_id: str,
) -> ExperimentSpec:
    return replace(
        experiment,
        algorithms=(target,),
        scenarios=scenarios,
        purpose=purpose,
        repetitions=(1 if purpose is RunPurpose.TRAIN else experiment.repetitions),
        tuning=None,
        metadata={
            **experiment.metadata,
            "tuning_candidate_id": candidate_id,
            "source_purpose": RunPurpose.TUNE.value,
        },
    )


def _benchmark_experiment(
    experiment: ExperimentSpec,
    target: AlgorithmRef,
    incumbent: TrialResult,
    frozen_artifacts: tuple[ArtifactRef, ...],
) -> ExperimentSpec:
    tuning = experiment.tuning
    if tuning is None:
        raise ValueError("tuning definition is missing")
    benchmark_target = replace(
        target,
        parameters={**target.parameters, **incumbent.parameters},
        input_artifacts=(frozen_artifacts or target.input_artifacts),
    )
    return replace(
        experiment,
        algorithms=(benchmark_target,),
        scenarios=tuning.benchmark_scenarios,
        purpose=RunPurpose.EVALUATE,
        tuning=None,
        metadata={
            **experiment.metadata,
            "benchmark_candidate_id": incumbent.candidate_id,
            "sealed_benchmark": True,
        },
    )


def _frozen_artifacts(
    target: AlgorithmRef,
    report: ExecutionReport,
    registry: PlatformRegistry,
) -> tuple[ArtifactRef, ...]:
    manifest = registry.algorithm_manifest(target.algorithm_id, target.version)
    trained_kinds = {
        ArtifactKind.MODEL,
        ArtifactKind.PARAMETERS,
        ArtifactKind.CHECKPOINT,
    }
    artifacts = tuple(
        artifact
        for result in report.run_results
        for artifact in result.artifacts
        if artifact.kind in trained_kinds
        and artifact.kind in manifest.input_artifact_kinds
    )
    if not artifacts:
        raise ValueError(
            "trainable benchmark evaluation requires a trained artifact kind "
            "declared as an algorithm input"
        )
    return artifacts


def _select_incumbents(
    experiment: ExperimentSpec,
    results: tuple[TrialResult, ...],
) -> tuple[TrialResult, ...]:
    results = tuple(
        result
        for result in results
        if result.status is TrialStatus.SUCCEEDED
    )
    if not results:
        raise ValueError("cannot select an incumbent from failed trials")
    engine = ObjectiveEngine(experiment.objective)
    evaluations = tuple(engine.evaluation_from_trial(result) for result in results)
    if experiment.objective.mode is not ComparisonMode.PARETO:
        best_index = min(
            range(len(results)),
            key=lambda index: engine.sort_key(evaluations[index]),
        )
        return (results[best_index],)
    return tuple(
        result
        for index, result in enumerate(results)
        if not any(
            engine.dominates(other, evaluations[index])
            for other_index, other in enumerate(evaluations)
            if other_index != index
        )
    )


def _parameter_key(parameters: ParameterSet) -> str:
    return json.dumps(
        to_jsonable(parameters),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _validate_execution_id(value: str) -> None:
    if value in {"", ".", ".."} or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        for character in value
    ):
        raise ValueError(
            "execution_id may contain only letters, digits, dash, underscore, and dot"
        )


def _raise_if_cancelled(cancel_event: Event) -> None:
    if cancel_event.is_set():
        raise AlgorithmExecutionCancelled("tuning execution was cancelled")
