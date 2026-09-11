"""Counterfactual coupling-value estimation for RACE-FMS.

The estimator predicts the marginal value of coordinated recovery:

    local recovery cost - coordinated recovery cost - coordination overhead

A positive prediction means that cross-layer coordination is expected to pay
for its extra rescheduling cost.  It intentionally uses a small ridge model as
the executable baseline; a learned neural head can implement the same API.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np


COUPLING_FEATURE_NAMES = (
    "transport_processing_ratio",
    "agv_load",
    "starvation_risk",
    "due_pressure",
    "queue_pressure",
    "congestion_pressure",
    "disturbance_pressure",
    "processing_uncertainty",
    "affected_fraction",
)


@dataclass(frozen=True)
class CounterfactualSample:
    features: Sequence[float]
    local_cost: float
    coordinated_cost: float
    coordination_cost: float = 0.0
    weight: float = 1.0

    @property
    def benefit(self) -> float:
        return float(self.local_cost - self.coordinated_cost - self.coordination_cost)


class CounterfactualCouplingEstimator:
    """Ridge-regression baseline with predictive uncertainty.

    The model is deliberately serializable as plain JSON data and does not
    require a deep-learning runtime.  Feature normalization is learned only
    from the supplied synthetic training split, never from benchmark cases.
    """

    def __init__(self, ridge: float = 1e-3):
        if ridge < 0:
            raise ValueError("ridge must be non-negative")
        self.ridge = float(ridge)
        self.mean = np.zeros(len(COUPLING_FEATURE_NAMES), dtype=np.float64)
        self.scale = np.ones(len(COUPLING_FEATURE_NAMES), dtype=np.float64)
        self.coef = np.zeros(len(COUPLING_FEATURE_NAMES) + 1, dtype=np.float64)
        self.residual_std = 1.0
        self.fitted = False

    @property
    def feature_dim(self) -> int:
        return len(COUPLING_FEATURE_NAMES)

    def _matrix(self, values: Sequence[Sequence[float]]) -> np.ndarray:
        matrix = np.asarray(values, dtype=np.float64)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        if matrix.shape[1] != self.feature_dim:
            raise ValueError(
                f"expected {self.feature_dim} coupling features, got {matrix.shape[1]}"
            )
        if not np.isfinite(matrix).all():
            raise ValueError("coupling features must be finite")
        return matrix

    def fit(self, samples: Iterable[CounterfactualSample]) -> "CounterfactualCouplingEstimator":
        rows = list(samples)
        if len(rows) < 2:
            raise ValueError("at least two counterfactual samples are required")

        x = self._matrix([row.features for row in rows])
        y = np.asarray([row.benefit for row in rows], dtype=np.float64)
        weights = np.asarray([max(0.0, float(row.weight)) for row in rows], dtype=np.float64)
        if not np.any(weights > 0):
            raise ValueError("at least one sample must have positive weight")

        self.mean = np.average(x, axis=0, weights=weights)
        variance = np.average((x - self.mean) ** 2, axis=0, weights=weights)
        self.scale = np.sqrt(np.maximum(variance, 1e-12))
        z = (x - self.mean) / self.scale
        design = np.column_stack([np.ones(len(z)), z])

        sqrt_w = np.sqrt(weights).reshape(-1, 1)
        weighted_design = design * sqrt_w
        weighted_y = y * sqrt_w[:, 0]
        penalty = np.eye(design.shape[1], dtype=np.float64) * self.ridge
        penalty[0, 0] = 0.0
        self.coef = np.linalg.solve(
            weighted_design.T @ weighted_design + penalty,
            weighted_design.T @ weighted_y,
        )

        residuals = y - design @ self.coef
        self.residual_std = float(
            np.sqrt(np.average(residuals ** 2, weights=np.maximum(weights, 1e-12)))
        )
        self.residual_std = max(self.residual_std, 1e-6)
        self.fitted = True
        return self

    def predict(self, features: Sequence[float]) -> tuple[float, float]:
        x = self._matrix(features)
        if not self.fitted:
            # Conservative cold start: do not claim coordination benefit.
            return 0.0, self.residual_std
        z = (x[0] - self.mean) / self.scale
        mean = float(self.coef[0] + z @ self.coef[1:])
        return mean, self.residual_std

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "feature_names": list(COUPLING_FEATURE_NAMES),
            "ridge": self.ridge,
            "mean": self.mean.tolist(),
            "scale": self.scale.tolist(),
            "coef": self.coef.tolist(),
            "residual_std": self.residual_std,
            "fitted": self.fitted,
        }

    @classmethod
    def from_dict(cls, payload: Mapping) -> "CounterfactualCouplingEstimator":
        names = tuple(payload.get("feature_names", ()))
        if names != COUPLING_FEATURE_NAMES:
            raise ValueError("coupling feature schema does not match this RACE-FMS version")
        model = cls(ridge=float(payload.get("ridge", 1e-3)))
        model.mean = np.asarray(payload["mean"], dtype=np.float64)
        model.scale = np.asarray(payload["scale"], dtype=np.float64)
        model.coef = np.asarray(payload["coef"], dtype=np.float64)
        model.residual_std = float(payload.get("residual_std", 1.0))
        model.fitted = bool(payload.get("fitted", False))
        if model.mean.shape != (model.feature_dim,) or model.coef.shape != (model.feature_dim + 1,):
            raise ValueError("invalid coupling estimator dimensions")
        return model


def sample_to_dict(sample: CounterfactualSample) -> dict:
    return asdict(sample)
