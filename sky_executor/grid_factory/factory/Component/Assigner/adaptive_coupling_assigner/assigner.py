"""
自适应耦合分配器 (Adaptive Coupling Assigner)

核心思想：根据当前环境状态（拥塞程度、机器利用率、任务积压量）
动态调整各维度权重，实现上下文感知的任务分配。

通过 FeatureExtractor 的 env_stats 获取环境状态，
不再自行计算环境指标。
"""

from typing import Dict, Any, List

from sky_executor.grid_factory.factory.Component.Assigner.assigner_factory import AssignerFactory
from sky_executor.grid_factory.factory.Component.Assigner.template_assigner.assigner import Assigner
from sky_executor.grid_factory.factory.Utils.feature_extractor import FeatureExtractor


@AssignerFactory.register_solver("adaptive_coupling")
class AdaptiveCouplingAssigner(Assigner):
    """
    自适应耦合分配器

    在 CouplingHungarianAssigner 基础上增加动态权重调整。
    通过 FeatureExtractor.env_stats 感知环境状态，调整权重。

    env_stats 索引:
      0. avg_congestion     1. starvation_ratio   2. avg_wait
      3. pending_ratio      4. loaded_utilization  5. episode_progress
      6. recent_transport_delay
    """

    def __init__(
        self,
        base_w_distance: float = 1.0,
        base_w_congestion: float = 2.0,
        base_w_starvation: float = 3.0,
        base_w_urgency: float = 2.0,
        base_w_wait: float = 1.5,
    ):
        self.base_weights = {
            "distance": base_w_distance,
            "congestion": base_w_congestion,
            "starvation": -base_w_starvation,
            "urgency": -base_w_urgency,
            "wait_time": -base_w_wait,
        }
        self._extractor = FeatureExtractor()

    def _compute_adaptive_weights(self, env_stats) -> Dict[str, float]:
        """根据 env_stats 动态调整权重"""
        weights = dict(self.base_weights)

        avg_cong = float(env_stats[0])          # 平均拥堵
        starvation_ratio = float(env_stats[1])   # 空闲机器占比
        avg_wait = float(env_stats[2])           # 平均等待
        pending_ratio = float(env_stats[3])      # 任务/AGV 比

        # 1. 拥堵严重 → 提高拥塞回避（注意：congestion 是正代价，提高绝对值）
        if avg_cong > 0.6:
            weights["congestion"] *= 2.0
        elif avg_cong > 0.4:
            weights["congestion"] *= 1.5

        # 2. 空闲机器多 → 提高饥饿奖励（starvation 是负代价，提高绝对值）
        if starvation_ratio > 0.6:
            weights["starvation"] *= 2.0
        elif starvation_ratio > 0.3:
            weights["starvation"] *= 1.5

        # 3. 任务积压 → 提高紧急度和等待奖励
        if avg_wait > 0.5:
            weights["urgency"] *= 1.5
            weights["wait_time"] *= 2.0
        elif avg_wait > 0.25:
            weights["wait_time"] *= 1.5

        if pending_ratio > 0.75:
            weights["urgency"] *= 1.3

        return weights

    def plan(self, obs: Dict[str, Any]):
        agents: List = obs.get("agents", [])

        # 1. 统一特征提取
        features = self._extractor.extract(obs, mode="coupling")

        idle_agents = features["idle_agents"]
        transfers = features["transfers"]

        if not idle_agents or not transfers:
            assignments = {agent.id: None for agent in agents}
            return {"assignments": assignments, "pending_transfers": transfers}

        # 2. 基于 env_stats 计算动态权重
        adaptive_weights = self._compute_adaptive_weights(features["env_stats"])

        # 3. 构建代价矩阵
        cost_matrix = FeatureExtractor.compute_cost_matrix(
            features["raw_features"], adaptive_weights
        )

        # 4. 匈牙利匹配（使用基类方法）
        assignments, remaining = self.hungarian_match(
            cost_matrix, agents, idle_agents, transfers
        )

        return {"assignments": assignments, "pending_transfers": remaining}
