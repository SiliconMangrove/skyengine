"""Formal SkyEngine adapter used by both RL and factory replay."""
from __future__ import annotations
import copy
from typing import Any, Mapping

class SkyEngineTrainingEnv:
    """A headless view of the same SimulationSession used by the GUI factory."""
    backend = "formal_headless"

    def __init__(self, instance: Mapping[str, Any], mapf_algorithm: str = "astar", coordinator: Any = None, *, capture_frames: bool = False):
        from sky_executor.session import SimulationSession
        self.instance = copy.deepcopy(dict(instance))
        self.mapf_algorithm = mapf_algorithm
        self.capture_frames: bool = capture_frames
        self.session = SimulationSession.from_config(
            self.instance, job_solver="greedy", route_solver=mapf_algorithm,
            assigner="nearest", mapf_algorithm=mapf_algorithm, headless=True,
        )
        if coordinator is not None:
            self.session.coordinator = coordinator
        self.raw_observation = self.session.obs
        self.raw_info = self.session.info

    def reset(self, seed: int | None = None, options: dict | None = None):
        del options
        self.raw_observation, self.raw_info = self.session.reset(seed=seed)
        return self.raw_observation, {**(self.raw_info or {}), "seed": seed, "backend": self.backend}

    def step(self, action: Mapping[str, Any] | None = None):
        next_observation, rewards, terminations, truncations, info = self.session.step(action or {})
        self.raw_observation = next_observation
        self.raw_info = info
        terminated = bool((terminations or {}).get("job_done", False))
        agent_truncated = (truncations or {}).get("agent_truncated", False)
        truncated = bool((truncations or {}).get("__all__", False)) or (isinstance(agent_truncated, dict) and bool(agent_truncated.get("__all__", False)))
        info = dict(info or {})
        info.update({
            "raw_action": self.session.last_actions,
            "metrics": self.metrics(),
            "termination_reason": "terminated" if terminated else "truncated" if truncated else None,
        })
        if self.capture_frames:
            info["frame"] = self.state_frame()
        return next_observation, rewards, terminated, truncated, info

    def metrics(self) -> dict[str, Any]:
        return dict(self.session.metrics() or {})

    def state_frame(self) -> dict[str, Any]:
        return self.session.state_frame()

    def close(self) -> None:
        self.session.close()
