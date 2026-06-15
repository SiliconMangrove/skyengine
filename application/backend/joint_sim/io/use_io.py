"""
@Project ：SkyEngine
@File    ：use_io.py
@IDE     ：PyCharm
@Author  ：Skyrim
@Date    ：2026/3/16
"""

import json
from os import makedirs
from pathlib import Path
from typing import Dict, Any, List, Tuple

from pogema import GridConfig

from joint_sim.utils.structure import MachineConfig, JobConfig
from joint_sim.grid_factory_env import GridFactoryEnv
import logging

logger = logging.getLogger(__name__)


def load_grid_factory_config(config_path: str) -> Dict[str, Any]:
    """
    读取 grid_factory.json 配置文件

    Args:
        config_path: 配置文件路径

    Returns:
        解析后的配置字典
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    return config


def build_obstacle_map(
    topology: Dict[str, Any],
    machine_positions: List[Tuple[int, int]],
    agent_positions: List[Tuple[int, int]],
) -> str:
    """
    从 topology.zones[type=obstacle] 构建 pogema 字符串障碍图。

    - 方正化为 size×size（size = max(gridWidth, gridHeight)），与 pogema 正方形约束一致。
    - 机器格、AGV 起点格强制保持可通行（否则 AGV 永远到不了目标，运行期才报错）。
    - 机器仍作为 possible_targets_xy（在 create_env_from_config 设置），不在此处挡路。

    Returns:
        多行字符串：行=y, 列=x, '#' 障碍, '.' 自由。
    """
    gw = topology.get("gridWidth", 20)
    gh = topology.get("gridHeight", 20)
    size = max(gw, gh)

    # 1. 展开 obstacle zone 为格子集合
    blocked = set()
    for z in topology.get("zones", []):
        if z.get("type") != "obstacle":
            continue
        area = z.get("area", {})
        x0 = area.get("x", 0)
        y0 = area.get("y", 0)
        w = area.get("w", 0)
        h = area.get("h", 0)
        for dy in range(h):
            for dx in range(w):
                blocked.add((x0 + dx, y0 + dy))

    # 2. 机器格、AGV 起点格不能是障碍
    for mx, my in machine_positions:
        blocked.discard((int(mx), int(my)))
    for ax, ay in agent_positions:
        blocked.discard((int(ax), int(ay)))

    # 3. 拼 size×size 字符串
    rows = []
    for y in range(size):
        rows.append("".join("#" if (x, y) in blocked else "." for x in range(size)))
    return "\n".join(rows)


def parse_grid_config(config: Dict[str, Any]) -> GridConfig:
    """
    从 JSON 配置解析 GridConfig

    Args:
        config: 完整的配置字典

    Returns:
        GridConfig 实例
    """
    topology = config.get("topology", {})
    agvs = config.get("agvs", [])

    grid_width = topology.get("gridWidth", 20)
    grid_height = topology.get("gridHeight", 20)
    grid_size = max(grid_width, grid_height)

    # 获取 AGV 初始位置
    agents_start_xy = [tuple(agv["initialLocation"]) for agv in agvs]

    # 机器位置（仅用于排除：机器格不挡路，AGV 必须能到达）
    machines = topology.get("machines", {})
    machine_positions = [tuple(m["location"]) for m in machines.values()]

    # 从 topology.zones[type=obstacle] 构建障碍图；提供 map 后 size/density 由 pogema 推导
    obstacle_map = build_obstacle_map(topology, machine_positions, agents_start_xy)

    # 注意：Grid 类要求 agents_xy 和 targets_xy 同时存在才会使用配置的位置
    # 否则会走到 else 分支随机生成位置，导致 initialLocation 被覆盖
    # 在 LifeLong 模式下，初始目标可以和起始位置相同（后续会被实际任务目标覆盖）
    # size 显式传入：部分 pogema 版本里 agents_xy 校验先于 map 推导 size 执行，
    # 不传 size 会用默认值导致 "Position is out of bounds!"。map 校验器仍会把它设为 map 推导值，一致。
    return GridConfig(
        size=grid_size,
        map=obstacle_map,
        num_agents=len(agvs),
        seed=42,
        max_episode_steps=256,
        obs_radius=5,
        on_target="restart",
        agents_xy=agents_start_xy if agents_start_xy else None,
        targets_xy=(agents_start_xy if agents_start_xy else None),  # 初始目标和起始位置相同
    )


def parse_machine_config(
    config: Dict[str, Any],
) -> Tuple[MachineConfig, List[Tuple[int, int]]]:
    topology = config.get("topology", {})
    machines = topology.get("machines", {})

    machine_positions = []
    for machine_key, machine_info in machines.items():
        location = tuple(machine_info["location"])
        machine_positions.append(location)

    machine_config = MachineConfig(
        num_machines=len(machines),
        strategy="custom",  # 改为 custom
        custom_positions=machine_positions,  # 传入自定义位置
        seed=42,
    )

    return machine_config, machine_positions


def parse_job_config(config: Dict[str, Any], num_machines: int) -> JobConfig:
    """
    从 JSON 配置解析 JobConfig

    Args:
        config: 完整的配置字典
        num_machines: 机器总数

    Returns:
        JobConfig 实例
    """
    logger.info(f"正在解析任务配置...{config}")
    jobs = config.get("jobs", {})
    logger.info(f"已解析任务配置，任务参数：\n{jobs}")
    job_list = jobs.get("job_list", [])
    logger.info(f"已解析任务配置，任务参数：\n{job_list}")
    if not job_list:
        # 如果没有任务，使用默认配置
        return JobConfig(
            num_jobs=1,
            min_ops_per_job=0,
            max_ops_per_job=0,
            min_proc_time=1,
            max_proc_time=1,
            machine_choices=1,
            total_machines=num_machines,
            seed=42,
        )

    # 解析 custom_jobs 格式: List[List[(machine_options, proc_time)]]
    custom_jobs: List[List[Tuple[List[int], int]]] = []
    all_durations = []
    # 优先使用 machine_options_with_time 字段（标准 FJSP：每 op 多机器候选 + 各自时间）；
    # 存在则走 custom_time strategy，generate_jobs 会填 Operation.machine_options_with_time。
    use_custom_time = False

    for job in job_list:
        job_ops: List[Tuple[List[int], int]] = []
        for op in job.get("operations", []):
            if "machine_options_with_time" in op:
                # 优先字段：List[[machine_id, proc_time]]
                use_custom_time = True
                mowt = op["machine_options_with_time"]
                # custom_time strategy 期望 op_info[0] = List[Tuple[machine_id, proc_time]]
                job_ops.append(([(m[0], m[1]) for m in mowt], mowt[0][1]))
                all_durations.extend(m[1] for m in mowt)
            else:
                # DEPRECATED: 旧格式 {machine_id, duration}（单机器，丢失 per-machine 时间）
                # 仅兼容历史配置（northeastFactoryMap.json / grid_factory_example.json 等），
                # 新数据集走 helper.convert_to_grid_factory 已输出 machine_options_with_time。
                machine_id = op.get("machine_id", 0)
                duration = op.get("duration", 1)
                # machine_options 是一个列表（支持多机器选择）
                machine_options = [machine_id] if isinstance(machine_id, int) else machine_id
                job_ops.append((machine_options, duration))
                all_durations.append(duration)
        custom_jobs.append(job_ops)

    # 计算工序数量的范围
    ops_counts = [len(job_ops) for job_ops in custom_jobs]
    min_ops = min(ops_counts) if ops_counts else 1
    max_ops = max(ops_counts) if ops_counts else 1

    # 计算处理时间的范围
    min_proc = min(all_durations) if all_durations else 1
    max_proc = max(all_durations) if all_durations else 1

    return JobConfig(
        num_jobs=len(job_list),
        min_ops_per_job=min_ops,
        max_ops_per_job=max_ops,
        min_proc_time=min_proc,
        max_proc_time=max_proc,
        machine_choices=1,  # JSON 中已指定机器
        total_machines=num_machines,
        seed=42,
        strategy="custom_time" if use_custom_time else "custom",
        custom_jobs=custom_jobs,
    )


def create_env_from_config(config_path: str | dict, random_target: bool = False) -> GridFactoryEnv:
    """
    从配置文件创建 GridFactoryEnv 环境

    Args:
        config_path: 配置文件路径
        random_target: 是否使用随机目标

    Returns:
        初始化后的 GridFactoryEnv 实例
    """
    # 1. 按类型划分逻辑，加载/验证配置
    if isinstance(config_path, str):
        config = load_grid_factory_config(config_path)
        # logger.info(f"成功从路径 {config_path} 加载配置")

    elif isinstance(config_path, dict):
        config = config_path
        # logger.info("直接使用传入的配置字典")

    else:
        pass
        # print(
        #     f"config_path 必须是 str（文件路径）或 dict（配置字典），但收到 {type(config_path).__name__} 类型"
        # )

    # 2. 解析配置
    grid_cfg = parse_grid_config(config)
    machine_cfg, machine_positions = parse_machine_config(config)
    job_cfg = parse_job_config(config, machine_cfg.num_machines)

    # 3. 设置机器位置到 grid_config
    grid_cfg.possible_targets_xy = machine_positions

    logger.info(
        f"已创建 GridFactoryEnv，环境参数：\n"
        f"grid_config: {grid_cfg}\n"
        f"machine_config: {machine_cfg}\n"
        f"job_config: {job_cfg}"
    )
    # 4. 创建环境
    env = GridFactoryEnv(
        grid_config=grid_cfg,
        machine_config=machine_cfg,
        job_config=job_cfg,
        random_target=random_target,
    )

    return env


# ============================================================
# 使用示例
# ============================================================