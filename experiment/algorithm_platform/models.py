"""Core value objects for the algorithm experiment platform.

The models in this module describe experiments and the messages exchanged at
runtime.  They deliberately contain no reinforcement-learning concepts and no
domain-specific scheduling structures.
"""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import StrEnum
from threading import Event
from types import MappingProxyType
from typing import TYPE_CHECKING, Generic, Mapping, TypeAlias, TypeVar


if TYPE_CHECKING:
    from .protocols import ArtifactPublisher, ArtifactResolver, EventPublisher


ParameterValue: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | tuple["ParameterValue", ...]
    | Mapping[str, "ParameterValue"]
)
ParameterSet: TypeAlias = Mapping[str, ParameterValue]
MetricSet: TypeAlias = Mapping[str, float]
PLATFORM_PROTOCOL_VERSION = "1.0"


class InfeasibleSolutionError(RuntimeError):
    """An algorithm proved that a problem has no feasible solution."""

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = {} if details is None else dict(details)


def _freeze_value(value: object) -> object:
    if isinstance(value, MappingABC):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


class _DeepFrozen:
    """Deep-freeze container fields after frozen dataclass construction."""

    __slots__ = ()

    def __post_init__(self) -> None:
        for definition in fields(self):
            current = getattr(self, definition.name)
            frozen = _freeze_value(current)
            if frozen is not current:
                object.__setattr__(self, definition.name, frozen)


class AlgorithmInterface(StrEnum):
    """Execution contracts an algorithm implementation can provide."""

    ONLINE = "online"
    BATCH = "batch"
    ITERATIVE = "iterative"
    TRAINABLE = "trainable"
    SEARCH_OPTIMIZER = "search_optimizer"
    PLUGIN = "plugin"


class DomainCapability(StrEnum):
    """Optional facilities exposed by a domain adapter."""

    ONLINE_EXECUTION = "online_execution"
    BATCH_EVALUATION = "batch_evaluation"
    TRAINING_DATA = "training_data"
    SNAPSHOT_RESTORE = "snapshot_restore"
    DETERMINISTIC_REPLAY = "deterministic_replay"


class RunPurpose(StrEnum):
    """Why a run exists in an experiment."""

    TRAIN = "train"
    VALIDATE = "validate"
    EVALUATE = "evaluate"
    TUNE = "tune"
    COMPARE = "compare"
    REPLAY = "replay"


class RunStatus(StrEnum):
    """Lifecycle state of an executable run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INFEASIBLE = "infeasible"


class TrialStatus(StrEnum):
    """Aggregate lifecycle state of a trial."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExperimentStatus(StrEnum):
    """Aggregate lifecycle state of an experiment."""

    DRAFT = "draft"
    COMPILED = "compiled"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionStatus(StrEnum):
    """Lifecycle of one concrete standard or tuning launch."""

    DRAFT = "draft"
    COMPILED = "compiled"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Direction(StrEnum):
    """Optimization direction of an objective component."""

    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class ComparisonMode(StrEnum):
    """How multiple objective components are compared."""

    SINGLE = "single"
    WEIGHTED_SUM = "weighted_sum"
    LEXICOGRAPHIC = "lexicographic"
    PARETO = "pareto"


class SearchDimensionKind(StrEnum):
    """Supported parameter domains for experiment-level search."""

    CATEGORICAL = "categorical"
    INTEGER = "integer"
    FLOAT = "float"


class Aggregation(StrEnum):
    """How per-run metric observations are reduced."""

    MEAN = "mean"
    MEDIAN = "median"
    MIN = "min"
    MAX = "max"
    SUM = "sum"
    LAST = "last"


class ConstraintOperator(StrEnum):
    """Comparison performed between a metric and its threshold."""

    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "le"
    EQUAL = "eq"
    GREATER_THAN_OR_EQUAL = "ge"
    GREATER_THAN = "gt"


class DecisionStatus(StrEnum):
    """Outcome reported for a decision request."""

    FEASIBLE = "feasible"
    OPTIMAL = "optimal"
    INFEASIBLE = "infeasible"
    TIME_LIMIT = "time_limit"


class ArtifactKind(StrEnum):
    """Portable artifact categories understood by the control plane."""

    MODEL = "model"
    PARAMETERS = "parameters"
    SOLUTION = "solution"
    CHECKPOINT = "checkpoint"
    EXECUTION_PLAN = "execution_plan"
    EVENT_LOG = "event_log"
    STATE_SNAPSHOT = "state_snapshot"
    REPORT = "report"
    DATASET = "dataset"
    OTHER = "other"


class EventType(StrEnum):
    """Platform-level event categories used by replay and diagnostics."""

    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    TRAINING_STARTED = "training_started"
    TRAINING_PROGRESS = "training_progress"
    TRAINING_FINISHED = "training_finished"
    EVALUATION_STARTED = "evaluation_started"
    EVALUATION_FINISHED = "evaluation_finished"
    ARTIFACT_LOADED = "artifact_loaded"
    TUNING_STARTED = "tuning_started"
    CANDIDATE_PROPOSED = "candidate_proposed"
    CANDIDATE_EVALUATED = "candidate_evaluated"
    BENCHMARK_STARTED = "benchmark_started"
    BENCHMARK_FINISHED = "benchmark_finished"
    TUNING_FINISHED = "tuning_finished"
    ENVIRONMENT_RESET = "environment_reset"
    OBSERVATION_PUBLISHED = "observation_published"
    DECISION_REQUESTED = "decision_requested"
    CANDIDATE_FOUND = "candidate_found"
    DECISION_RETURNED = "decision_returned"
    ACTION_VALIDATED = "action_validated"
    ACTION_EXECUTED = "action_executed"
    STATE_CHANGED = "state_changed"
    METRIC_UPDATED = "metric_updated"
    ARTIFACT_CREATED = "artifact_created"
    CHECKPOINT_CREATED = "checkpoint_created"
    SNAPSHOT_CREATED = "snapshot_created"
    ALGORITHM_EVENT = "algorithm_event"
    DOMAIN_EVENT = "domain_event"


@dataclass(frozen=True, slots=True)
class SchemaRef(_DeepFrozen):
    """Reference to a versioned schema published by a registry."""

    name: str
    version: str
    uri: str | None = None


@dataclass(frozen=True, slots=True)
class Budget(_DeepFrozen):
    """Hard resource and work limits for an algorithm invocation or run."""

    wall_time_seconds: float | None = None
    decision_time_seconds: float | None = None
    max_iterations: int | None = None
    max_evaluations: int | None = None
    max_steps: int | None = None
    cpu_cores: float | None = None
    gpu_count: int | None = None
    memory_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class ObjectiveComponent(_DeepFrozen):
    """One named metric participating in an optimization objective."""

    name: str
    metric: str
    direction: Direction
    weight: float = 1.0
    aggregation: Aggregation = Aggregation.MEAN


@dataclass(frozen=True, slots=True)
class ConstraintSpec(_DeepFrozen):
    """A feasibility rule evaluated from a named metric."""

    metric: str
    operator: ConstraintOperator
    threshold: float
    aggregation: Aggregation = Aggregation.MAX
    tolerance: float = 0.0
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ObjectiveSpec(_DeepFrozen):
    """Complete comparison rule for trial and run results."""

    mode: ComparisonMode
    components: tuple[ObjectiveComponent, ...]
    constraints: tuple[ConstraintSpec, ...] = ()
    feasibility_first: bool = True


@dataclass(frozen=True, slots=True)
class ArtifactRef(_DeepFrozen):
    """Content-addressed reference to a run input or output."""

    digest: str
    kind: ArtifactKind
    media_type: str
    size_bytes: int
    uri: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EventRecord(_DeepFrozen):
    """Append-only event persisted for replay and diagnostics."""

    sequence: int
    event_type: EventType
    execution_id: str
    run_id: str
    wall_time: datetime
    simulation_time: float | None = None
    state_hash: str | None = None
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeRef(_DeepFrozen):
    """Exact runtime plugin selected by an algorithm manifest."""

    runtime_id: str
    version: str


@dataclass(frozen=True, slots=True)
class RuntimeManifest(_DeepFrozen):
    """Versioned capability declaration for an execution driver plugin."""

    runtime_id: str
    name: str
    version: str
    protocol_version: str
    interfaces: frozenset[AlgorithmInterface]
    entrypoint: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AlgorithmManifest(_DeepFrozen):
    """Registry metadata and entry points published by an algorithm package."""

    algorithm_id: str
    name: str
    version: str
    protocol_version: str
    interfaces: frozenset[AlgorithmInterface]
    entrypoints: Mapping[AlgorithmInterface, str]
    supported_domains: frozenset[str] = frozenset()
    parameter_schema: Mapping[str, object] = field(default_factory=dict)
    input_artifact_kinds: frozenset[ArtifactKind] = frozenset()
    minimum_input_artifacts: Mapping[AlgorithmInterface, int] = field(
        default_factory=dict
    )
    output_artifact_kinds: frozenset[ArtifactKind] = frozenset()
    runtime: RuntimeRef | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DomainManifest(_DeepFrozen):
    """Registry metadata and schemas published by a domain package."""

    domain_id: str
    name: str
    version: str
    protocol_version: str
    adapter_entrypoint: str
    capabilities: frozenset[DomainCapability]
    problem_schema: SchemaRef | None = None
    observation_schema: SchemaRef | None = None
    action_schema: SchemaRef | None = None
    solution_schema: SchemaRef | None = None
    metric_schema: Mapping[str, object] = field(default_factory=dict)
    parameter_schema: Mapping[str, object] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AlgorithmRef(_DeepFrozen):
    """A selected algorithm version and execution interface."""

    algorithm_id: str
    version: str
    interface: AlgorithmInterface
    budget: Budget | None = None
    parameters: ParameterSet = field(default_factory=dict)
    input_artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True, slots=True)
class DomainRef(_DeepFrozen):
    """A selected domain version and its configuration."""

    domain_id: str
    version: str
    parameters: ParameterSet = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScenarioRef(_DeepFrozen):
    """Immutable reference to one problem instance or scenario."""

    scenario_id: str
    uri: str
    digest: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchDimension(_DeepFrozen):
    """One typed dimension in an algorithm parameter search space."""

    kind: SearchDimensionKind
    choices: tuple[ParameterValue, ...] = ()
    low: float | int | None = None
    high: float | int | None = None
    step: float | int | None = None
    logarithmic: bool = False


@dataclass(frozen=True, slots=True)
class TuningSpec(_DeepFrozen):
    """Experiment-level ask/tell parameter optimization definition."""

    optimizer: AlgorithmRef
    search_space: Mapping[str, SearchDimension]
    max_trials: int
    batch_size: int = 1
    training_scenarios: tuple[ScenarioRef, ...] = ()
    benchmark_scenarios: tuple[ScenarioRef, ...] = ()


@dataclass(frozen=True, slots=True)
class RunSeeds(_DeepFrozen):
    """Separated random streams used to reproduce a run fairly."""

    algorithm: int
    environment: int
    instance: int
    exogenous: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExperimentSpec(_DeepFrozen):
    """User-authored definition of an experiment before compilation."""

    experiment_id: str
    name: str
    domain: DomainRef
    algorithms: tuple[AlgorithmRef, ...]
    scenarios: tuple[ScenarioRef, ...]
    objective: ObjectiveSpec
    purpose: RunPurpose
    budget: Budget = Budget()
    repetitions: int = 1
    base_seed: int = 0
    tuning: TuningSpec | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TrialSpec(_DeepFrozen):
    """One fixed algorithm version and parameter candidate."""

    trial_id: str
    experiment_id: str
    algorithm: AlgorithmRef
    objective: ObjectiveSpec
    budget: Budget
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunSpec(_DeepFrozen):
    """Smallest independently reproducible execution unit."""

    run_id: str
    trial_id: str
    experiment_id: str
    domain: DomainRef
    algorithm: AlgorithmRef
    scenario: ScenarioRef
    objective: ObjectiveSpec
    purpose: RunPurpose
    budget: Budget
    seeds: RunSeeds
    repetition: int = 0
    input_artifacts: tuple[ArtifactRef, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionPlan(_DeepFrozen):
    """Frozen experiment graph produced by an experiment compiler."""

    experiment: ExperimentSpec
    trials: tuple[TrialSpec, ...]
    runs: tuple[RunSpec, ...]
    compiled_at: datetime
    plan_digest: str


@dataclass(frozen=True, slots=True)
class RunContext(_DeepFrozen):
    """Immutable execution context delivered to algorithms and domains."""

    run: RunSpec
    execution_id: str
    algorithm_manifest: AlgorithmManifest
    domain_manifest: DomainManifest
    workspace_uri: str
    event_log_uri: str
    artifact_publisher: ArtifactPublisher
    artifact_resolver: ArtifactResolver
    event_publisher: EventPublisher
    cancel_event: Event = field(default_factory=Event)


@dataclass(frozen=True, slots=True)
class RunFailure(_DeepFrozen):
    """Machine-readable failure captured by the runtime boundary."""

    code: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionRecord(_DeepFrozen):
    """Durable control-plane state for one actual platform launch."""

    execution_id: str
    experiment_id: str
    purpose: RunPurpose
    status: ExecutionStatus
    created_at: datetime
    updated_at: datetime
    plan_digest: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result_manifest_id: str | None = None
    failure: RunFailure | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunResult(_DeepFrozen):
    """Terminal result of one run."""

    run_id: str
    status: RunStatus
    metrics: MetricSet
    objective_values: tuple[float, ...]
    constraint_values: Mapping[str, float]
    constraint_violations: Mapping[str, float]
    feasible: bool
    artifacts: tuple[ArtifactRef, ...] = ()
    event_log: ArtifactRef | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure: RunFailure | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


ObservationT = TypeVar("ObservationT")
ActionT = TypeVar("ActionT")


@dataclass(frozen=True, slots=True)
class DecisionRequest(_DeepFrozen, Generic[ObservationT]):
    """Information made visible to an online algorithm at one decision point."""

    run_id: str
    request_id: str
    simulation_time: float
    state_version: int
    observation: ObservationT
    objective: ObjectiveSpec
    budget: Budget
    legal_actions: object | None = None
    commitments: tuple[object, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DecisionResponse(_DeepFrozen, Generic[ActionT]):
    """Decision and optional anytime diagnostics returned by an algorithm."""

    request_id: str
    status: DecisionStatus
    action: ActionT | None
    plan: object | None = None
    objective_values: tuple[float, ...] = ()
    bounds: tuple[float, ...] = ()
    diagnostics: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DomainEvent(_DeepFrozen):
    """Domain-owned event before platform sequencing and timestamping."""

    event_type: str
    payload: Mapping[str, object] = field(default_factory=dict)
    simulation_time: float | None = None
    state_hash: str | None = None


@dataclass(frozen=True, slots=True)
class Feedback(_DeepFrozen, Generic[ActionT, ObservationT]):
    """Authoritative domain outcome sent back after a proposed decision."""

    run_id: str
    request_id: str
    accepted: bool
    proposed_action: ActionT | None
    executed_action: ActionT | None
    simulation_time: float
    state_version: int
    observation: ObservationT
    metrics: MetricSet = field(default_factory=dict)
    events: tuple[DomainEvent, ...] = ()
    terminated: bool = False
    rejection_reason: str | None = None


CandidateT = TypeVar("CandidateT")


@dataclass(frozen=True, slots=True)
class Candidate(_DeepFrozen, Generic[CandidateT]):
    """A candidate produced by an iterative solver or search optimizer."""

    candidate_id: str
    value: CandidateT
    objective_values: tuple[float, ...] = ()
    constraint_values: Mapping[str, float] = field(default_factory=dict)
    constraint_violations: Mapping[str, float] = field(default_factory=dict)
    feasible: bool | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TrialResult(_DeepFrozen):
    """Aggregated result returned to a parameter search optimizer."""

    trial_id: str
    candidate_id: str | None
    parameters: ParameterSet
    objective_values: tuple[float, ...]
    feasible: bool
    run_results: tuple[RunResult, ...]
    constraint_values: Mapping[str, float] = field(default_factory=dict)
    constraint_violations: Mapping[str, float] = field(default_factory=dict)
    metrics: MetricSet = field(default_factory=dict)
    status: TrialStatus = TrialStatus.SUCCEEDED
    failure: RunFailure | None = None


@dataclass(frozen=True, slots=True)
class StateSnapshot(_DeepFrozen):
    """Opaque, content-addressed snapshot produced by a domain session."""

    state_version: int
    simulation_time: float
    artifact: ArtifactRef
    state_hash: str


@dataclass(frozen=True, slots=True)
class ValidationResult(_DeepFrozen):
    """Authoritative domain validation of a proposed action or solution."""

    valid: bool
    violations: tuple[str, ...] = ()
    repaired_value: object | None = None


@dataclass(frozen=True, slots=True)
class StepResult(_DeepFrozen, Generic[ObservationT, ActionT]):
    """Authoritative result of applying one online action."""

    observation: ObservationT
    executed_action: ActionT
    simulation_time: float
    state_version: int
    metrics: MetricSet
    state_hash: str | None = None
    events: tuple[DomainEvent, ...] = ()
    terminated: bool = False
    termination_reason: str | None = None
