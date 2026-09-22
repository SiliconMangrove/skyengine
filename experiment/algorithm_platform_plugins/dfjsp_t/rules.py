"""Deterministic online dispatch rules for the formal DFJSP-T domain."""

from __future__ import annotations

import math
from typing import Mapping

from experiment.algorithm_platform.models import (
    ArtifactKind,
    ArtifactRef,
    DecisionRequest,
    DecisionResponse,
    DecisionStatus,
    Feedback,
    RunContext,
)

from .domain import DFJSPT_URGENT_PRIORITY


class DispatchRulePolicy:
    """Dispatch ready operations using SPT, EDD, or a weighted rule."""

    def __init__(self, rule: str, parameters: Mapping[str, object]):
        self._rule = rule
        self._parameters = dict(parameters)
        self._dispatch_batch_size = int(parameters.get("dispatch_batch_size", 1))
        self._machine_load_weight = float(
            parameters.get("machine_load_weight", 1.0)
        )
        self._transport_weight = float(parameters.get("transport_weight", 1.0))
        self._processing_time_weight = float(
            parameters.get("processing_time_weight", 1.0)
        )
        self._due_date_weight = float(parameters.get("due_date_weight", 1.0))
        self._remaining_work_weight = float(
            parameters.get("remaining_work_weight", 0.0)
        )
        self._priority_weight = float(parameters.get("priority_weight", 0.0))
        if self._dispatch_batch_size < 1:
            raise ValueError("dispatch_batch_size must be positive")
        self._context: RunContext | None = None
        self._dispatched: set[tuple[int, int]] = set()
        self._decisions: list[dict[str, object]] = []
        self._feedback: list[dict[str, object]] = []
        self._idle_decisions = 0

    def initialize(self, context: RunContext) -> None:
        self._context = context
        self._dispatched.clear()
        self._decisions.clear()
        self._feedback.clear()
        self._idle_decisions = 0

    def decide(
        self,
        request: DecisionRequest[Mapping[str, object]],
    ) -> DecisionResponse[dict[str, object]]:
        observation = request.observation
        ready = _ready_operations(observation, self._dispatched)
        ranked = sorted(
            ready,
            key=lambda item: self._priority_key(item, observation),
        )
        dispatches = []
        for job, operation in ranked[: self._dispatch_batch_size]:
            machine_id, estimate = _select_machine(
                job,
                operation,
                observation,
                load_weight=self._machine_load_weight,
                transport_weight=self._transport_weight,
            )
            dispatch = {
                "job_id": int(job["job_id"]),
                "op_id": int(operation["op_id"]),
                "machine_id": machine_id,
            }
            dispatches.append(dispatch)
            self._dispatched.add((dispatch["job_id"], dispatch["op_id"]))
            self._decisions.append(
                {
                    "simulation_time": float(request.simulation_time),
                    "state_version": int(request.state_version),
                    "dispatch": dispatch,
                    "estimated_completion": estimate,
                }
            )
        if not dispatches:
            self._idle_decisions += 1
        return DecisionResponse(
            request_id=request.request_id,
            status=DecisionStatus.FEASIBLE,
            action={"dispatches": dispatches},
            plan={
                "rule": self._rule,
                "ranked_ready_operations": [
                    {
                        "job_id": int(job["job_id"]),
                        "op_id": int(operation["op_id"]),
                    }
                    for job, operation in ranked
                ],
            },
            diagnostics={
                "ready_operations": len(ready),
                "dispatched_operations": len(dispatches),
                "idle_decisions": self._idle_decisions,
            },
        )

    def observe(
        self,
        feedback: Feedback[dict[str, object], Mapping[str, object]],
    ) -> None:
        proposed = feedback.proposed_action or {}
        if proposed.get("dispatches"):
            self._feedback.append(
                {
                    "request_id": feedback.request_id,
                    "accepted": feedback.accepted,
                    "simulation_time": float(feedback.simulation_time),
                    "state_version": int(feedback.state_version),
                    "metrics": dict(feedback.metrics),
                }
            )

    def finalize(self) -> tuple[ArtifactRef, ...]:
        if self._context is None:
            raise RuntimeError("dispatch rule was not initialized")
        solution = self._context.artifact_publisher.publish_json(
            {
                "schema_version": 1,
                "algorithm": self._context.run.algorithm.algorithm_id,
                "rule": self._rule,
                "parameters": self._parameters,
                "dispatches": self._decisions,
                "feedback": self._feedback,
                "idle_decisions": self._idle_decisions,
            },
            kind=ArtifactKind.SOLUTION,
            metadata={
                "domain": "dfjsp_t",
                "solution_type": "online_dispatch_trace",
                "rule": self._rule,
            },
        )
        return (solution,)

    def _priority_key(
        self,
        item: tuple[Mapping[str, object], Mapping[str, object]],
        observation: Mapping[str, object],
    ) -> tuple[float, ...]:
        job, operation = item
        urgent_rank = (
            0.0
            if int(job["priority"]) >= DFJSPT_URGENT_PRIORITY
            else 1.0
        )
        processing_time = _minimum_processing_time(operation)
        due = float(job["due"]) if job["due"] is not None else math.inf
        identity = (float(job["job_id"]), float(operation["op_id"]))
        if self._rule == "SPT":
            return (urgent_rank, processing_time, due, *identity)
        if self._rule == "EDD":
            return (urgent_rank, due, processing_time, *identity)

        remaining_work = sum(
            _minimum_processing_time(candidate)
            for candidate in job["operations"]
            if candidate["status"] != "FINISHED"
        )
        priority = float(job["priority"])
        now = float(observation["simulation_time"])
        slack = due - now - remaining_work if math.isfinite(due) else remaining_work
        score = (
            self._processing_time_weight * processing_time
            + self._due_date_weight * slack
            + self._remaining_work_weight * remaining_work
            - self._priority_weight * priority
        )
        return (urgent_rank, score, processing_time, *identity)


def _ready_operations(
    observation: Mapping[str, object],
    dispatched: set[tuple[int, int]],
) -> list[tuple[Mapping[str, object], Mapping[str, object]]]:
    ready = []
    for job in observation["jobs"]:
        operations = job["operations"]
        for operation in operations:
            key = (int(job["job_id"]), int(operation["op_id"]))
            if key in dispatched:
                continue
            if operation["status"] != "PENDING":
                continue
            if operation["assigned_machine"] is not None:
                continue
            operation_id = int(operation["op_id"])
            if operation_id > 0 and operations[operation_id - 1]["status"] != "FINISHED":
                continue
            ready.append((job, operation))
            break
    return ready


def _select_machine(
    job: Mapping[str, object],
    operation: Mapping[str, object],
    observation: Mapping[str, object],
    *,
    load_weight: float,
    transport_weight: float,
) -> tuple[int, float]:
    machines = {
        int(machine["machine_id"]): machine
        for machine in observation["machines"]
    }
    previous_location = None
    operation_id = int(operation["op_id"])
    if operation_id > 0:
        previous_machine = job["operations"][operation_id - 1]["assigned_machine"]
        if previous_machine is not None:
            previous_location = machines[int(previous_machine)]["location"]

    best: tuple[float, int] | None = None
    now = float(observation["simulation_time"])
    for option in operation["machine_options"]:
        machine_id = int(option["machine_id"])
        machine = machines[machine_id]
        load = _machine_load(machine, observation["planning_observation"]["failure_priors"]["machine_failure"]["repair_time"])
        distance = (
            0.0
            if previous_location is None
            else float(
                abs(int(previous_location[0]) - int(machine["location"][0]))
                + abs(int(previous_location[1]) - int(machine["location"][1]))
            )
        )
        estimate = (
            now
            + load_weight * load
            + float(option["processing_time"])
            + transport_weight * distance
        )
        candidate = (estimate, machine_id)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        raise ValueError("ready operation has no eligible machine")
    return best[1], best[0]


def _minimum_processing_time(operation: Mapping[str, object]) -> float:
    return min(
        float(option["processing_time"])
        for option in operation["machine_options"]
    )


def _machine_load(machine: Mapping[str, object], repair_prior: Mapping[str, object] | int | None) -> float:
    repair: float = 0.0
    if machine["status"] != "OK":
        repair = _expected_repair_remaining(repair_prior, int(machine["down_elapsed"]))
    return (
        repair
        + float(machine["current_remaining_time"])
        + sum(
            float(operation["processing_time"])
            for operation in machine["input_queue"]
        )
        + sum(
            float(operation["processing_time"])
            for operation in machine["suspended_operations"]
        )
    )


def _expected_repair_remaining(prior: Mapping[str, object] | int | None, elapsed: int) -> float:
    """E[D - elapsed | D > elapsed] for the injector's public integer law."""
    if prior is None or isinstance(prior, int):
        return float(max(1, (1 if prior is None else prior) - elapsed))
    kind: str = str(prior.get("dist", "fixed")).lower()
    if kind in {"uniform", "discrete_uniform"}:
        low, high = int(prior.get("low", 1)), int(prior.get("high", 1))
        lower: int = max(1, elapsed + 1, low)
        if high >= lower:
            return (lower + high) / 2 - elapsed
    elif kind in {"triangular", "discrete_triangular"}:
        low: int = int(prior.get("low", 1))
        high: int = int(prior.get("high", low))
        mode: int = int(prior.get("mode", low))
        if high == low:
            return float(max(1, low - elapsed))
        mass: float = 0.0
        residual: float = 0.0
        for duration in range(max(1, elapsed + 1, low), high + 1):
            cdf: list[float] = []
            for boundary in (float("-inf") if duration == 1 else duration - .5, duration + .5):
                if boundary <= low:
                    cdf.append(0.0)
                elif boundary >= high:
                    cdf.append(1.0)
                elif boundary < mode:
                    cdf.append((boundary - low) ** 2 / ((high - low) * (mode - low)))
                else:
                    cdf.append(1 - (high - boundary) ** 2 / ((high - low) * (high - mode)))
            probability: float = cdf[1] - cdf[0]
            mass += probability
            residual += probability * (duration - elapsed)
        if mass > 0:
            return residual / mass
    else:
        # Fixed and unrecognized kinds follow ExceptionInjector._sample_duration.
        return float(max(1, int(prior.get("value", 1)) - elapsed))
    # An outage beyond the public support cannot expose its hidden recovery date.
    # Reassess on the next simulator tick until an observable recovery arrives.
    return 1.0
