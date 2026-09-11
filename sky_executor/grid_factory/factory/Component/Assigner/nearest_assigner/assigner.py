"""
最近距离任务分配器
为每个空闲 AGV 分配距离最近的任务 (基于 AGV → task.destination 曼哈顿距离)
"""

from typing import Dict, Any, List
from sky_executor.grid_factory.factory.Component.Assigner.assigner_factory import AssignerFactory
from sky_executor.grid_factory.factory.Component.Assigner.template_assigner.assigner import Assigner
from sky_executor.grid_factory.factory.Utils.feature_extractor import FeatureExtractor


@AssignerFactory.register_solver("nearest")
class NearestAssigner(Assigner):
    """
    全局最近距离分配器。
    使用距离代价矩阵匹配空闲 AGV 与任务，任务首目标由取料需求决定。
    """

    def __init__(self):
        self._extractor = FeatureExtractor()
        self.weights = {"distance": 1.0}

    def plan(self, obs: Dict[str, Any]):
        agents: List = obs.get("agents", [])

        # 先按优先级分层，距离只在最高优先级任务内部参与选择。
        pending = list(obs.get("pending_transfers", []))
        if pending:
            highest = max(getattr(task, "priority", 0) for task in pending)
            obs = dict(obs)
            obs["pending_transfers"] = [
                task for task in pending if getattr(task, "priority", 0) == highest
            ]
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
        selected_ids = {id(task) for task in transfers}
        remaining.extend(task for task in pending if id(task) not in selected_ids)
        return {"assignments": assignments, "pending_transfers": remaining}
