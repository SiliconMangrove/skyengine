"""
MAPF 侧指标采集：

标量指标（每步）：
- swap_conflict_count: AGV 交换位置冲突数（edge swap conflict）
- tasked_stationary_count: 有任务但未移动的 AGV 数（不含正常取放料停留）
- agv_loaded_utilization: AGV 载货行驶时间占比（有效运输效率）
- agv_busy_utilization: AGV 非空闲时间占比（运输 + 取放料 + 任务阻塞）
- agv_travel_time_total: 总行驶时间（loaded + empty）
- agv_waiting_time_total: 总空闲时间
- agv_handling_time_total: 总取放料停留时间
- agv_task_waiting_time_total: 有任务但未移动的阻塞等待时间

热力图指标（累积，通过 get_heatmaps() 获取）：
- transit_heatmap: 每个 cell 被 AGV 进入的次数（流量 / 经过次数）
- occupancy_heatmap: 每个 cell 被 AGV 占据的总步数（拥堵）
"""

import numpy as np


def _mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


# ---------------------------------------------------------------------------
# 热力图状态管理 — 存在 penv 上，obstacles 变化时自动重置
# ---------------------------------------------------------------------------

def _ensure_heatmaps(penv) -> dict:
    """
    获取或初始化热力图累积器。
    obstacles 引用变化（env reset 后重建网格）时自动清零。
    """
    obstacles = penv.grid.obstacles
    hm = getattr(penv, '_mapf_heatmaps', None)
    if hm is not None and getattr(penv, '_mapf_hm_obstacles', None) is obstacles:
        return hm

    h, w = obstacles.shape
    penv._mapf_heatmaps = {
        'transit': np.zeros((h, w), dtype=np.int32),
        'occupancy': np.zeros((h, w), dtype=np.int32),
    }
    penv._mapf_hm_obstacles = obstacles
    return penv._mapf_heatmaps


def get_heatmaps(penv) -> dict:
    """
    获取当前累积的热力图（numpy 矩阵）。

    Returns
    -------
    {
        'transit':     np.ndarray (h, w) — 每个 cell 被进入的次数
        'occupancy':   np.ndarray (h, w) — 每个 cell 被占据的总步数
        'obstacles':   np.ndarray (h, w) — 障碍物掩码 (1=障碍)
    }
    """
    hm = _ensure_heatmaps(penv)
    return {
        'transit': hm['transit'].copy(),
        'occupancy': hm['occupancy'].copy(),
        'obstacles': penv.grid.obstacles.copy(),
    }


# ---------------------------------------------------------------------------
# 每步采集
# ---------------------------------------------------------------------------

def collect(penv, t: int, prev_positions) -> dict:
    """
    Parameters
    ----------
    penv : PogemaLifeLongWithAssign
    t : int — 当前 env_timeline
    prev_positions : list[tuple] | None — 上一步各 AGV 的位置
    """
    num_agents = penv.grid_config.num_agents
    positions = [tuple(p) for p in penv.grid.positions_xy]

    swap_conflict_count = 0
    tasked_stationary_count = 0

    # --- 更新热力图 ---
    hm = _ensure_heatmaps(penv)
    for i in range(num_agents):
        x, y = int(positions[i][0]), int(positions[i][1])

        # 占据：每步 +1
        hm['occupancy'][y, x] += 1

        # 经过：仅当 AGV 移入新 cell 时 +1
        if prev_positions is not None:
            px, py = int(prev_positions[i][0]), int(prev_positions[i][1])
            if (x, y) != (px, py):
                hm['transit'][y, x] += 1

    # --- 标量指标 ---
    if prev_positions is not None:
        for i in range(num_agents):
            for j in range(i + 1, num_agents):
                # 两 AGV 交换位置 = edge swap conflict
                if prev_positions[i] == positions[j] and prev_positions[j] == positions[i]:
                    swap_conflict_count += 1
            # 有任务但没动 — 不严格等于 blocking，
            # 可能已到达目标、等待 pickup/dropoff、被阻塞等
            has_task = penv.agv_current_task[i] is not None
            phase = (
                penv.agv_last_step_phase[i]
                if hasattr(penv, "agv_last_step_phase") and i < len(penv.agv_last_step_phase)
                else "IDLE"
            )
            if has_task and phase not in {"PICKING", "DROPPING"} and prev_positions[i] == positions[i]:
                tasked_stationary_count += 1

    # AGV 统计
    agv_stats = getattr(penv, 'agv_stats', {})
    if not agv_stats:
        agv_stats = {i: {"loaded": 0, "empty": 0, "idle": 0, "handling": 0, "task_waiting": 0, "dist": 0}
                     for i in range(num_agents)}

    total_loaded = sum(s.get('loaded', 0) for s in agv_stats.values())
    total_empty = sum(s.get('empty', 0) for s in agv_stats.values())
    total_idle = sum(s.get('idle', 0) for s in agv_stats.values())
    total_handling = sum(s.get('handling', 0) for s in agv_stats.values())
    total_task_waiting = sum(s.get('task_waiting', 0) for s in agv_stats.values())
    total_time = total_loaded + total_empty + total_idle + total_handling + total_task_waiting

    return {
        "swap_conflict_count": swap_conflict_count,
        "tasked_stationary_count": tasked_stationary_count,
        "agv_loaded_utilization": total_loaded / max(total_time, 1),
        "agv_busy_utilization": (
            total_loaded + total_empty + total_handling + total_task_waiting
        ) / max(total_time, 1),
        "agv_travel_time_total": total_loaded + total_empty,
        "agv_waiting_time_total": total_idle,
        "agv_handling_time_total": total_handling,
        "agv_task_waiting_time_total": total_task_waiting,
    }
