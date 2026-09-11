"""
耦合感知匈牙利分配器 (Coupling-Aware Hungarian Assigner)

核心思想：将 FJSP-MAPF 耦合层的多维信息融合到代价矩阵中，
通过匈牙利算法求全局最优匹配。

代价公式:
  cost(agv, task) = w_d × norm(distance)
                  + w_c × norm(congestion)
                  - w_s × starvation_bonus
                  - w_u × urgency_bonus
                  - w_w × wait_bonus

特征提取已统一到 FeatureExtractor，本类只负责权重配置和匈牙利求解。
"""

from typing import Dict, Any, List

from sky_executor.grid_factory.factory.Component.Assigner.assigner_factory import AssignerFactory
from sky_executor.grid_factory.factory.Component.Assigner.template_assigner.assigner import Assigner
from sky_executor.grid_factory.factory.Utils.feature_extractor import FeatureExtractor


@AssignerFactory.register_solver("coupling_hungarian")
class CouplingHungarianAssigner(Assigner):
    """
    耦合感知匈牙利分配器

    通过 FeatureExtractor 统一提取特征，构建加权代价矩阵，匈牙利算法全局最优匹配。

    参数:
        w_distance:      运输距离权重（正=代价）
        w_congestion:    拥塞惩罚权重（正=代价）
        w_starvation:    机器饥饿奖励权重（正=奖励→取负变代价）
        w_urgency:       Job紧急度奖励权重（正=奖励→取负变代价）
        w_wait:          任务等待时间奖励权重（正=奖励→取负变代价）
    """

    def __init__(
        self,
        w_distance: float = 1.0,
        w_congestion: float = 2.0,
        w_starvation: float = 3.0,
        w_urgency: float = 2.0,
        w_wait: float = 1.5,
    ):
        self.weights = {
            "distance": w_distance,
            "congestion": w_congestion,
            "starvation": -w_starvation,
            "urgency": -w_urgency,
            "wait_time": -w_wait,
        }
        self._extractor = FeatureExtractor()

    def plan(self, obs: Dict[str, Any]):
        agents: List = obs.get("agents", [])

        # 1. 统一特征提取
        features = self._extractor.extract(obs, mode="coupling")

        idle_agents = features["idle_agents"]
        transfers = features["transfers"]

        if not idle_agents or not transfers:
            assignments = {agent.id: None for agent in agents}
            return {"assignments": assignments, "pending_transfers": transfers}

        # 2. 用 FeatureExtractor 构建代价矩阵
        cost_matrix = FeatureExtractor.compute_cost_matrix(
            features["raw_features"], self.weights
        )

        # 3. 匈牙利匹配（使用基类方法）
        assignments, remaining = self.hungarian_match(
            cost_matrix, agents, idle_agents, transfers
        )

        return {"assignments": assignments, "pending_transfers": remaining}
