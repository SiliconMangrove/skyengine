"""
紧急度优先任务分配器 (MWKR-style)

策略：优先分配"剩余工作最多"的 Job 的运输任务
- 统计每个 Job 在待分配列表中的任务数作为剩余工作量的估计
- 同等紧急度时，选距离最近的

调度理论中的 MWKR (Most Work Remaining) 原则应用于运输层：
优先完成剩余工序多的 Job，减少在制品 (WIP) 积压
"""

from typing import Dict, Any, List
from sky_executor.grid_factory.factory.Component.Assigner.assigner_factory import AssignerFactory
from sky_executor.grid_factory.factory.Component.Assigner.template_assigner.assigner import Assigner
from sky_executor.grid_factory.factory.Utils.feature_extractor import FeatureExtractor


@AssignerFactory.register_solver("urgency")
class UrgencyAssigner(Assigner):
    """
    MWKR 紧急度分配器
    优先为剩余工作多的 Job 运输，距离作为 tiebreaker
    """

    def __init__(self, distance_weight: float = 0.3):
        self._extractor = FeatureExtractor()
        self.weights = {
            "distance": distance_weight,
            "urgency": -1.0,
        }

    def plan(self, obs: Dict[str, Any]):
        agents: List = obs.get("agents", [])

        features = self._extractor.extract(obs)
        idle_agents = features["idle_agents"]
        transfers = features["transfers"]

        if not idle_agents or not transfers:
            assignments = {agent.id: None for agent in agents}
            return {"assignments": assignments, "pending_transfers": transfers}

        cost_matrix = FeatureExtractor.compute_cost_matrix(
            features["raw_features"], self.weights
        )

        assignments, remaining = self.greedy_assign(
            cost_matrix, agents, idle_agents, transfers
        )
        return {"assignments": assignments, "pending_transfers": remaining}
