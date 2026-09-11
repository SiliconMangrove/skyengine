"""Loss functions for training the optional RACE-FMS actor-critic."""

from __future__ import annotations

from dataclasses import dataclass

try:
    import torch
    from torch import Tensor
    import torch.nn.functional as F
except ImportError:  # pragma: no cover
    torch = None
    Tensor = object
    F = None


@dataclass
class RaceLosses:
    total: Tensor
    policy: Tensor
    value: Tensor
    coupling: Tensor
    recovery: Tensor
    cvar: Tensor


if torch is not None:

    def quantile_huber_loss(
        predicted: Tensor,
        target: Tensor,
        *,
        kappa: float = 1.0,
    ) -> Tensor:
        """Quantile-regression Huber loss for a distributional cost critic."""
        if predicted.ndim != 1:
            raise ValueError("predicted quantiles must be a one-dimensional tensor")
        target = target.reshape(1).expand_as(predicted)
        error = target - predicted
        absolute = error.abs()
        huber = torch.where(
            absolute <= kappa,
            0.5 * error.pow(2),
            kappa * (absolute - 0.5 * kappa),
        )
        taus = (torch.arange(predicted.numel(), device=predicted.device, dtype=predicted.dtype) + 0.5) / predicted.numel()
        return (torch.abs(taus - (error.detach() < 0).to(predicted.dtype)) * huber / kappa).mean()


    def upper_cvar(quantiles: Tensor, alpha: float = 0.9) -> Tensor:
        """Worst-tail CVaR for cost quantiles (larger values are worse)."""
        if not 0.0 <= alpha < 1.0:
            raise ValueError("alpha must be in [0, 1)")
        ordered = quantiles.sort().values
        start = min(int(alpha * ordered.numel()), ordered.numel() - 1)
        return ordered[start:].mean()


    def compute_race_losses(
        *,
        production_log_prob: Tensor,
        logistics_log_prob: Tensor,
        advantage: Tensor,
        value_quantiles: Tensor,
        target_cost: Tensor,
        predicted_coupling_benefit: Tensor,
        target_coupling_benefit: Tensor,
        recovery_logits: Tensor,
        target_recovery_scope: Tensor,
        value_weight: float = 0.5,
        coupling_weight: float = 1.0,
        recovery_weight: float = 0.5,
        cvar_weight: float = 0.1,
        alpha: float = 0.9,
    ) -> RaceLosses:
        policy = -(
            production_log_prob.reshape(()) + logistics_log_prob.reshape(())
        ) * advantage.detach().reshape(())
        value = quantile_huber_loss(value_quantiles, target_cost)
        coupling = F.smooth_l1_loss(
            predicted_coupling_benefit.reshape(()),
            target_coupling_benefit.reshape(()),
        )
        recovery = F.cross_entropy(
            recovery_logits.reshape(1, -1), target_recovery_scope.long().reshape(1)
        )
        tail = upper_cvar(value_quantiles, alpha)
        total = (
            policy
            + value_weight * value
            + coupling_weight * coupling
            + recovery_weight * recovery
            + cvar_weight * tail
        )
        return RaceLosses(total, policy, value, coupling, recovery, tail)

else:

    def _missing(*args, **kwargs):  # pragma: no cover
        raise ImportError("RACE-FMS training losses require PyTorch")

    quantile_huber_loss = _missing
    upper_cvar = _missing
    compute_race_losses = _missing
