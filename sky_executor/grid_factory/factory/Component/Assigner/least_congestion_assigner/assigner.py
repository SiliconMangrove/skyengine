"""
最少拥堵任务分配器
综合考虑距离和目标区域附近的 AGV 密度，避免把多个 AGV 派往同一区域
"""

from typing import Dict, Any, List
from sky_executor.grid_factory.factory.Component.Assigner.assigner_factory import AssignerFactory
from sky_executor.grid_factory.factory.Component.Assigner.template_assigner.assigner import Assigner
from sky_executor.grid_factory.factory.Utils.feature_extractor import FeatureExtractor


@AssignerFactory.register_solver("least_congestion")
class LeastCongestionAssigner(Assigner):
    """
    拥堵感知任务分配器
    score = AGV→dest距离 + congestion_weight × dest附近AGV数
    """

    def __init__(self, congestion_weight: float = 3.0):
        self._extractor = FeatureExtractor()
        self.weights = {
            "distance": 1.0,
            "congestion": congestion_weight,
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
