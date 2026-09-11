"""
匈牙利算法最优匹配分配器

使用 scipy.optimize.linear_sum_assignment 实现全局最优匹配
最小化所有 AGV→task 的总曼哈顿距离
这是单步最优匹配的上界 baseline

如果没有 scipy，自动退化为贪心匹配
"""

from typing import Dict, Any, List
from sky_executor.grid_factory.factory.Component.Assigner.assigner_factory import AssignerFactory
from sky_executor.grid_factory.factory.Component.Assigner.template_assigner.assigner import Assigner
from sky_executor.grid_factory.factory.Utils.feature_extractor import FeatureExtractor


@AssignerFactory.register_solver("hungarian")
class HungarianAssigner(Assigner):
    """
    匈牙利算法最优匹配分配器
    通过 FeatureExtractor 统一提取特征，构建代价矩阵，匈牙利算法全局最优匹配。
    """

    def __init__(self):
        self._extractor = FeatureExtractor()
        self.weights = {"distance": 1.0}

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

        assignments, remaining = self.hungarian_match(
            cost_matrix, agents, idle_agents, transfers
        )
        return {"assignments": assignments, "pending_transfers": remaining}
