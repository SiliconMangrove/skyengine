"""Event-driven adaptive recovery policy for RACE-FMS."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Mapping, Optional, Sequence

from .coupling import CounterfactualCouplingEstimator


class RecoveryScope(IntEnum):
    KEEP_PLAN = 0
    PATH_REPLAN = 1
    AGV_REASSIGN = 2
    LOCAL_PRODUCTION_REPLAN = 3
    JOINT_ROLLING_REPLAN = 4


class RecoveryWindow:
    """Public event/impact analysis shared by learned rolling controllers.

    This identifies affected objects, not the action to execute. The learned
    controller owns the single rescheduling transaction for its session.
    """

    @staticmethod
    def inspect(scene: dict) -> dict:
        machines: set[int] = {m["id"] for m in scene["machines"] if m["status"] != "OK"}
        agents: set[int] = {a["id"] for a in scene["agents"] if a["status"] != "OK"}
        jobs: set[int] = set()
        hard: bool = False
        event_types: set[str] = set()
        for event in scene["events"]:
            kind = event["type"]
            event_types.add(kind)
            payload = event.get("payload", event)
            if "machine_id" in payload:
                machines.add(int(payload["machine_id"]))
            if "agv_id" in payload:
                agents.add(int(payload["agv_id"]))
            if "job_id" in payload:
                jobs.add(int(payload["job_id"]))
            hard |= kind in {"machine_breakdown", "agv_breakdown", "route_infeasible", "deadlock", "temporary_obstacle"}
        for task in scene["tasks"]:
            destination = tuple(task["destination"])
            if task["assigned_agent_id"] in agents or any(m["id"] in machines and tuple(m["location"]) == destination for m in scene["machines"]):
                jobs.add(task["job_id"])
        for machine in scene["machines"]:
            if machine["id"] in machines or len(machine["buffer_jobs"]) >= machine["buffer_capacity"]:
                machines.add(machine["id"])
                jobs.update(key[0] for key in machine["input_queue"])
                jobs.update(machine["buffer_jobs"])
        if not machines and scene["machines"]:
            # Periodic optimization starts at the current queue bottleneck.
            bottleneck = max(scene["machines"], key=lambda m: (len(m["input_queue"]), len(m["buffer_jobs"])))
            if bottleneck["input_queue"]:
                machines.add(bottleneck["id"])
                jobs.update(key[0] for key in bottleneck["input_queue"])
        return {"machines": machines, "agents": agents, "jobs": jobs,
                "hard": hard, "event_types": event_types}


@dataclass(frozen=True)
class RecoveryDecision:
    scope: RecoveryScope
    predicted_benefit: float
    uncertainty: float
    trigger: str
    event_epoch: int


class AdaptiveRecoveryGate:
    """Choose the smallest recovery scope justified by expected benefit.

    Hysteresis and a cooldown prevent small state changes from repeatedly
    invalidating the production plan.
    """

    def __init__(
        self,
        estimator: Optional[CounterfactualCouplingEstimator] = None,
        coordinate_threshold: float = 0.05,
        joint_threshold: float = 0.20,
        risk_threshold: float = 0.65,
        cooldown_steps: int = 2,
        uncertainty_penalty: float = 0.25,
        retrigger_margin: float = 0.10,
    ):
        if joint_threshold < coordinate_threshold:
            raise ValueError("joint_threshold must be >= coordinate_threshold")
        self.estimator = estimator or CounterfactualCouplingEstimator()
        self.coordinate_threshold = float(coordinate_threshold)
        self.joint_threshold = float(joint_threshold)
        self.risk_threshold = float(risk_threshold)
        self.cooldown_steps = max(0, int(cooldown_steps))
        self.uncertainty_penalty = max(0.0, float(uncertainty_penalty))
        self.retrigger_margin = max(0.0, float(retrigger_margin))
        self._last_event_epoch = -1
        self._last_decision_step = -10**9
        self._last_scope = RecoveryScope.KEEP_PLAN
        self._last_risk = 0.0
        self._last_coordinated_benefit = float("-inf")

    def reset(self) -> None:
        self._last_event_epoch = -1
        self._last_decision_step = -10**9
        self._last_scope = RecoveryScope.KEEP_PLAN
        self._last_risk = 0.0
        self._last_coordinated_benefit = float("-inf")

    @staticmethod
    def _base_scope(event_types: set[str]) -> RecoveryScope:
        if event_types & {"machine_breakdown", "machine_recovery", "job_release", "urgent_job_arrival", "job_replan_started"}:
            return RecoveryScope.LOCAL_PRODUCTION_REPLAN
        if event_types & {"agv_breakdown", "agv_recovery"}:
            return RecoveryScope.AGV_REASSIGN
        if event_types & {"temporary_obstacle", "temporary_obstacle_cleared", "map_changed"}:
            return RecoveryScope.PATH_REPLAN
        if "processing_time_deviation" in event_types:
            return RecoveryScope.LOCAL_PRODUCTION_REPLAN
        return RecoveryScope.KEEP_PLAN

    def decide(
        self,
        *,
        features: Sequence[float],
        coupling: Mapping[str, float],
        events: Sequence[Mapping],
        event_epoch: int,
        step: int,
    ) -> RecoveryDecision:
        event_types = {str(event.get("type", "")) for event in events}
        base_scope = self._base_scope(event_types)
        new_event = int(event_epoch) != self._last_event_epoch

        benefit, uncertainty = self.estimator.predict(features)
        conservative_benefit = benefit - self.uncertainty_penalty * uncertainty
        risk = max(
            float(coupling.get("starvation_risk", 0.0)),
            float(coupling.get("due_pressure", 0.0)),
            float(coupling.get("queue_pressure", 0.0)),
            float(coupling.get("congestion_pressure", 0.0)),
            float(coupling.get("disturbance_pressure", 0.0)),
            float(coupling.get("processing_uncertainty", 0.0)),
            float(coupling.get("affected_fraction", 0.0)),
        )

        continuous_escalation = (
            risk >= self.risk_threshold
            and (
                self._last_risk < self.risk_threshold
                or risk >= self._last_risk + self.retrigger_margin
                or conservative_benefit
                >= self._last_coordinated_benefit + self.retrigger_margin
            )
        )

        if not new_event and not continuous_escalation:
            scope = RecoveryScope.KEEP_PLAN
            trigger = "no_new_material_change"
        elif step - self._last_decision_step < self.cooldown_steps and risk < 0.9:
            scope = min(base_scope, self._last_scope)
            trigger = "cooldown"
        elif conservative_benefit >= self.joint_threshold and risk >= self.risk_threshold:
            scope = RecoveryScope.JOINT_ROLLING_REPLAN
            trigger = "counterfactual_joint_value"
        elif conservative_benefit >= self.coordinate_threshold:
            scope = max(base_scope, RecoveryScope.AGV_REASSIGN)
            trigger = "counterfactual_coordination_value"
        else:
            scope = base_scope
            trigger = "minimal_event_recovery"

        if new_event:
            self._last_event_epoch = int(event_epoch)
        if scope != RecoveryScope.KEEP_PLAN:
            self._last_decision_step = int(step)
            self._last_scope = scope
            self._last_coordinated_benefit = conservative_benefit
        self._last_risk = risk
        return RecoveryDecision(scope, benefit, uncertainty, trigger, int(event_epoch))
