"""
神经网络任务分配器
为每个空闲 AGV 分配任务，保证NN格式的输入输出。
此处进行特征提取,并调用NN进行预测,根据NN的输出结果转译为系统可读的分配结果。
"""

import os
from typing import Dict, Any, List
from sky_executor.grid_factory.factory.Component.Assigner.assigner_factory import (
    AssignerFactory,
)
from sky_executor.grid_factory.factory.Component.Assigner.template_assigner.assigner import (
    Assigner,
)
from sky_executor.grid_factory.factory.Utils.structure import RoutingTask, AGV
from sky_executor.grid_factory.factory.Utils.feature_extractor import FeatureExtractor


@AssignerFactory.register_solver("nn")
class NeuralNetworkAssigner(Assigner):
    """
    AGV-神经网络格式的分配器
    -------------------
    输入:
        obs: 环境观测信息，包括：
            {
                "pending_transfers": list[RoutingTask],
                "machines": list[Machine],
                "agents": list[AGV],
            }

    输出:
        actions: 包含分配结果的字典，格式如下：
            {
                "assignments": dict  {agv_id: RoutingTask | None},
                "pending_transfers": list[RoutingTask]
            }
    """

    def __init__(self):
        self._extractor = FeatureExtractor()
        self._model = self._load_model()

    @staticmethod
    def _load_model():
        import torch
        from sky_executor.grid_factory.factory.Utils.model.plain_assigner_model import AssignerNet

        model = AssignerNet()
        model_path = os.getenv("NN_MODEL_PATH", "nn_model.pth")
        if os.path.exists(model_path):
            state_dict = torch.load(model_path, map_location="cpu", weights_only=True)
            model.load_state_dict(state_dict)
        model.eval()
        return model

    def plan(self, obs: Dict[str, Any]):
        agents: List[AGV] = obs.get("agents", [])
        transfers_to_assign: List[RoutingTask] = obs.get("pending_transfers", [])

        if not agents or not transfers_to_assign:
            assignments = {agent.id: None for agent in agents}
            return {
                "assignments": assignments,
                "pending_transfers": transfers_to_assign,
            }

        available_tasks = transfers_to_assign.copy()
        assignments = {}

        raw_result = self._extractor.extract_raw(obs)
        nn_input = self._extractor._translate_feature_to_nn_input(raw_result)

        import torch
        with torch.no_grad():
            nn_output = self._model.score_matrix(
                nn_input["agv_feat"],
                nn_input["task_feat"],
                nn_input["pair_feat"],
                nn_input["env_stats"],
                pair_mask=nn_input["pair_mask"],
            )

        assignments, available_tasks = (
            self._extractor._translate_nn_output_to_assignments(
                nn_output, agents, available_tasks, assignments
            )
        )

        return {"assignments": assignments, "pending_transfers": available_tasks}
