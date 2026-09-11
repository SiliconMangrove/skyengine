"""
统一特征提取器 — 所有调度器的唯一特征入口

提供两种模式:
1. extract()      — pairwise 模式: [n_idle, n_task, 8] + env_stats [7]
                    供规则式 Assigner (CouplingHungarian) 和 MoE Expert 使用
2. extract_raw()  — 节点特征模式: AGV / Task / Machine / Job / Map 全部原始状态
                    供 MoE-GRPO Encoder / GNN / Cross-Attention 使用
                    不做人为压缩，把全部信息都拿出来

架构:
    ComponentExtractor  — 第一类：从环境对象提取组件特征
    MetricsExtractor    — 第二类：从 MetricsHub 上一步指标读取 env_stats
    PosteriorExtractor  — 第三类：透传 extra_features（Phase 2-3 扩展）
    FeatureExtractor    — Facade，委托三个子组件，对外接口不变

=== Pairwise 特征 (8D) ===
0. distance           1. congestion         2. starvation
3. urgency            4. wait_time          5. competition
6. machine_ready_time 7. agv_workload

=== 节点特征维度 (extract_raw) ===
AGV    [10D]: pos_x, pos_y, is_idle, workload, total_distance,
            total_loaded_time, total_empty_time, total_idle_time,
            finished_task_count, has_task
Task    [9D]: src_x, src_y, dst_x, dst_y, has_dest, wait_time,
            urgency, create_time, n_candidate_machines
Machine [8D]: loc_x, loc_y, is_idle, has_history, total_work_time,
            processed_ops_count, current_op_remaining, current_op_proc_time
Job     [8D]: n_total_ops, n_finished_ops, n_pending_ops, n_processing_ops,
            completion_ratio, release_time, due_time, is_completed
Map     [4D]: grid_height, grid_width, obstacle_density,
            n_zones_with_machines

=== 全局环境统计量 (env_stats, 7D) ===
0. avg_congestion         1. starvation_ratio      2. avg_wait
3. pending_ratio          4. loaded_utilization     5. episode_progress
6. recent_transport_delay

设计文档: explore/featureextractor重构.md
"""

import numpy as np
from collections import Counter
from typing import Dict, Any, List, Optional, Tuple

from .env_const import MAX_N_IDLE, MAX_N_TASK


def _manhattan(p1, p2):
    if p1 is None or p2 is None:
        return float('inf')
    return abs(p1[0] - p2[0]) + abs(p1[1] - p2[1])


def _task_first_target(task):
    """Physical point an idle AGV must reach first for this transfer."""
    return task.source if getattr(task, "pickup_required", True) else task.destination


# 特征名 → 索引映射，供 compute_cost_matrix 使用
FEATURE_NAMES = [
    "distance",
    "congestion",
    "starvation",
    "urgency",
    "wait_time",
    "competition",
    "machine_ready_time",
    "agv_workload",
]
FEATURE_NAME_TO_IDX = {name: i for i, name in enumerate(FEATURE_NAMES)}

# 模块级常量 — 下游直接 from ... import FEATURE_DIM 使用
FEATURE_DIM = len(FEATURE_NAMES)


# ======================================================================
# 第一类：ComponentExtractor — 组件特征
# ======================================================================

class ComponentExtractor:
    """
    从环境当前状态的对象（AGV, Task, Machine, Job, Map）中提取单实体数值特征。
    不维护任何指标计算逻辑，也不计算 pairwise 等交叉特征。
    """

    # 节点特征维度
    AGV_DIM = 10
    TASK_DIM = 9
    MACHINE_DIM = 8
    JOB_DIM = 8
    MAP_DIM = 4

    def __init__(self):
        pass

    def encode_agvs(self, agents: List, env_timeline: int) -> np.ndarray:
        """编码 AGV 节点特征 [n_agv, 10]。"""
        n = len(agents)
        if n == 0:
            return np.zeros((0, self.AGV_DIM), dtype=np.float32)

        raw = np.zeros((n, self.AGV_DIM), dtype=np.float32)
        for i, a in enumerate(agents):
            pos = a.pos or (0, 0)
            raw[i, 0] = float(pos[0])
            raw[i, 1] = float(pos[1])
            raw[i, 2] = 1.0 if a.current_task is None else 0.0

            total_loaded = float(getattr(a, 'total_loaded_time', 0))
            total_all = (
                total_loaded
                + float(getattr(a, 'total_empty_time', 0))
                + float(getattr(a, 'total_idle_time', 0))
            )
            raw[i, 3] = total_loaded / max(total_all, 1)
            raw[i, 4] = float(getattr(a, 'total_distance', 0))
            raw[i, 5] = total_loaded
            raw[i, 6] = float(getattr(a, 'total_empty_time', 0))
            raw[i, 7] = float(getattr(a, 'total_idle_time', 0))
            raw[i, 8] = float(len(getattr(a, 'finished_tasks', [])))
            raw[i, 9] = 0.0 if a.current_task is None else 1.0

        return raw

    def encode_tasks(
        self, transfers: List, env_timeline: int
    ) -> np.ndarray:
        """编码 Task 节点特征 [n_task, 9]。"""
        n = len(transfers)
        if n == 0:
            return np.zeros((0, self.TASK_DIM), dtype=np.float32)

        job_pending_count = Counter(t.job_id for t in transfers)

        raw = np.zeros((n, self.TASK_DIM), dtype=np.float32)
        for i, t in enumerate(transfers):
            src = t.source or (0, 0)
            raw[i, 0] = float(src[0])
            raw[i, 1] = float(src[1])

            if t.destination is not None:
                raw[i, 2] = float(t.destination[0])
                raw[i, 3] = float(t.destination[1])
                raw[i, 4] = 1.0
            else:
                raw[i, 2] = -1.0
                raw[i, 3] = -1.0
                raw[i, 4] = 0.0

            create_time = t.create_time if t.create_time >= 0 else t.ready_time
            raw[i, 5] = max(0, float(env_timeline - create_time))
            raw[i, 6] = float(job_pending_count.get(t.job_id, 0))
            raw[i, 7] = float(create_time)
            raw[i, 8] = float(len(getattr(t, 'candidate_machines', [])))

        return raw

    def encode_machines(self, machines: List, env_timeline: int) -> np.ndarray:
        """编码 Machine 节点特征 [n_machine, 8]。"""
        n = len(machines)
        if n == 0:
            return np.zeros((0, self.MACHINE_DIM), dtype=np.float32)

        raw = np.zeros((n, self.MACHINE_DIM), dtype=np.float32)
        for i, m in enumerate(machines):
            loc = m.location or (0, 0)
            raw[i, 0] = float(loc[0])
            raw[i, 1] = float(loc[1])
            raw[i, 2] = 1.0 if m.current_op is None else 0.0
            raw[i, 3] = 1.0 if m.history_ops else 0.0
            raw[i, 4] = float(getattr(m, 'total_work_time', 0))
            raw[i, 5] = float(getattr(m, 'processed_ops_count', 0))

            if m.current_op is not None:
                proc_time = float(getattr(m.current_op, 'proc_time', 0))
                arrive_at = getattr(m.current_op, 'arrive_machine_at', -1)
                raw[i, 7] = proc_time
                if proc_time > 0 and arrive_at >= 0:
                    elapsed = max(0, env_timeline - arrive_at)
                    raw[i, 6] = max(0, proc_time - elapsed)
                else:
                    raw[i, 6] = proc_time

        return raw

    def encode_jobs(
        self, jobs: List, transfers: List, env_timeline: int
    ) -> np.ndarray:
        """编码 Job 节点特征 [n_job, 8]。"""
        n = len(jobs)
        if n == 0:
            return np.zeros((0, self.JOB_DIM), dtype=np.float32)

        transfer_job_count = Counter(t.job_id for t in transfers)

        raw = np.zeros((n, self.JOB_DIM), dtype=np.float32)
        for i, job in enumerate(jobs):
            ops = getattr(job, 'ops', [])
            n_total = len(ops)
            n_finished = sum(1 for op in ops if getattr(op, 'status', '') == 'FINISHED')
            n_processing = sum(1 for op in ops if getattr(op, 'status', '') == 'PROCESSING')
            n_pending = transfer_job_count.get(getattr(job, 'job_id', i), 0)

            raw[i, 0] = float(n_total)
            raw[i, 1] = float(n_finished)
            raw[i, 2] = float(n_pending)
            raw[i, 3] = float(n_processing)
            raw[i, 4] = n_finished / max(n_total, 1)
            raw[i, 5] = float(getattr(job, 'release', 0))
            raw[i, 6] = float(getattr(job, 'due', -1) or -1)
            raw[i, 7] = 1.0 if getattr(job, 'is_completed', False) else 0.0

        return raw

    def encode_map(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        """
        编码地图特征。

        Returns:
            {
                "map_features":      np.ndarray [4],  原始数值（不归一化）
                "obstacle_grid":     np.ndarray [H, W] | None,  原始地图矩阵（供后续 CNN）
                "grid_height":       int,
                "grid_width":        int,
            }

        map_features [4]:
            0. grid_height        地图高度（原始值）
            1. grid_width         地图宽度（原始值）
            2. obstacle_density   障碍物占比 [0, 1]
            3. n_zones_with_machines   有机器的区域数（原始值）
        """
        obstacle_grid = obs.get("obstacle_grid", None)
        grid_h = float(obs.get("grid_height", 0))
        grid_w = float(obs.get("grid_width", 0))
        grid_arr = None

        if obstacle_grid is not None:
            grid_arr = np.asarray(obstacle_grid)
            if grid_h == 0:
                grid_h = float(grid_arr.shape[0])
            if grid_w == 0:
                grid_w = float(grid_arr.shape[1])
            total_cells = grid_arr.size
            obstacle_count = int(np.sum(grid_arr > 0))
            obstacle_density = obstacle_count / max(total_cells, 1)
        else:
            agents = obs.get("agents", [])
            machines = obs.get("machines", [])
            all_positions = []
            for a in agents:
                if a.pos is not None:
                    all_positions.append(a.pos)
            for m in machines:
                if m.location is not None:
                    all_positions.append(m.location)

            if all_positions:
                xs = [p[0] for p in all_positions]
                ys = [p[1] for p in all_positions]
                grid_h = float(max(xs) + 2)
                grid_w = float(max(ys) + 2)
            obstacle_density = 0.0

        machines = obs.get("machines", [])
        if len(machines) > 0:
            machine_locs = set()
            for m in machines:
                loc = m.location
                if loc is not None:
                    zone = (loc[0] // 5, loc[1] // 5)
                    machine_locs.add(zone)
            n_zones = float(len(machine_locs))
        else:
            n_zones = 0.0

        map_features = np.array(
            [grid_h, grid_w, obstacle_density, n_zones],
            dtype=np.float32,
        )

        return {
            "map_features": map_features,
            "obstacle_grid": grid_arr,
            "grid_height": int(grid_h),
            "grid_width": int(grid_w),
        }


# ======================================================================
# 第二类：MetricsExtractor — 指标特征
# ======================================================================

class MetricsExtractor:
    """
    从 MetricsHub 上一步指标构造 env_stats [7]。
    全部从 obs["prev_metrics"] 读取，无 prev_metrics 时返回零向量。
    """

    ENV_STATS_DIM = 7

    def __init__(self, max_steps: int = 1):
        self.max_steps = max_steps

    def build_env_stats(self, obs: Dict[str, Any]) -> np.ndarray:
        """
        从 prev_metrics 构建 env_stats [7]。

        env_stats 维度:
            0. swap_conflict_ratio         swap 冲突数 / AGV 数 (mapf)
            1. starvation_ratio            1 - machine_utilization (fjsp)
            2. avg_wait_norm               operation_queue_waiting_time_mean / timeline (fjsp)
            3. pending_ratio               n_tasks / n_agv / 4 (obs)
            4. loaded_utilization          agv_loaded_utilization (mapf)
            5. episode_progress            timeline / max_steps (obs)
            6. transport_delay_ratio       transport_delay_ratio (coupling)
        """
        m = obs.get("prev_metrics") or {}
        agents = obs.get("agents", [])
        transfers = obs.get("pending_transfers", [])
        env_timeline = obs.get("env_timeline", 0)
        n_agv = max(len(agents), 1)
        n_tasks = len(transfers)

        return np.array([
            m.get("swap_conflict_count", 0.0) / n_agv,                       # 0
            1.0 - m.get("machine_utilization", 0.0),                          # 1
            m.get("operation_queue_waiting_time_mean", 0.0) / max(env_timeline, 1),  # 2
            min(n_tasks / n_agv / 4.0, 1.0),                                  # 3
            m.get("agv_loaded_utilization", 0.0),                              # 4
            min(env_timeline / max(self.max_steps, 1), 1.0),                   # 5
            min(abs(float(m.get("transport_delay_ratio", 0.0))), 1.0),         # 6
        ], dtype=np.float32)


# ======================================================================
# 第三类：PosteriorExtractor — 后验特征（Phase 2-3 扩展）
# ======================================================================

class PosteriorExtractor:
    """
    后验/额外特征提取器。

    包含：
    - pairwise 交叉特征 [n_idle, n_task, 8]：从多组件交叉计算，供规则 Assigner 使用
    - extra_features 透传：Phase 2-3 扩展 expert_reliability / bayesian 等

    Pairwise 不是单组件状态，而是「工程化的评分表」，属于第三类特征。
    """

    FEATURE_DIM = 8
    CONGESTION_RADIUS = 5
    COMPETITION_RADIUS = 8

    def __init__(
        self,
        distance_mode: str = "manhattan",
        bfs_distance_matrix: Optional[Dict[Tuple, float]] = None,
    ):
        self.distance_mode = distance_mode
        self.bfs_distance_matrix = bfs_distance_matrix

    def _distance(self, p1, p2) -> float:
        if self.distance_mode == "bfs" and self.bfs_distance_matrix is not None:
            key = (p1, p2)
            return self.bfs_distance_matrix.get(key, _manhattan(p1, p2))
        return _manhattan(p1, p2)

    def compute_pairwise(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        """
        计算 pairwise 特征矩阵 [n_idle, n_task, 8]。

        Returns:
            {
                "raw_features":   np.ndarray [n_idle, n_task, 8],
                "pair_features":  np.ndarray [n_idle, n_task, 8],
                "idle_agents":    List[AGV],
                "transfers":      List[RoutingTask],
                "machines":       List[Machine],
                "idle_indices":   List[int],
            }
        """
        agents: List = obs.get("agents", [])
        machines: List = obs.get("machines", [])
        transfers: List = obs.get("pending_transfers", [])
        env_timeline = obs.get("env_timeline", 0)

        idle_agents = [a for a in agents if a.current_task is None]
        idle_indices = [i for i, a in enumerate(agents) if a.current_task is None]

        n_idle = len(idle_agents)
        n_task = len(transfers)

        empty_pair = np.zeros((0, 0, self.FEATURE_DIM), dtype=np.float32)

        if n_idle == 0 or n_task == 0:
            return {
                "raw_features": empty_pair.copy(),
                "pair_features": empty_pair.copy(),
                "idle_agents": idle_agents,
                "transfers": transfers,
                "machines": machines,
                "idle_indices": idle_indices,
            }

        # ---- 预计算 ----
        all_agv_positions = [(a.id, a.pos) for a in agents]
        machine_at = {m.location: m for m in machines}
        job_pending_count = Counter(t.job_id for t in transfers)

        # task 级预计算：congestion 和 competition 对同一 task 所有 AGV 相同（或仅差自身）
        task_congestion = {}
        task_competition = {}
        for j, task in enumerate(transfers):
            first_target = _task_first_target(task)
            if first_target is None:
                task_congestion[j] = 0
                task_competition[j] = 0.0
                continue
            cong_count = 0
            comp_count = 0
            for ag_id, ag_pos in all_agv_positions:
                if self._distance(ag_pos, first_target) < self.CONGESTION_RADIUS:
                    cong_count += 1
            for ag in idle_agents:
                if self._distance(ag.pos, first_target) < self.COMPETITION_RADIUS:
                    comp_count += 1
            task_congestion[j] = cong_count
            task_competition[j] = float(max(0, comp_count - 1))

        # ---- 逐对计算 8D 特征 ----
        raw = np.zeros((n_idle, n_task, self.FEATURE_DIM), dtype=np.float32)

        for i, agent in enumerate(idle_agents):
            total_loaded = float(getattr(agent, 'total_loaded_time', 0))
            total_all = (
                total_loaded
                + float(getattr(agent, 'total_empty_time', 0))
                + float(getattr(agent, 'total_idle_time', 0))
            )
            agv_workload = total_loaded / max(total_all, 1)

            for j, task in enumerate(transfers):
                first_target = _task_first_target(task)
                # 0. distance
                if first_target is not None:
                    raw[i, j, 0] = self._distance(agent.pos, first_target)
                else:
                    raw[i, j, 0] = float('inf')

                # 1. congestion: task 级总数 - 自身是否在范围内
                cong = task_congestion.get(j, 0)
                if first_target is not None and self._distance(agent.pos, first_target) < self.CONGESTION_RADIUS:
                    cong = max(0, cong - 1)  # 排除自身
                raw[i, j, 1] = float(cong)

                # 2. starvation
                starvation_score = 0.0
                if task.destination is not None:
                    dest_machine = machine_at.get(task.destination)
                    if dest_machine is not None:
                        if dest_machine.current_op is None:
                            starvation_score += 1.0
                            if not dest_machine.history_ops:
                                starvation_score += 0.5
                raw[i, j, 2] = starvation_score

                # 3. urgency (MWKR)
                raw[i, j, 3] = float(job_pending_count.get(task.job_id, 0))

                # 4. wait_time
                wait_time = max(
                    0,
                    env_timeline - (task.create_time if task.create_time >= 0 else task.ready_time),
                )
                raw[i, j, 4] = float(wait_time)

                # 5. competition
                raw[i, j, 5] = task_competition.get(j, 0.0)

                # 6. machine_ready_time
                machine_ready_penalty = 0.0
                if task.destination is not None:
                    dest_machine = machine_at.get(task.destination)
                    if dest_machine is not None and dest_machine.current_op is not None:
                        cur_op = dest_machine.current_op
                        proc_time = float(getattr(cur_op, 'proc_time', 0))
                        if proc_time > 0:
                            arrive_at = getattr(cur_op, 'arrive_machine_at', -1)
                            if arrive_at >= 0:
                                elapsed = max(0, env_timeline - arrive_at)
                            else:
                                elapsed = 0
                            remaining = max(0, proc_time - elapsed)
                            travel_est = raw[i, j, 0]
                            machine_ready_penalty = max(0, remaining - travel_est)
                raw[i, j, 6] = machine_ready_penalty

                # 7. agv_workload
                raw[i, j, 7] = agv_workload

        return {
            "raw_features": raw,
            "pair_features": raw,
            "idle_agents": idle_agents,
            "transfers": transfers,
            "machines": machines,
            "idle_indices": idle_indices,
        }

    def extract_posterior(self, obs: Dict[str, Any]) -> Optional[Dict]:
        """提取后验特征。当前只透传 extra_features。"""
        return obs.get("extra_features", None)


# ======================================================================
# Facade: FeatureExtractor — 对外接口不变
# ======================================================================

class FeatureExtractor:
    """
    统一特征提取器 — 所有调度器的唯一特征入口。

    内部委托 ComponentExtractor + MetricsExtractor + PosteriorExtractor，
    对外接口与改造前完全一致。

    两种模式:
        extract()      — pairwise 特征 (供规则式 Assigner / MoE Expert)
        extract_raw()  — 全部节点原始状态 (供 Encoder / GNN / Cross-Attention)
    """

    # 保持常量在 FeatureExtractor 上，供下游直接引用
    FEATURE_DIM = PosteriorExtractor.FEATURE_DIM
    ENV_STATS_DIM = MetricsExtractor.ENV_STATS_DIM

    AGV_DIM = ComponentExtractor.AGV_DIM
    TASK_DIM = ComponentExtractor.TASK_DIM
    MACHINE_DIM = ComponentExtractor.MACHINE_DIM
    JOB_DIM = ComponentExtractor.JOB_DIM
    MAP_DIM = ComponentExtractor.MAP_DIM

    CONGESTION_RADIUS = PosteriorExtractor.CONGESTION_RADIUS
    COMPETITION_RADIUS = PosteriorExtractor.COMPETITION_RADIUS

    def __init__(
        self,
        distance_mode: str = "manhattan",
        bfs_distance_matrix: Optional[Dict[Tuple, float]] = None,
        max_steps: int = 1,
    ):
        self.distance_mode = distance_mode
        self.bfs_distance_matrix = bfs_distance_matrix
        self.max_steps = max_steps

        self._component = ComponentExtractor()
        self._metrics = MetricsExtractor(max_steps)
        self._posterior = PosteriorExtractor(distance_mode, bfs_distance_matrix)

    def extract(self, obs: Dict[str, Any], mode: str = "default") -> Dict[str, Any]:
        """
        提取 pairwise 特征矩阵 + 环境统计量。

        Returns:
            {
                "pair_features":  np.ndarray [n_idle, n_task, 8],
                "raw_features":   np.ndarray [n_idle, n_task, 8],
                "env_stats":      np.ndarray [7],
                "extra_features": Optional[Dict],
                "idle_agents":    List[AGV],
                "transfers":      List[RoutingTask],
                "machines":       List[Machine],
                "idle_indices":   List[int],
            }
        """
        pw = self._posterior.compute_pairwise(obs)
        env_stats = self._metrics.build_env_stats(obs)
        extra = self._posterior.extract_posterior(obs)

        return {
            "pair_features": pw["pair_features"],
            "raw_features": pw["raw_features"],
            "env_stats": env_stats,
            "extra_features": extra,
            "idle_agents": pw["idle_agents"],
            "transfers": pw["transfers"],
            "machines": pw["machines"],
            "idle_indices": pw["idle_indices"],
        }

    def extract_raw(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        """
        提取全部节点的原始状态特征（不做 pairwise 压缩）。

        Returns:
            {
                "agv_features":     np.ndarray [n_agv, 10],
                "task_features":    np.ndarray [n_task, 9],
                "machine_features": np.ndarray [n_machine, 8],
                "job_features":     np.ndarray [n_job, 8] | None,
                "map_features":     np.ndarray [4],
                "agents":           List[AGV],
                "transfers":        List[RoutingTask],
                "machines":         List[Machine],
                "jobs":             List[Job] | None,
                "idle_agents":      List[AGV],
                "idle_indices":     List[int],
                "task_job_ids":     List[int],
                "task_dest_machines": List[int|None],
                "pair_features":    np.ndarray [n_idle, n_task, 8],
                "raw_features":     np.ndarray [n_idle, n_task, 8],
                "env_stats":        np.ndarray [7],
                "extra_features":   Optional[Dict],
            }
        """
        agents: List = obs.get("agents", [])
        machines: List = obs.get("machines", [])
        transfers: List = obs.get("pending_transfers", [])
        jobs: List = obs.get("jobs", None)
        env_timeline = obs.get("env_timeline", 0)

        idle_agents = [a for a in agents if a.current_task is None]
        idle_indices = [i for i, a in enumerate(agents) if a.current_task is None]

        # ---- 节点特征 ----
        agv_features = self._component.encode_agvs(agents, env_timeline)
        task_features = self._component.encode_tasks(transfers, env_timeline)
        machine_features = self._component.encode_machines(machines, env_timeline)
        job_features = self._component.encode_jobs(jobs, transfers, env_timeline) if jobs else None
        map_result = self._component.encode_map(obs)
        map_features = map_result["map_features"]

        # ---- 关系信息 ----
        machine_loc_to_idx = {}
        for idx, m in enumerate(machines):
            machine_loc_to_idx[m.location] = idx

        task_job_ids = [t.job_id for t in transfers]
        task_dest_machines = []
        for t in transfers:
            if t.destination is not None:
                task_dest_machines.append(machine_loc_to_idx.get(t.destination, None))
            else:
                task_dest_machines.append(None)

        # ---- pairwise + env_stats ----
        pw = self._posterior.compute_pairwise(obs)
        env_stats = self._metrics.build_env_stats(obs)
        extra = self._posterior.extract_posterior(obs)

        return {
            "agv_features": agv_features,
            "task_features": task_features,
            "machine_features": machine_features,
            "job_features": job_features,
            "map_features": map_features,
            "agents": agents,
            "transfers": transfers,
            "machines": machines,
            "jobs": jobs,
            "idle_agents": idle_agents,
            "idle_indices": idle_indices,
            "task_job_ids": task_job_ids,
            "task_dest_machines": task_dest_machines,
            "pair_features": pw["pair_features"],
            "raw_features": pw["raw_features"],
            "env_stats": env_stats,
            "extra_features": extra,
            "obstacle_grid": map_result.get("obstacle_grid"),
            "grid_height": map_result.get("grid_height", 0),
            "grid_width": map_result.get("grid_width", 0),
        }

    @staticmethod
    def compute_cost_matrix(
        raw_features: np.ndarray,
        weights: Dict[str, float],
    ) -> np.ndarray:
        """
        从原始特征 + 权重字典构建代价矩阵。

        Args:
            raw_features: [n_idle, n_task, 8] 未归一化的原始特征
            weights: {"distance": 1.0, "congestion": 2.0, ...}
        """
        n_idle, n_task, feat_dim = raw_features.shape

        cost_matrix = np.zeros((n_idle, n_task), dtype=np.float32)
        for name, w in weights.items():
            idx = FEATURE_NAME_TO_IDX.get(name)
            if idx is not None and idx < feat_dim:
                col = raw_features[:, :, idx]
                # 处理 inf: 替换为有限值的最大值
                finite_mask = np.isfinite(col)
                if finite_mask.any():
                    finite_max = float(col[finite_mask].max())
                else:
                    finite_max = 0.0
                safe_col = np.where(finite_mask, col, finite_max)
                cost_matrix += w * safe_col

        return cost_matrix

    def _translate_feature_to_nn_input(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        将提取的节点特征转换为神经网络可用的 tensor 格式。
        自动 padding 到 (MAX_N_IDLE, MAX_N_TASK) 并生成 bool mask。
        """
        import torch

        idle_agents = features.get("idle_agents", [])
        idle_indices = features.get("idle_indices", [])
        transfers = features.get("transfers", [])

        agv_features = features.get("agv_features", np.zeros((0, self.AGV_DIM), dtype=np.float32))
        task_features = features.get("task_features", np.zeros((0, self.TASK_DIM), dtype=np.float32))
        pair_features = features.get("pair_features", np.zeros((0, 0, self.FEATURE_DIM), dtype=np.float32))
        env_stats = features.get("env_stats", np.zeros(self.ENV_STATS_DIM, dtype=np.float32))

        n_idle = len(idle_agents)
        n_task = len(transfers)

        # 只取空闲 AGV 的特征子集
        if n_idle > 0 and agv_features.shape[0] > 0:
            idle_agv_feat = agv_features[idle_indices]
        else:
            idle_agv_feat = np.zeros((0, self.AGV_DIM), dtype=np.float32)

        # 处理 pair_features 维度不匹配的情况
        if pair_features.shape[0] != n_idle or pair_features.shape[1] != n_task:
            pair_features = np.zeros((n_idle, n_task, self.FEATURE_DIM), dtype=np.float32)

        # ── 截断到 cap ──
        cap_idle = min(n_idle, MAX_N_IDLE)
        cap_task = min(n_task, MAX_N_TASK)

        idle_agv_feat = idle_agv_feat[:cap_idle]
        task_features_t = task_features[:cap_task] if task_features.shape[0] > 0 else task_features
        pair_features = pair_features[:cap_idle, :cap_task]

        n_idle_eff = cap_idle
        n_task_eff = cap_task

        # ── Padding + Mask ──
        padded_agv = np.zeros((MAX_N_IDLE, self.AGV_DIM), dtype=np.float32)
        padded_agv[:n_idle_eff] = idle_agv_feat
        agv_mask = np.zeros(MAX_N_IDLE, dtype=bool)
        agv_mask[:n_idle_eff] = True

        padded_task = np.zeros((MAX_N_TASK, self.TASK_DIM), dtype=np.float32)
        padded_task[:n_task_eff] = task_features_t
        task_mask = np.zeros(MAX_N_TASK, dtype=bool)
        task_mask[:n_task_eff] = True

        padded_pair = np.zeros((MAX_N_IDLE, MAX_N_TASK, self.FEATURE_DIM), dtype=np.float32)
        padded_pair[:n_idle_eff, :n_task_eff] = pair_features
        pair_mask = np.zeros((MAX_N_IDLE, MAX_N_TASK), dtype=bool)
        pair_mask[:n_idle_eff, :n_task_eff] = True

        idle_agents_capped = idle_agents[:cap_idle]
        transfers_capped = transfers[:cap_task]

        return {
            "agv_feat":   torch.from_numpy(padded_agv).float(),
            "task_feat":  torch.from_numpy(padded_task).float(),
            "pair_feat":  torch.from_numpy(padded_pair).float(),
            "env_stats":  torch.from_numpy(env_stats.copy()).float(),
            "agv_mask":   torch.from_numpy(agv_mask),
            "task_mask":  torch.from_numpy(task_mask),
            "pair_mask":  torch.from_numpy(pair_mask),
            "idle_agents":    idle_agents_capped,
            "transfers":      transfers_capped,
            "n_idle": n_idle_eff,
            "n_task": n_task_eff,
        }

    def _translate_nn_output_to_assignments(
        self,
        nn_output,
        agents: List,
        available_tasks: List,
        assignments: Dict,
    ) -> tuple:
        """将神经网络输出的分数矩阵转换为贪心分配结果。"""
        import torch

        idle_agents = [a for a in agents if a.current_task is None]
        n_idle = len(idle_agents)
        n_task = len(available_tasks)

        if n_idle == 0 or n_task == 0:
            for a in agents:
                if a.id not in assignments:
                    assignments[a.id] = None
            return assignments, available_tasks

        if isinstance(nn_output, torch.Tensor):
            scores = nn_output.detach().cpu()
            if scores.dim() == 1 or (scores.dim() == 2 and scores.shape[1] == 1):
                scores = scores.reshape(n_idle, n_task)
            elif scores.dim() != 2:
                scores = scores.reshape(n_idle, n_task)
        else:
            return assignments, available_tasks

        assigned_task_indices = set()
        for i, agent in enumerate(idle_agents):
            if i >= scores.shape[0]:
                assignments[agent.id] = None
                continue

            row = scores[i]
            mask = torch.full_like(row, float('-inf'))
            for j in range(row.shape[0]):
                if j not in assigned_task_indices:
                    mask[j] = row[j]

            best_j = torch.argmax(mask).item()
            if mask[best_j] == float('-inf'):
                assignments[agent.id] = None
                continue

            assigned_task_indices.add(best_j)
            assignments[agent.id] = available_tasks[best_j]

        remaining = [t for idx, t in enumerate(available_tasks) if idx not in assigned_task_indices]

        for a in agents:
            if a.id not in assignments:
                assignments[a.id] = None

        return assignments, remaining
