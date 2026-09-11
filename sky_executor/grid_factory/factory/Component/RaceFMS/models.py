"""Optional PyTorch model definitions for the RACE-FMS research policy.

The environment runtime intentionally does not require PyTorch.  Install the
project's CUDA-compatible torch build before constructing these classes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .state import ENTITY_DIMS

try:
    import torch
    from torch import Tensor, nn
except ImportError:  # pragma: no cover - exercised by the explicit placeholder
    torch = None
    Tensor = object
    nn = None


RELATION_TYPES = {
    "job_operation": ("jobs", "operations"),
    "operation_precedence": ("operations", "operations"),
    "operation_machine": ("operations", "machines"),
    "task_job": ("tasks", "jobs"),
    "task_machine": ("tasks", "machines"),
    "agv_task": ("agvs", "tasks"),
}


@dataclass
class RaceModelOutput:
    production_pair_logits: Tensor
    logistics_pair_logits: Tensor
    logistics_pairs: Tensor
    recovery_logits: Tensor
    value_quantiles: Tensor
    coupling_benefit: Tensor
    coupling_log_std: Tensor
    production_message: Tensor
    logistics_message: Tensor


if nn is not None:

    class _MLP(nn.Module):
        def __init__(self, input_dim: int, hidden_dim: int, output_dim: int):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(input_dim, hidden_dim),
                nn.SiLU(),
                nn.LayerNorm(hidden_dim),
                nn.Linear(hidden_dim, output_dim),
            )

        def forward(self, x: Tensor) -> Tensor:
            return self.net(x)


    class RaceActorCritic(nn.Module):
        """Heterogeneous dual-actor, recovery gate, and distributional critic.

        This model consumes one variable-sized factory graph.  Candidate masks
        are represented structurally: production logits are emitted only for
        operation-machine edges, while logistics logits are emitted for all
        currently visible task-AGV pairs.
        """

        def __init__(
            self,
            hidden_dim: int = 128,
            message_dim: int = 32,
            quantiles: int = 32,
            message_passing_steps: int = 2,
        ):
            super().__init__()
            self.hidden_dim = int(hidden_dim)
            self.quantiles = int(quantiles)
            self.message_passing_steps = int(message_passing_steps)
            self.node_encoders = nn.ModuleDict({
                name: _MLP(dim, hidden_dim, hidden_dim)
                for name, dim in ENTITY_DIMS.items()
            })
            self.forward_relations = nn.ModuleDict({
                name: nn.Linear(hidden_dim, hidden_dim, bias=False)
                for name in RELATION_TYPES
            })
            self.reverse_relations = nn.ModuleDict({
                name: nn.Linear(hidden_dim, hidden_dim, bias=False)
                for name in RELATION_TYPES
            })
            self.updates = nn.ModuleDict({
                name: nn.GRUCell(hidden_dim, hidden_dim)
                for name in ENTITY_DIMS
            })

            pooled_dim = hidden_dim * len(ENTITY_DIMS) + 9
            self.global_encoder = _MLP(pooled_dim, hidden_dim, hidden_dim)
            self.production_message_head = _MLP(hidden_dim * 3, hidden_dim, message_dim)
            self.logistics_message_head = _MLP(hidden_dim * 3, hidden_dim, message_dim)
            joint_dim = hidden_dim + message_dim * 2

            self.production_pair_head = _MLP(hidden_dim * 2 + joint_dim, hidden_dim, 1)
            self.logistics_pair_head = _MLP(hidden_dim * 2 + joint_dim, hidden_dim, 1)
            self.recovery_head = _MLP(joint_dim, hidden_dim, 5)
            self.quantile_critic = _MLP(joint_dim, hidden_dim, quantiles)
            self.coupling_head = _MLP(joint_dim, hidden_dim, 2)

        @staticmethod
        def _pool(values: Tensor, hidden_dim: int) -> Tensor:
            if values.shape[0] == 0:
                return values.new_zeros(hidden_dim)
            return values.mean(dim=0)

        def _message_pass(self, nodes: Dict[str, Tensor], edges: Dict[str, Tensor]) -> Dict[str, Tensor]:
            for _ in range(self.message_passing_steps):
                messages = {name: value.new_zeros(value.shape) for name, value in nodes.items()}
                counts = {
                    name: value.new_zeros((value.shape[0], 1))
                    for name, value in nodes.items()
                }
                for relation, (source_name, target_name) in RELATION_TYPES.items():
                    edge = edges.get(relation)
                    if edge is None or edge.numel() == 0:
                        continue
                    source_idx, target_idx = edge[0].long(), edge[1].long()
                    forward = self.forward_relations[relation](nodes[source_name][source_idx])
                    reverse = self.reverse_relations[relation](nodes[target_name][target_idx])
                    messages[target_name].index_add_(0, target_idx, forward)
                    counts[target_name].index_add_(0, target_idx, torch.ones_like(target_idx, dtype=forward.dtype).unsqueeze(1))
                    messages[source_name].index_add_(0, source_idx, reverse)
                    counts[source_name].index_add_(0, source_idx, torch.ones_like(source_idx, dtype=reverse.dtype).unsqueeze(1))
                for name in nodes:
                    aggregate = messages[name] / counts[name].clamp_min(1.0)
                    if nodes[name].shape[0] > 0:
                        nodes[name] = self.updates[name](aggregate, nodes[name])
            return nodes

        def forward(
            self,
            node_features: Dict[str, Tensor],
            edges: Dict[str, Tensor],
            coupling_vector: Tensor,
            action_masks: Optional[Dict[str, Tensor]] = None,
        ) -> RaceModelOutput:
            nodes = {
                name: self.node_encoders[name](node_features[name])
                for name in ENTITY_DIMS
            }
            nodes = self._message_pass(nodes, edges)
            pooled = {
                name: self._pool(nodes[name], self.hidden_dim)
                for name in ENTITY_DIMS
            }
            global_state = self.global_encoder(torch.cat([
                *(pooled[name] for name in ENTITY_DIMS), coupling_vector.reshape(-1)
            ]))
            production_message = self.production_message_head(torch.cat([
                pooled["operations"], pooled["machines"], global_state
            ]))
            logistics_message = self.logistics_message_head(torch.cat([
                pooled["tasks"], pooled["agvs"], global_state
            ]))
            joint = torch.cat([global_state, production_message, logistics_message])

            production_edges = edges.get("operation_machine")
            if production_edges is None or production_edges.numel() == 0:
                production_logits = joint.new_zeros((0,))
            else:
                op_idx, machine_idx = production_edges[0].long(), production_edges[1].long()
                pair_joint = joint.expand(op_idx.shape[0], -1)
                production_logits = self.production_pair_head(torch.cat([
                    nodes["operations"][op_idx], nodes["machines"][machine_idx], pair_joint
                ], dim=-1)).squeeze(-1)
                if action_masks is not None and "production" in action_masks:
                    production_mask = action_masks["production"].bool().reshape(-1)
                    if production_mask.numel() != production_logits.numel():
                        raise ValueError("production action mask size mismatch")
                    production_logits = production_logits.masked_fill(~production_mask, -torch.inf)

            n_tasks, n_agvs = nodes["tasks"].shape[0], nodes["agvs"].shape[0]
            if n_tasks == 0 or n_agvs == 0:
                logistics_pairs = torch.zeros((2, 0), dtype=torch.long, device=joint.device)
                logistics_logits = joint.new_zeros((0,))
            else:
                task_idx = torch.arange(n_tasks, device=joint.device).repeat_interleave(n_agvs)
                agv_idx = torch.arange(n_agvs, device=joint.device).repeat(n_tasks)
                logistics_pairs = torch.stack([task_idx, agv_idx])
                pair_joint = joint.expand(task_idx.shape[0], -1)
                logistics_logits = self.logistics_pair_head(torch.cat([
                    nodes["tasks"][task_idx], nodes["agvs"][agv_idx], pair_joint
                ], dim=-1)).squeeze(-1)
                if action_masks is not None and "logistics" in action_masks:
                    logistics_mask = action_masks["logistics"].bool().reshape(-1)
                    if logistics_mask.numel() != logistics_logits.numel():
                        raise ValueError("logistics action mask size mismatch")
                    logistics_logits = logistics_logits.masked_fill(~logistics_mask, -torch.inf)

            coupling_stats = self.coupling_head(joint)
            return RaceModelOutput(
                production_pair_logits=production_logits,
                logistics_pair_logits=logistics_logits,
                logistics_pairs=logistics_pairs,
                recovery_logits=self.recovery_head(joint),
                value_quantiles=self.quantile_critic(joint),
                coupling_benefit=coupling_stats[0],
                coupling_log_std=coupling_stats[1].clamp(-6.0, 3.0),
                production_message=production_message,
                logistics_message=logistics_message,
            )

else:

    class RaceActorCritic:  # pragma: no cover - behavior depends on optional dependency
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "RaceActorCritic requires PyTorch. Install the CUDA-compatible "
                "torch build documented for the training machine."
            )
