"""Discrete disruption exception injector for GridFactoryEnv.

ExceptionInjector is part of environment dynamics, not a solver. It injects
reproducible disruptions into the actual machine, AGV, and map state, then
exposes event records and epoch counters through observations and infos.
"""

from __future__ import annotations

import json
import math
import os
import random
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

class ExceptionInjector:
    """Applies schedule-driven and probabilistic disruption events."""

    SUPPORTED_TYPES = {"machine_breakdown", "agv_breakdown", "temporary_obstacle"}

    PRESETS = {
        # ``none`` is accepted as the configuration-level spelling for
        # disabling exception injection (processing-time configs use the same
        # spelling).  Keep ``no_event`` as the canonical preset name.
        "none": {},
        "no_event": {},
        "mild_failure": {
            "machine_failure": {
                "enabled": True,
                "mtbf_steps": 300,
                "busy_only": True,
                "repair_time": {"dist": "fixed", "value": 10},
            },
            "agv_failure": {
                "enabled": True,
                "mtbf_steps": 500,
                "busy_only": False,
                "repair_time": {"dist": "fixed", "value": 8},
            },
        },
        "moderate_failure": {
            "machine_failure": {
                "enabled": True,
                "mtbf_steps": 180,
                "busy_only": False,
                "repair_time": {"dist": "discrete_uniform", "low": 10, "high": 30},
            },
            "agv_failure": {
                "enabled": True,
                "mtbf_steps": 260,
                "busy_only": False,
                "repair_time": {"dist": "discrete_uniform", "low": 5, "high": 20},
            },
            "temporary_obstacle": {
                "enabled": True,
                "mean_interarrival_steps": 140,
                "max_new_per_step": 1,
                "preserve_connectivity": True,
                "duration": {"dist": "discrete_uniform", "low": 8, "high": 24},
            },
        },
        "stress_failure": {
            "machine_failure": {
                "enabled": True,
                "mtbf_steps": 30,
                "busy_only": False,
                "repair_time": {"dist": "discrete_uniform", "low": 20, "high": 60},
            },
            "agv_failure": {
                "enabled": True,
                "mtbf_steps": 50,
                "busy_only": False,
                "repair_time": {"dist": "discrete_uniform", "low": 10, "high": 40},
            },
            "temporary_obstacle": {
                "enabled": True,
                "mean_interarrival_steps": 35,
                "max_new_per_step": 1,
                "preserve_connectivity": True,
                "duration": {"dist": "discrete_uniform", "low": 8, "high": 24},
            },
        },
        "routing_disruption": {
            "temporary_obstacle": {
                "enabled": True,
                "mean_interarrival_steps": 25,
                "max_new_per_step": 1,
                "preserve_connectivity": True,
                "duration": {"dist": "discrete_uniform", "low": 8, "high": 24},
            },
        },
        "combined_moderate": {
            "machine_failure": {
                "enabled": True,
                "mtbf_steps": 80,
                "busy_only": False,
                "repair_time": {"dist": "discrete_uniform", "low": 10, "high": 30},
            },
            "agv_failure": {
                "enabled": True,
                "mtbf_steps": 120,
                "busy_only": False,
                "repair_time": {"dist": "discrete_uniform", "low": 5, "high": 20},
            },
            "temporary_obstacle": {
                "enabled": True,
                "mean_interarrival_steps": 40,
                "max_new_per_step": 1,
                "preserve_connectivity": True,
                "duration": {"dist": "discrete_uniform", "low": 8, "high": 24},
            },
        },
    }

    DEFAULT_CONFIG = {
        "enabled": False,
        "random_seed": None,
        "preset": None,
        "schedule": [],
        "replay_file": None,
        "exception_log_path": None,
        "machine_failure": {
            "enabled": False,
            "prob_per_machine_step": 0.0,
            "mtbf_steps": None,
            "busy_only": False,
            "repair_time": {"dist": "fixed", "value": 10},
        },
        "agv_failure": {
            "enabled": False,
            "prob_per_agv_step": 0.0,
            "mtbf_steps": None,
            "busy_only": False,
            "repair_time": {"dist": "fixed", "value": 10},
        },
        "temporary_obstacle": {
            "enabled": False,
            "prob_per_step": 0.0,
            "mean_interarrival_steps": None,
            "lambda_per_step": None,
            "max_new_per_step": 1,
            "preserve_connectivity": True,
            "duration": {"dist": "fixed", "value": 10},
        },
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        explicit_config = config or {}
        preset_name = explicit_config.get("preset")
        preset_config = {}
        if preset_name:
            preset_key = str(preset_name)
            if preset_key not in self.PRESETS:
                raise ValueError(f"Unknown exception preset: {preset_name}")
            preset_config = self.PRESETS[preset_key]
        self.config = self._merge_config(
            self._merge_config(self.DEFAULT_CONFIG, preset_config),
            explicit_config,
        )
        auto_enabled = bool(
            self.config.get("schedule")
            or self.config.get("replay_file")
            or self.config.get("machine_failure", {}).get("enabled", False)
            or self.config.get("agv_failure", {}).get("enabled", False)
            or self.config.get("temporary_obstacle", {}).get("enabled", False)
        )
        if "enabled" in explicit_config:
            self.enabled = bool(self.config.get("enabled", False))
        else:
            self.enabled = auto_enabled
        self.random_seed = self.config.get("random_seed", None)
        self.rng = random.Random(self.random_seed)
        self._schedule = self._load_schedule()
        self._schedule_idx = 0
        self._step_events: List[Dict[str, Any]] = []
        self._all_events: List[Dict[str, Any]] = []
        self._active_obstacles: Dict[Tuple[int, int], Dict[str, Any]] = {}
        self._metrics = self._new_metrics()
        self._exception_log_path = self.config.get("exception_log_path")

    @classmethod
    def from_env(cls, config: Optional[Dict[str, Any]] = None) -> "ExceptionInjector":
        """Build an ExceptionInjector from an explicit config plus environment vars."""
        env_config: Dict[str, Any] = {}
        if os.getenv("EXCEPTIONS_ENABLED") is not None:
            env_config["enabled"] = _truthy(os.getenv("EXCEPTIONS_ENABLED"))
        if os.getenv("EXCEPTION_RANDOM_SEED") is not None:
            env_config["random_seed"] = int(os.getenv("EXCEPTION_RANDOM_SEED"))
        if os.getenv("EXCEPTIONS_PRESET"):
            env_config["preset"] = os.getenv("EXCEPTIONS_PRESET")
        if os.getenv("EXCEPTIONS_SCHEDULE_JSON"):
            env_config["schedule"] = json.loads(os.getenv("EXCEPTIONS_SCHEDULE_JSON", "[]"))
        if os.getenv("EXCEPTIONS_REPLAY_FILE"):
            env_config["replay_file"] = os.getenv("EXCEPTIONS_REPLAY_FILE")
            env_config["enabled"] = True
        if os.getenv("EXCEPTIONS_LOG_PATH"):
            env_config["exception_log_path"] = os.getenv("EXCEPTIONS_LOG_PATH")

        machine_prob = float(os.getenv("MACHINE_FAILURE_PROB", "0") or 0)
        machine_mtbf = float(os.getenv("MACHINE_MTBF_STEPS", "0") or 0)
        agv_prob = float(os.getenv("AGV_FAILURE_PROB", "0") or 0)
        agv_mtbf = float(os.getenv("AGV_MTBF_STEPS", "0") or 0)
        obstacle_prob = float(os.getenv("TEMP_OBSTACLE_PROB", "0") or 0)
        obstacle_mean = float(os.getenv("TEMP_OBSTACLE_MEAN_INTERARRIVAL_STEPS", "0") or 0)

        if machine_prob > 0 or machine_mtbf > 0:
            env_config.setdefault("machine_failure", {})
            env_config["machine_failure"].update({
                "enabled": True,
                "repair_time": {
                    "dist": "fixed",
                    "value": int(os.getenv("MACHINE_REPAIR_TIME_STEPS", "10")),
                },
            })
            if machine_prob > 0:
                env_config["machine_failure"]["prob_per_machine_step"] = machine_prob
            if machine_mtbf > 0:
                env_config["machine_failure"]["mtbf_steps"] = machine_mtbf
            env_config["enabled"] = True
        if agv_prob > 0 or agv_mtbf > 0:
            env_config.setdefault("agv_failure", {})
            env_config["agv_failure"].update({
                "enabled": True,
                "repair_time": {
                    "dist": "fixed",
                    "value": int(os.getenv("AGV_REPAIR_TIME_STEPS", "10")),
                },
            })
            if agv_prob > 0:
                env_config["agv_failure"]["prob_per_agv_step"] = agv_prob
            if agv_mtbf > 0:
                env_config["agv_failure"]["mtbf_steps"] = agv_mtbf
            env_config["enabled"] = True
        if obstacle_prob > 0 or obstacle_mean > 0:
            env_config.setdefault("temporary_obstacle", {})
            env_config["temporary_obstacle"].update({
                "enabled": True,
                "duration": {
                    "dist": "fixed",
                    "value": int(os.getenv("TEMP_OBSTACLE_DURATION_STEPS", "10")),
                },
            })
            if obstacle_prob > 0:
                env_config["temporary_obstacle"]["prob_per_step"] = obstacle_prob
            if obstacle_mean > 0:
                env_config["temporary_obstacle"]["mean_interarrival_steps"] = obstacle_mean
            if os.getenv("TEMP_OBSTACLE_MAX_NEW_PER_STEP"):
                env_config["temporary_obstacle"]["max_new_per_step"] = int(os.getenv("TEMP_OBSTACLE_MAX_NEW_PER_STEP", "1"))
            env_config["enabled"] = True
        merged = cls._merge_config(env_config, config or {})
        return cls(merged)

    @staticmethod
    def _merge_config(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        result = deepcopy(base)
        for key, value in (override or {}).items():
            if (
                isinstance(value, dict)
                and isinstance(result.get(key), dict)
            ):
                result[key] = ExceptionInjector._merge_config(result[key], value)
            else:
                result[key] = deepcopy(value)
        return result

    @staticmethod
    def _new_metrics() -> Dict[str, int]:
        return {
            "event_count_total": 0,
            "machine_failure_count": 0,
            "machine_recovery_count": 0,
            "machine_down_steps_total": 0,
            "agv_failure_count": 0,
            "agv_recovery_count": 0,
            "agv_down_steps_total": 0,
            "temporary_obstacle_count": 0,
            "obstacle_clear_count": 0,
        }

    def reset(self, env, seed: int | None = None) -> None:
        self.rng = random.Random(self.random_seed if seed is None else int(seed) ^ 0x5EEDFA17)
        self._schedule = self._load_schedule()
        self._schedule_idx = 0
        self._step_events = []
        self._all_events = []
        self._active_obstacles = {}
        self._metrics = self._new_metrics()
        self._prepare_event_log()
        self._ensure_state(env.pogema_env)
        self._publish_state(env.pogema_env)

    def before_step(self, env) -> None:
        penv = env.pogema_env
        self._ensure_state(penv)
        self._step_events = []
        if not self.enabled:
            self._publish_state(penv)
            return

        step = int(penv.env_timeline)
        self._apply_scheduled_events(penv, step)
        self._apply_probabilistic_events(penv, step)
        self._publish_state(penv)

    @contextmanager
    def step_context(self, env, agent_actions):
        """Apply event phases around normal environment execution."""
        self.before_step(env)
        patched_agent_actions = self.before_move(env, agent_actions)
        try:
            yield patched_agent_actions
        finally:
            self.after_step(env)

    def before_move(self, env, agent_actions):
        """Patch AGV actions so broken AGVs stay in place."""
        penv = env.pogema_env
        self._ensure_state(penv)
        if not agent_actions:
            return agent_actions
        patched = list(agent_actions)
        for idx, status in enumerate(penv.agv_status):
            if status != "OK" and idx < len(patched):
                patched[idx] = 0
        return patched

    def after_step(self, env) -> None:
        penv = env.pogema_env
        self._ensure_state(penv)
        if self.enabled:
            self._tick_repairs(penv, int(env.env_timeline))
            self._tick_obstacles(penv, int(env.env_timeline))
        self._publish_state(penv)

    def get_step_events(self) -> List[Dict[str, Any]]:
        return list(self._step_events)

    def get_metrics(self) -> Dict[str, int]:
        metrics = dict(self._metrics)
        metrics["event_count_total"] = len(self._all_events)
        return metrics

    def inject_manual(self, penv, step: int, event: Dict[str, Any]) -> Dict[str, Any]:
        """Inject one operator-requested Exception now or schedule it for a later step."""
        self._ensure_state(penv)
        self.enabled = True
        manual_event = deepcopy(event or {})
        event_type = manual_event.get("type")
        if event_type not in self.SUPPORTED_TYPES:
            return {
                "status": "error",
                "step": int(step),
                "type": event_type,
                "message": f"Unsupported Exception type: {event_type}",
            }
        target_step = int(manual_event.get("start_step", manual_event.get("step", step)))
        manual_event["step"] = target_step

        if target_step > int(step):
            self._schedule.append(manual_event)
            self._schedule = sorted(self._schedule, key=lambda item: int(item.get("step", 0)))
            return {
                "status": "scheduled",
                "step": target_step,
                "type": manual_event.get("type"),
            }

        self._step_events = []
        before_count = len(self._all_events)
        self._apply_event(penv, int(step), manual_event)
        self._publish_state(penv)
        if len(self._all_events) == before_count:
            return {
                "status": "ignored",
                "step": int(step),
                "type": manual_event.get("type"),
                "message": "Exception did not apply, target may be invalid or already affected.",
            }
        return {
            "status": "ok",
            "step": int(step),
            "type": manual_event.get("type"),
            "events": self.get_step_events(),
        }

    def clear_manual(self, penv, step: int, target: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Clear active operator-visible Exception effects."""
        self._ensure_state(penv)
        self.enabled = True
        target = target or {}
        target_type = target.get("type")
        cleared = []
        self._step_events = []

        def should_clear_machine(machine_id: int) -> bool:
            return target_type in (None, "machine_breakdown") and (
                target.get("machine_id") is None or int(target.get("machine_id")) == int(machine_id)
            )

        def should_clear_agv(agv_id: int) -> bool:
            return target_type in (None, "agv_breakdown") and (
                target.get("agv_id") is None or int(target.get("agv_id")) == int(agv_id)
            )

        def should_clear_cell(cell: Tuple[int, int]) -> bool:
            if target_type not in (None, "temporary_obstacle"):
                return False
            if target.get("cell") is None:
                return True
            raw = target.get("cell")
            return tuple(int(v) for v in raw[:2]) == tuple(cell)

        for machine in getattr(penv, "machines", []) or []:
            if getattr(machine, "status", "OK") == "DOWN" and should_clear_machine(machine.id):
                machine.status = "OK"
                machine.repair_remaining = 0
                machine.down_reason = None
                penv.machine_epoch += 1
                self._metrics["machine_recovery_count"] += 1
                self._record(penv, step, "machine_recovery", {"machine_id": machine.id}, level="info")
                cleared.append({"type": "machine_breakdown", "machine_id": machine.id})

        for idx, status in enumerate(getattr(penv, "agv_status", []) or []):
            if status == "DOWN" and should_clear_agv(idx):
                penv.agv_status[idx] = "OK"
                penv.agv_repair_remaining[idx] = 0
                penv.agv_down_reason[idx] = None
                penv.agv_epoch += 1
                self._metrics["agv_recovery_count"] += 1
                self._record(penv, step, "agv_recovery", {"agv_id": idx}, level="info")
                cleared.append({"type": "agv_breakdown", "agv_id": idx})

        for cell in list(self._active_obstacles):
            if not should_clear_cell(cell):
                continue
            state = self._active_obstacles[cell]
            penv.grid.obstacles[cell] = int(state.get("original", 0))
            del self._active_obstacles[cell]
            penv.map_epoch += 1
            self._metrics["obstacle_clear_count"] += 1
            self._record(penv, step, "obstacle_clear", {"cell": list(cell)}, level="info")
            cleared.append({"type": "temporary_obstacle", "cell": list(cell)})

        self._publish_state(penv)
        return {
            "status": "ok",
            "step": int(step),
            "cleared": cleared,
            "events": self.get_step_events(),
        }

    def get_epochs(self, penv=None) -> Dict[str, int]:
        source = penv
        return {
            "event_epoch": int(getattr(source, "event_epoch", 0)),
            "map_epoch": int(getattr(source, "map_epoch", 0)),
            "machine_epoch": int(getattr(source, "machine_epoch", 0)),
            "agv_epoch": int(getattr(source, "agv_epoch", 0)),
            "job_epoch": int(getattr(source, "job_epoch", 0)),
        }

    def enrich_task_observation(self, task_obs: Dict[str, Any], penv) -> None:
        if not isinstance(task_obs, dict):
            return
        self._ensure_state(penv)
        task_obs.update(self.get_epochs(penv))
        task_obs["events"] = [dict(event, payload=dict(event.get("payload", {}))) for event in penv.last_events]
        task_obs["event_metrics"] = self.get_metrics()
        task_obs["agv_status"] = list(penv.agv_status)
        task_obs["agv_down_elapsed"] = list(penv.agv_down_elapsed)
        task_obs["blocked_cells"] = [list(cell) for cell in self._active_obstacles]

        for m in task_obs.get("machines", []) or []:
            if not hasattr(m, "status"):
                m.status = "OK"
            if not hasattr(m, "repair_remaining"):
                m.repair_remaining = 0
            if not hasattr(m, "down_reason"):
                m.down_reason = None

    def _ensure_state(self, penv) -> None:
        if not hasattr(penv, "event_epoch"):
            penv.event_epoch = 0
        if not hasattr(penv, "map_epoch"):
            penv.map_epoch = 0
        if not hasattr(penv, "machine_epoch"):
            penv.machine_epoch = 0
        if not hasattr(penv, "agv_epoch"):
            penv.agv_epoch = 0
        if not hasattr(penv, "job_epoch"):
            penv.job_epoch = 0
        if not hasattr(penv, "last_events"):
            penv.last_events = []
        if not hasattr(penv, "event_metrics"):
            penv.event_metrics = {}
        if not hasattr(penv, "agv_status") or len(penv.agv_status) != penv.grid_config.num_agents:
            penv.agv_status = ["OK"] * penv.grid_config.num_agents
        if not hasattr(penv, "agv_repair_remaining") or len(penv.agv_repair_remaining) != penv.grid_config.num_agents:
            penv.agv_repair_remaining = [0] * penv.grid_config.num_agents
        if not hasattr(penv, "agv_down_reason") or len(penv.agv_down_reason) != penv.grid_config.num_agents:
            penv.agv_down_reason = [None] * penv.grid_config.num_agents

        for m in getattr(penv, "machines", []) or []:
            if not hasattr(m, "status"):
                m.status = "OK"
            if not hasattr(m, "repair_remaining"):
                m.repair_remaining = 0
            if not hasattr(m, "down_reason"):
                m.down_reason = None

    def _publish_state(self, penv) -> None:
        penv.event_metrics = self.get_metrics()

    def _record(self, penv, step: int, etype: str, payload: Dict[str, Any], level: str = "info") -> None:
        payload = {key: value for key, value in payload.items() if key not in {"duration_steps", "repair_remaining"}}
        penv.emit_event(etype, payload, step=step)
        event = {
            "step": int(step),
            "type": etype,
            "level": level,
            "payload": dict(payload),
        }
        self._step_events.append(event)
        self._all_events.append(event)
        self._write_event_log(event)

    def _load_schedule(self) -> List[Dict[str, Any]]:
        schedule: List[Dict[str, Any]] = list(self.config.get("schedule") or [])
        replay_file = self.config.get("replay_file")
        if replay_file:
            schedule.extend(self._read_replay_file(str(replay_file)))
        unsupported = sorted({
            str(item.get("type")) for item in schedule
            if item.get("type") not in self.SUPPORTED_TYPES
        })
        if unsupported:
            raise ValueError(f"Unsupported Exception types: {', '.join(unsupported)}")
        return sorted(schedule, key=lambda item: int(item.get("step", 0)))

    def _read_replay_file(self, replay_file: str) -> List[Dict[str, Any]]:
        with open(replay_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return []
        if content[0] == "[":
            data = json.loads(content)
            if not isinstance(data, list):
                raise ValueError(f"Replay file must contain a JSON array or JSONL events: {replay_file}")
            return data
        events: List[Dict[str, Any]] = []
        for line_no, line in enumerate(content.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError(f"Replay JSONL line {line_no} is not an event object: {replay_file}")
            events.append(event)
        return events

    def _prepare_event_log(self) -> None:
        self._exception_log_path = self.config.get("exception_log_path")
        if not self._exception_log_path:
            return
        log_dir = os.path.dirname(os.path.abspath(str(self._exception_log_path)))
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with open(self._exception_log_path, "w", encoding="utf-8"):
            pass

    def _write_event_log(self, event: Dict[str, Any]) -> None:
        if not self._exception_log_path:
            return
        with open(self._exception_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def _prob_from_config(self, cfg: Dict[str, Any], prob_key: str, mean_key: str) -> float:
        prob = float(cfg.get(prob_key, 0.0) or 0.0)
        if prob > 0:
            return self._clamp_probability(prob)
        mean_steps = float(cfg.get(mean_key, 0.0) or 0.0)
        if mean_steps > 0:
            return self._clamp_probability(1.0 - math.exp(-1.0 / mean_steps))
        return 0.0

    def _sample_arrival_count(self, cfg: Dict[str, Any], prob_key: str, max_key: str) -> int:
        if not cfg.get("enabled"):
            return 0

        max_count = int(cfg.get(max_key, 1) or 1)
        if max_count <= 0:
            return 0

        lambda_per_step = float(cfg.get("lambda_per_step", 0.0) or 0.0)
        mean_steps = float(cfg.get("mean_interarrival_steps", 0.0) or 0.0)
        if lambda_per_step <= 0 and mean_steps > 0:
            lambda_per_step = 1.0 / mean_steps

        if lambda_per_step > 0:
            return min(max_count, self._sample_poisson(lambda_per_step))

        prob = self._clamp_probability(float(cfg.get(prob_key, 0.0) or 0.0))
        if max_count == 1:
            return 1 if self.rng.random() < prob else 0
        return sum(1 for _ in range(max_count) if self.rng.random() < prob)

    def _sample_poisson(self, lambda_per_step: float) -> int:
        if lambda_per_step <= 0:
            return 0
        threshold = math.exp(-lambda_per_step)
        count = 0
        product = 1.0
        while product > threshold:
            count += 1
            product *= self.rng.random()
        return max(0, count - 1)

    @staticmethod
    def _clamp_probability(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _apply_scheduled_events(self, penv, step: int) -> None:
        while self._schedule_idx < len(self._schedule):
            event = self._schedule[self._schedule_idx]
            event_step = int(event.get("step", 0))
            if event_step > step:
                break
            self._schedule_idx += 1
            if event_step < step:
                continue
            self._apply_event(penv, step, event)

    def _apply_probabilistic_events(self, penv, step: int) -> None:
        cfg = self.config.get("machine_failure", {})
        if cfg.get("enabled"):
            prob = self._prob_from_config(cfg, "prob_per_machine_step", "mtbf_steps")
            busy_only = bool(cfg.get("busy_only", False))
            for m in penv.machines:
                if m.status != "OK":
                    continue
                if busy_only and m.current_op is None:
                    continue
                if self.rng.random() < prob:
                    self._machine_breakdown(
                        penv, step, m.id,
                        self._sample_duration(cfg.get("repair_time")),
                        reason="probabilistic_machine_failure",
                    )

        cfg = self.config.get("agv_failure", {})
        if cfg.get("enabled"):
            prob = self._prob_from_config(cfg, "prob_per_agv_step", "mtbf_steps")
            busy_only = bool(cfg.get("busy_only", False))
            for idx in range(penv.grid_config.num_agents):
                if penv.agv_status[idx] != "OK":
                    continue
                if busy_only and penv.agv_current_task[idx] is None:
                    continue
                if self.rng.random() < prob:
                    self._agv_breakdown(
                        penv, step, idx,
                        self._sample_duration(cfg.get("repair_time")),
                        reason="probabilistic_agv_failure",
                    )

        cfg = self.config.get("temporary_obstacle", {})
        for _ in range(self._sample_arrival_count(cfg, "prob_per_step", "max_new_per_step")):
            cell = self._choose_obstacle_cell(penv)
            if cell is not None:
                self._temporary_obstacle(
                    penv, step, cell,
                    self._sample_duration(cfg.get("duration")),
                    reason="probabilistic_temporary_obstacle",
                )

    def _apply_event(self, penv, step: int, event: Dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "machine_breakdown":
            duration = int(event.get("duration_steps", event.get("duration", 1)))
            self._machine_breakdown(penv, step, int(event["machine_id"]), duration, event.get("reason"))
        elif etype == "agv_breakdown":
            duration = int(event.get("duration_steps", event.get("duration", 1)))
            self._agv_breakdown(penv, step, int(event["agv_id"]), duration, event.get("reason"))
        elif etype == "temporary_obstacle":
            duration = int(event.get("duration_steps", event.get("duration", 1)))
            cell = tuple(event["cell"])
            self._temporary_obstacle(penv, step, cell, duration, event.get("reason"))

    def _machine_breakdown(self, penv, step: int, machine_id: int, duration: int, reason: Optional[str] = None) -> None:
        if machine_id < 0 or machine_id >= len(penv.machines):
            return
        machine = penv.machines[machine_id]
        if machine.status != "OK":
            return
        machine.status = "DOWN"
        machine.down_elapsed = 0
        machine.repair_remaining = max(1, int(duration))
        machine.down_reason = reason or "machine_breakdown"
        penv.machine_epoch += 1
        self._metrics["machine_failure_count"] += 1
        current_op = machine.current_op
        self._record(
            penv, step, "machine_breakdown",
            {
                "machine_id": machine_id,
                "duration_steps": machine.repair_remaining,
                "reason": machine.down_reason,
                "current_op": (
                    {
                        "job_id": current_op.job_id,
                        "op_id": current_op.op_id,
                    }
                    if current_op is not None else None
                ),
            },
            level="warning",
        )

    def _agv_breakdown(self, penv, step: int, agv_id: int, duration: int, reason: Optional[str] = None) -> None:
        if agv_id < 0 or agv_id >= penv.grid_config.num_agents:
            return
        if penv.agv_status[agv_id] != "OK":
            return
        penv.agv_status[agv_id] = "DOWN"
        penv.agv_down_elapsed[agv_id] = 0
        penv.agv_repair_remaining[agv_id] = max(1, int(duration))
        penv.agv_down_reason[agv_id] = reason or "agv_breakdown"
        penv.agv_epoch += 1
        self._metrics["agv_failure_count"] += 1
        current_task = penv.agv_current_task[agv_id]
        self._record(
            penv, step, "agv_breakdown",
            {
                "agv_id": agv_id,
                "duration_steps": penv.agv_repair_remaining[agv_id],
                "reason": penv.agv_down_reason[agv_id],
                "position": list(penv.grid.positions_xy[agv_id]),
                "current_task_id": (
                    current_task.task_id if current_task is not None else None
                ),
            },
            level="warning",
        )

    def _temporary_obstacle(self, penv, step: int, cell: Tuple[int, int], duration: int, reason: Optional[str] = None) -> None:
        cell = (int(cell[0]), int(cell[1]))
        grid = penv.grid.obstacles
        if not (0 <= cell[0] < grid.shape[0] and 0 <= cell[1] < grid.shape[1]):
            return
        if not self._is_playable_map_cell(penv, cell):
            return
        if cell in self._active_obstacles:
            return
        if cell in {tuple(pos) for pos in penv.grid.positions_xy}:
            return
        if cell in {penv._to_internal_xy(m.location) for m in penv.machines}:
            return
        original = int(grid[cell])
        if original != 0:
            return
        if self.config.get("temporary_obstacle", {}).get("preserve_connectivity", True):
            if not self._preserves_connectivity(grid, cell):
                return
        grid[cell] = 1
        self._active_obstacles[cell] = {
            "remaining": max(1, int(duration)),
            "original": original,
            "reason": reason or "temporary_obstacle",
        }
        penv.map_epoch += 1
        self._metrics["temporary_obstacle_count"] += 1
        self._record(
            penv, step, "temporary_obstacle",
            {"cell": list(cell), "duration_steps": self._active_obstacles[cell]["remaining"]},
            level="warning",
        )

    def _tick_repairs(self, penv, step: int) -> None:
        for machine in penv.machines:
            if machine.status == "DOWN":
                machine.down_elapsed += 1
                self._metrics["machine_down_steps_total"] += 1
                machine.repair_remaining = max(0, int(machine.repair_remaining) - 1)
                if machine.repair_remaining == 0:
                    machine.status = "OK"
                    machine.down_reason = None
                    penv.machine_epoch += 1
                    self._metrics["machine_recovery_count"] += 1
                    self._record(
                        penv, step, "machine_recovery",
                        {"machine_id": machine.id},
                        level="info",
                    )

        for idx, status in enumerate(penv.agv_status):
            if status == "DOWN":
                penv.agv_down_elapsed[idx] += 1
                self._metrics["agv_down_steps_total"] += 1
                penv.agv_repair_remaining[idx] = max(0, int(penv.agv_repair_remaining[idx]) - 1)
                if penv.agv_repair_remaining[idx] == 0:
                    penv.agv_status[idx] = "OK"
                    penv.agv_down_reason[idx] = None
                    penv.agv_epoch += 1
                    self._metrics["agv_recovery_count"] += 1
                    self._record(
                        penv, step, "agv_recovery",
                        {"agv_id": idx},
                        level="info",
                    )

    def _tick_obstacles(self, penv, step: int) -> None:
        for cell in list(self._active_obstacles):
            state = self._active_obstacles[cell]
            state["remaining"] = max(0, int(state["remaining"]) - 1)
            if state["remaining"] > 0:
                continue
            penv.grid.obstacles[cell] = int(state.get("original", 0))
            del self._active_obstacles[cell]
            penv.map_epoch += 1
            self._metrics["obstacle_clear_count"] += 1
            self._record(
                penv, step, "obstacle_clear",
                {"cell": list(cell)},
                level="info",
            )

    def _choose_obstacle_cell(self, penv) -> Optional[Tuple[int, int]]:
        grid = penv.grid.obstacles
        occupied = {tuple(pos) for pos in penv.grid.positions_xy}
        machine_cells: set[Tuple[int, int]] = {penv._to_internal_xy(m.location) for m in penv.machines}
        preserve_connectivity = self.config.get("temporary_obstacle", {}).get("preserve_connectivity", True)
        candidates: List[Tuple[int, int]] = []
        for i in range(grid.shape[0]):
            for j in range(grid.shape[1]):
                cell = (i, j)
                if not self._is_playable_map_cell(penv, cell):
                    continue
                if grid[cell] != 0:
                    continue
                if cell in occupied or cell in machine_cells:
                    continue
                if preserve_connectivity and not self._preserves_connectivity(grid, cell):
                    continue
                candidates.append(cell)
        if not candidates:
            return None
        return self.rng.choice(candidates)

    def _is_playable_map_cell(self, penv, cell: Tuple[int, int]) -> bool:
        """Reject Pogema obs_radius padding cells that map outside the frontend grid."""
        grid = penv.grid.obstacles
        obs_radius = int(getattr(penv.grid_config, "obs_radius", 0) or 0)
        if obs_radius <= 0:
            return 0 <= cell[0] < grid.shape[0] and 0 <= cell[1] < grid.shape[1]
        return (
            obs_radius <= cell[0] < grid.shape[0] - obs_radius
            and obs_radius <= cell[1] < grid.shape[1] - obs_radius
        )

    def _preserves_connectivity(self, grid, blocked_cell: Tuple[int, int]) -> bool:
        return (
            self._walkable_component_count(grid)
            >= self._walkable_component_count(grid, blocked_cell=blocked_cell)
        )

    def _walkable_component_count(self, grid, blocked_cell: Optional[Tuple[int, int]] = None) -> int:
        visited = set()
        components = 0
        height, width = grid.shape

        def is_walkable(cell: Tuple[int, int]) -> bool:
            if blocked_cell is not None and cell == blocked_cell:
                return False
            return int(grid[cell]) == 0

        for i in range(height):
            for j in range(width):
                start = (i, j)
                if start in visited or not is_walkable(start):
                    continue
                components += 1
                queue = deque([start])
                visited.add(start)
                while queue:
                    cur_i, cur_j = queue.popleft()
                    for nxt in (
                        (cur_i - 1, cur_j),
                        (cur_i + 1, cur_j),
                        (cur_i, cur_j - 1),
                        (cur_i, cur_j + 1),
                    ):
                        ni, nj = nxt
                        if not (0 <= ni < height and 0 <= nj < width):
                            continue
                        if nxt in visited or not is_walkable(nxt):
                            continue
                        visited.add(nxt)
                        queue.append(nxt)
        return components

    def _sample_duration(self, spec) -> int:
        if spec is None:
            return 1
        if isinstance(spec, int):
            return max(1, spec)
        dist = str(spec.get("dist", "fixed")).lower()
        if dist == "fixed":
            return max(1, int(spec.get("value", 1)))
        if dist in {"uniform", "discrete_uniform"}:
            return max(1, self.rng.randint(int(spec.get("low", 1)), int(spec.get("high", 1))))
        if dist in {"triangular", "discrete_triangular"}:
            low = int(spec.get("low", 1))
            high = int(spec.get("high", low))
            mode = int(spec.get("mode", low))
            return max(1, int(round(self.rng.triangular(low, high, mode))))
        return max(1, int(spec.get("value", 1)))


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}
