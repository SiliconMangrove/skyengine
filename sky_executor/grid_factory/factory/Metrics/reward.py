"""
RL Reward 计算：
- Dense reward: 每 step 基于停滞、冲突、机器利用率
- Sparse reward: episode 结束时基于 makespan、transport_delay、starvation
"""


class RewardCalculator:
    """权重可通过 set_weights() 调整"""

    _weights = {
        # Dense (per-step)
        "tasked_stationary_count": -0.1,
        "swap_conflict_count": -0.05,
        "machine_utilization": 0.01,
        # Sparse (episode-end)
        "full_makespan": -1.0,
        "transport_delay_ratio": -10.0,
        "machine_waiting_for_inbound_transfer_ratio": -5.0,
    }

    @classmethod
    def set_weights(cls, weights: dict):
        cls._weights.update(weights)

    @classmethod
    def get_weights(cls) -> dict:
        return cls._weights.copy()

    @classmethod
    def compute(cls, metrics: dict, done: bool = False) -> float:
        """
        Parameters
        ----------
        metrics : dict — MetricsHub.on_step_end 产出的指标 dict
        done : bool — episode 是否结束
        """
        w = cls._weights

        # Dense reward
        r = (
            w["tasked_stationary_count"] * metrics.get("tasked_stationary_count", 0)
            + w["swap_conflict_count"] * metrics.get("swap_conflict_count", 0)
            + w["machine_utilization"] * metrics.get("machine_utilization", 0)
        )

        # Sparse reward (episode 结束时)
        if done:
            r += (
                w["full_makespan"] * metrics.get("full_makespan", 0)
                + w["transport_delay_ratio"] * metrics.get("transport_delay_ratio", 0)
                + w["machine_waiting_for_inbound_transfer_ratio"]
                * metrics.get("machine_waiting_for_inbound_transfer_ratio", 0)
            )

        return r
