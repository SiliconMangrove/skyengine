"""
数据集选取与统一问题描述转换
============================

将各种来源的数据集 (FJSP benchmark, MAPF 地图) 统一转换为
SkyEngine 的 grid_factory.json 格式。

支持的输入格式:
    1. FJSP JSON (dataset/fjsp-instances/**/*.json)
    2. FJSP Benchmark 文本 (dataset/fjsp-instances/**/*.txt)
    3. MAPF YAML 地图 (dataset/mapf/*.yaml)

输出: grid_factory.json 格式, 可被 SkyEngine 直接加载。

用法:
    from dataset.helper import select_and_convert
    config = select_and_convert(
        fjsp="k1",                   # FJSP 实例名 (支持 k1, kacem/k1, kacem/k1.json)
        map_name="random-32-32-20",  # MAPF 地图名
        num_agvs=4,
        data_dir="./dataset",
    )

# 列出可用数据集
python -m dataset.helper --list

# 用 k1 实例 + 默认地图，4台 AGV
python -m dataset.helper --fjsp k1 --agvs 4

# 用 mfjs01 实例 + 指定地图
python -m dataset.helper --fjsp mfjs01 --map "medium-mazes-seed-0001" --agvs 6 --output config/grid_factory.json

"""

import json
import math
import os
import random
from pathlib import Path
from typing import Optional

import yaml


# ============================================================
# 1. FJSP 数据加载
# ============================================================


def load_fjsp_json(path: str) -> dict:
    """加载 FJSP JSON 格式 (dataset/fjsp-instances/**/*.json)

    JSON 格式:
        {
            "machines": int,
            "jobs": [
                [  # job 0
                    [{"machine": 0, "processing": 2}, ...],  # operation 0 的可选方案
                    ...
                ],
                ...
            ]
        }

    返回:
        {
            "machine_number": int,
            "jobs": [
                [  # job 0
                    [(processing_time, machine_id), ...],  # operation 0
                    ...
                ],
                ...
            ]
        }
    """
    with open(path, "r") as f:
        data = json.load(f)

    num_machines = data["machines"]
    jobs = []
    for job in data["jobs"]:
        job_tasks = []
        for op in job:
            alternatives = [(alt["processing"], alt["machine"]) for alt in op]
            job_tasks.append(alternatives)
        jobs.append(job_tasks)

    return {"machine_number": num_machines, "jobs": jobs}


def load_fjsp_benchmark(path: str) -> dict:
    """加载 Brandimarte 格式的 FJSP benchmark 文本文件

    格式: 第一行 = n_jobs n_machines, 之后每行一个 job
    每个 job 行: num_ops (n_candidates machine_id duration)* ...

    返回格式同 load_fjsp_json。
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]

    n_jobs, n_machines = map(int, lines[0].split())

    jobs = []
    for job_idx in range(n_jobs):
        nums = list(map(int, lines[1 + job_idx].split()))
        num_ops = nums[0]
        idx = 1
        operations = []
        for _ in range(num_ops):
            n_candidates = nums[idx]
            idx += 1
            alternatives = []
            for _ in range(n_candidates):
                machine_id = nums[idx] - 1  # 转为 0-based
                duration = nums[idx + 1]
                alternatives.append((duration, machine_id))
                idx += 2
            operations.append(alternatives)
        jobs.append(operations)

    return {"machine_number": n_machines, "jobs": jobs}


# ============================================================
# 2. MAPF 地图加载
# ============================================================


def load_mapf_yaml(path: str, map_name: Optional[str] = None) -> dict:
    """加载 MAPF YAML 地图文件

    Args:
        path: YAML 文件路径 (通常为 maps.yaml)
        map_name: 地图名, None 则取第一个

    返回:
        {
            "map": str,          # 原始地图字符串 (. = 空地, # = 障碍物)
            "width": int,
            "height": int,
            "map_name": str,
        }
    """
    with open(path, "r") as f:
        maps = yaml.safe_load(f)

    if map_name and map_name in maps:
        chosen = map_name
    else:
        chosen = next(iter(maps))

    map_str = maps[chosen]
    lines = map_str.strip().split("\n")
    height = len(lines)
    width = max(len(l) for l in lines) if lines else 0

    return {
        "map": map_str,
        "width": width,
        "height": height,
        "map_name": chosen,
    }


# ============================================================
# 3. AGV 初始化策略
# ============================================================


def _get_free_cells(map_str: str) -> list[tuple[int, int]]:
    """从地图字符串提取所有可通行格子 (.)"""
    lines = map_str.strip().split("\n")
    cells = []
    for y, row in enumerate(lines):
        for x, ch in enumerate(row):
            if ch != "#":
                cells.append((x, y))
    return cells


def _place_on_grid(
    map_str: str,
    n_items: int,
    seed: int = 42,
    strategy: str = "spread",
) -> list[tuple[int, int]]:
    """在地图上为 items (机器/AGV) 分配位置

    strategy:
        "spread" - 尽量均匀分布在地图上
        "random" - 随机选择
    """
    free = _get_free_cells(map_str)
    if not free:
        raise ValueError("地图上无可通行的格子")

    rng = random.Random(seed)

    if strategy == "random" or n_items <= 1:
        return rng.sample(free, min(n_items, len(free)))

    # spread: 将地图划分为 n_items 个区域, 每个区域内随机选一个
    lines = map_str.strip().split("\n")
    height = len(lines)
    width = max(len(l) for l in lines)

    cols = math.ceil(math.sqrt(n_items * width / max(height, 1)))
    rows = math.ceil(n_items / max(cols, 1))

    positions = []
    cell_w = width / max(cols, 1)
    cell_h = height / max(rows, 1)

    for i in range(n_items):
        r = i // max(cols, 1)
        c = i % max(cols, 1)
        x_min, x_max = int(r * cell_w), int((r + 1) * cell_w)
        y_min, y_max = int(c * cell_h), int((c + 1) * cell_h)

        used = set(positions)
        candidates = [
            (x, y) for x, y in free
            if x_min <= x < x_max and y_min <= y < y_max and (x, y) not in used
        ]
        if not candidates:
            candidates = [p for p in free if p not in used]  # fallback
        if not candidates:
            candidates = free  # final fallback
        positions.append(rng.choice(candidates))

    return positions


def init_agvs(
    map_str: str,
    machine_positions: list[tuple[int, int]],
    num_agvs: int = 4,
    seed: int = 42,
) -> list[tuple[int, int]]:
    """AGV 初始化策略：在机器之间的路径上均匀放置

    策略：选取离所有机器距离之和最小的空地（交通枢纽），
    然后围绕这些枢纽分散放置 AGV，确保每台 AGV 能快速服务各机器。

    Args:
        map_str: 地图字符串 (. = 空地, # = 障碍物)
        machine_positions: 机器位置列表 [(x, y), ...]
        num_agvs: AGV 数量
        seed: 随机种子

    Returns:
        AGV 初始位置列表 [(x, y), ...]
    """
    free = _get_free_cells(map_str)
    if not free:
        raise ValueError("地图上无可通行的格子")

    # 排除机器占用的格子，防止 AGV 与机器重叠
    machine_set = set(machine_positions)
    free = [p for p in free if p not in machine_set]

    if not machine_positions:
        return _place_on_grid(map_str, num_agvs, seed=seed, strategy="spread")

    rng = random.Random(seed)

    # 计算每个空格到所有机器的曼哈顿距离之和
    def total_dist(pos: tuple[int, int]) -> float:
        return sum(abs(pos[0] - mx) + abs(pos[1] - my) for mx, my in machine_positions)

    # 按距离排序，取前 20% 作为候选枢纽区域
    sorted_cells = sorted(free, key=total_dist)
    top_k = max(len(sorted_cells) // 5, num_agvs * 3)
    hub_candidates = sorted_cells[:top_k]

    # 从候选中选取 AGV 位置，尽量互相保持距离
    agv_positions = []
    for _ in range(num_agvs):
        if not hub_candidates:
            break

        if not agv_positions:
            # 第一个 AGV 放在最优枢纽
            pos = hub_candidates[0]
        else:
            # 选离已有 AGV 最远的候选点
            best = max(
                hub_candidates,
                key=lambda p: min(
                    abs(p[0] - ax) + abs(p[1] - ay) for ax, ay in agv_positions
                ),
            )
            pos = best

        agv_positions.append(pos)
        # 从候选中移除附近点，避免扎堆
        hub_candidates = [
            p for p in hub_candidates if abs(p[0] - pos[0]) + abs(p[1] - pos[1]) > 2
        ]

    # 如果候选不够，从全局空地补充
    if len(agv_positions) < num_agvs:
        remaining = num_agvs - len(agv_positions)
        used = set(agv_positions)
        extra = rng.sample(
            [p for p in free if p not in used], min(remaining, len(free) - len(used))
        )
        agv_positions.extend(extra)

    return agv_positions[:num_agvs]


# ============================================================
# 4. 统一问题描述转换 → grid_factory.json
# ============================================================

# 操作名称池, 按顺序循环分配
_OP_NAMES = [
    "涂胶",
    "贴片",
    "焊接",
    "检测",
    "组装",
    "清洗",
    "切割",
    "打磨",
    "喷涂",
    "冲压",
]


def _map_to_obstacle_zones(map_str: str) -> list[dict]:
    """将地图中的 # 转换为 obstacle zone 列表

    把相邻的 # 合并为最小矩形块，减少 zone 数量。
    """
    lines = map_str.strip().split("\n")
    height = len(lines)
    width = max(len(l) for l in lines) if lines else 0

    # 标记障碍物格子
    blocked = set()
    for y, row in enumerate(lines):
        for x, ch in enumerate(row):
            if ch == "#":
                blocked.add((x, y))

    if not blocked:
        return []

    # 按行合并连续障碍物为矩形 (贪心)
    visited = set()
    zones = []
    zone_idx = 0

    for y in range(height):
        for x in range(width):
            if (x, y) in blocked and (x, y) not in visited:
                # 向右延伸
                w = 1
                while (x + w, y) in blocked and (x + w, y) not in visited:
                    w += 1
                # 向下延伸（检查整行是否都是未访问的障碍物）
                h = 1
                can_extend = True
                while can_extend and y + h < height:
                    for dx in range(w):
                        if (x + dx, y + h) not in blocked or (x + dx, y + h) in visited:
                            can_extend = False
                            break
                    if can_extend:
                        h += 1

                # 标记已访问
                for dy in range(h):
                    for dx in range(w):
                        visited.add((x + dx, y + dy))

                zones.append({
                    "id": f"obstacle_{zone_idx}",
                    "name": f"障碍物 {zone_idx}",
                    "area": {"x": x, "y": y, "w": w, "h": h},
                    "type": "obstacle",
                    "color": "rgba(60, 60, 70, 0.8)",
                })
                zone_idx += 1

    return zones


def convert_to_grid_factory(
    fjsp_data: dict,
    map_data: dict,
    num_agvs: int = 4,
    agv_velocity: float = 1.0,
    agv_capacity: int = 100,
    seed: int = 42,
    arrival_time: int = 0,
    due_time_base: int = 200,
    priority: int = 1,
) -> dict:
    """将 FJSP + MAPF 数据转换为 grid_factory.json 格式

    Args:
        fjsp_data: load_fjsp_json / load_fjsp_benchmark 的返回值
        map_data: load_mapf_yaml 的返回值
        num_agvs: AGV 数量
        agv_velocity: 默认 AGV 速度
        agv_capacity: AGV 容量
        seed: 随机种子
        arrival_time: job 默认到达时间
        due_time_base: 工期基准值 (会根据工序总时长动态调整)
        priority: job 优先级

    Returns:
        完整的 grid_factory.json 字典
    """
    n_machines = fjsp_data["machine_number"]
    jobs = fjsp_data["jobs"]
    map_str = map_data["map"]
    width = map_data["width"]
    height = map_data["height"]

    # --- 机器位置 (均匀分布) ---
    m_positions = _place_on_grid(map_str, n_machines, seed=seed, strategy="spread")

    # --- AGV 位置 (枢纽策略) ---
    agv_positions = init_agvs(map_str, m_positions, num_agvs=num_agvs, seed=seed)

    # --- 构建 machines 字典 ---
    machines_dict = {}
    machine_id_map = {}
    for i in range(n_machines):
        key = f"MACHINE_{i // 2 + 1}_{i % 2 + 1}"
        machine_id_map[str(i)] = key
        machines_dict[key] = {
            "id": f"TABLE_{i // 2 + 1}_MACHINE_{i % 2 + 1}",
            "name": f"PLC {i // 2 + 1}-{i % 2 + 1}",
            "location": list(m_positions[i]),
            "size": [1, 1],
            "status": "IDLE",
        }

    # --- 构建 jobs ---
    job_list = []
    for job_idx, job in enumerate(jobs):
        # 估算总加工时间来设置 due_time
        min_total_time = sum(min(alt[0] for alt in op) if op else 0 for op in job)
        due_time = arrival_time + min_total_time * 3 + due_time_base

        operations = []
        for op_idx, alternatives in enumerate(job):
            # 保留 op 的全部候选机器与各自处理时间（标准 FJSP）。
            # 字段格式: List[[machine_id, processing_time]]
            # 注意数据集内部存的是 (processing_time, machine_id) 元组，
            # 输出时必须互换顺序，匹配 Operation.machine_options_with_time
            # 的 List[Tuple[machine_id, proc_time]] 约定。
            operations.append(
                {
                    "machine_options_with_time": [
                        [mid, pt] for pt, mid in alternatives
                    ],
                    "name": _OP_NAMES[op_idx % len(_OP_NAMES)],
                }
            )

        job_list.append(
            {
                "job_id": job_idx,
                "name": f"工件-{job_idx:03d}",
                "operations": operations,
                "arrival_time": arrival_time,
                "due_time": due_time,
                "priority": priority,
            }
        )

    # --- 从地图生成障碍物 zones ---
    obstacle_zones = _map_to_obstacle_zones(map_str)

    # --- 组装完整配置 ---
    config = {
        "id": "grid_factory",
        "name": "SkyEngine 统一问题描述",
        "version": "1.2.0",
        "createdAt": _today_str(),
        "description": f"自动生成: {n_machines} 台机器, {len(jobs)} 个工件, {len(agv_positions)} 台 AGV",
        "topology": {
            "gridWidth": width,
            "gridHeight": height,
            "zones": obstacle_zones,
            "machines": machines_dict,
            "waypoints": {},
        },
        "agvs": [
            {
                "id": i,
                "name": f"AGV-{i + 1:02d}",
                "initialLocation": [x, y],
                "velocity": agv_velocity,
                "capacity": agv_capacity,
                "status": "IDLE",
            }
            for i, (x, y) in enumerate(agv_positions)
        ],
        "jobs": {
            "_comment": "machine_id 映射: 0-based 索引",
            "_machine_id_map": machine_id_map,
            "job_list": job_list,
        },
        "renderConfig": _default_render_config(width, height),
        "metadata": {
            "gridUnit": "米（假设每个网格 = 1m）",
            "agvCapacity": agv_capacity,
            "machineCount": n_machines,
            "waypointCount": 0,
            "notes": f"由 helper.py 自动生成。map_name={map_data.get('map_name', 'N/A')}",
        },
    }

    return config


# ============================================================
# 5. 高层 API: 选取数据集并转换
# ============================================================


def select_and_convert(
    fjsp: str,
    map_name: Optional[str] = None,
    num_agvs: int = 4,
    agv_velocity: float = 1.0,
    seed: int = 42,
    data_dir: str = "./dataset",
    output_path: Optional[str] = None,
) -> dict:
    """选取数据集并转换为 grid_factory.json 格式

    Args:
        fjsp: FJSP 实例标识, 支持以下格式:
            - "k1" / "kacem/k1" / "kacem/k1.json"  → 在 fjsp-instances/ 下递归查找
            - 完整路径                                → 直接使用
        map_name: MAPF 地图名 (如 "medium-mazes-seed-0000"), None 则使用默认地图
        num_agvs: AGV 数量
        agv_velocity: AGV 速度
        seed: 随机种子
        data_dir: 数据集根目录
        output_path: 若指定则将结果写入该路径

    Returns:
        grid_factory.json 格式的字典
    """
    data_dir = Path(data_dir)
    fjsp_data = None

    # --- 解析 FJSP ---
    fjsp_path = _find_fjsp(data_dir, fjsp)
    if fjsp_path is None:
        raise FileNotFoundError(f"FJSP 实例未找到: {fjsp}")

    if fjsp_path.suffix == ".json":
        fjsp_data = load_fjsp_json(str(fjsp_path))
    else:
        fjsp_data = load_fjsp_benchmark(str(fjsp_path))

    # --- 解析 MAPF 地图 ---
    mapf_dir = data_dir / "mapf"
    mapf_candidates = list(mapf_dir.glob("*.yaml")) + list(mapf_dir.glob("*.yml"))
    if not mapf_candidates:
        raise FileNotFoundError(f"MAPF 目录下无 YAML 文件: {mapf_dir}")
    mapf_path = mapf_candidates[0]
    map_data = load_mapf_yaml(str(mapf_path), map_name)

    # --- 转换 ---
    config = convert_to_grid_factory(
        fjsp_data=fjsp_data,
        map_data=map_data,
        num_agvs=num_agvs,
        agv_velocity=agv_velocity,
        seed=seed,
    )

    # --- 输出 ---
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"[helper] 已写入: {out}")

    return config


def list_available_datasets(data_dir: str = "./dataset") -> dict:
    """列出所有可用的数据集

    Returns:
        {
            "fjsp_json": [filename, ...],
            "fjsp_benchmarks": {"brandimarte": [name, ...], ...},
            "mapf_maps": {filename: [map_name, ...], ...},
        }
    """
    data_dir = Path(data_dir)
    result = {
        "fjsp_json": [],
        "fjsp_benchmarks": {},
        "mapf_maps": {},
    }

    # FJSP JSON (递归查找)
    fjsp_dir = data_dir / "fjsp-instances"
    if fjsp_dir.exists():
        result["fjsp_json"] = sorted(
            str(p.relative_to(fjsp_dir))
            for p in fjsp_dir.rglob("*.json")
            if p.name != "instances.json" and not p.name.startswith(".")
        )

    # FJSP benchmarks (txt 文件)
    if fjsp_dir.exists():
        for subdir in sorted(fjsp_dir.iterdir()):
            if subdir.is_dir() and not subdir.name.startswith("."):
                # 跳过只含 json 的目录（kacem, fattahi）
                txt_files = sorted(
                    p.stem
                    for p in subdir.glob("*.txt")
                    if not p.name.startswith(".") and not p.name.startswith("simple")
                )
                if txt_files:
                    result["fjsp_benchmarks"][subdir.name] = txt_files
                # 也检查子目录 (hurink/edata, hurink/rdata 等)
                for sub2 in sorted(subdir.iterdir()):
                    if sub2.is_dir() and not sub2.name.startswith("."):
                        txt_files = sorted(
                            p.stem
                            for p in sub2.glob("*.txt")
                            if not p.name.startswith(".")
                        )
                        if txt_files:
                            key = f"{subdir.name}/{sub2.name}"
                            result["fjsp_benchmarks"][key] = txt_files

    # MAPF maps
    mapf_dir = data_dir / "mapf"
    if mapf_dir.exists():
        for yaml_file in sorted(mapf_dir.glob("*.yaml")):
            try:
                with open(yaml_file) as f:
                    maps = yaml.safe_load(f)
                result["mapf_maps"][yaml_file.name] = list(maps.keys())[:10]
            except Exception:
                pass

    return result


# ============================================================
# 辅助函数
# ============================================================


def _find_fjsp(data_dir: Path, name: str) -> Optional[Path]:
    """在 fjsp-instances 下查找 FJSP 文件 (支持 JSON 和 TXT)

    查找顺序:
      1. 作为完整路径直接使用
      2. fjsp-instances/<name>
      3. fjsp-instances/<name>.json
      4. fjsp-instances/<name>.txt
      5. 递归查找 fjsp-instances/**/<name>.json
      6. 递归查找 fjsp-instances/**/<name>.txt
    """
    # 1. 完整路径
    p = Path(name)
    if p.is_absolute() and p.exists():
        return p

    fjsp_dir = data_dir / "fjsp-instances"
    if not fjsp_dir.exists():
        # fallback 到旧路径
        fjsp_dir = data_dir / "fjsp"

    # 2-4. 直接匹配
    candidates = [
        fjsp_dir / name,
        fjsp_dir / (name + ".json"),
        fjsp_dir / (name + ".txt"),
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c

    # 5-6. 递归查找 (去掉用户可能带的扩展名)
    stem = Path(name).stem
    for suffix in [".json", ".txt"]:
        matches = list(fjsp_dir.rglob(f"**/{stem}{suffix}"))
        if matches:
            # 优先 JSON
            return matches[0]

    return None


def _today_str() -> str:
    from datetime import date

    return date.today().isoformat()


def _default_render_config(grid_width: int, grid_height: int) -> dict:
    """生成默认渲染配置"""
    return {
        "baseGridSize": 40,
        "gridWidth": grid_width,
        "gridHeight": min(grid_height, 20),
        "colors": {
            "background": "#ECEFF1",
            "grid": "#CFD8DC",
            "dock": "#1565C0",
            "route": "#4CAF50",
            "route_text": "#FFF",
            "agv": "#FF5722",
            "agv_stroke": "#FFF",
            "machine_working": "#FFD700",
            "machine_idle": "#90EE90",
            "machine_broken": "#F44336",
            "machine_maintenance": "#FF9800",
            "machine_stroke": "#333",
        },
    }


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="数据集选取与转换")
    parser.add_argument("--list", action="store_true", help="列出可用数据集")
    parser.add_argument(
        "--fjsp", default=None, help="FJSP 实例 (如 k1, kacem/k1, brandimarte/mk01)"
    )
    parser.add_argument("--map", default=None, help="MAPF 地图名")
    parser.add_argument("--agvs", type=int, default=4, help="AGV 数量")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--data-dir", default="./dataset", help="数据集目录")
    parser.add_argument(
        "--output", default=None, help="输出路径 (默认: dataset/grid_factory.json)"
    )

    args = parser.parse_args()

    if args.list:
        datasets = list_available_datasets(args.data_dir)
        print(json.dumps(datasets, ensure_ascii=False, indent=2))
    elif args.fjsp:
        output = args.output or str(Path(args.data_dir) / "grid_factory.json")
        config = select_and_convert(
            fjsp=args.fjsp,
            map_name=args.map,
            num_agvs=args.agvs,
            seed=args.seed,
            data_dir=args.data_dir,
            output_path=output,
        )
        print(
            f"转换完成: {len(config['jobs']['job_list'])} jobs, "
            f"{len(config['topology']['machines'])} machines, "
            f"{len(config['agvs'])} AGVs"
        )
    else:
        parser.print_help()
