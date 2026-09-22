"""对 JSONL 实例执行轻量结构检查。"""

from __future__ import annotations

from typing import Mapping, Any


def validate_instance(instance: Mapping[str, Any]) -> None:
    for key in ("schema_version", "instance_id", "seed", "topology", "agvs", "jobs", "processing_time_config", "exception_config"):
        if key not in instance:
            raise ValueError(f"实例缺少字段: {key}")
    machines = instance["topology"].get("machines", {})
    if not machines:
        raise ValueError("实例没有机器")
    jobs = instance["jobs"].get("job_list", [])
    if not jobs:
        raise ValueError("实例没有作业")
    machine_ids = {int(key) for key in machines}
    occupied: dict[tuple[int, int], int] = {}
    for agv in instance["agvs"]:
        position: tuple[int, int] = tuple(agv["initialLocation"])
        if position in occupied:
            raise ValueError(f"实例 {instance['instance_id']} 中 AGV {occupied[position]} 和 AGV {agv['id']} 初始位置重复: {position}")
        occupied[position] = agv["id"]
    for job in jobs:
        if not job.get("operations"):
            raise ValueError("作业没有工序")
        for operation in job["operations"]:
            options = operation.get("machine_options_with_time", [])
            if not options or any(int(machine) not in machine_ids or float(duration) <= 0 for machine, duration in options):
                raise ValueError("存在无效工序机器选项")
