"""生成训练随机化参数和 benchmark 固定事件表。"""

from __future__ import annotations

import random


def build_disturbances(rng: random.Random, mode: str, num_machines: int, num_agvs: int, max_steps: int) -> tuple[dict, dict]:
    if mode == "none":
        return {"preset": "none", "enabled": False, "random_seed": rng.randrange(2**31)}, {"preset": "none", "enabled": False}
    if mode == "random":
        return {
            "enabled": True,
            "random_seed": rng.randrange(2**31),
            "machine_failure": {"enabled": True, "mtbf_steps": 180, "repair_time": {"dist": "discrete_uniform", "low": 8, "high": 25}},
            "agv_failure": {"enabled": True, "mtbf_steps": 260, "repair_time": {"dist": "discrete_uniform", "low": 5, "high": 18}},
            "temporary_obstacle": {"enabled": True, "mean_interarrival_steps": 140, "max_new_per_step": 1, "preserve_connectivity": True, "duration": {"dist": "discrete_uniform", "low": 8, "high": 20}},
        }, {
            "mode": "parameter_randomization",
            "random_seed": rng.randrange(2**31),
            "machine_failure": {"enabled": True, "mtbf_steps": 180, "repair_time": {"dist": "discrete_uniform", "low": 8, "high": 25}},
            "agv_failure": {"enabled": True, "mtbf_steps": 260, "repair_time": {"dist": "discrete_uniform", "low": 5, "high": 18}},
            "temporary_obstacle": {"enabled": True, "mean_interarrival_steps": 140, "max_new_per_step": 1, "preserve_connectivity": True, "duration": {"dist": "discrete_uniform", "low": 8, "high": 20}},
        }
    events = []
    event_count = 3 if mode == "failure" else 5
    for index in range(event_count):
        step = rng.randint(max_steps // 8, max_steps * 3 // 4)
        if index % 2 == 0:
            events.append({"step": step, "type": "machine_breakdown", "machine_id": rng.randrange(num_machines), "duration_steps": rng.randint(10, 30)})
        else:
            events.append({"step": step, "type": "agv_breakdown", "agv_id": rng.randrange(num_agvs), "duration_steps": rng.randint(6, 20)})
    if mode == "failure":
        processing = {"preset": "high_variance", "random_seed": rng.randrange(2**31)}
    else:
        processing = {"preset": "moderate_variance", "random_seed": rng.randrange(2**31)}
    return processing, {"enabled": True, "schedule": sorted(events, key=lambda event: event["step"]), "random_seed": rng.randrange(2**31)}
