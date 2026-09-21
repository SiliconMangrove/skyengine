"""平台统一轨迹采集器。"""

from __future__ import annotations

from typing import Any, Callable, Sequence
from copy import deepcopy
import json
from pathlib import Path

from .api import Transition, Trajectory


class RolloutCollector:
    def __init__(self, policy, reward, trace_dir: str | None = None, *, training_only: bool = False):
        self.policy = policy
        self.reward = reward
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.training_only: bool = training_only
        if training_only and trace_dir:
            raise ValueError("training-only collection cannot record replay traces")

    def collect(
        self,
        env,
        max_steps: int,
        deterministic: bool = False,
        episode_id: str | None = None,
        seed: int | None = None,
        progress_callback: Callable[[int], None] | None = None,
        progress_interval: int = 100,
        cancel_check: Callable[[], None] | None = None,
    ) -> Trajectory:
        if hasattr(self.policy, "reset"):
            self.policy.reset()
        observation, info = env.reset(seed=seed)
        transitions = []
        trace = []
        if self.trace_dir:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            trace.append({"type": "reset", "episode_id": episode_id, "seed": seed, "frame": deepcopy(env.state_frame()) if hasattr(env, "state_frame") else {}})
        for _ in range(max_steps):
            if cancel_check is not None:
                cancel_check()
            if _ and _ % 10 == 0:
                print(json.dumps({"episode": episode_id, "status": "collecting", "transitions": _, "collecting_step": _}), flush=True)
            observation_snapshot = None if self.training_only else deepcopy(observation)
            mask = None
            if isinstance(observation, dict):
                mask = observation.get("action_mask", observation.get("action_masks"))
            before_metrics = env.metrics() if hasattr(env, "metrics") else {}
            before_timeline = float(observation.get("timeline", before_metrics.get("timeline", 0.0))) if isinstance(observation, dict) else float(before_metrics.get("timeline", 0.0))
            action = self.policy.act(observation, mask, deterministic=deterministic)
            next_observation, raw_reward, terminated, truncated, step_info = env.step(action)
            next_observation_snapshot = None if self.training_only else deepcopy(next_observation)
            after_metrics = dict(step_info.get("after_metrics", step_info.get("metrics", {})))
            if not after_metrics and isinstance(next_observation, dict):
                after_metrics = {"timeline": float(next_observation.get("timeline", 0.0))}
            after_timeline = float(next_observation.get("timeline", after_metrics.get("timeline", before_timeline))) if isinstance(next_observation, dict) else float(after_metrics.get("timeline", before_timeline))
            transition_info = {
                "reset": info,
                **step_info,
                "before_metrics": before_metrics,
                "after_metrics": after_metrics,
                "metrics": after_metrics,
                "delta_t": step_info.get("delta_t", after_timeline - before_timeline),
                "termination_reason": step_info.get("termination_reason"),
            }
            policy_data = getattr(self.policy, "last_action_data", None)
            if policy_data is not None:
                transition_info["_policy"] = policy_data
            transition = Transition(observation_snapshot, None if self.training_only else deepcopy(action), raw_reward, next_observation_snapshot, terminated, truncated, transition_info)
            transition.reward = self.reward.compute(transition)
            if self.training_only:
                transition.info = {"delta_t": transition_info["delta_t"], "_policy": policy_data}
            transitions.append(transition)
            if progress_callback is not None and (
                len(transitions) % progress_interval == 0 or terminated or truncated
            ):
                progress_callback(len(transitions))
            if self.trace_dir:
                from sky_executor.runtime_log import serialize_action
                trace.append({"type": "step", "step": len(transitions) - 1,
                              "action": serialize_action(action),
                              "formal_action": serialize_action(step_info.get("raw_action")),
                              "frame": deepcopy(step_info.get("frame", env.state_frame() if hasattr(env, "state_frame") else {})),
                              "metrics": deepcopy(after_metrics), "terminated": bool(terminated), "truncated": bool(truncated)})
            observation = next_observation
            if terminated or truncated:
                break
        if self.trace_dir:
            trace_path = self.trace_dir / f"episode_{episode_id or len(list(self.trace_dir.glob('episode_*.jsonl'))):06d}.jsonl"
            with trace_path.open("w", encoding="utf-8") as stream:
                for record in trace:
                    stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return Trajectory(transitions, episode_id=episode_id)

    def collect_batch(self, envs: Sequence[Any], max_steps: int, deterministic: bool = False) -> list[Trajectory]:
        return [self.collect(env, max_steps, deterministic, str(index)) for index, env in enumerate(envs)]
