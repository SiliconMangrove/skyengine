"""
运输时间预估器 (TransferTimeEstimator)

为 FJSP 调度器提供机器间运输时间的预估，替代默认的恒零函数。

三层预估策略:
  Level 1: BFS 栅格最短路（考虑障碍物）— 基础层
  Level 2: 历史实际运输时间校正 — 在线反馈层
  Level 3: 拥堵溢价 — 进阶层

使用方式:
    # 在环境 reset 后创建
    obstacles = pogema_env.grid.get_obstacles()
    estimator = TransferTimeEstimator(machines, obstacles)

    # 传给 Coordinator → GreedyJobSolver
    coordinator = Coordinator(
        job_solver="greedy",
        job_solver_kwargs={"transfer_time_estimator": estimator},
    )

    # 仿真中持续更新
    estimator.update_from_task(task)  # Level 2 反馈
"""

from collections import defaultdict
from typing import List, Dict, Tuple, Optional

import numpy as np


def bfs_shortest_path(obstacles: np.ndarray, start: Tuple[int, int], end: Tuple[int, int]) -> float:
    """
    在栅格地图上用 BFS 计算两点之间的最短路径长度。
    obstacles[y][x] = 1 表示障碍，0 表示可通行。

    Returns:
        最短路径步数，不可达时返回 float('inf')
    """
    if start == end:
        return 0.0

    h, w = obstacles.shape
    sy, sx = start[1], start[0]  # 注意: location 是 (x, y)，grid 是 [y][x]
    ey, ex = end[1], end[0]

    # 边界检查
    if not (0 <= sy < h and 0 <= sx < w and 0 <= ey < h and 0 <= ex < w):
        return float('inf')
    if obstacles[sy][sx] or obstacles[ey][ex]:
        return float('inf')

    from collections import deque
    queue = deque([(sx, sy, 0)])
    visited = {(sx, sy)}
    directions = [(0, 1), (0, -1), (1, 0), (-1, 0)]

    while queue:
        x, y, dist = queue.popleft()
        for dx, dy in directions:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in visited:
                if obstacles[ny][nx]:
                    continue
                if nx == ex and ny == ey:
                    return float(dist + 1)
                visited.add((nx, ny))
                queue.append((nx, ny, dist + 1))

    return float('inf')


class TransferTimeEstimator:
    """
    运输时间预估器

    结合 BFS 最短路 + 历史反馈校正，为 FJSP 提供机器间运输时间预估。
    可直接作为 transfer_time_estimator(from_machine_id, to_machine_id) 调用。
    """

    def __init__(
        self,
        machines: list,
        obstacles: np.ndarray,
        use_feedback: bool = True,
        feedback_window: int = 50,
        congestion_weight: float = 0.0,
    ):
        """
        Args:
            machines: Machine 对象列表，需要有 .id 和 .location 属性
            obstacles: 栅格障碍物矩阵 (np.ndarray, shape=[H, W], 0/1)
            use_feedback: 是否启用历史反馈校正
            feedback_window: 反馈滑动窗口大小
            congestion_weight: 拥堵溢价权重 (0 = 不启用)
        """
        self.machines = {m.id: m for m in machines}
        self.obstacles = obstacles
        self.use_feedback = use_feedback
        self.feedback_window = feedback_window
        self.congestion_weight = congestion_weight

        # Level 1: BFS 预计算距离矩阵
        self._base_distances: Dict[Tuple[int, int], float] = {}
        self._precompute_distances()

        # Level 2: 历史反馈
        self._actual_times: Dict[Tuple[int, int], list] = defaultdict(list)
        self._correction_ratios: Dict[Tuple[int, int], float] = {}

    def _precompute_distances(self):
        """预计算所有机器对之间的 BFS 最短路径"""
        machine_ids = list(self.machines.keys())
        for i in machine_ids:
            for j in machine_ids:
                if i == j:
                    self._base_distances[(i, j)] = 0.0
                elif (j, i) in self._base_distances:
                    # 无向图，距离对称
                    self._base_distances[(i, j)] = self._base_distances[(j, i)]
                else:
                    loc_i = self.machines[i].location
                    loc_j = self.machines[j].location
                    dist = bfs_shortest_path(self.obstacles, loc_i, loc_j)
                    self._base_distances[(i, j)] = dist

    def __call__(self, from_machine_id: int, to_machine_id: int) -> float:
        """
        预估从机器 from_machine_id 到 to_machine_id 的运输时间。

        Args:
            from_machine_id: 起始机器 ID
            to_machine_id: 目标机器 ID

        Returns:
            预估运输时间（步数）
        """
        if from_machine_id == to_machine_id:
            return 0.0

        # Level 1: 基础 BFS 最短路
        base = self._base_distances.get((from_machine_id, to_machine_id), float('inf'))

        # Level 2: 历史反馈校正
        if self.use_feedback and (from_machine_id, to_machine_id) in self._correction_ratios:
            ratio = self._correction_ratios[(from_machine_id, to_machine_id)]
            return base * ratio

        return base

    def update_from_task(self, task):
        """
        从已完成的搬运任务中更新历史反馈。

        Args:
            task: RoutingTask 对象，需要有 assign_time, finish_time, source, destination
        """
        if not self.use_feedback:
            return
        if task.assign_time < 0 or task.finish_time < 0:
            return
        if task.source is None or task.destination is None:
            return

        actual_time = task.finish_time - task.assign_time
        if actual_time <= 0:
            return

        # 找到 source/destination 对应的机器 ID
        from_id = self._loc_to_machine_id(task.source)
        to_id = self._loc_to_machine_id(task.destination)

        if from_id is not None and to_id is not None and from_id != to_id:
            key = (from_id, to_id)
            self._actual_times[key].append(actual_time)
            # 滑动窗口
            if len(self._actual_times[key]) > self.feedback_window:
                self._actual_times[key] = self._actual_times[key][-self.feedback_window:]
            # 更新校正系数
            base = self._base_distances.get(key, float('inf'))
            if base > 0 and base < float('inf'):
                avg_actual = np.mean(self._actual_times[key])
                self._correction_ratios[key] = avg_actual / base

    def _loc_to_machine_id(self, loc: Tuple[int, int]) -> Optional[int]:
        """根据坐标反查机器 ID"""
        for mid, m in self.machines.items():
            if m.location == loc:
                return mid
        return None

    def get_stats(self) -> Dict:
        """获取预估器统计信息，用于调试和日志"""
        stats = {
            "num_machine_pairs": len(self._base_distances),
            "avg_base_distance": 0.0,
            "num_corrected_pairs": len(self._correction_ratios),
            "correction_ratios": dict(self._correction_ratios),
        }
        valid_dists = [v for v in self._base_distances.values() if v < float('inf') and v > 0]
        if valid_dists:
            stats["avg_base_distance"] = float(np.mean(valid_dists))
        return stats
