"""Structural runtime contracts for algorithm and domain implementations.

Each algorithm family has its own protocol.  A fixed online rule therefore
does not pretend to be a trainer, and a batch solver does not pretend to emit
step-by-step actions.  Values written into platform events must be composed of
platform dataclasses or JSON-compatible scalars, mappings, and sequences;
domains should expose an explicit serializable view instead of relying on an
implicit fallback codec.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Mapping, Protocol, Sequence, TypeVar

from .events import EventRecorder
from .models import (
    AlgorithmInterface,
    ArtifactKind,
    ArtifactRef,
    Budget,
    Candidate,
    DecisionRequest,
    DecisionResponse,
    EventRecord,
    EventType,
    Feedback,
    MetricSet,
    ObjectiveSpec,
    ParameterSet,
    RunContext,
    RunResult,
    RuntimeManifest,
    SearchDimension,
    ScenarioRef,
    StateSnapshot,
    StepResult,
    TrialResult,
    ValidationResult,
)


ObservationT = TypeVar("ObservationT")
ActionT = TypeVar("ActionT")
ProblemT = TypeVar("ProblemT")
SolutionT = TypeVar("SolutionT")
CandidateT = TypeVar("CandidateT")
TrainingDataT = TypeVar("TrainingDataT")
EvaluationDataT = TypeVar("EvaluationDataT")
AlgorithmT = TypeVar("AlgorithmT")
DomainRuntimeT = TypeVar("DomainRuntimeT")


class ArtifactPublisher(Protocol):
    """Publish run-owned files and values into the platform artifact store."""

    def publish_bytes(
        self,
        data: bytes,
        *,
        kind: ArtifactKind,
        media_type: str,
        metadata: Mapping[str, object] | None = None,
    ) -> ArtifactRef:
        ...

    def publish_file(
        self,
        source: str | Path,
        *,
        kind: ArtifactKind,
        media_type: str,
        metadata: Mapping[str, object] | None = None,
    ) -> ArtifactRef:
        ...

    def publish_json(
        self,
        value: object,
        *,
        kind: ArtifactKind,
        metadata: Mapping[str, object] | None = None,
    ) -> ArtifactRef:
        ...


class ArtifactResolver(Protocol):
    """Read immutable input artifacts selected in a RunSpec."""

    def resolve(self, artifact: ArtifactRef) -> Path:
        ...

    def read_bytes(self, artifact: ArtifactRef) -> bytes:
        ...


class EventPublisher(Protocol):
    """Publish ordered run events for progress reporting and diagnostics."""

    def emit(
        self,
        event_type: EventType,
        payload: Mapping[str, object] | None = None,
        *,
        simulation_time: float | None = None,
        state_hash: str | None = None,
        wall_time: datetime | None = None,
    ) -> EventRecord:
        ...

class OnlineAlgorithm(Protocol[ObservationT, ActionT]):
    """Algorithm that reacts to a sequence of domain decision requests."""

    def initialize(self, context: RunContext) -> None:
        """Initialize isolated state for one run."""
        ...

    def decide(
        self,
        request: DecisionRequest[ObservationT],
    ) -> DecisionResponse[ActionT]:
        """Return the action proposed for the current visible state."""
        ...

    def observe(self, feedback: Feedback[ActionT, ObservationT]) -> None:
        """Receive the authoritative outcome of the proposed action."""
        ...

    def finalize(self) -> tuple[ArtifactRef, ...]:
        """Publish artifacts produced during the run."""
        ...


class BatchSolver(Protocol[ProblemT, SolutionT]):
    """Solver that consumes a complete problem and returns a complete solution."""

    def solve(
        self,
        problem: ProblemT,
        context: RunContext,
    ) -> Candidate[SolutionT]:
        """Solve under the objective and budget carried by ``context``."""
        ...

    def finalize(self) -> tuple[ArtifactRef, ...]:
        """Publish additional solver diagnostics or checkpoints."""
        ...


class IterativeSolver(Protocol[ProblemT, CandidateT]):
    """Anytime solver that proposes and learns from evaluated candidates."""

    def initialize(self, problem: ProblemT, context: RunContext) -> None:
        """Initialize a new independent search."""
        ...

    def propose(self, count: int) -> tuple[Candidate[CandidateT], ...]:
        """Propose candidates to be evaluated by the authoritative domain."""
        ...

    def observe(self, evaluated: Sequence[Candidate[CandidateT]]) -> None:
        """Consume candidates populated with authoritative objective values."""
        ...

    def best(self) -> Candidate[CandidateT] | None:
        """Return the best incumbent known to the solver."""
        ...

    def should_stop(self) -> bool:
        """Report whether the search has met its own stopping condition."""
        ...

    def finalize(self) -> tuple[ArtifactRef, ...]:
        """Publish additional search state or diagnostic artifacts."""
        ...


class TrainableAlgorithm(Protocol[TrainingDataT, EvaluationDataT]):
    """Algorithm that constructs an artifact from training data."""

    def fit(
        self,
        training_data: TrainingDataT,
        validation_data: EvaluationDataT | None,
        context: RunContext,
    ) -> tuple[ArtifactRef, ...]:
        """Train within the run budget and return produced artifacts."""
        ...

    def load_artifacts(
        self,
        artifacts: Sequence[ArtifactRef],
        context: RunContext,
    ) -> None:
        """Load an immutable trained state for evaluation without fitting."""
        ...

    def evaluate(
        self,
        evaluation_data: EvaluationDataT,
        context: RunContext,
    ) -> MetricSet:
        """Evaluate a trained state without changing it."""
        ...


class SearchOptimizer(Protocol):
    """Experiment-level optimizer for algorithm parameter candidates."""

    def initialize(
        self,
        search_space: Mapping[str, SearchDimension],
        objective: ObjectiveSpec,
        budget: Budget,
        seed: int,
        artifact_publisher: ArtifactPublisher,
    ) -> None:
        """Start an independent parameter search."""
        ...

    def ask(self, count: int) -> tuple[Candidate[ParameterSet], ...]:
        """Return parameter candidates that require trial evaluation."""
        ...

    def tell(self, results: Sequence[TrialResult]) -> None:
        """Supply aggregated, authoritative trial results."""
        ...

    def should_stop(self) -> bool:
        """Report whether the experiment-level search is complete."""
        ...

    def snapshot(self) -> ArtifactRef:
        """Persist optimizer state for provenance and continuation."""
        ...


class OnlineDomainSession(Protocol[ObservationT, ActionT]):
    """One isolated, authoritative online execution of a domain problem."""

    @property
    def simulation_time(self) -> float:
        ...

    @property
    def state_version(self) -> int:
        ...

    @property
    def terminated(self) -> bool:
        ...

    def reset(self) -> ObservationT:
        """Reset the session and return the first visible observation."""
        ...

    def observe(self) -> ObservationT:
        """Return the state currently visible to an online algorithm."""
        ...

    def validate(self, action: ActionT) -> ValidationResult:
        """Validate an action without mutating domain state."""
        ...

    def step(self, action: ActionT) -> StepResult[ObservationT, ActionT]:
        """Apply an action and advance the authoritative domain state."""
        ...

    def metrics(self) -> MetricSet:
        """Return current objective-independent domain metrics."""
        ...

    def close(self) -> None:
        """Release resources owned by this session."""
        ...


class ReplayableDomainSession(
    OnlineDomainSession[ObservationT, ActionT],
    Protocol[ObservationT, ActionT],
):
    """Online session that can publish and restore exact state snapshots."""

    def snapshot(self) -> StateSnapshot:
        ...

    def restore(self, snapshot: StateSnapshot) -> ObservationT:
        ...


class OnlineDomainAdapter(Protocol[ProblemT, ObservationT, ActionT]):
    """Domain boundary required only by online algorithm runtimes."""

    def load_problem(self, scenario: ScenarioRef) -> ProblemT:
        """Load an immutable domain problem from a scenario reference."""
        ...

    def create_session(
        self,
        problem: ProblemT,
        context: RunContext,
    ) -> OnlineDomainSession[ObservationT, ActionT]:
        """Create one isolated online domain session."""
        ...


class BatchDomainAdapter(Protocol[ProblemT, SolutionT]):
    """Domain boundary required by complete-solution and iterative solvers."""

    def load_problem(self, scenario: ScenarioRef) -> ProblemT:
        ...

    def validate_solution(
        self,
        problem: ProblemT,
        solution: SolutionT,
    ) -> ValidationResult:
        """Validate a complete batch solution without executing an algorithm."""
        ...

    def evaluate_solution(
        self,
        problem: ProblemT,
        solution: SolutionT,
    ) -> MetricSet:
        """Compute objective-independent metrics for a valid solution."""
        ...


class TrainingDomainAdapter(
    Protocol[ProblemT, TrainingDataT, EvaluationDataT],
):
    """Domain boundary that prepares data for a trainable algorithm."""

    def load_problem(self, scenario: ScenarioRef) -> ProblemT:
        ...

    def load_training_data(
        self,
        problem: ProblemT,
        context: RunContext,
    ) -> TrainingDataT:
        ...

    def load_validation_data(
        self,
        problem: ProblemT,
        context: RunContext,
    ) -> EvaluationDataT | None:
        ...

    def load_evaluation_data(
        self,
        problem: ProblemT,
        context: RunContext,
    ) -> EvaluationDataT:
        ...


class DomainAdapter(
    OnlineDomainAdapter[ProblemT, ObservationT, ActionT],
    BatchDomainAdapter[ProblemT, SolutionT],
    Protocol[ProblemT, ObservationT, ActionT, SolutionT],
):
    """Convenience contract for a domain supporting both execution families."""


class RuntimeDriver(Protocol[AlgorithmT, DomainRuntimeT]):
    """Execution driver specialized for one algorithm interface."""

    @property
    def interface(self) -> AlgorithmInterface:
        """Algorithm interface implemented by this driver."""
        ...

    def execute(
        self,
        context: RunContext,
        algorithm: AlgorithmT,
        domain: DomainRuntimeT,
    ) -> RunResult:
        """Execute one reproducible run and return its terminal result."""
        ...


class RuntimePlugin(Protocol):
    """Explicitly registered factory for a versioned execution driver."""

    @property
    def manifest(self) -> RuntimeManifest:
        ...

    def create(self, recorder: EventRecorder) -> RuntimeDriver[object, object]:
        ...
