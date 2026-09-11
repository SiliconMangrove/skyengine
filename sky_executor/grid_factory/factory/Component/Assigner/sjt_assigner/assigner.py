"""
SJT (Shortest Job Transport) 最短运输优先分配器

策略：优先完成运输距离最短的任务，减少 AGV 在途时间
- 计算每个任务的预估运输距离: source → destination (曼哈顿)
- 运输距离短的任务优先分配给最近的 AGV

类比调度理论中的 SPT (Shortest Processing Time)：
SPT 最小化平均完成时间，SJT 最小化平均运输完成时间
"""

from typing import Dict, Any, List
import numpy as np
from sky_executor.grid_factory.factory.Component.Assigner.assigner_factory import AssignerFactory
from sky_executor.grid_factory.factory.Component.Assigner.template_assigner.assigner import Assigner
from sky_executor.grid_factory.factory.Utils.feature_extractor import FeatureExtractor


@AssignerFactory.register_solver("sjt")
class SJTAssigner(Assigner):
    """
    SJT 最短运输优先分配器
    任务按运输距离排序 (短距离优先)，每个任务分配给最近的空闲 AGV
    """

    def __init__(self):
        self._extractor = FeatureExtractor()

    @staticmethod
    def _transport_distance(task) -> float:
        """计算 source → destination 运输距离（SJT 独有逻辑，不在 pairwise 特征中）"""
        src, dst = task.source, task.destination
        if src and len(src) == 2 and src[0] < 0:
            return 0  # depot 任务优先
        if src is None or dst is None:
            return float('inf')
        return abs(src[0] - dst[0]) + abs(src[1] - dst[1])

    def plan(self, obs: Dict[str, Any]):
        agents: List = obs.get("agents", [])

        features = self._extractor.extract(obs)
        idle_agents = features["idle_agents"]
        transfers = features["transfers"]
        raw = features["raw_features"]  # [n_idle, n_task, 8]

        if not idle_agents or not transfers:
            assignments = {agent.id: None for agent in agents}
            return {"assignments": assignments, "pending_transfers": transfers}

        # 按 src→dst 运输距离排序 (短距离优先)
        sorted_indices = sorted(
            range(len(transfers)),
            key=lambda i: self._transport_distance(transfers[i])
        )

        idle_available = list(range(len(idle_agents)))
        assignments = {a.id: None for a in agents}
        assigned_task_indices = set()

        for task_j in sorted_indices:
            if not idle_available:
                break
            # 从 raw_features 取 AGV→dest distance，找最近 AGV
            best_i = min(
                idle_available,
                key=lambda i: raw[i, task_j, 0] if np.isfinite(raw[i, task_j, 0]) else float('inf')
            )
            idle_available.remove(best_i)
            assignments[idle_agents[best_i].id] = transfers[task_j]
            assigned_task_indices.add(task_j)

        remaining = [transfers[j] for j in range(len(transfers)) if j not in assigned_task_indices]
        return {"assignments": assignments, "pending_transfers": remaining}
