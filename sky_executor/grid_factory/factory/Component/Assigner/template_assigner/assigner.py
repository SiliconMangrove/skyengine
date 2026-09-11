# 根据当前环境中的pending_transfer的任务，和agv、machine本身的特点和信息，
# 从pending_transfer任务与可行的machine中按照一定策略构建一个,分配给当前的agent

from abc import ABC
from typing import Dict, Any, List, Tuple

import numpy as np

try:
    from scipy.optimize import linear_sum_assignment
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


class Assigner(ABC):
    """
    任务分配器基类（Assigner）
    - 输入：环境观测信息 obs
    - 输出：为 agents 分配的任务 (transfers) 或 None
    """

    def plan(self, obs: Dict[str, Any]):
        """
        输入：
            obs: 环境观测信息，包括：
                {
                    "pending_transfers": list[RoutingTask],
                    "machines": list[Machine],
                    "agents": list[AGV],
                }

        输出：
            actions: 包含分配结果的字典，格式如下：
                {
                    "assignments": {agv_id: RoutingTask | None, ...},
                    "pending_transfers": list[RoutingTask]
                }
        """
        pass

    @staticmethod
    def greedy_assign(
        cost_matrix: np.ndarray,
        all_agents: List,
        idle_agents: List,
        transfers: List,
    ) -> Tuple[Dict, List]:
        """
        AGV 中心贪心匹配。
        对每个空闲 AGV，选当前代价最小的未分配 task。

        Args:
            cost_matrix: [n_idle, n_task] 代价矩阵
            all_agents: 全部 AGV 列表（用于构建完整 assignments）
            idle_agents: 空闲 AGV 列表
            transfers: 待分配任务列表

        Returns:
            (assignments, remaining_tasks)
        """
        n_idle, n_task = cost_matrix.shape
        available = list(range(n_task))
        assignments = {a.id: None for a in all_agents}

        for i, agent in enumerate(idle_agents):
            if not available:
                break
            best_j = min(available, key=lambda j: cost_matrix[i, j])
            available.remove(best_j)
            assignments[agent.id] = transfers[best_j]

        remaining = [transfers[j] for j in available]
        return assignments, remaining

    @staticmethod
    def hungarian_match(
        cost_matrix: np.ndarray,
        all_agents: List,
        idle_agents: List,
        transfers: List,
    ) -> Tuple[Dict, List]:
        """
        匈牙利算法全局最优匹配（scipy fallback 贪心）。

        Args:
            cost_matrix: [n_idle, n_task] 代价矩阵
            all_agents: 全部 AGV 列表
            idle_agents: 空闲 AGV 列表
            transfers: 待分配任务列表

        Returns:
            (assignments, remaining_tasks)
        """
        n_idle, n_task = cost_matrix.shape
        assignments = {a.id: None for a in all_agents}

        if HAS_SCIPY and n_idle > 1 and n_task > 1:
            cost = np.array(cost_matrix, dtype=np.float64)
            row_ind, col_ind = linear_sum_assignment(cost)
            matched_pairs = list(zip(row_ind, col_ind))
        else:
            matched_pairs = Assigner._greedy_match(cost_matrix, n_idle, n_task)

        assigned_task_indices = set()
        for agv_idx, task_idx in matched_pairs:
            if agv_idx < n_idle and task_idx < n_task:
                assignments[idle_agents[agv_idx].id] = transfers[task_idx]
                assigned_task_indices.add(task_idx)

        remaining = [t for i, t in enumerate(transfers) if i not in assigned_task_indices]
        return assignments, remaining

    @staticmethod
    def _greedy_match(cost_matrix, n_agv, n_task):
        """贪心匹配 (scipy 不可用时的 fallback)"""
        used_agvs = set()
        used_tasks = set()
        pairs = []

        edges = []
        for i in range(n_agv):
            for j in range(n_task):
                edges.append((cost_matrix[i, j], i, j))
        edges.sort()

        for cost, i, j in edges:
            if i not in used_agvs and j not in used_tasks:
                pairs.append((i, j))
                used_agvs.add(i)
                used_tasks.add(j)
                if len(pairs) == min(n_agv, n_task):
                    break

        return pairs
