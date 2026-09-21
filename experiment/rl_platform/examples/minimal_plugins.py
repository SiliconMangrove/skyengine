"""最小插件示例：用于检查平台接线，不执行参数学习。"""

from ..api import PolicyPlugin, RewardPlugin, TrainerPlugin, Transition


class FirstFeasiblePolicy(PolicyPlugin):
    def act(self, observation, action_mask, deterministic=False):
        del observation, deterministic
        production = [
            {"job_id": item["job_id"], "op_id": item["op_id"], "machine_id": item["machine_ids"][0]}
            for item in (action_mask or {}).get("production", [])
            if item.get("machine_ids")
        ]
        logistics = [
            {"task_id": item["task_id"], "agv_id": item["agv_ids"][0]}
            for item in (action_mask or {}).get("logistics", [])
            if item.get("agv_ids")
        ]
        return {"production": production, "logistics": logistics}


class CompletionDeltaReward(RewardPlugin):
    def compute(self, transition: Transition):
        after = transition.info.get("metrics", {})
        return float(after.get("operation_completion_rate", 0.0))


class CountingTrainer(TrainerPlugin):
    @classmethod
    def hyperparameter_schema(cls):
        return {"fields": [{"name": "log_every", "label": "日志间隔", "type": "integer", "default": 1, "min": 1, "step": 1}]}

    def update(self, trajectory_batch):
        return {"transitions": sum(len(trajectory.transitions) for trajectory in trajectory_batch)}
