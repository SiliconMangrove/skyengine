"""
负载均衡任务分配器
优先为任务完成数少的 AGV 分配任务，同时选距离最近的任务
"""

from typing import Dict, Any, List
import numpy as np
from sky_executor.grid_factory.factory.Component.Assigner.assigner_factory import AssignerFactory
from sky_executor.grid_factory.factory.Component.Assigner.template_assigner.assigner import Assigner
from sky_executor.grid_factory.factory.Utils.feature_extractor import FeatureExtractor


@AssignerFactory.register_solver("load_balance")
class LoadBalanceAssigner(Assigner):
    """
    负载均衡 + 最近距离分配器
    按已完成任务数排序 AGV (少的优先)，每个 AGV 从 raw_features 取 distance 选最近任务
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

        # 按 finished_tasks 排序 AGV (负载少的优先分配)
        indexed_idle = list(enumerate(idle_agents))
        indexed_idle.sort(
            key=lambda x: len(x[1].finished_tasks) if hasattr(x[1], 'finished_tasks') else 0
        )

        available = list(range(len(transfers)))
        assignments = {a.id: None for a in agents}

        for orig_i, agent in indexed_idle:
            if not available:
                break
            # 从 raw_features[:,:,0] 取 distance
            best_j = min(
                available,
                key=lambda j: raw[orig_i, j, 0] if np.isfinite(raw[orig_i, j, 0]) else float('inf')
            )
            available.remove(best_j)
            assignments[agent.id] = transfers[best_j]

        remaining = [transfers[j] for j in available]
        return {"assignments": assignments, "pending_transfers": remaining}
