"""
耦合指标采集（核心 motivation 指标）：
- transport_delay_ratio: 运输延迟比 = (actual - bfs_shortest) / bfs_shortest
- transport_blocking_delay_mean: 运输绝对延迟均值（时间步）
- machine_waiting_for_inbound_transfer_ratio: 当前被 AGV 饿住的机器占比（每步快照）

注：operation_queue_waiting_time_mean 已统一由 fjsp.py 采集，此处不再重复。
"""

from collections import deque


def _mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


# ---------------------------------------------------------------------------
# BFS 最短路：每个机器位置跑一次 BFS，缓存所有机器间真实最短距离
# ---------------------------------------------------------------------------

def _bfs(obstacles, start) -> dict:
    """从 start 做 BFS，返回 {(x,y): dist}（只走空地）。"""
    h, w = obstacles.shape
    dist = {start: 0}
    q = deque([start])
    while q:
        x, y = q.popleft()
        d = dist[(x, y)]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and obstacles[ny, nx] == 0 and (nx, ny) not in dist:
                dist[(nx, ny)] = d + 1
                q.append((nx, ny))
    return dist


def _get_bfs_cache(penv) -> dict:
    """
    惰性预计算：对每台机器位置跑 BFS，缓存 {(src_loc): {(dst_loc): distance}}。
    当 obstacles 引用变化时（reset 后重建网格）自动重算。
    """
    obstacles = penv.grid.obstacles
    if getattr(penv, '_bfs_cache_obstacles', None) is obstacles:
        return penv._bfs_cache

    machine_locs = [tuple(m.location) for m in penv.machines]
    cache = {}
    for loc in machine_locs:
        dist_map = _bfs(obstacles, loc)
        cache[loc] = {other: dist_map[other] for other in machine_locs if other in dist_map}

    penv._bfs_cache_obstacles = obstacles
    penv._bfs_cache = cache
    return cache


def _bfs_distance(cache, source, destination) -> int:
    """查表获取两位置间的 BFS 最短路，不可达返回 0。"""
    if source is None or destination is None:
        return 0
    return cache.get(tuple(source), {}).get(tuple(destination), 0)


def collect(penv, t: int) -> dict:
    """
    Parameters
    ----------
    penv : PogemaLifeLongWithAssign
    t : int — 当前 env_timeline
    """
    bfs_cache = _get_bfs_cache(penv)

    # --- Transport Delay ---
    # shortest 使用 BFS 在网格上计算无冲突最短路（考虑障碍物），
    # 比 Manhattan 更精确，跨地图可比。
    # actual = finish_time - assign_time 包含 AGV 空驶接货时间，
    # 真正的运输延迟仅是 source → destination 段。
    delay_ratios = []
    delay_absolutes = []
    for agent_tasks in penv.agv_finished_tasks:
        for task in agent_tasks:
            if (task.create_time is not None and task.finish_time is not None
                    and task.assign_time is not None
                    and task.create_time >= 0 and task.finish_time >= 0
                    and task.assign_time >= 0):
                actual = task.finish_time - task.assign_time
                shortest = _bfs_distance(bfs_cache, task.source, task.destination)
                if shortest > 0:
                    delay_ratios.append((actual - shortest) / shortest)
                    delay_absolutes.append(actual - shortest)

    # --- Machine Waiting for Inbound Transfer ---
    # 每步统计：当前空闲 + 无队列 + 有 AGV 正在送往该机器 的机器数 / 总机器数
    # 历史平均值即为累积饥饿比率
    machines = penv.machines
    active_destinations = set()
    for task in penv.active_transfers:
        if task.destination is not None:
            active_destinations.add(tuple(task.destination))

    waiting_count = 0
    for m in machines:
        if (m.current_op is None
                and (not hasattr(m, 'input_queue') or not m.input_queue)
                and tuple(m.location) in active_destinations):
            waiting_count += 1

    return {
        "transport_delay_ratio": _mean(delay_ratios),
        "transport_blocking_delay_mean": _mean(delay_absolutes),
        "machine_waiting_for_inbound_transfer_ratio": waiting_count / max(len(machines), 1),
    }


def episode_summary(penv) -> dict:
    """Episode 结束后的汇总指标"""
    t = getattr(penv, 'env_timeline', 0)
    jobs = penv.jobs
    completed = [j for j in jobs if getattr(j, 'is_completed', False)]
    completion_times = [j.completion_time for j in completed if j.completion_time > 0]

    # completed_makespan: 最后一个完成 job 的完成时间（仅统计已完成的）
    completed_makespan = max(completion_times) if completion_times else 0

    # full_makespan: 所有 job 都完成时等于 completed_makespan，
    # 否则用当前 episode 时间（代表未全部完成时的实际跨度）
    all_completed = len(completed) == len(jobs) and len(jobs) > 0
    full_makespan = completed_makespan if all_completed else t

    success_rate = len(completed) / max(len(jobs), 1)
    total_tardiness = 0.0
    weighted_tardiness = 0.0
    for job in jobs:
        due = getattr(job, "due", None)
        if due is None or float(due) < 0:
            continue
        completion = float(job.completion_time) if job.completion_time > 0 else float(t)
        tardiness = max(0.0, completion - float(due))
        priority_weight = max(1.0, float(getattr(job, "priority", 0) or 0) / 100.0)
        total_tardiness += tardiness
        weighted_tardiness += priority_weight * tardiness

    return {
        "completed_makespan": completed_makespan,
        "full_makespan": full_makespan,
        "success_rate": success_rate,
        "job_completion_rate": success_rate,
        "total_tardiness": total_tardiness,
        "weighted_tardiness": weighted_tardiness,
    }
