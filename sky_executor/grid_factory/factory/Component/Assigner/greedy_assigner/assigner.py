"""
贪心复合任务分配器
综合考虑距离 + 任务等待时间，为每个空闲 AGV 选择最优任务

评分公式: score = distance_to_dest - α * wait_time
优先选择距离近且等待久的任务
"""

from typing import Dict, Any, List
from sky_executor.grid_factory.factory.Component.Assigner.assigner_factory import AssignerFactory
from sky_executor.grid_factory.factory.Component.Assigner.template_assigner.assigner import Assigner
from sky_executor.grid_factory.factory.Utils.feature_extractor import FeatureExtractor


@AssignerFactory.register_solver("greedy")
class GreedyAssigner(Assigner):
    """
    贪心复合分配器
    综合距离和等待时间的加权和，选择最优 task-AGV 对
    """

    def __init__(self, wait_weight: float = 0.5):
        self._extractor = FeatureExtractor()
        self.weights = {
            "distance": 1.0,
            "wait_time": -wait_weight,
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
