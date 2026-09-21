"""Runtime drivers for the algorithm experiment platform.

The drivers in this module own execution control only.  Algorithms propose
actions or solutions, while domain adapters remain authoritative for
validation, state transitions, and metrics.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Generic, Mapping, TypeVar

from .events import EventRecorder
from .models import (
    AlgorithmInterface,
    ArtifactKind,
    ArtifactRef,
    Candidate,
    DecisionRequest,
    DecisionStatus,
    DomainCapability,
    DomainEvent,
    EventRecord,
    EventType,
    Feedback,
    InfeasibleSolutionError,
    MetricSet,
    RunContext,
    RunFailure,
    RunPurpose,
    RunResult,
    RunStatus,
    StateSnapshot,
    ValidationResult,
)
from .objectives import ObjectiveEngine, ObjectiveEvaluation
from .protocols import (
    BatchDomainAdapter,
    BatchSolver,
    IterativeSolver,
    OnlineDomainAdapter,
    OnlineDomainSession,
    OnlineAlgorithm,
    TrainableAlgorithm,
    TrainingDomainAdapter,
)
from .schema import validate_schema_value


ProblemT = TypeVar("ProblemT")
ObservationT = TypeVar("ObservationT")
ActionT = TypeVar("ActionT")
SolutionT = TypeVar("SolutionT")
CandidateT = TypeVar("CandidateT")
TrainingDataT = TypeVar("TrainingDataT")
EvaluationDataT = TypeVar("EvaluationDataT")


def _snapshot_artifact_payload(artifact: ArtifactRef) -> dict[str, object]:
    """Serialize the content identity without run-specific manifest metadata."""

    return {
        "digest": artifact.digest,
        "kind": artifact.kind.value,
        "media_type": artifact.media_type,
        "size_bytes": artifact.size_bytes,
    }


class _RuntimeDriverBase:
    """Shared result and event handling for one isolated run."""

    def __init__(self, recorder: EventRecorder | None = None) -> None:
        self._recorder = recorder

    def _begin(self, context: RunContext, interface: AlgorithmInterface) -> datetime:
        if self._recorder is not None:
            if self._recorder.execution_id != context.execution_id:
                raise ValueError(
                    "event recorder belongs to execution "
                    f"{self._recorder.execution_id}, not {context.execution_id}"
                )
            if self._recorder.run_id != context.run.run_id:
                raise ValueError(
                    f"event recorder belongs to run {self._recorder.run_id}, "
                    f"not {context.run.run_id}"
                )
        started_at = datetime.now(timezone.utc)
        self._emit(
            EventType.RUN_STARTED,
            {
                "algorithm_id": context.run.algorithm.algorithm_id,
                "algorithm_version": context.run.algorithm.version,
                "interface": interface.value,
                "domain_id": context.run.domain.domain_id,
                "domain_version": context.run.domain.version,
                "scenario_id": context.run.scenario.scenario_id,
                "scenario_digest": context.run.scenario.digest,
                "purpose": context.run.purpose.value,
                "seeds": {
                    "algorithm": context.run.seeds.algorithm,
                    "environment": context.run.seeds.environment,
                    "instance": context.run.seeds.instance,
                    "exogenous": dict(context.run.seeds.exogenous),
                },
                "run_metadata": dict(context.run.metadata),
            },
            wall_time=started_at,
        )
        return started_at

    def _evaluate(
        self,
        context: RunContext,
        metrics: MetricSet,
    ) -> ObjectiveEvaluation:
        validate_schema_value(
            metrics,
            context.domain_manifest.metric_schema,
            path="metrics",
        )
        return ObjectiveEngine(context.run.objective).evaluate_metrics([metrics])

    def _result(
        self,
        context: RunContext,
        *,
        status: RunStatus,
        metrics: MetricSet,
        evaluation: ObjectiveEvaluation | None,
        started_at: datetime,
        artifacts: tuple[ArtifactRef, ...] = (),
        failure: RunFailure | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> RunResult:
        result = RunResult(
            run_id=context.run.run_id,
            status=status,
            metrics=dict(metrics),
            objective_values=(
                () if evaluation is None else evaluation.objective_values
            ),
            constraint_values=(
                {} if evaluation is None else evaluation.constraint_values
            ),
            constraint_violations=(
                {} if evaluation is None else evaluation.constraint_violations
            ),
            feasible=(
                status is RunStatus.SUCCEEDED
                and evaluation is not None
                and evaluation.feasible
            ),
            artifacts=artifacts,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            failure=failure,
            metadata={} if metadata is None else dict(metadata),
        )
        self._emit(
            EventType.RUN_FINISHED,
            {
                "status": result.status.value,
                "metrics": dict(result.metrics),
                "objective_values": result.objective_values,
                "constraint_values": dict(result.constraint_values),
                "constraint_violations": dict(result.constraint_violations),
                "feasible": result.feasible,
                "failure_code": None if failure is None else failure.code,
            },
            wall_time=result.finished_at,
        )
        return result

    def _cancelled_result(
        self,
        context: RunContext,
        *,
        started_at: datetime,
        artifacts: tuple[ArtifactRef, ...] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> RunResult:
        return self._result(
            context,
            status=RunStatus.CANCELLED,
            metrics={},
            evaluation=None,
            started_at=started_at,
            artifacts=artifacts,
            failure=RunFailure(
                code="execution_cancelled",
                message="execution was cancelled by request",
            ),
            metadata=metadata,
        )

    def _emit(
        self,
        event_type: EventType,
        payload: Mapping[str, object],
        *,
        simulation_time: float | None = None,
        state_hash: str | None = None,
        wall_time: datetime | None = None,
    ) -> EventRecord | None:
        if self._recorder is not None:
            return self._recorder.emit(
                event_type,
                payload,
                simulation_time=simulation_time,
                state_hash=state_hash,
                wall_time=wall_time,
            )
        return None

    def _emit_domain_events(self, events: tuple[DomainEvent, ...]) -> None:
        for event in events:
            self._emit(
                EventType.DOMAIN_EVENT,
                {
                    "domain_event_type": event.event_type,
                    "payload": dict(event.payload),
                },
                simulation_time=event.simulation_time,
                state_hash=event.state_hash,
            )


class OnlineRuntimeDriver(
    _RuntimeDriverBase,
    Generic[ProblemT, ObservationT, ActionT],
):
    """Drive an online algorithm against an authoritative domain session."""

    def __init__(
        self,
        recorder: EventRecorder | None = None,
        *,
        snapshot_interval: int = 25,
    ) -> None:
        super().__init__(recorder)
        if snapshot_interval <= 0:
            raise ValueError("snapshot_interval must be positive")
        self._snapshot_interval = snapshot_interval

    @property
    def interface(self) -> AlgorithmInterface:
        return AlgorithmInterface.ONLINE

    def execute(
        self,
        context: RunContext,
        algorithm: OnlineAlgorithm[ObservationT, ActionT],
        domain: OnlineDomainAdapter[ProblemT, ObservationT, ActionT],
        *,
        initial_snapshot: StateSnapshot | None = None,
    ) -> RunResult:
        started_at = self._begin(context, self.interface)
        if context.cancel_event.is_set():
            return self._cancelled_result(context, started_at=started_at)
        started_monotonic = time.monotonic()
        session: OnlineDomainSession[ObservationT, ActionT] | None = None
        step_count = 0
        snapshot_artifacts: list[ArtifactRef] = []
        last_snapshot_step = -1

        try:
            problem = domain.load_problem(context.run.scenario)
            session = domain.create_session(problem, context)
            algorithm.initialize(context)
            if initial_snapshot is None:
                observation = session.reset()
            else:
                if (
                    DomainCapability.SNAPSHOT_RESTORE
                    not in context.domain_manifest.capabilities
                ):
                    raise ValueError(
                        "branch replay requires a snapshot-capable domain"
                    )
                observation = session.restore(initial_snapshot)
            self._emit(
                EventType.ENVIRONMENT_RESET,
                {
                    "state_version": session.state_version,
                    "restored_snapshot": (
                        None
                        if initial_snapshot is None
                        else {
                            "digest": initial_snapshot.artifact.digest,
                            "state_version": initial_snapshot.state_version,
                            "simulation_time": initial_snapshot.simulation_time,
                            "state_hash": initial_snapshot.state_hash,
                        }
                    ),
                },
                simulation_time=session.simulation_time,
                state_hash=(
                    None
                    if initial_snapshot is None
                    else initial_snapshot.state_hash
                ),
            )
            if (
                initial_snapshot is None
                and
                DomainCapability.SNAPSHOT_RESTORE
                in context.domain_manifest.capabilities
            ):
                snapshot = session.snapshot()
                snapshot_artifacts.append(snapshot.artifact)
                self._emit(
                    EventType.SNAPSHOT_CREATED,
                    {
                        "state_version": snapshot.state_version,
                        "snapshot_artifact": _snapshot_artifact_payload(
                            snapshot.artifact
                        ),
                    },
                    simulation_time=snapshot.simulation_time,
                    state_hash=snapshot.state_hash,
                )
                last_snapshot_step = 0

            terminal_status: RunStatus | None = None
            failure: RunFailure | None = None
            while not session.terminated:
                if context.cancel_event.is_set():
                    terminal_status = RunStatus.CANCELLED
                    failure = RunFailure(
                        code="execution_cancelled",
                        message="execution was cancelled by request",
                    )
                    break
                wall_time_seconds = context.run.budget.wall_time_seconds
                if (
                    wall_time_seconds is not None
                    and time.monotonic() - started_monotonic
                    >= wall_time_seconds
                ):
                    terminal_status = RunStatus.TIMED_OUT
                    failure = RunFailure(
                        code="wall_time_reached",
                        message=(
                            "run reached its wall-time limit of "
                            f"{wall_time_seconds} seconds"
                        ),
                    )
                    break
                max_steps = context.run.budget.max_steps
                if max_steps is not None and step_count >= max_steps:
                    terminal_status = RunStatus.TIMED_OUT
                    failure = RunFailure(
                        code="max_steps_reached",
                        message=f"run reached its maximum of {max_steps} steps",
                    )
                    break

                self._emit(
                    EventType.OBSERVATION_PUBLISHED,
                    {
                        "state_version": session.state_version,
                        "observation": observation,
                    },
                    simulation_time=session.simulation_time,
                )

                request_id = f"{context.run.run_id}:decision:{step_count}"
                request = DecisionRequest(
                    run_id=context.run.run_id,
                    request_id=request_id,
                    simulation_time=session.simulation_time,
                    state_version=session.state_version,
                    observation=observation,
                    objective=context.run.objective,
                    budget=context.run.budget,
                )
                self._emit(
                    EventType.DECISION_REQUESTED,
                    {
                        "request_id": request_id,
                        "state_version": request.state_version,
                    },
                    simulation_time=request.simulation_time,
                )
                decision_started = time.monotonic()
                response = algorithm.decide(request)
                decision_seconds = time.monotonic() - decision_started
                if context.cancel_event.is_set():
                    terminal_status = RunStatus.CANCELLED
                    failure = RunFailure(
                        code="execution_cancelled",
                        message="execution was cancelled by request",
                    )
                    break
                if response.request_id != request_id:
                    raise ValueError(
                        f"decision response belongs to {response.request_id}, "
                        f"expected {request_id}"
                    )
                decision_limit = context.run.budget.decision_time_seconds
                decision_timed_out = (
                    decision_limit is not None
                    and decision_seconds > decision_limit
                )
                self._emit(
                    EventType.DECISION_RETURNED,
                    {
                        "request_id": request_id,
                        "status": (
                            DecisionStatus.TIME_LIMIT.value
                            if decision_timed_out
                            else response.status.value
                        ),
                        "action": None if decision_timed_out else response.action,
                        "plan": response.plan,
                        "diagnostics": {
                            **dict(response.diagnostics),
                            "decision_seconds": decision_seconds,
                            "returned_status": response.status.value,
                        },
                    },
                    simulation_time=session.simulation_time,
                )

                if decision_timed_out:
                    terminal_status = RunStatus.TIMED_OUT
                    failure = RunFailure(
                        code="decision_time_limit",
                        message=(
                            "algorithm exceeded its per-decision limit of "
                            f"{decision_limit} seconds"
                        ),
                        details={"decision_seconds": decision_seconds},
                    )
                    break

                if response.status is DecisionStatus.INFEASIBLE:
                    terminal_status = RunStatus.INFEASIBLE
                    failure = RunFailure(
                        code="algorithm_reported_infeasible",
                        message="algorithm reported that no feasible decision exists",
                    )
                    break
                if response.action is None:
                    if response.status is DecisionStatus.TIME_LIMIT:
                        terminal_status = RunStatus.TIMED_OUT
                        failure = RunFailure(
                            code="decision_time_limit",
                            message="algorithm reached its decision limit without an action",
                        )
                        break
                    raise ValueError(
                        f"{response.status.value} decision did not contain an action"
                    )

                validation = session.validate(response.action)
                self._emit(
                    EventType.ACTION_VALIDATED,
                    {
                        "request_id": request_id,
                        "valid": validation.valid,
                        "violations": validation.violations,
                        "proposed_action": response.action,
                        "repaired_value_available": (
                            validation.repaired_value is not None
                        ),
                    },
                    simulation_time=session.simulation_time,
                )
                if not validation.valid:
                    current_observation = session.observe()
                    feedback = Feedback(
                        run_id=context.run.run_id,
                        request_id=request_id,
                        accepted=False,
                        proposed_action=response.action,
                        executed_action=None,
                        simulation_time=session.simulation_time,
                        state_version=session.state_version,
                        observation=current_observation,
                        metrics=session.metrics(),
                        terminated=session.terminated,
                        rejection_reason="; ".join(validation.violations),
                    )
                    algorithm.observe(feedback)
                    terminal_status = RunStatus.INFEASIBLE
                    failure = RunFailure(
                        code="invalid_action",
                        message="domain rejected the proposed action",
                        details={"violations": validation.violations},
                    )
                    break

                step_result = session.step(response.action)
                step_count += 1
                self._emit(
                    EventType.ACTION_EXECUTED,
                    {
                        "request_id": request_id,
                        "proposed_action": response.action,
                        "executed_action": step_result.executed_action,
                    },
                    simulation_time=step_result.simulation_time,
                )
                self._emit(
                    EventType.STATE_CHANGED,
                    {
                        "request_id": request_id,
                        "state_version": step_result.state_version,
                        "terminated": step_result.terminated,
                        "termination_reason": step_result.termination_reason,
                    },
                    simulation_time=step_result.simulation_time,
                    state_hash=step_result.state_hash,
                )
                self._emit(
                    EventType.METRIC_UPDATED,
                    {
                        "request_id": request_id,
                        "metrics": dict(step_result.metrics),
                    },
                    simulation_time=step_result.simulation_time,
                )
                self._emit_domain_events(step_result.events)
                if (
                    DomainCapability.SNAPSHOT_RESTORE
                    in context.domain_manifest.capabilities
                    and step_count % self._snapshot_interval == 0
                ):
                    snapshot = session.snapshot()
                    snapshot_artifacts.append(snapshot.artifact)
                    self._emit(
                        EventType.SNAPSHOT_CREATED,
                        {
                            "state_version": snapshot.state_version,
                            "snapshot_artifact": _snapshot_artifact_payload(
                                snapshot.artifact
                            ),
                        },
                        simulation_time=snapshot.simulation_time,
                        state_hash=snapshot.state_hash,
                    )
                    last_snapshot_step = step_count
                observation = step_result.observation
                algorithm.observe(
                    Feedback(
                        run_id=context.run.run_id,
                        request_id=request_id,
                        accepted=True,
                        proposed_action=response.action,
                        executed_action=step_result.executed_action,
                        simulation_time=step_result.simulation_time,
                        state_version=step_result.state_version,
                        observation=observation,
                        metrics=step_result.metrics,
                        events=step_result.events,
                        terminated=step_result.terminated,
                    )
                )

            metrics = session.metrics()
            evaluation = None
            if (
                DomainCapability.SNAPSHOT_RESTORE
                in context.domain_manifest.capabilities
                and step_count != last_snapshot_step
            ):
                snapshot = session.snapshot()
                snapshot_artifacts.append(snapshot.artifact)
                self._emit(
                    EventType.SNAPSHOT_CREATED,
                    {
                        "state_version": snapshot.state_version,
                        "snapshot_artifact": _snapshot_artifact_payload(
                            snapshot.artifact
                        ),
                    },
                    simulation_time=snapshot.simulation_time,
                    state_hash=snapshot.state_hash,
                )
            artifacts = (*algorithm.finalize(), *snapshot_artifacts)
            if terminal_status is None:
                evaluation = self._evaluate(context, metrics)
                terminal_status = (
                    RunStatus.SUCCEEDED
                    if evaluation.feasible
                    else RunStatus.INFEASIBLE
                )
            return self._result(
                context,
                status=terminal_status,
                metrics=metrics,
                evaluation=evaluation,
                started_at=started_at,
                artifacts=artifacts,
                failure=failure,
                metadata={"steps": step_count},
            )
        finally:
            if session is not None:
                session.close()


class BatchRuntimeDriver(_RuntimeDriverBase, Generic[ProblemT, SolutionT]):
    """Run a complete-problem solver and validate its complete solution."""

    @property
    def interface(self) -> AlgorithmInterface:
        return AlgorithmInterface.BATCH

    def execute(
        self,
        context: RunContext,
        algorithm: BatchSolver[ProblemT, SolutionT],
        domain: BatchDomainAdapter[ProblemT, SolutionT],
    ) -> RunResult:
        started_at = self._begin(context, self.interface)
        if context.cancel_event.is_set():
            return self._cancelled_result(context, started_at=started_at)
        problem = domain.load_problem(context.run.scenario)
        if context.cancel_event.is_set():
            return self._cancelled_result(context, started_at=started_at)
        try:
            candidate = algorithm.solve(problem, context)
        except InfeasibleSolutionError as error:
            return self._result(
                context,
                status=RunStatus.INFEASIBLE,
                metrics={},
                evaluation=None,
                started_at=started_at,
                artifacts=algorithm.finalize(),
                failure=RunFailure(
                    code="no_feasible_solution",
                    message=str(error),
                    details=error.details,
                ),
            )
        except TimeoutError as error:
            return self._result(
                context,
                status=RunStatus.TIMED_OUT,
                metrics={},
                evaluation=None,
                started_at=started_at,
                artifacts=algorithm.finalize(),
                failure=RunFailure(
                    code="solver_time_limit",
                    message=str(error),
                ),
            )
        if context.cancel_event.is_set():
            return self._cancelled_result(
                context,
                started_at=started_at,
                artifacts=algorithm.finalize(),
            )
        algorithm_artifacts = algorithm.finalize()
        self._emit(
            EventType.CANDIDATE_FOUND,
            {
                "candidate_id": candidate.candidate_id,
                "solution": candidate.value,
                "metadata": dict(candidate.metadata),
            },
        )
        validation = domain.validate_solution(problem, candidate.value)
        self._emit_solution_validation(candidate, validation)
        if not validation.valid:
            return self._result(
                context,
                status=RunStatus.INFEASIBLE,
                metrics={},
                evaluation=None,
                started_at=started_at,
                artifacts=algorithm_artifacts,
                failure=RunFailure(
                    code="invalid_solution",
                    message="domain rejected the complete solution",
                    details={"violations": validation.violations},
                ),
                metadata={"candidate_id": candidate.candidate_id},
            )

        metrics = domain.evaluate_solution(problem, candidate.value)
        evaluation = self._evaluate(context, metrics)
        self._emit(
            EventType.METRIC_UPDATED,
            {
                "candidate_id": candidate.candidate_id,
                "metrics": dict(metrics),
            },
        )
        status = (
            RunStatus.SUCCEEDED
            if evaluation.feasible
            else RunStatus.INFEASIBLE
        )
        solution_artifact = context.artifact_publisher.publish_json(
            candidate.value,
            kind=ArtifactKind.SOLUTION,
            metadata={"candidate_id": candidate.candidate_id},
        )
        return self._result(
            context,
            status=status,
            metrics=metrics,
            evaluation=evaluation,
            started_at=started_at,
            artifacts=(*algorithm_artifacts, solution_artifact),
            metadata={"candidate_id": candidate.candidate_id},
        )

    def _emit_solution_validation(
        self,
        candidate: Candidate[SolutionT],
        validation: ValidationResult,
    ) -> None:
        self._emit(
            EventType.ACTION_VALIDATED,
            {
                "candidate_id": candidate.candidate_id,
                "valid": validation.valid,
                "violations": validation.violations,
                "repaired_value_available": validation.repaired_value is not None,
            },
        )


class IterativeRuntimeDriver(_RuntimeDriverBase, Generic[ProblemT, CandidateT]):
    """Drive an anytime solver through propose/evaluate/observe rounds."""

    def __init__(
        self,
        recorder: EventRecorder | None = None,
        *,
        proposal_batch_size: int = 1,
    ) -> None:
        super().__init__(recorder)
        if proposal_batch_size < 1:
            raise ValueError("proposal_batch_size must be positive")
        self._proposal_batch_size = proposal_batch_size

    @property
    def interface(self) -> AlgorithmInterface:
        return AlgorithmInterface.ITERATIVE

    def execute(
        self,
        context: RunContext,
        algorithm: IterativeSolver[ProblemT, CandidateT],
        domain: BatchDomainAdapter[ProblemT, CandidateT],
    ) -> RunResult:
        started_at = self._begin(context, self.interface)
        if context.cancel_event.is_set():
            return self._cancelled_result(context, started_at=started_at)
        started_monotonic = time.monotonic()
        iterations = 0
        evaluations = 0
        evaluated: dict[
            str,
            tuple[
                Candidate[CandidateT],
                MetricSet,
                ObjectiveEvaluation | None,
                ValidationResult,
            ],
        ] = {}
        problem = domain.load_problem(context.run.scenario)
        if context.cancel_event.is_set():
            return self._cancelled_result(context, started_at=started_at)
        algorithm.initialize(problem, context)
        stop_reason = "solver"

        while not algorithm.should_stop():
            if context.cancel_event.is_set():
                stop_reason = "cancelled"
                break
            wall_time_seconds = context.run.budget.wall_time_seconds
            if (
                wall_time_seconds is not None
                and time.monotonic() - started_monotonic >= wall_time_seconds
            ):
                stop_reason = "wall_time"
                break
            max_iterations = context.run.budget.max_iterations
            if max_iterations is not None and iterations >= max_iterations:
                stop_reason = "max_iterations"
                break
            max_evaluations = context.run.budget.max_evaluations
            if max_evaluations is not None and evaluations >= max_evaluations:
                stop_reason = "max_evaluations"
                break

            proposal_count = self._proposal_batch_size
            if max_evaluations is not None:
                proposal_count = min(
                    proposal_count,
                    max_evaluations - evaluations,
                )
            proposals = algorithm.propose(proposal_count)
            if context.cancel_event.is_set():
                stop_reason = "cancelled"
                break
            if len(proposals) > proposal_count:
                raise ValueError(
                    f"solver proposed {len(proposals)} candidates after "
                    f"being asked for at most {proposal_count}"
                )
            if not proposals:
                raise ValueError(
                    "solver returned no candidates without reporting completion"
                )
            proposal_ids = tuple(candidate.candidate_id for candidate in proposals)
            if len(proposal_ids) != len(set(proposal_ids)):
                raise ValueError("solver proposed duplicate candidate IDs in one batch")
            repeated_ids = tuple(
                candidate_id
                for candidate_id in proposal_ids
                if candidate_id in evaluated
            )
            if repeated_ids:
                raise ValueError(
                    f"solver reused candidate IDs within one run: {repeated_ids}"
                )

            evaluated_batch: list[Candidate[CandidateT]] = []
            for candidate in proposals:
                if context.cancel_event.is_set():
                    stop_reason = "cancelled"
                    break
                self._emit(
                    EventType.CANDIDATE_FOUND,
                    {
                        "candidate_id": candidate.candidate_id,
                        "iteration": iterations,
                        "candidate": candidate.value,
                        "metadata": dict(candidate.metadata),
                    },
                )
                assessed = self._evaluate_candidate(
                    context,
                    domain,
                    problem,
                    candidate,
                    iteration=iterations,
                )
                evaluated[candidate.candidate_id] = assessed
                evaluated_batch.append(assessed[0])
                evaluations += 1

            if stop_reason == "cancelled":
                break
            algorithm.observe(tuple(evaluated_batch))
            iterations += 1

        if stop_reason == "cancelled":
            return self._cancelled_result(
                context,
                started_at=started_at,
                artifacts=algorithm.finalize(),
                metadata={
                    "iterations": iterations,
                    "evaluations": evaluations,
                    "stop_reason": stop_reason,
                },
            )
        incumbent = algorithm.best()
        if incumbent is None:
            artifacts = algorithm.finalize()
            budget_stopped = stop_reason in {
                "max_iterations",
                "max_evaluations",
            }
            return self._result(
                context,
                status=(
                    RunStatus.TIMED_OUT
                    if budget_stopped
                    else RunStatus.INFEASIBLE
                ),
                metrics={},
                evaluation=None,
                started_at=started_at,
                artifacts=artifacts,
                failure=RunFailure(
                    code="no_incumbent",
                    message="iterative solver finished without an incumbent",
                ),
                metadata={
                    "iterations": iterations,
                    "evaluations": evaluations,
                    "stop_reason": stop_reason,
                },
            )

        incumbent_assessment = evaluated.get(incumbent.candidate_id)
        if incumbent_assessment is None:
            max_evaluations = context.run.budget.max_evaluations
            if max_evaluations is not None and evaluations >= max_evaluations:
                artifacts = algorithm.finalize()
                return self._result(
                    context,
                    status=RunStatus.TIMED_OUT,
                    metrics={},
                    evaluation=None,
                    started_at=started_at,
                    artifacts=artifacts,
                    failure=RunFailure(
                        code="unevaluated_incumbent",
                        message=(
                            "solver returned an incumbent that could not be "
                            "evaluated within the run budget"
                        ),
                    ),
                    metadata={
                        "iterations": iterations,
                        "evaluations": evaluations,
                        "stop_reason": stop_reason,
                        "incumbent_id": incumbent.candidate_id,
                    },
                )
            incumbent_assessment = self._evaluate_candidate(
                context,
                domain,
                problem,
                incumbent,
                iteration=iterations,
            )
            evaluated[incumbent.candidate_id] = incumbent_assessment
            algorithm.observe((incumbent_assessment[0],))
            evaluations += 1

        assessed_incumbent, metrics, evaluation, validation = incumbent_assessment
        if not validation.valid:
            artifacts = algorithm.finalize()
            return self._result(
                context,
                status=RunStatus.INFEASIBLE,
                metrics={},
                evaluation=None,
                started_at=started_at,
                artifacts=artifacts,
                failure=RunFailure(
                    code="invalid_solution",
                    message="domain rejected the solver's incumbent",
                    details={"violations": validation.violations},
                ),
                metadata={
                    "iterations": iterations,
                    "evaluations": evaluations,
                    "stop_reason": stop_reason,
                    "incumbent_id": incumbent.candidate_id,
                },
            )

        if evaluation is None:
            raise RuntimeError("valid incumbent has no objective evaluation")
        status = (
            RunStatus.SUCCEEDED
            if evaluation.feasible
            else RunStatus.INFEASIBLE
        )
        artifacts = algorithm.finalize()
        solution_artifact = context.artifact_publisher.publish_json(
            assessed_incumbent.value,
            kind=ArtifactKind.SOLUTION,
            metadata={"candidate_id": assessed_incumbent.candidate_id},
        )
        return self._result(
            context,
            status=status,
            metrics=metrics,
            evaluation=evaluation,
            started_at=started_at,
            artifacts=(*artifacts, solution_artifact),
            metadata={
                "iterations": iterations,
                "evaluations": evaluations,
                "stop_reason": stop_reason,
                "incumbent_id": incumbent.candidate_id,
            },
        )

    def _evaluate_candidate(
        self,
        context: RunContext,
        domain: BatchDomainAdapter[ProblemT, CandidateT],
        problem: ProblemT,
        candidate: Candidate[CandidateT],
        *,
        iteration: int,
    ) -> tuple[
        Candidate[CandidateT],
        MetricSet,
        ObjectiveEvaluation | None,
        ValidationResult,
    ]:
        validation = domain.validate_solution(problem, candidate.value)
        self._emit(
            EventType.ACTION_VALIDATED,
            {
                "candidate_id": candidate.candidate_id,
                "iteration": iteration,
                "valid": validation.valid,
                "violations": validation.violations,
                "repaired_value_available": validation.repaired_value is not None,
            },
        )
        if not validation.valid:
            assessed = Candidate(
                candidate_id=candidate.candidate_id,
                value=candidate.value,
                objective_values=(),
                feasible=False,
                metadata={
                    **candidate.metadata,
                    "domain_violations": validation.violations,
                },
            )
            return assessed, {}, None, validation

        metrics = domain.evaluate_solution(problem, candidate.value)
        evaluation = self._evaluate(context, metrics)
        assessed = Candidate(
            candidate_id=candidate.candidate_id,
            value=candidate.value,
            objective_values=evaluation.objective_values,
            constraint_values=evaluation.constraint_values,
            constraint_violations=evaluation.constraint_violations,
            feasible=evaluation.feasible,
            metadata={
                **candidate.metadata,
                "metrics": dict(metrics),
            },
        )
        self._emit(
            EventType.METRIC_UPDATED,
            {
                "candidate_id": candidate.candidate_id,
                "iteration": iteration,
                "metrics": dict(metrics),
                "objective_values": evaluation.objective_values,
                "constraint_values": dict(evaluation.constraint_values),
                "constraint_violations": dict(evaluation.constraint_violations),
                "feasible": evaluation.feasible,
            },
        )
        return assessed, metrics, evaluation, validation


class TrainableRuntimeDriver(
    _RuntimeDriverBase,
    Generic[ProblemT, TrainingDataT, EvaluationDataT],
):
    """Fit and evaluate an artifact-producing algorithm on domain-owned data."""

    @property
    def interface(self) -> AlgorithmInterface:
        return AlgorithmInterface.TRAINABLE

    def execute(
        self,
        context: RunContext,
        algorithm: TrainableAlgorithm[TrainingDataT, EvaluationDataT],
        domain: TrainingDomainAdapter[ProblemT, TrainingDataT, EvaluationDataT],
    ) -> RunResult:
        started_at = self._begin(context, self.interface)
        if context.cancel_event.is_set():
            return self._cancelled_result(context, started_at=started_at)
        problem = domain.load_problem(context.run.scenario)
        if context.cancel_event.is_set():
            return self._cancelled_result(context, started_at=started_at)
        artifacts: tuple[ArtifactRef, ...] = ()
        if context.run.purpose is RunPurpose.TRAIN:
            training_data = domain.load_training_data(problem, context)
            validation_data = domain.load_validation_data(problem, context)
            self._emit(
                EventType.TRAINING_STARTED,
                {"has_validation_data": validation_data is not None},
            )
            try:
                artifacts = algorithm.fit(
                    training_data,
                    validation_data,
                    context,
                )
            except TimeoutError as error:
                return self._result(
                    context,
                    status=RunStatus.TIMED_OUT,
                    metrics={},
                    evaluation=None,
                    started_at=started_at,
                    failure=RunFailure(
                        code="training_time_limit",
                        message=str(error),
                    ),
                )
            if context.cancel_event.is_set():
                return self._cancelled_result(
                    context,
                    started_at=started_at,
                    artifacts=artifacts,
                )
            if not artifacts:
                raise ValueError(
                    "trainable algorithm did not publish a trained artifact"
                )
            self._emit(
                EventType.TRAINING_FINISHED,
                {
                    "artifact_digests": tuple(
                        artifact.digest for artifact in artifacts
                    )
                },
            )
        else:
            if not context.run.input_artifacts:
                raise ValueError(
                    "frozen trainable evaluation requires input artifacts"
                )
            algorithm.load_artifacts(context.run.input_artifacts, context)
            if context.cancel_event.is_set():
                return self._cancelled_result(context, started_at=started_at)
            self._emit(
                EventType.ARTIFACT_LOADED,
                {
                    "artifact_digests": tuple(
                        artifact.digest
                        for artifact in context.run.input_artifacts
                    )
                },
            )

        evaluation_data = domain.load_evaluation_data(problem, context)
        self._emit(EventType.EVALUATION_STARTED, {})
        metrics = algorithm.evaluate(evaluation_data, context)
        if context.cancel_event.is_set():
            return self._cancelled_result(
                context,
                started_at=started_at,
                artifacts=artifacts,
            )
        evaluation = self._evaluate(context, metrics)
        self._emit(
            EventType.EVALUATION_FINISHED,
            {
                "metrics": dict(metrics),
                "objective_values": evaluation.objective_values,
                "constraint_values": dict(evaluation.constraint_values),
                "constraint_violations": dict(
                    evaluation.constraint_violations
                ),
                "feasible": evaluation.feasible,
            },
        )
        status = (
            RunStatus.SUCCEEDED
            if evaluation.feasible
            else RunStatus.INFEASIBLE
        )
        return self._result(
            context,
            status=status,
            metrics=metrics,
            evaluation=evaluation,
            started_at=started_at,
            artifacts=artifacts,
        )
