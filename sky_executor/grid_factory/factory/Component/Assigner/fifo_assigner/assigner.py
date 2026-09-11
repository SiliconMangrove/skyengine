"""
FIFO 先到先服务分配器

按任务创建时间 (create_time) 排序，最早的任务优先分配
分配时为每个任务选距离最近的空闲 AGV

这是调度理论中最简单也最常用的公平策略
"""

from typing import Dict, Any, List
import numpy as np
from sky_executor.grid_factory.factory.Component.Assigner.assigner_factory import AssignerFactory
from sky_executor.grid_factory.factory.Component.Assigner.template_assigner.assigner import Assigner
from sky_executor.grid_factory.factory.Utils.feature_extractor import FeatureExtractor


@AssignerFactory.register_solver("fifo")
class FIFOAssigner(Assigner):
    """
    FIFO 任务分配器
    任务按 create_time 排序，每个任务分配给最近的空闲 AGV
    """

    def __init__(self):
        self._extractor = FeatureExtractor()

    def plan(self, obs: Dict[str, Any]):
        agents: List = obs.get("agents", [])

        features = self._extractor.extract(obs)
        idle_agents = features["idle_agents"]
        transfers = features["transfers"]
        raw = features["raw_features"]  # [n_idle, n_task, 8]

        if not idle_agents or not transfers:
            assignments = {agent.id: None for agent in agents}
            return {"assignments": assignments, "pending_transfers": transfers}

        # 按 create_time 排序 (最早的优先)
        sorted_indices = sorted(
            range(len(transfers)),
            key=lambda i: transfers[i].create_time if transfers[i].create_time >= 0 else transfers[i].ready_time
        )

        idle_available = list(range(len(idle_agents)))
        assignments = {a.id: None for a in agents}
        assigned_task_indices = set()

        for task_j in sorted_indices:
            if not idle_available:
                break
            # 从 raw_features 取 distance，找最近 AGV
            best_i = min(
                idle_available,
                key=lambda i: raw[i, task_j, 0] if np.isfinite(raw[i, task_j, 0]) else float('inf')
            )
            idle_available.remove(best_i)
            assignments[idle_agents[best_i].id] = transfers[task_j]
            assigned_task_indices.add(task_j)

        remaining = [transfers[j] for j in range(len(transfers)) if j not in assigned_task_indices]
        return {"assignments": assignments, "pending_transfers": remaining}
