"""Processing-time uncertainty for GridFactoryEnv.

This is not an Exception mechanism. It samples the actual operation duration
when a machine starts processing an operation, while keeping the nominal time
available for scheduling and display.
"""

from __future__ import annotations

import math
import random
from copy import deepcopy
from typing import Any, Optional


class ProcessingTimeSampler:
    PRESETS = {
        "none": {"enabled": False},
        "no_variance": {"enabled": False},
        "mild_variance": {
            "enabled": True,
            "default_distribution": {"dist": "multiplier_uniform", "low": 0.9, "high": 1.1},
        },
        "moderate_variance": {
            "enabled": True,
            "default_distribution": {"dist": "multiplier_uniform", "low": 0.8, "high": 1.25},
        },
        "high_variance": {
            "enabled": True,
            "default_distribution": {"dist": "multiplier_uniform", "low": 0.6, "high": 1.6},
        },
    }

    DEFAULT_CONFIG = {
        "enabled": False,
        "preset": "none",
        "random_seed": 42,
        "sample_on": "operation_start",
        "rounding": "round",
        "min_value": 1,
    }

    def __init__(self, config: Optional[dict] = None):
        self.raw_config = deepcopy(config) if isinstance(config, dict) else None
        self.config = self._normalize_config(config)
        self.enabled = bool(self.config.get("enabled", False))
        self._seed = int(self.config.get("random_seed", 42))
        self.rng = random.Random(self._seed)

    @classmethod
    def from_config(cls, config: Optional[dict]) -> "ProcessingTimeSampler":
        return cls(config)

    def reset(self):
        self.rng = random.Random(self._seed)

    def sample_for_operation(self, operation: Any, machine_id: int, nominal_time: float) -> tuple[float, Optional[dict]]:
        nominal = float(nominal_time or 0)
        if not self.enabled:
            return nominal, None

        dist = self._resolve_distribution(operation, machine_id)
        if not dist:
            return nominal, None

        sampled = self._sample_distribution(dist, nominal)
        sampled = self._apply_bounds_and_rounding(sampled, nominal)
        return sampled, deepcopy(dist)

    def _normalize_config(self, config: Optional[dict]) -> dict:
        if not isinstance(config, dict):
            return deepcopy(self.DEFAULT_CONFIG)
        preset_name = config.get("preset", "none")
        preset = deepcopy(self.PRESETS.get(preset_name, {}))
        merged = deepcopy(self.DEFAULT_CONFIG)
        merged.update(preset)
        merged.update(deepcopy(config))
        if preset_name not in ("none", "no_variance") and "enabled" not in config:
            merged["enabled"] = True
        return merged

    def _resolve_distribution(self, operation: Any, machine_id: int) -> Optional[dict]:
        op_cfg = getattr(operation, "processing_time_config", None)
        for cfg in (op_cfg, self.config):
            dist = self._distribution_from_config(cfg, operation, machine_id)
            if dist:
                return dist
        return None

    def _distribution_from_config(self, cfg: Any, operation: Any, machine_id: int) -> Optional[dict]:
        if not isinstance(cfg, dict):
            return None

        machine_dist = self._lookup_machine_distribution(
            cfg.get("machine_distributions")
            or cfg.get("distributions_by_machine")
            or cfg.get("processing_time_distributions"),
            machine_id,
        )
        if machine_dist:
            return machine_dist

        op_dist = self._lookup_operation_distribution(
            cfg.get("operation_distributions")
            or cfg.get("operations")
            or cfg.get("processing_time_distributions"),
            operation,
            machine_id,
        )
        if op_dist:
            return op_dist

        for key in ("distribution", "processing_time_distribution", "default_distribution"):
            value = cfg.get(key)
            if self._looks_like_distribution(value):
                return value
        if self._looks_like_distribution(cfg):
            return cfg
        return None

    def _lookup_machine_distribution(self, mapping: Any, machine_id: int) -> Optional[dict]:
        if not isinstance(mapping, dict):
            return None
        value = mapping.get(str(machine_id), mapping.get(machine_id))
        return value if self._looks_like_distribution(value) else None

    def _lookup_operation_distribution(self, mapping: Any, operation: Any, machine_id: int) -> Optional[dict]:
        if not isinstance(mapping, dict):
            return None
        job_id = getattr(operation, "job_id", None)
        op_id = getattr(operation, "op_id", None)
        keys = [
            f"{job_id}:{op_id}",
            f"{job_id}.{op_id}",
            f"{job_id}-{op_id}",
            str(op_id),
            op_id,
        ]
        for key in keys:
            value = mapping.get(key)
            if self._looks_like_distribution(value):
                return value
            if isinstance(value, dict):
                machine_value = self._lookup_machine_distribution(
                    value.get("machine_distributions")
                    or value.get("distributions_by_machine")
                    or value.get("processing_time_distributions"),
                    machine_id,
                )
                if machine_value:
                    return machine_value
                if self._looks_like_distribution(value.get("distribution")):
                    return value.get("distribution")
        return None

    def _looks_like_distribution(self, value: Any) -> bool:
        return isinstance(value, dict) and isinstance(value.get("dist"), str)

    def _sample_distribution(self, dist: dict, nominal: float) -> float:
        kind = str(dist.get("dist", "")).lower()
        if kind == "fixed":
            return float(dist.get("value", nominal))
        if kind == "uniform":
            return self.rng.uniform(float(dist.get("low", nominal)), float(dist.get("high", nominal)))
        if kind == "discrete_uniform":
            return float(self.rng.randint(int(dist.get("low", nominal)), int(dist.get("high", nominal))))
        if kind == "normal":
            return self.rng.gauss(float(dist.get("mean", nominal)), float(dist.get("std", 0)))
        if kind == "triangular":
            return self.rng.triangular(
                float(dist.get("low", nominal)),
                float(dist.get("high", nominal)),
                float(dist.get("mode", nominal)),
            )
        if kind == "multiplier_uniform":
            return nominal * self.rng.uniform(float(dist.get("low", 1.0)), float(dist.get("high", 1.0)))
        if kind == "multiplier_normal":
            return nominal * self.rng.gauss(float(dist.get("mean", 1.0)), float(dist.get("std", 0.0)))
        if kind == "multiplier_triangular":
            return nominal * self.rng.triangular(
                float(dist.get("low", 1.0)),
                float(dist.get("high", 1.0)),
                float(dist.get("mode", 1.0)),
            )
        return nominal

    def _apply_bounds_and_rounding(self, value: float, nominal: float) -> float:
        min_value = float(self.config.get("min_value", 1))
        max_value = self.config.get("max_value")
        value = max(min_value, float(value))
        if max_value is not None:
            value = min(float(max_value), value)

        rounding = str(self.config.get("rounding", "round")).lower()
        if rounding == "ceil":
            value = math.ceil(value)
        elif rounding == "floor":
            value = math.floor(value)
        elif rounding in ("none", "float"):
            return float(value)
        else:
            value = round(value)
        return float(max(min_value, value))
