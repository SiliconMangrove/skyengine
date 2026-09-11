"""Pluggable RACE-FMS coordinator for the existing SkyEngine lifecycle."""

from __future__ import annotations

from collections import Counter
from time import perf_counter
from typing import Any, Mapping, Optional, Protocol

import numpy as np

from sky_executor.grid_factory.factory.Component.Coordinator.coordinator import Coordinator

from .coupling import CounterfactualCouplingEstimator
from .recovery import AdaptiveRecoveryGate, RecoveryDecision, RecoveryScope
from .state import RaceState, RaceStateEncoder


class RacePolicy(Protocol):
    """Policy adapter returning environment-compatible action overrides."""

    def decide(
        self,
        state: RaceState,
        observation: Mapping[str, Any],
        recovery: RecoveryDecision,
    ) -> Mapping[str, Any]: ...


class RecoveryHandler(Protocol):
    """Runtime-owned recovery hook.

    The hook may call a residual scheduling service and atomically update the
    runtime buffers it owns.  Keeping this outside the gate prevents the model
    from mutating environment queues directly.
    """

    def recover(
        self,
        decision: RecoveryDecision,
        state: RaceState,
        observation: Mapping[str, Any],
        delegate: Coordinator,
    ) -> Optional[Mapping[str, Any]]: ...


class RaceFMSCoordinator:
    """Adaptive wrapper around the production, assignment, and routing stack.

    Without a learned policy or recovery handler it is behavior-compatible
    with the wrapped Coordinator while still collecting RACE-FMS state and gate
    diagnostics.  This makes materiality studies possible before DRL training.
    """

    def __init__(
        self,
        delegate: Optional[Coordinator] = None,
        *,
        job_solver=None,
        route_solver=None,
        assigner=None,
        estimator: Optional[CounterfactualCouplingEstimator] = None,
        gate: Optional[AdaptiveRecoveryGate] = None,
        policy: Optional[RacePolicy] = None,
        recovery_handler: Optional[RecoveryHandler] = None,
        **coordinator_kwargs,
    ):
        self.delegate = delegate or Coordinator(
            job_solver=job_solver,
            route_solver=route_solver,
            assigner=assigner,
            **coordinator_kwargs,
        )
        self.encoder = RaceStateEncoder()
        self.gate = gate or AdaptiveRecoveryGate(estimator=estimator)
        self.policy = policy
        self.recovery_handler = recovery_handler
        self._history: list[dict] = []
        self._last_state: Optional[RaceState] = None
        self._last_decision: Optional[RecoveryDecision] = None

    @property
    def job_solver(self):
        return self.delegate.job_solver

    @property
    def route_solver(self):
        return self.delegate.route_solver

    @property
    def assigner(self):
        return self.delegate.assigner

    @staticmethod
    def _merge_actions(base: Mapping[str, Any], override: Optional[Mapping[str, Any]]) -> dict:
        merged = dict(base)
        for key, value in (override or {}).items():
            if key not in {"job_actions", "agent_actions", "assign_actions"}:
                raise ValueError(f"unsupported RACE-FMS action override: {key}")
            merged[key] = value
        return merged

    def decide(self, obs: Mapping[str, Any]) -> dict:
        started = perf_counter()
        state = self.encoder.encode(obs)
        decision = self.gate.decide(
            features=state.coupling_vector,
            coupling=state.coupling,
            events=state.events,
            event_epoch=state.epochs["event_epoch"],
            step=state.timeline,
        )

        recovery_override = None
        recovery_error = None
        if self.recovery_handler is not None and decision.scope != RecoveryScope.KEEP_PLAN:
            try:
                recovery_override = self.recovery_handler.recover(
                    decision, state, obs, self.delegate
                )
            except Exception as exc:  # runtime keeps the previous valid plan
                recovery_error = str(exc)

        actions = self.delegate.decide(obs)
        actions = self._merge_actions(actions, recovery_override)
        if self.policy is not None:
            actions = self._merge_actions(
                actions,
                self.policy.decide(state, obs, decision),
            )

        elapsed_ms = (perf_counter() - started) * 1000.0
        self._last_state = state
        self._last_decision = decision
        self._history.append({
            "step": state.timeline,
            "scope": decision.scope.name,
            "scope_id": int(decision.scope),
            "predicted_benefit": decision.predicted_benefit,
            "prediction_uncertainty": decision.uncertainty,
            "trigger": decision.trigger,
            "event_epoch": decision.event_epoch,
            "decision_latency_ms": elapsed_ms,
            "recovery_error": recovery_error,
            **state.coupling,
        })
        return actions

    def reset(self) -> None:
        self.reset_diagnostics()
        for component in (self.job_solver, self.route_solver, self.assigner):
            reset = getattr(component, "reset", None)
            if callable(reset):
                reset()

    def reset_diagnostics(self) -> None:
        """Reset episode-local RACE state without resetting solver services."""
        self.gate.reset()
        self._history.clear()
        self._last_state = None
        self._last_decision = None

    def get_last_state(self) -> Optional[RaceState]:
        return self._last_state

    def get_last_recovery_decision(self) -> Optional[RecoveryDecision]:
        return self._last_decision

    def get_decision_history(self) -> list[dict]:
        return list(self._history)

    def get_race_metrics(self) -> dict:
        if not self._history:
            return {
                "race_decision_count": 0,
                "race_coordination_rate": 0.0,
                "race_joint_replan_rate": 0.0,
                "race_recovery_errors": 0,
            }
        scopes = Counter(row["scope"] for row in self._history)
        coordinated = sum(
            count for name, count in scopes.items()
            if name not in {RecoveryScope.KEEP_PLAN.name, RecoveryScope.PATH_REPLAN.name}
        )
        coupling_keys = list(self._last_state.coupling) if self._last_state else []
        result = {
            "race_decision_count": len(self._history),
            "race_coordination_rate": coordinated / len(self._history),
            "race_joint_replan_rate": scopes[RecoveryScope.JOINT_ROLLING_REPLAN.name] / len(self._history),
            "race_recovery_errors": sum(bool(row["recovery_error"]) for row in self._history),
            "race_decision_latency_ms_mean": float(np.mean([row["decision_latency_ms"] for row in self._history])),
            "race_scope_counts": dict(scopes),
        }
        for key in coupling_keys:
            values = [float(row[key]) for row in self._history if key in row]
            if values:
                result[f"race_{key}_mean"] = float(np.mean(values))
                result[f"race_{key}_p95"] = float(np.percentile(values, 95))
        return result
