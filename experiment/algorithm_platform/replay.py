"""Recorded, deterministic, branched, and differential replay contracts.

This module is domain-neutral.  It defines the evidence carried by a replay,
extracts decision lifecycles from standard events, reconstructs state through a
domain reducer, and compares two traces.  A domain integration supplies only
snapshot decoding, event reduction, deterministic reproduction, and branch
execution; platform diagnostics and diff semantics remain shared.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Generic, Mapping, Protocol, Sequence, TypeVar

from .events import read_events
from .models import (
    AlgorithmRef,
    ArtifactKind,
    ArtifactRef,
    EventRecord,
    EventType,
    StateSnapshot,
    _DeepFrozen,
)


StateT = TypeVar("StateT")


class ReplayMode(StrEnum):
    """Execution semantics used to produce a replay trace."""

    RECORDED = "recorded"
    DETERMINISTIC = "deterministic"
    BRANCH = "branch"


class ReplayView(StrEnum):
    """Granularity exposed to a replay consumer."""

    FULL = "full"
    DECISIONS = "decisions"


class DiagnosticSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ReplayIssueCode(StrEnum):
    EMPTY_TRACE = "empty_trace"
    SEQUENCE_GAP = "sequence_gap"
    MIXED_RUN_IDENTITY = "mixed_run_identity"
    WALL_TIME_REVERSED = "wall_time_reversed"
    SIMULATION_TIME_REVERSED = "simulation_time_reversed"
    RUN_START_MISSING = "run_start_missing"
    RUN_FINISH_MISSING = "run_finish_missing"
    REPRODUCTION_METADATA_MISSING = "reproduction_metadata_missing"
    OBSERVATION_MISSING = "observation_missing"
    DECISION_RESPONSE_MISSING = "decision_response_missing"
    ACTION_VALIDATION_MISSING = "action_validation_missing"
    ACTION_EXECUTION_MISSING = "action_execution_missing"
    STATE_CHANGE_MISSING = "state_change_missing"
    METRIC_UPDATE_MISSING = "metric_update_missing"
    DECISION_EVENT_DUPLICATE = "decision_event_duplicate"
    ORPHAN_DECISION_EVENT = "orphan_decision_event"
    STATE_HASH_MISSING = "state_hash_missing"
    SNAPSHOT_SEQUENCE_INVALID = "snapshot_sequence_invalid"
    SNAPSHOT_HASH_MISMATCH = "snapshot_hash_mismatch"
    SNAPSHOT_STATE_VERSION_MISMATCH = "snapshot_state_version_mismatch"


class ReplayDifferenceKind(StrEnum):
    ITEM_MISSING = "item_missing"
    SEQUENCE = "sequence"
    EVENT_TYPE = "event_type"
    SIMULATION_TIME = "simulation_time"
    STATE_HASH = "state_hash"
    PAYLOAD = "payload"
    DECISION = "decision"


@dataclass(frozen=True, slots=True)
class ReplaySnapshot(_DeepFrozen):
    """State captured immediately after an event sequence number.

    ``after_sequence`` may be ``-1`` for the state before the first event.
    """

    after_sequence: int
    snapshot: StateSnapshot


@dataclass(frozen=True, slots=True)
class ReplayTrace(_DeepFrozen):
    """Immutable event and snapshot evidence for one run."""

    execution_id: str
    run_id: str
    mode: ReplayMode
    events: tuple[EventRecord, ...]
    snapshots: tuple[ReplaySnapshot, ...] = ()
    event_log: ArtifactRef | None = None
    parent_run_id: str | None = None
    branch_after_sequence: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DecisionReplay(_DeepFrozen):
    """All recorded evidence associated with one decision request."""

    ordinal: int
    request_id: str
    observation: EventRecord | None
    request: EventRecord
    response: EventRecord | None
    validation: EventRecord | None
    execution: EventRecord | None
    state_change: EventRecord | None
    metric_update: EventRecord | None
    related_events: tuple[EventRecord, ...] = ()

    @property
    def proposed_action(self) -> object | None:
        if self.response is None:
            return None
        return self.response.payload.get("action")

    @property
    def executed_action(self) -> object | None:
        if self.execution is None:
            return None
        return self.execution.payload.get("executed_action")

    @property
    def diagnostics(self) -> Mapping[str, object]:
        if self.response is None:
            return {}
        value = self.response.payload.get("diagnostics", {})
        return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True, slots=True)
class ReplayIssue(_DeepFrozen):
    severity: DiagnosticSeverity
    code: ReplayIssueCode
    message: str
    sequence: int | None = None
    request_id: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReplayDiagnosticsReport(_DeepFrozen):
    execution_id: str
    run_id: str
    event_count: int
    event_type_counts: Mapping[str, int]
    decision_count: int
    snapshot_count: int
    state_transition_count: int
    hashed_state_transition_count: int
    issues: tuple[ReplayIssue, ...]
    record_replay_ready: bool
    deterministic_replay_ready: bool
    branch_replay_ready: bool


@dataclass(frozen=True, slots=True)
class ReplayFrame(_DeepFrozen, Generic[StateT]):
    """One event together with the reconstructed state immediately after it."""

    event: EventRecord
    state: StateT
    anchor: ReplaySnapshot
    state_hash_matches: bool | None


@dataclass(frozen=True, slots=True)
class ReplayDifference(_DeepFrozen):
    kind: ReplayDifferenceKind
    item_ordinal: int
    field_path: str
    left_sequence: int | None
    right_sequence: int | None
    left_value: object | None
    right_value: object | None


@dataclass(frozen=True, slots=True)
class ReplayDiffPolicy(_DeepFrozen):
    """Semantic fields and tolerances used by differential replay."""

    float_tolerance: float = 0.0
    simulation_time_tolerance: float = 0.0
    compare_sequence_numbers: bool = True
    compare_payloads: bool = True
    compare_state_hashes: bool = True
    event_types: frozenset[EventType] = frozenset()
    ignored_payload_paths: frozenset[str] = frozenset(
        {
            "payload.diagnostics.decision_seconds",
            "decision.response.diagnostics.decision_seconds",
        }
    )
    max_differences: int = 1000


@dataclass(frozen=True, slots=True)
class ReplayDiffReport(_DeepFrozen):
    left_run_id: str
    right_run_id: str
    view: ReplayView
    left_item_count: int
    right_item_count: int
    compared_item_count: int
    matched_prefix_count: int
    differences: tuple[ReplayDifference, ...]
    truncated: bool

    @property
    def equivalent(self) -> bool:
        return not self.differences and self.left_item_count == self.right_item_count

    @property
    def first_divergence(self) -> ReplayDifference | None:
        return None if not self.differences else self.differences[0]


@dataclass(frozen=True, slots=True)
class BranchReplayRequest(_DeepFrozen):
    """Domain-neutral request to continue from an immutable snapshot."""

    source: ReplayTrace
    anchor: ReplaySnapshot
    algorithm: AlgorithmRef
    execution_id: str
    run_id: str
    metadata: Mapping[str, object] = field(default_factory=dict)


class ReplayStateCodec(Protocol[StateT]):
    """Decode snapshots and hash reconstructed domain state."""

    def load(self, snapshot: StateSnapshot) -> StateT:
        ...

    def state_hash(self, state: StateT) -> str:
        ...


class ReplayEventReducer(Protocol[StateT]):
    """Apply one recorded event or delta without invoking an algorithm."""

    def apply(self, state: StateT, event: EventRecord) -> StateT:
        ...


class DeterministicReplayExecutor(Protocol):
    """Domain integration that reruns a trace from versions, seeds, and events."""

    def reproduce(self, source: ReplayTrace) -> ReplayTrace:
        ...


class BranchReplayExecutor(Protocol):
    """Domain integration that switches algorithms at a snapshot boundary."""

    def branch(self, request: BranchReplayRequest) -> ReplayTrace:
        ...


def load_replay_trace(
    event_log_path: str | Path,
    *,
    mode: ReplayMode = ReplayMode.RECORDED,
    snapshots: Sequence[ReplaySnapshot] = (),
    event_log: ArtifactRef | None = None,
    metadata: Mapping[str, object] | None = None,
) -> ReplayTrace:
    """Load one canonical JSONL event log as a typed replay trace."""

    events = tuple(read_events(event_log_path))
    if not events:
        raise ValueError("an event log must contain at least one event")
    discovered_snapshots = (
        tuple(snapshots) if snapshots else snapshots_from_events(events)
    )
    return ReplayTrace(
        execution_id=events[0].execution_id,
        run_id=events[0].run_id,
        mode=mode,
        events=events,
        snapshots=discovered_snapshots,
        event_log=event_log,
        metadata={} if metadata is None else metadata,
    )


def snapshots_from_events(
    events: Sequence[EventRecord],
) -> tuple[ReplaySnapshot, ...]:
    """Rehydrate snapshot anchors emitted by the online runtime."""

    anchors: list[ReplaySnapshot] = []
    for event in events:
        if event.event_type is not EventType.SNAPSHOT_CREATED:
            continue
        artifact_value = event.payload["snapshot_artifact"]
        if event.state_hash is None or event.simulation_time is None:
            raise ValueError("snapshot events require state hash and simulation time")
        if isinstance(artifact_value, ArtifactRef):
            artifact = artifact_value
        elif isinstance(artifact_value, Mapping):
            digest = str(artifact_value["digest"])
            artifact = ArtifactRef(
                digest=digest,
                kind=ArtifactKind(str(artifact_value["kind"])),
                media_type=str(artifact_value["media_type"]),
                size_bytes=int(artifact_value["size_bytes"]),
                uri=str(artifact_value.get("uri", f"artifact:{digest}")),
                metadata=dict(artifact_value.get("metadata", {})),
            )
        else:
            raise TypeError("snapshot_artifact event payload must be an object")
        anchors.append(
            ReplaySnapshot(
                after_sequence=event.sequence,
                snapshot=StateSnapshot(
                    state_version=int(event.payload["state_version"]),
                    simulation_time=event.simulation_time,
                    artifact=artifact,
                    state_hash=event.state_hash,
                ),
            )
        )
    return tuple(anchors)


def extract_decisions(trace: ReplayTrace) -> tuple[DecisionReplay, ...]:
    """Group standard events into ordered decision lifecycles."""

    builders: dict[str, dict[str, object]] = {}
    order: list[str] = []
    latest_observation: EventRecord | None = None
    active_request_id: str | None = None

    for event in trace.events:
        if event.event_type is EventType.OBSERVATION_PUBLISHED:
            latest_observation = event
            continue
        if event.event_type is EventType.DECISION_REQUESTED:
            request_id = str(event.payload.get("request_id", ""))
            if request_id not in builders:
                order.append(request_id)
                builders[request_id] = {
                    "observation": latest_observation,
                    "request": event,
                    "related_events": [],
                }
            else:
                related = builders[request_id]["related_events"]
                if isinstance(related, list):
                    related.append(event)
            latest_observation = None
            active_request_id = request_id
            continue

        request_id_value = event.payload.get("request_id")
        request_id = (
            str(request_id_value)
            if request_id_value is not None
            else active_request_id
        )
        if request_id is None or request_id not in builders:
            continue
        builder = builders[request_id]
        slot = _decision_slot(event.event_type)
        if slot is None:
            related = builder["related_events"]
            if isinstance(related, list):
                related.append(event)
            continue
        if slot not in builder:
            builder[slot] = event
        else:
            related = builder["related_events"]
            if isinstance(related, list):
                related.append(event)

    return tuple(
        DecisionReplay(
            ordinal=ordinal,
            request_id=request_id,
            observation=_event_or_none(builders[request_id].get("observation")),
            request=_required_event(builders[request_id]["request"]),
            response=_event_or_none(builders[request_id].get("response")),
            validation=_event_or_none(builders[request_id].get("validation")),
            execution=_event_or_none(builders[request_id].get("execution")),
            state_change=_event_or_none(builders[request_id].get("state_change")),
            metric_update=_event_or_none(builders[request_id].get("metric_update")),
            related_events=tuple(builders[request_id]["related_events"]),
        )
        for ordinal, request_id in enumerate(order)
    )


class ReplayDiagnostics:
    """Audit whether a trace contains sufficient, internally consistent evidence."""

    def inspect(self, trace: ReplayTrace) -> ReplayDiagnosticsReport:
        issues: list[ReplayIssue] = []
        events = trace.events
        if not events:
            issues.append(
                ReplayIssue(
                    severity=DiagnosticSeverity.ERROR,
                    code=ReplayIssueCode.EMPTY_TRACE,
                    message="the replay trace contains no events",
                )
            )

        for expected, event in enumerate(events):
            if event.sequence != expected:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.SEQUENCE_GAP,
                        message=(
                            f"expected event sequence {expected}, received "
                            f"{event.sequence}"
                        ),
                        sequence=event.sequence,
                    )
                )
            if (
                event.execution_id != trace.execution_id
                or event.run_id != trace.run_id
            ):
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.MIXED_RUN_IDENTITY,
                        message="an event belongs to another execution or run",
                        sequence=event.sequence,
                    )
                )
            if expected > 0 and event.wall_time < events[expected - 1].wall_time:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.WARNING,
                        code=ReplayIssueCode.WALL_TIME_REVERSED,
                        message="event wall time moved backwards",
                        sequence=event.sequence,
                    )
                )

        timed_events = tuple(
            event for event in events if event.simulation_time is not None
        )
        for previous, current in zip(timed_events, timed_events[1:]):
            if current.simulation_time < previous.simulation_time:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.SIMULATION_TIME_REVERSED,
                        message="simulation time moved backwards",
                        sequence=current.sequence,
                    )
                )

        run_starts = tuple(
            event for event in events if event.event_type is EventType.RUN_STARTED
        )
        run_finishes = tuple(
            event for event in events if event.event_type is EventType.RUN_FINISHED
        )
        if len(run_starts) != 1 or (events and run_starts[0] is not events[0]):
            issues.append(
                ReplayIssue(
                    severity=DiagnosticSeverity.ERROR,
                    code=ReplayIssueCode.RUN_START_MISSING,
                    message="a replay must begin with exactly one run_started event",
                )
            )
        if len(run_finishes) != 1 or (events and run_finishes[-1] is not events[-1]):
            issues.append(
                ReplayIssue(
                    severity=DiagnosticSeverity.ERROR,
                    code=ReplayIssueCode.RUN_FINISH_MISSING,
                    message="a replay must end with exactly one run_finished event",
                )
            )

        reproduction_metadata_complete = False
        if run_starts:
            required = {
                "algorithm_id",
                "algorithm_version",
                "interface",
                "domain_id",
                "domain_version",
                "scenario_id",
                "scenario_digest",
                "seeds",
            }
            missing = tuple(sorted(required - set(run_starts[0].payload)))
            reproduction_metadata_complete = not missing
            if missing:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.REPRODUCTION_METADATA_MISSING,
                        message="run_started lacks deterministic reproduction metadata",
                        sequence=run_starts[0].sequence,
                        details={"missing": missing},
                    )
                )

        decisions = extract_decisions(trace)
        request_ids = [decision.request_id for decision in decisions]
        duplicate_request_ids = {
            request_id
            for request_id, count in Counter(request_ids).items()
            if count > 1
        }
        for request_id in sorted(duplicate_request_ids):
            issues.append(
                ReplayIssue(
                    severity=DiagnosticSeverity.ERROR,
                    code=ReplayIssueCode.DECISION_EVENT_DUPLICATE,
                    message="a decision request identifier is reused",
                    request_id=request_id,
                )
            )
        known_request_ids = set(request_ids)
        for event in events:
            if _decision_slot(event.event_type) is None:
                continue
            request_id = event.payload.get("request_id")
            if request_id is not None and str(request_id) not in known_request_ids:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.ORPHAN_DECISION_EVENT,
                        message="a decision lifecycle event has no matching request",
                        sequence=event.sequence,
                        request_id=str(request_id),
                    )
                )
        self._inspect_decisions(decisions, issues)
        state_events = tuple(
            event for event in events if event.event_type is EventType.STATE_CHANGED
        )
        hashed_state_events = tuple(
            event for event in state_events if event.state_hash is not None
        )
        for event in state_events:
            if event.state_hash is None:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.STATE_HASH_MISSING,
                        message="a state transition lacks its canonical state hash",
                        sequence=event.sequence,
                    )
                )

        self._inspect_snapshots(trace, issues)
        error_count = sum(
            issue.severity is DiagnosticSeverity.ERROR for issue in issues
        )
        record_ready = bool(events) and error_count == 0
        snapshot_errors = {
            ReplayIssueCode.SNAPSHOT_SEQUENCE_INVALID,
            ReplayIssueCode.SNAPSHOT_HASH_MISMATCH,
            ReplayIssueCode.SNAPSHOT_STATE_VERSION_MISMATCH,
        }
        return ReplayDiagnosticsReport(
            execution_id=trace.execution_id,
            run_id=trace.run_id,
            event_count=len(events),
            event_type_counts=dict(Counter(event.event_type.value for event in events)),
            decision_count=len(decisions),
            snapshot_count=len(trace.snapshots),
            state_transition_count=len(state_events),
            hashed_state_transition_count=len(hashed_state_events),
            issues=tuple(issues),
            record_replay_ready=record_ready,
            deterministic_replay_ready=(
                record_ready
                and reproduction_metadata_complete
                and len(state_events) == len(hashed_state_events)
            ),
            branch_replay_ready=(
                record_ready
                and bool(trace.snapshots)
                and not any(issue.code in snapshot_errors for issue in issues)
            ),
        )

    @staticmethod
    def _inspect_decisions(
        decisions: Sequence[DecisionReplay],
        issues: list[ReplayIssue],
    ) -> None:
        for decision in decisions:
            if decision.observation is None:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.OBSERVATION_MISSING,
                        message="the observation actually supplied for a decision is absent",
                        sequence=decision.request.sequence,
                        request_id=decision.request_id,
                    )
                )
            if decision.response is None:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.DECISION_RESPONSE_MISSING,
                        message="a decision request has no recorded response",
                        sequence=decision.request.sequence,
                        request_id=decision.request_id,
                    )
                )
                continue

            action = decision.response.payload.get("action")
            if action is not None and decision.validation is None:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.ACTION_VALIDATION_MISSING,
                        message="a proposed action has no validation event",
                        sequence=decision.response.sequence,
                        request_id=decision.request_id,
                    )
                )
            valid = (
                decision.validation is not None
                and decision.validation.payload.get("valid") is True
            )
            if valid and decision.execution is None:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.ACTION_EXECUTION_MISSING,
                        message="a valid action has no execution event",
                        request_id=decision.request_id,
                    )
                )
            if decision.execution is not None and decision.state_change is None:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.STATE_CHANGE_MISSING,
                        message="an executed action has no state transition event",
                        sequence=decision.execution.sequence,
                        request_id=decision.request_id,
                    )
                )
            if decision.execution is not None and decision.metric_update is None:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.METRIC_UPDATE_MISSING,
                        message="an executed action has no metric update event",
                        sequence=decision.execution.sequence,
                        request_id=decision.request_id,
                    )
                )
            if decision.related_events:
                duplicate_types = tuple(
                    event.event_type.value
                    for event in decision.related_events
                    if event.event_type is EventType.DECISION_REQUESTED
                    or _decision_slot(event.event_type) is not None
                )
                if duplicate_types:
                    issues.append(
                        ReplayIssue(
                            severity=DiagnosticSeverity.ERROR,
                            code=ReplayIssueCode.DECISION_EVENT_DUPLICATE,
                            message="a decision contains duplicate lifecycle events",
                            request_id=decision.request_id,
                            details={"event_types": duplicate_types},
                        )
                    )

    @staticmethod
    def _inspect_snapshots(
        trace: ReplayTrace,
        issues: list[ReplayIssue],
    ) -> None:
        seen_sequences: set[int] = set()
        maximum_sequence = len(trace.events) - 1
        events_by_sequence = {event.sequence: event for event in trace.events}
        for anchor in trace.snapshots:
            if (
                anchor.after_sequence < -1
                or anchor.after_sequence > maximum_sequence
                or anchor.after_sequence in seen_sequences
            ):
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.SNAPSHOT_SEQUENCE_INVALID,
                        message="snapshot anchors must be unique valid event boundaries",
                        sequence=anchor.after_sequence,
                    )
                )
            seen_sequences.add(anchor.after_sequence)
            event = events_by_sequence.get(anchor.after_sequence)
            if event is None:
                continue
            if event.state_hash is not None and event.state_hash != anchor.snapshot.state_hash:
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.SNAPSHOT_HASH_MISMATCH,
                        message="snapshot and anchored event carry different state hashes",
                        sequence=anchor.after_sequence,
                        details={
                            "event_state_hash": event.state_hash,
                            "snapshot_state_hash": anchor.snapshot.state_hash,
                        },
                    )
                )
            state_version = event.payload.get("state_version")
            if (
                state_version is not None
                and int(state_version) != anchor.snapshot.state_version
            ):
                issues.append(
                    ReplayIssue(
                        severity=DiagnosticSeverity.ERROR,
                        code=ReplayIssueCode.SNAPSHOT_STATE_VERSION_MISMATCH,
                        message="snapshot and anchored event carry different state versions",
                        sequence=anchor.after_sequence,
                    )
                )


class RecordedReplayPlayer(Generic[StateT]):
    """Play raw events, decision views, or reconstructed state frames."""

    @staticmethod
    def events(
        trace: ReplayTrace,
        *,
        start_sequence: int = 0,
        end_sequence: int | None = None,
    ) -> tuple[EventRecord, ...]:
        upper = len(trace.events) - 1 if end_sequence is None else end_sequence
        return tuple(
            event
            for event in trace.events
            if start_sequence <= event.sequence <= upper
        )

    @staticmethod
    def decisions(trace: ReplayTrace) -> tuple[DecisionReplay, ...]:
        return extract_decisions(trace)

    def frames(
        self,
        trace: ReplayTrace,
        codec: ReplayStateCodec[StateT],
        reducer: ReplayEventReducer[StateT],
        *,
        start_sequence: int = 0,
        end_sequence: int | None = None,
    ) -> tuple[ReplayFrame[StateT], ...]:
        """Reconstruct state using the nearest snapshot at or before the start."""

        if start_sequence < 0:
            raise ValueError("start_sequence must be non-negative")
        upper = len(trace.events) - 1 if end_sequence is None else end_sequence
        if upper < start_sequence:
            return ()
        anchors = tuple(
            anchor
            for anchor in trace.snapshots
            if anchor.after_sequence <= start_sequence
        )
        if not anchors:
            raise ValueError("state playback requires a snapshot at or before the start")
        anchor = max(anchors, key=lambda item: item.after_sequence)
        state = codec.load(anchor.snapshot)
        if codec.state_hash(state) != anchor.snapshot.state_hash:
            raise ValueError("decoded snapshot does not match its recorded state hash")
        frames: list[ReplayFrame[StateT]] = []

        if anchor.after_sequence == start_sequence:
            event = trace.events[start_sequence]
            frames.append(
                ReplayFrame(
                    event=event,
                    state=state,
                    anchor=anchor,
                    state_hash_matches=(
                        None
                        if event.state_hash is None
                        else codec.state_hash(state) == event.state_hash
                    ),
                )
            )

        for event in trace.events:
            if event.sequence <= anchor.after_sequence:
                continue
            if event.sequence > upper:
                break
            state = reducer.apply(state, event)
            if event.sequence < start_sequence:
                continue
            frames.append(
                ReplayFrame(
                    event=event,
                    state=state,
                    anchor=anchor,
                    state_hash_matches=(
                        None
                        if event.state_hash is None
                        else codec.state_hash(state) == event.state_hash
                    ),
                )
            )
        return tuple(frames)


class ReplayDiffer:
    """Compare complete event streams or their decision-level projections."""

    def compare(
        self,
        left: ReplayTrace,
        right: ReplayTrace,
        *,
        view: ReplayView = ReplayView.FULL,
        policy: ReplayDiffPolicy = ReplayDiffPolicy(),
    ) -> ReplayDiffReport:
        self._validate_policy(policy)
        if view is ReplayView.DECISIONS:
            left_items = tuple(_decision_view(item) for item in extract_decisions(left))
            right_items = tuple(_decision_view(item) for item in extract_decisions(right))
            return self._compare_values(
                left,
                right,
                left_items,
                right_items,
                view,
                policy,
            )

        left_events = self._selected_events(left.events, policy)
        right_events = self._selected_events(right.events, policy)
        differences: list[ReplayDifference] = []
        matched_prefix = 0
        compared = min(len(left_events), len(right_events))
        truncated = False

        for ordinal, (left_event, right_event) in enumerate(
            zip(left_events, right_events, strict=False)
        ):
            before = len(differences)
            if policy.compare_sequence_numbers and left_event.sequence != right_event.sequence:
                self._append(
                    differences,
                    ReplayDifferenceKind.SEQUENCE,
                    ordinal,
                    "sequence",
                    left_event,
                    right_event,
                    left_event.sequence,
                    right_event.sequence,
                    policy,
                )
            if left_event.event_type is not right_event.event_type:
                self._append(
                    differences,
                    ReplayDifferenceKind.EVENT_TYPE,
                    ordinal,
                    "event_type",
                    left_event,
                    right_event,
                    left_event.event_type.value,
                    right_event.event_type.value,
                    policy,
                )
            if not _numbers_equal(
                left_event.simulation_time,
                right_event.simulation_time,
                policy.simulation_time_tolerance,
            ):
                self._append(
                    differences,
                    ReplayDifferenceKind.SIMULATION_TIME,
                    ordinal,
                    "simulation_time",
                    left_event,
                    right_event,
                    left_event.simulation_time,
                    right_event.simulation_time,
                    policy,
                )
            if (
                policy.compare_state_hashes
                and left_event.state_hash != right_event.state_hash
            ):
                self._append(
                    differences,
                    ReplayDifferenceKind.STATE_HASH,
                    ordinal,
                    "state_hash",
                    left_event,
                    right_event,
                    left_event.state_hash,
                    right_event.state_hash,
                    policy,
                )
            if policy.compare_payloads:
                self._diff_value(
                    left_event.payload,
                    right_event.payload,
                    path="payload",
                    ordinal=ordinal,
                    left_event=left_event,
                    right_event=right_event,
                    kind=ReplayDifferenceKind.PAYLOAD,
                    differences=differences,
                    policy=policy,
                )
            if len(differences) == before and matched_prefix == ordinal:
                matched_prefix += 1
            if len(differences) >= policy.max_differences:
                truncated = ordinal + 1 < compared or len(left_events) != len(right_events)
                break

        if len(differences) < policy.max_differences and len(left_events) != len(right_events):
            ordinal = compared
            left_event = left_events[ordinal] if ordinal < len(left_events) else None
            right_event = right_events[ordinal] if ordinal < len(right_events) else None
            differences.append(
                ReplayDifference(
                    kind=ReplayDifferenceKind.ITEM_MISSING,
                    item_ordinal=ordinal,
                    field_path="event",
                    left_sequence=None if left_event is None else left_event.sequence,
                    right_sequence=None if right_event is None else right_event.sequence,
                    left_value=(
                        None if left_event is None else left_event.event_type.value
                    ),
                    right_value=(
                        None if right_event is None else right_event.event_type.value
                    ),
                )
            )
        return ReplayDiffReport(
            left_run_id=left.run_id,
            right_run_id=right.run_id,
            view=view,
            left_item_count=len(left_events),
            right_item_count=len(right_events),
            compared_item_count=compared,
            matched_prefix_count=matched_prefix,
            differences=tuple(differences),
            truncated=truncated,
        )

    def _compare_values(
        self,
        left: ReplayTrace,
        right: ReplayTrace,
        left_items: Sequence[object],
        right_items: Sequence[object],
        view: ReplayView,
        policy: ReplayDiffPolicy,
    ) -> ReplayDiffReport:
        differences: list[ReplayDifference] = []
        compared = min(len(left_items), len(right_items))
        matched_prefix = 0
        truncated = False
        for ordinal, (left_item, right_item) in enumerate(
            zip(left_items, right_items, strict=False)
        ):
            before = len(differences)
            self._diff_value(
                left_item,
                right_item,
                path="decision",
                ordinal=ordinal,
                left_event=None,
                right_event=None,
                kind=ReplayDifferenceKind.DECISION,
                differences=differences,
                policy=policy,
            )
            if len(differences) == before and matched_prefix == ordinal:
                matched_prefix += 1
            if len(differences) >= policy.max_differences:
                truncated = ordinal + 1 < compared or len(left_items) != len(right_items)
                break
        if len(differences) < policy.max_differences and len(left_items) != len(right_items):
            differences.append(
                ReplayDifference(
                    kind=ReplayDifferenceKind.ITEM_MISSING,
                    item_ordinal=compared,
                    field_path="decision",
                    left_sequence=None,
                    right_sequence=None,
                    left_value=("present" if compared < len(left_items) else None),
                    right_value=("present" if compared < len(right_items) else None),
                )
            )
        return ReplayDiffReport(
            left_run_id=left.run_id,
            right_run_id=right.run_id,
            view=view,
            left_item_count=len(left_items),
            right_item_count=len(right_items),
            compared_item_count=compared,
            matched_prefix_count=matched_prefix,
            differences=tuple(differences),
            truncated=truncated,
        )

    def _diff_value(
        self,
        left: object,
        right: object,
        *,
        path: str,
        ordinal: int,
        left_event: EventRecord | None,
        right_event: EventRecord | None,
        kind: ReplayDifferenceKind,
        differences: list[ReplayDifference],
        policy: ReplayDiffPolicy,
    ) -> None:
        if len(differences) >= policy.max_differences:
            return
        if path in policy.ignored_payload_paths:
            return
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            for key in sorted(set(left) | set(right), key=str):
                self._diff_value(
                    left.get(key),
                    right.get(key),
                    path=f"{path}.{key}",
                    ordinal=ordinal,
                    left_event=left_event,
                    right_event=right_event,
                    kind=kind,
                    differences=differences,
                    policy=policy,
                )
            return
        if (
            isinstance(left, Sequence)
            and not isinstance(left, (str, bytes, bytearray))
            and isinstance(right, Sequence)
            and not isinstance(right, (str, bytes, bytearray))
        ):
            for index in range(max(len(left), len(right))):
                self._diff_value(
                    left[index] if index < len(left) else None,
                    right[index] if index < len(right) else None,
                    path=f"{path}[{index}]",
                    ordinal=ordinal,
                    left_event=left_event,
                    right_event=right_event,
                    kind=kind,
                    differences=differences,
                    policy=policy,
                )
            return
        if _values_equal(left, right, policy.float_tolerance):
            return
        self._append(
            differences,
            kind,
            ordinal,
            path,
            left_event,
            right_event,
            left,
            right,
            policy,
        )

    @staticmethod
    def _append(
        differences: list[ReplayDifference],
        kind: ReplayDifferenceKind,
        ordinal: int,
        path: str,
        left_event: EventRecord | None,
        right_event: EventRecord | None,
        left_value: object,
        right_value: object,
        policy: ReplayDiffPolicy,
    ) -> None:
        if len(differences) >= policy.max_differences:
            return
        differences.append(
            ReplayDifference(
                kind=kind,
                item_ordinal=ordinal,
                field_path=path,
                left_sequence=(
                    None if left_event is None else left_event.sequence
                ),
                right_sequence=(
                    None if right_event is None else right_event.sequence
                ),
                left_value=left_value,
                right_value=right_value,
            )
        )

    @staticmethod
    def _selected_events(
        events: Sequence[EventRecord],
        policy: ReplayDiffPolicy,
    ) -> tuple[EventRecord, ...]:
        if not policy.event_types:
            return tuple(events)
        return tuple(event for event in events if event.event_type in policy.event_types)

    @staticmethod
    def _validate_policy(policy: ReplayDiffPolicy) -> None:
        if policy.float_tolerance < 0.0 or not math.isfinite(policy.float_tolerance):
            raise ValueError("float_tolerance must be finite and non-negative")
        if (
            policy.simulation_time_tolerance < 0.0
            or not math.isfinite(policy.simulation_time_tolerance)
        ):
            raise ValueError(
                "simulation_time_tolerance must be finite and non-negative"
            )
        if policy.max_differences <= 0:
            raise ValueError("max_differences must be positive")


def _decision_slot(event_type: EventType) -> str | None:
    return {
        EventType.DECISION_RETURNED: "response",
        EventType.ACTION_VALIDATED: "validation",
        EventType.ACTION_EXECUTED: "execution",
        EventType.STATE_CHANGED: "state_change",
        EventType.METRIC_UPDATED: "metric_update",
    }.get(event_type)


def _required_event(value: object) -> EventRecord:
    if not isinstance(value, EventRecord):
        raise TypeError("decision request is not an EventRecord")
    return value


def _event_or_none(value: object) -> EventRecord | None:
    return value if isinstance(value, EventRecord) else None


def _decision_view(decision: DecisionReplay) -> Mapping[str, object]:
    return {
        "observation": (
            None
            if decision.observation is None
            else _without_request_id(decision.observation.payload)
        ),
        "request": _without_request_id(decision.request.payload),
        "response": (
            None
            if decision.response is None
            else _without_request_id(decision.response.payload)
        ),
        "validation": (
            None
            if decision.validation is None
            else _without_request_id(decision.validation.payload)
        ),
        "execution": (
            None
            if decision.execution is None
            else _without_request_id(decision.execution.payload)
        ),
        "state_hash": (
            None if decision.state_change is None else decision.state_change.state_hash
        ),
        "state_change": (
            None
            if decision.state_change is None
            else _without_request_id(decision.state_change.payload)
        ),
        "metrics": (
            None
            if decision.metric_update is None
            else _without_request_id(decision.metric_update.payload)
        ),
    }


def _without_request_id(payload: Mapping[str, object]) -> Mapping[str, object]:
    return {key: value for key, value in payload.items() if key != "request_id"}


def _numbers_equal(
    left: float | None,
    right: float | None,
    tolerance: float,
) -> bool:
    if left is None or right is None:
        return left is right
    return abs(left - right) <= tolerance


def _values_equal(left: object, right: object, tolerance: float) -> bool:
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return abs(float(left) - float(right)) <= tolerance
    return left == right
