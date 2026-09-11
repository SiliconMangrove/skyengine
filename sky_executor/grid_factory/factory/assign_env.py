from pogema.envs import PogemaLifeLong, GridConfig
import random
from typing import Optional, List, Dict, Union
from sky_executor.grid_factory.factory.Utils.structure import (
    Machine,
    Job,
    RoutingTask,
    AGV,
    Operation,
)
from sky_executor.grid_factory.factory.Utils.processing_time import ProcessingTimeSampler
import pickle, json


AGV_PHASE_IDLE = "IDLE"
AGV_PHASE_TO_PICKUP = "TO_PICKUP"
AGV_PHASE_PICKING = "PICKING"
AGV_PHASE_TO_DROPOFF = "TO_DROPOFF"
AGV_PHASE_DROPPING = "DROPPING"
HANDLING_PHASES = {AGV_PHASE_PICKING, AGV_PHASE_DROPPING}


class PogemaLifeLongWithAssign(PogemaLifeLong):
    def __init__(
        self,
        grid_config: Optional[GridConfig] = None,  # [修复] 避免使用可变对象作为默认参数
        random_target=False,
        padding_init_request=True,
        debug_mode=False,
        processing_time_config: Optional[dict] = None,
        material_handling_config: Optional[dict] = None,
    ):
        if grid_config is None:
            grid_config = GridConfig(num_agents=2)

        super().__init__(grid_config)
        self.debug_mode = debug_mode
        self.env_timeline = 0
        self.random_target = random_target
        self.padding_init_request = padding_init_request
        self.processing_time_sampler = ProcessingTimeSampler.from_config(processing_time_config)
        handling_config = material_handling_config or {}
        self.pickup_dwell_steps = int(handling_config.get("pickup_dwell_steps", 2))
        self.dropoff_dwell_steps = int(handling_config.get("dropoff_dwell_steps", 2))
        raw_source = handling_config.get("raw_material_source")
        self.raw_material_source = tuple(raw_source) if raw_source is not None else None

        # machine机器相关信息
        self.machines: List[Machine] = []
        self.activated_machines: List[Machine] = []
        self.machine_process_time = {}
        self.hash_machines = {}

        # job任务相关信息
        self.jobs: List[Job] = []
        self.hash_operations: Dict[tuple[int, int], Operation] = {}
        self.pending_transfers: List[RoutingTask] = []
        self.transfers_to_assign: List[RoutingTask] = []
        self.active_transfers: List[RoutingTask] = []
        self.active_transfers: List[RoutingTask] = []
        # 存放那些 Solver 发过来了，但因为前置工序没做完，暂时不能执行的任务
        self.buffered_tasks: List[RoutingTask] = []

        # AGV相关的路由信息
        self.agv_current_task: List[Union[RoutingTask, None]] = [
            None
        ] * self.grid_config.num_agents
        self.agv_finished_tasks: List[List[RoutingTask]] = [
            [] for _ in range(self.grid_config.num_agents)
        ]
        self.agv_status: List[str] = ["OK"] * self.grid_config.num_agents
        self.agv_repair_remaining: List[int] = [0] * self.grid_config.num_agents
        self.agv_down_reason: List[Union[str, None]] = [None] * self.grid_config.num_agents
        self.agv_task_phase: List[str] = [AGV_PHASE_IDLE] * self.grid_config.num_agents
        self.agv_handling_remaining: List[int] = [0] * self.grid_config.num_agents
        self.agv_loaded: List[bool] = [False] * self.grid_config.num_agents
        self.agv_last_step_phase: List[str] = [AGV_PHASE_IDLE] * self.grid_config.num_agents
        self._assigned_this_step = set()

        self.event_epoch = 0
        self.map_epoch = 0
        self.machine_epoch = 0
        self.agv_epoch = 0
        self.job_epoch = 0
        self.last_events = []
        self.event_metrics = {}

        # 初始化统计信息
        self.agv_stats = {
            i: {"dist": 0, "loaded": 0, "empty": 0, "idle": 0, "handling": 0, "task_waiting": 0}
            for i in range(self.grid_config.num_agents)
        }

    def get_agv_info(self):
        agv_list = []
        # 确保统计数据存在
        if not hasattr(self, "agv_stats"):
            self.agv_stats = {
                i: {"dist": 0, "loaded": 0, "empty": 0, "idle": 0, "handling": 0, "task_waiting": 0}
                for i in range(self.grid_config.num_agents)
            }

        for idx in range(self.grid_config.num_agents):
            stats = self.agv_stats[idx]
            agv_list.append(
                AGV(
                    id=idx,
                    pos=self._to_public_xy(self.grid.positions_xy[idx]),
                    current_task=self.agv_current_task[idx],
                    finished_tasks=self.agv_finished_tasks[idx],
                    status=self.agv_status[idx] if idx < len(self.agv_status) else "OK",
                    repair_remaining=(
                        self.agv_repair_remaining[idx]
                        if idx < len(self.agv_repair_remaining) else 0
                    ),
                    down_reason=(
                        self.agv_down_reason[idx]
                        if idx < len(self.agv_down_reason) else None
                    ),
                    task_phase=self.agv_task_phase[idx],
                    handling_remaining=self.agv_handling_remaining[idx],
                    loaded=self.agv_loaded[idx],
                    total_distance=stats["dist"],
                    total_loaded_time=stats["loaded"],
                    total_empty_time=stats["empty"],
                    total_idle_time=stats["idle"],
                    total_handling_time=stats.get("handling", 0),
                    total_task_waiting_time=stats.get("task_waiting", 0),
                )
            )
        return agv_list

    def reset(
        self,
        seed: Optional[int] = None,
        return_info: bool = True,
        options: Optional[dict] = None,
    ):
        # 确保 possible_targets_xy 存在 (防止在 machine_reset 之前调用 reset 报错)
        if (
            not hasattr(self.grid_config, "possible_targets_xy")
            or not self.grid_config.possible_targets_xy
        ):
            # 如果没有机器位置信息，暂时用全图或当前位置兜底
            self.grid_config.possible_targets_xy = list(self.grid.positions_xy)

        super().reset(seed, return_info, options)

        for idx in range(self.grid_config.num_agents):
            if self.padding_init_request and self.grid_config.possible_targets_xy:
                self.grid.finishes_xy[idx] = self._to_internal_xy(
                    random.choice(self.grid_config.possible_targets_xy)
                )
                self.agv_current_task[idx] = None
            else:
                self.grid.finishes_xy[idx] = self.grid.positions_xy[idx]
                self.agv_current_task[idx] = None

        self.agv_status = ["OK"] * self.grid_config.num_agents
        self.agv_repair_remaining = [0] * self.grid_config.num_agents
        self.agv_down_reason = [None] * self.grid_config.num_agents
        self.agv_task_phase = [AGV_PHASE_IDLE] * self.grid_config.num_agents
        self.agv_handling_remaining = [0] * self.grid_config.num_agents
        self.agv_loaded = [False] * self.grid_config.num_agents
        self.agv_last_step_phase = [AGV_PHASE_IDLE] * self.grid_config.num_agents
        self.agv_stats = {
            i: {"dist": 0, "loaded": 0, "empty": 0, "idle": 0, "handling": 0, "task_waiting": 0}
            for i in range(self.grid_config.num_agents)
        }

        if return_info:
            return self._obs(), self._get_infos()
        return self._obs()

    def create_hash_machines(self):
        hash_machines = {}
        for m in self.machines:
            hash_machines[m.location] = m
        return hash_machines

    def _to_internal_xy(self, pos):
        """Convert map-local coordinates to Pogema's obs_radius-padded grid coordinates."""
        if pos is None:
            return None
        offset = getattr(self.grid_config, "obs_radius", 0) or 0
        x, y = pos
        return (int(x) + offset, int(y) + offset)

    def _to_public_xy(self, pos):
        """Convert Pogema's padded coordinates to map-local coordinates."""
        if pos is None:
            return None
        offset = getattr(self.grid_config, "obs_radius", 0) or 0
        x, y = pos
        return (int(x) - offset, int(y) - offset)

    def create_hash_operations(self):
        hash_operations = {}
        for job in self.jobs:
            for op in job.ops:
                hash_operations[(job.job_id, op.op_id)] = op
        return hash_operations

    def machine_reset(self, machines: list[Machine]):
        self.machines = machines if machines else []
        self.grid_config.possible_targets_xy = [m.location for m in self.machines]
        self.activated_machines = []
        self.machine_process_time = {m.id: 0 for m in self.machines}

        # [修复] 为每个机器初始化输入缓冲区 (Input Queue)，防止任务覆盖
        for m in self.machines:
            if not hasattr(m, "input_queue"):
                m.input_queue = []
            else:
                m.input_queue.clear()  # 清空旧数据
            m.current_op = None  # 确保初始状态为空
            m.suspended_ops = []
            m.urgent_reservations = set()
            m.status = "OK"
            m.repair_remaining = 0
            m.down_reason = None

        self.hash_machines = self.create_hash_machines()

        obs = {
            "machines": self.machines,
            "pending_transfers": self.pending_transfers,
            "agents": self.get_agv_info(),
        }
        infos = {"num_machines": len(self.machines)}
        return obs, infos

    def job_reset(self, jobs: list[Job]):
        self.jobs = jobs if jobs else []
        self.pending_transfers.clear()
        self.active_transfers.clear()
        self.transfers_to_assign.clear()
        self.buffered_tasks.clear()
        self.processing_time_sampler.reset()
        for job in self.jobs:
            job.completion_time = -1.0
            for op in job.ops:
                nominal = op.nominal_proc_time if op.nominal_proc_time is not None else op.proc_time
                op.proc_time = nominal
                op.sampled_proc_time = None
                op.processing_time_distribution = None
                op.assigned_machine = None
                op.assigned_robot = None
                op.assigned_node = None
                op.status = "PENDING"
                op.arrive_machine_at = -1
                op.start_process_at = -1
                op.finish_process_at = -1
                op.wait_for_machine_time = 0.0
                op.remaining_proc_time = None
                op.accumulated_process_time = 0.0
                op.preemption_count = 0
        self.hash_operations = self.create_hash_operations()
        self.event_epoch = 0
        self.map_epoch = 0
        self.machine_epoch = 0
        self.agv_epoch = 0
        self.job_epoch = 0
        self.last_events = []
        self.event_metrics = {}

        obs = {"jobs": self.jobs, "machines": self.machines}
        infos = {"num_jobs": len(self.jobs)}
        return obs, infos

    def machine_process(self):
        """
        [修复] 机器处理逻辑：
        1. 处理当前任务。
        2. 如果完成或空闲，检查输入队列 (input_queue)。
        3. 自动管理 activated_machines 列表。
        """
        # 使用副本遍历，因为我们可能会在循环中从 activated_machines 移除机器
        for m in list(self.activated_machines):
            if getattr(m, "status", "OK") != "OK":
                continue

            # --- 阶段 1: 尝试加载新任务 ---
            if m.current_op is None:
                urgent_index = next(
                    (idx for idx, op in enumerate(m.input_queue)
                     if getattr(op, "priority", 0) >= 200),
                    None,
                )
                if urgent_index is not None:
                    m.current_op = m.input_queue.pop(urgent_index)
                    self.machine_process_time[m.id] = 0
                elif m.urgent_reservations:
                    # 特急物料尚在运输，机器为它保留，不启动普通工序。
                    self.machine_process_time[m.id] = 0
                    continue
                elif m.suspended_ops:
                    m.current_op = m.suspended_ops.pop(0)
                    self.machine_process_time[m.id] = 0
                elif m.input_queue:
                    # 普通队列按优先级取任务；同优先级保持到达顺序。
                    best_index = max(
                        range(len(m.input_queue)),
                        key=lambda idx: getattr(m.input_queue[idx], "priority", 0),
                    )
                    m.current_op = m.input_queue.pop(best_index)
                    self.machine_process_time[m.id] = 0
                else:
                    # 既没有当前任务，队列也是空的 -> 休眠
                    self.activated_machines.remove(m)
                    self.machine_process_time[m.id] = 0
                    continue

            # --- 阶段 2: 执行加工逻辑 ---
            current_op = m.current_op
            # 获取 Operation 的真身引用
            hash_op = (current_op.job_id, current_op.op_id)
            real_op = self.hash_operations[hash_op]

            # [Metrics] 记录开始时间
            if self.machine_process_time[m.id] == 0:
                if real_op.remaining_proc_time is not None:
                    real_op.proc_time = float(real_op.remaining_proc_time)
                    real_op.remaining_proc_time = None
                else:
                    nominal = (
                        real_op.nominal_proc_time
                        if real_op.nominal_proc_time is not None
                        else real_op.proc_time
                    )
                    sampled, distribution = self.processing_time_sampler.sample_for_operation(
                        real_op,
                        m.id,
                        nominal,
                    )
                    real_op.proc_time = sampled
                    real_op.sampled_proc_time = sampled
                    real_op.processing_time_distribution = distribution
                if real_op.start_process_at < 0:
                    real_op.start_process_at = self.env_timeline
                real_op.status = "PROCESSING"

            # 推进时间
            self.machine_process_time[m.id] += 1
            m.total_work_time += 1

            # --- 阶段 3: 检查完成 ---
            if self.machine_process_time[m.id] >= current_op.proc_time:
                # [Metrics] 记录完成
                real_op.finish_process_at = self.env_timeline
                real_op.status = "FINISHED"
                real_op.accumulated_process_time += self.machine_process_time[m.id]

                # 记录历史
                m.processed_ops_count += 1
                m.history_ops.append(
                    (
                        real_op.job_id,
                        real_op.op_id,
                        real_op.start_process_at,
                        real_op.finish_process_at,
                    )
                )

                # 任务完成，清空槽位
                m.current_op = None
                self.machine_process_time[m.id] = 0

                # 检查 Job 是否全部完成 (Metrics)
                job = next(job for job in self.jobs if job.job_id == real_op.job_id)
                hash_ops_completed = all(
                    self.hash_operations[(job.job_id, op.op_id)].status == "FINISHED"
                    for op in job.ops
                )

                if hash_ops_completed and job.completion_time == -1:
                    job.completion_time = self.env_timeline
                    if self.debug_mode:
                        print(
                            f"  - [SUCCESS] Job {job.job_id} finished at {self.env_timeline}"
                        )

                # 注意：这里不从 activated_machines 移除
                # 下一次循环会检查 input_queue，如果有新任务则继续，没有则移除

    def _resolve_transfer(self, t: dict) -> RoutingTask:
        """将 HTTP solver 返回的 transfer dict 转为 RoutingTask，解析 machine_id → 实际坐标"""
        fields = {}
        for k, v in t.items():
            if k in ("source", "destination") and isinstance(v, list):
                fields[k] = tuple(v)
            else:
                fields[k] = v

        # destination: [machine_id, 0] → 机器的实际 location
        dest = fields.get("destination")
        if dest and len(dest) == 2 and dest[1] == 0:
            mid = dest[0]
            if 0 <= mid < len(self.machines):
                fields["destination"] = self.machines[mid].location

        # source: [machine_id, 0] → 机器的实际 location; [-1, 0] = depot 保持不变
        src = fields.get("source")
        if src and len(src) == 2 and src[0] >= 0 and src[1] == 0:
            mid = src[0]
            if 0 <= mid < len(self.machines):
                fields["source"] = self.machines[mid].location

        return RoutingTask(**fields)

    def _resolve_routing_task(self, task: RoutingTask) -> RoutingTask:
        """将本地 solver 生成的 RoutingTask 中的 (machine_id, 0) 解析为实际坐标"""
        src = task.source
        if src and isinstance(src, tuple) and len(src) == 2 and src[0] >= 0 and src[1] == 0:
            mid = src[0]
            if 0 <= mid < len(self.machines):
                task.source = self.machines[mid].location

        dst = task.destination
        if dst and isinstance(dst, tuple) and len(dst) == 2 and dst[0] >= 0 and dst[1] == 0:
            mid = dst[0]
            if 0 <= mid < len(self.machines):
                task.destination = self.machines[mid].location

        return task

    def _reserve_for_urgent_transfer(self, task: RoutingTask) -> None:
        """优先级 200 的运输任务可暂停目标机器上的非特急加工。"""
        if getattr(task, "priority", 0) < 200:
            return
        target_machine = self.hash_machines.get(task.destination)
        if target_machine is None:
            return
        task_key = (task.job_id, task.op_id)
        target_machine.urgent_reservations.add(task_key)
        current = target_machine.current_op
        if current is not None and getattr(current, "priority", 0) < 200:
            elapsed = float(self.machine_process_time.get(target_machine.id, 0))
            remaining = max(0.0, float(current.proc_time) - elapsed)
            if elapsed > 0 and remaining > 0:
                current.accumulated_process_time += elapsed
                current.remaining_proc_time = remaining
                current.preemption_count += 1
                current.status = "SUSPENDED"
                target_machine.suspended_ops.append(current)
                target_machine.current_op = None
                self.machine_process_time[target_machine.id] = 0
        if target_machine not in self.activated_machines:
            self.activated_machines.append(target_machine)

    def job_step(self, actions=None):
        if actions is not None:
            new_transfers = actions.get("transfer_requests", [])
            resolved = []
            for t in new_transfers:
                if isinstance(t, dict):
                    task = self._resolve_transfer(t)
                elif isinstance(t, RoutingTask):
                    task = self._resolve_routing_task(t)
                else:
                    task = t
                if isinstance(task, RoutingTask):
                    self._reserve_for_urgent_transfer(task)
                resolved.append(task)
            self.pending_transfers = resolved

        rewards = {}
        terminations = {"job_done": self.job_all_done()}
        truncations = {}
        infos = {}
        observations = {
            "jobs": self.jobs,
            "machines": self.machines,
        }
        return observations, rewards, terminations, truncations, infos

    def _prepare_transfer(self, task: RoutingTask) -> RoutingTask:
        """Normalize the physical pickup source independently of solver conventions."""
        if task.op_id == 0:
            if self.raw_material_source is None:
                task.pickup_required = False
            else:
                task.source = self.raw_material_source
                task.pickup_required = True
            return task

        task.pickup_required = True
        job = next((item for item in self.jobs if item.job_id == task.job_id), None)
        if job is not None and 0 < task.op_id < len(job.ops):
            previous_machine_id = job.ops[task.op_id - 1].assigned_machine
            if previous_machine_id is not None and 0 <= previous_machine_id < len(self.machines):
                task.source = self.machines[previous_machine_id].location
        return task

    def _set_agv_phase(self, agent_idx: int, phase: str, remaining: int = 0) -> None:
        self.agv_task_phase[agent_idx] = phase
        self.agv_handling_remaining[agent_idx] = max(0, int(remaining))

    def _set_agv_target(self, agent_idx: int, target) -> None:
        if target is not None:
            self.grid.finishes_xy[agent_idx] = self._to_internal_xy(target)

    def _finish_pickup(self, agent_idx: int, transfer: RoutingTask) -> None:
        transfer.pickup_finish_time = self.env_timeline
        self.agv_loaded[agent_idx] = True
        self._set_agv_phase(agent_idx, AGV_PHASE_TO_DROPOFF)
        self._set_agv_target(agent_idx, transfer.destination)

    def _finish_dropoff(self, agent_idx: int, transfer: RoutingTask) -> bool:
        if not self._deliver(agent_idx, transfer):
            return False
        self.agv_loaded[agent_idx] = False
        self._set_agv_phase(agent_idx, AGV_PHASE_IDLE)
        self.grid.finishes_xy[agent_idx] = self.grid.positions_xy[agent_idx]
        return True

    def _settle_arrival(self, agent_idx: int) -> None:
        """Enter a handling phase, resolving zero-duration phases immediately."""
        while True:
            transfer = self.agv_current_task[agent_idx]
            if transfer is None:
                return
            position = tuple(self.grid.positions_xy[agent_idx])
            phase = self.agv_task_phase[agent_idx]

            if phase == AGV_PHASE_TO_PICKUP:
                if position != self._to_internal_xy(transfer.source):
                    return
                transfer.pickup_start_time = self.env_timeline
                if self.pickup_dwell_steps > 0:
                    self._set_agv_phase(agent_idx, AGV_PHASE_PICKING, self.pickup_dwell_steps)
                    self._set_agv_target(agent_idx, transfer.source)
                    return
                self._finish_pickup(agent_idx, transfer)
                continue

            if phase == AGV_PHASE_TO_DROPOFF:
                if position != self._to_internal_xy(transfer.destination):
                    return
                transfer.dropoff_start_time = self.env_timeline
                if self.dropoff_dwell_steps > 0:
                    self._set_agv_phase(agent_idx, AGV_PHASE_DROPPING, self.dropoff_dwell_steps)
                    self._set_agv_target(agent_idx, transfer.destination)
                    return
                self._finish_dropoff(agent_idx, transfer)
            return

    def _assign_transfer(self, agent_idx: int, transfer: RoutingTask) -> None:
        transfer = self._prepare_transfer(transfer)
        transfer.assign_time = self.env_timeline
        transfer.assigned_agent_id = agent_idx
        self.agv_current_task[agent_idx] = transfer
        self._assigned_this_step.add(agent_idx)
        if transfer not in self.active_transfers:
            self.active_transfers.append(transfer)

        if transfer.pickup_required:
            self.agv_loaded[agent_idx] = False
            self._set_agv_phase(agent_idx, AGV_PHASE_TO_PICKUP)
            self._set_agv_target(agent_idx, transfer.source)
        else:
            self.agv_loaded[agent_idx] = True
            self._set_agv_phase(agent_idx, AGV_PHASE_TO_DROPOFF)
            self._set_agv_target(agent_idx, transfer.destination)
        self._settle_arrival(agent_idx)

    def _advance_handling(self, agent_idx: int, phase_at_step_start: str) -> None:
        if phase_at_step_start not in HANDLING_PHASES:
            return
        if self.agv_status[agent_idx] != "OK":
            return
        transfer = self.agv_current_task[agent_idx]
        if transfer is None or self.agv_task_phase[agent_idx] != phase_at_step_start:
            return

        remaining = max(0, self.agv_handling_remaining[agent_idx] - 1)
        self.agv_handling_remaining[agent_idx] = remaining
        if remaining > 0:
            target = transfer.source if phase_at_step_start == AGV_PHASE_PICKING else transfer.destination
            self._set_agv_target(agent_idx, target)
            return

        if phase_at_step_start == AGV_PHASE_PICKING:
            self._finish_pickup(agent_idx, transfer)
        else:
            self._finish_dropoff(agent_idx, transfer)

    def task_step(self, action: dict):
        # === Step 0: 获取输入 ===
        self._assigned_this_step.clear()
        assignments = action.get("assignments", {})
        # 上一步未分配的任务 (可能因为没有 AGV)
        last_step_unassigned = action.get("pending_transfers", [])

        # 来自 Solver 的新请求 (基于时间的)
        new_requests_from_solver = self.pending_transfers

        # 给新任务打时间戳
        for task in new_requests_from_solver:
            task.create_time = self.env_timeline

        # 清空 pending，防止重复
        self.pending_transfers = []

        # === [核心修复] Step 1: 物理约束检查 (Gatekeeping) ===
        # 将新任务加入缓冲池
        self.buffered_tasks.extend(new_requests_from_solver)

        ready_to_assign = []
        still_buffered = []

        for task in self.buffered_tasks:
            self._prepare_transfer(task)
            if self._check_physical_precondition(task):
                ready_to_assign.append(task)
            else:
                still_buffered.append(task)

        # 更新缓冲池 (剩下的继续等)
        self.buffered_tasks = still_buffered

        # 只有物理上就绪的任务，才会被加入待分配列表
        # 待分配列表 = 上次没分配完的 + 这次刚就绪的
        self.transfers_to_assign = last_step_unassigned + ready_to_assign

        # === Step 3: 机器处理 (先处理机器，可能腾出位置或者消耗队列) ===
        self.machine_process()

        # === Step 4: AGV 逻辑 ===
        infos = [dict() for _ in range(self.grid_config.num_agents)]

        for agent_idx in range(self.grid_config.num_agents):
            agv_pos = tuple(self.grid.positions_xy[agent_idx])
            current_transfer = self.agv_current_task[agent_idx]
            agv_down = (
                hasattr(self, "agv_status")
                and agent_idx < len(self.agv_status)
                and self.agv_status[agent_idx] != "OK"
            )

            if agv_down:
                infos[agent_idx]["is_active"] = self.grid.is_active[agent_idx]
                infos[agent_idx]["agv_status"] = self.agv_status[agent_idx]
                infos[agent_idx]["repair_remaining"] = self.agv_repair_remaining[agent_idx]
                continue

            # --- Case A: 到达当前阶段目标，进入取料/放料停留 ---
            if current_transfer is not None:
                self._settle_arrival(agent_idx)

            # --- Case B: AGV 空闲分配新任务 ---
            if self.agv_current_task[agent_idx] is None:
                if self.random_target:
                    if self.grid_config.possible_targets_xy:
                        self.grid.finishes_xy[agent_idx] = self._to_internal_xy(
                            random.choice(self.grid_config.possible_targets_xy)
                        )
                    continue

                if assignments.get(agent_idx) is not None:
                    self._assign_transfer(agent_idx, assignments[agent_idx])

            infos[agent_idx]["is_active"] = self.grid.is_active[agent_idx]

        obs = {
            "machines": self.machines,
            "pending_transfers": self.transfers_to_assign,
            "agents": self.get_agv_info(),
            "env_timeline": self.env_timeline,
            "obstacle_grid": self.grid.obstacles,
            "grid_height": self.grid.obstacles.shape[0],
            "grid_width": self.grid.obstacles.shape[1],
            "event_epoch": getattr(self, "event_epoch", 0),
            "map_epoch": getattr(self, "map_epoch", 0),
            "machine_epoch": getattr(self, "machine_epoch", 0),
            "agv_epoch": getattr(self, "agv_epoch", 0),
            "job_epoch": getattr(self, "job_epoch", 0),
            "events": getattr(self, "last_events", []),
            "event_metrics": getattr(self, "event_metrics", {}),
            "agv_status": list(getattr(self, "agv_status", [])),
            "agv_repair_remaining": list(getattr(self, "agv_repair_remaining", [])),
            "agv_task_phase": list(getattr(self, "agv_task_phase", [])),
            "agv_handling_remaining": list(getattr(self, "agv_handling_remaining", [])),
            "agv_loaded": list(getattr(self, "agv_loaded", [])),
        }
        terminated = [False] * self.grid_config.num_agents
        truncated = [False] * self.grid_config.num_agents
        return obs, [], terminated, truncated, infos

    def step(self, action: list):
        prev_positions = self.grid.positions_xy.copy()
        phase_at_step_start = list(self.agv_task_phase)
        self.agv_last_step_phase = phase_at_step_start

        patched_action = list(action or [])
        if len(patched_action) < self.grid_config.num_agents:
            patched_action.extend([0] * (self.grid_config.num_agents - len(patched_action)))
        for idx, phase in enumerate(phase_at_step_start):
            if (
                phase in HANDLING_PHASES
                or self.agv_status[idx] != "OK"
                or idx in self._assigned_this_step
            ):
                patched_action[idx] = 0

        rewards = []
        infos = [dict() for _ in range(self.grid_config.num_agents)]
        self.move_agents(patched_action)
        obs = self._obs()
        terminated = [False] * self.grid_config.num_agents
        truncated = [False] * self.grid_config.num_agents

        if not hasattr(self, "agv_stats"):
            self.agv_stats = {
                i: {"dist": 0, "loaded": 0, "empty": 0, "idle": 0, "handling": 0, "task_waiting": 0}
                for i in range(self.grid_config.num_agents)
            }

        for idx in range(self.grid_config.num_agents):
            current_pos = self.grid.positions_xy[idx]
            prev_pos = prev_positions[idx]
            dist = abs(current_pos[0] - prev_pos[0]) + abs(current_pos[1] - prev_pos[1])

            stats = self.agv_stats[idx]
            stats["dist"] += dist

            has_task = self.agv_current_task[idx] is not None
            is_moving = dist > 0
            phase = phase_at_step_start[idx]
            is_down = self.agv_status[idx] != "OK"

            if phase in HANDLING_PHASES and not is_down:
                stats["handling"] = stats.get("handling", 0) + 1
            elif has_task and is_moving and phase == AGV_PHASE_TO_DROPOFF:
                stats["loaded"] += 1
            elif is_moving:
                stats["empty"] += 1
            elif has_task:
                stats["task_waiting"] = stats.get("task_waiting", 0) + 1
            else:
                stats["idle"] += 1

        for idx, phase in enumerate(phase_at_step_start):
            self._advance_handling(idx, phase)

        self.env_timeline += 1
        return obs, rewards, terminated, truncated, infos

    def get_state(self) -> dict:
        grid_state = {
            "positions_xy": list(self.grid.positions_xy),
            "finishes_xy": list(self.grid.finishes_xy),
            "is_active": dict(self.grid.is_active) if isinstance(self.grid.is_active, dict) else list(self.grid.is_active),
        }

        # 由于 Machine 对象是自定义类，pickle 会自动保存其属性包括 input_queue
        state_dict = {
            "env_timeline": self.env_timeline,
            "machines": self.machines,
            "jobs": self.jobs,
            "activated_machines": self.activated_machines,
            "machine_process_time": self.machine_process_time,
            "pending_transfers": self.pending_transfers,
            "buffered_tasks": self.buffered_tasks,  # [新增]
            "transfers_to_assign": self.transfers_to_assign,
            "active_transfers": self.active_transfers,
            "agv_current_task": self.agv_current_task,
            "agv_finished_tasks": self.agv_finished_tasks,
            "agv_status": list(getattr(self, "agv_status", [])),
            "agv_repair_remaining": list(getattr(self, "agv_repair_remaining", [])),
            "agv_down_reason": list(getattr(self, "agv_down_reason", [])),
            "agv_task_phase": list(getattr(self, "agv_task_phase", [])),
            "agv_handling_remaining": list(getattr(self, "agv_handling_remaining", [])),
            "agv_loaded": list(getattr(self, "agv_loaded", [])),
            "agv_last_step_phase": list(getattr(self, "agv_last_step_phase", [])),
            "agv_stats": getattr(self, "agv_stats", None),
            "event_epoch": getattr(self, "event_epoch", 0),
            "map_epoch": getattr(self, "map_epoch", 0),
            "machine_epoch": getattr(self, "machine_epoch", 0),
            "agv_epoch": getattr(self, "agv_epoch", 0),
            "job_epoch": getattr(self, "job_epoch", 0),
            "last_events": getattr(self, "last_events", []),
            "event_metrics": getattr(self, "event_metrics", {}),
            "grid_state": grid_state,
        }
        return state_dict

    def set_state(self, state_files):
        if isinstance(state_files, dict):
            state_dict = state_files
        else:
            try:
                with open(state_files, "rb") as f:
                    state_dict = pickle.load(f)
            except Exception:
                with open(state_files, "r", encoding="utf-8") as f:
                    state_dict = json.load(f)

        self.env_timeline = state_dict["env_timeline"]
        self.machines = state_dict["machines"]
        self.jobs = state_dict["jobs"]
        self.activated_machines = state_dict["activated_machines"]
        self.machine_process_time = state_dict["machine_process_time"]
        self.pending_transfers = state_dict["pending_transfers"]
        self.buffered_tasks = state_dict.get("buffered_tasks", [])  # [新增]
        self.transfers_to_assign = state_dict["transfers_to_assign"]
        self.active_transfers = state_dict["active_transfers"]
        self.agv_current_task = state_dict["agv_current_task"]
        self.agv_finished_tasks = state_dict["agv_finished_tasks"]
        self.agv_status = state_dict.get(
            "agv_status", ["OK"] * self.grid_config.num_agents
        )
        self.agv_repair_remaining = state_dict.get(
            "agv_repair_remaining", [0] * self.grid_config.num_agents
        )
        self.agv_down_reason = state_dict.get(
            "agv_down_reason", [None] * self.grid_config.num_agents
        )
        self.agv_task_phase = state_dict.get(
            "agv_task_phase", [AGV_PHASE_IDLE] * self.grid_config.num_agents
        )
        self.agv_handling_remaining = state_dict.get(
            "agv_handling_remaining", [0] * self.grid_config.num_agents
        )
        self.agv_loaded = state_dict.get(
            "agv_loaded", [False] * self.grid_config.num_agents
        )
        self.agv_last_step_phase = state_dict.get(
            "agv_last_step_phase", list(self.agv_task_phase)
        )
        self.event_epoch = state_dict.get("event_epoch", 0)
        self.map_epoch = state_dict.get("map_epoch", 0)
        self.machine_epoch = state_dict.get("machine_epoch", 0)
        self.agv_epoch = state_dict.get("agv_epoch", 0)
        self.job_epoch = state_dict.get("job_epoch", 0)
        self.last_events = state_dict.get("last_events", [])
        self.event_metrics = state_dict.get("event_metrics", {})

        if state_dict.get("agv_stats") is not None:
            self.agv_stats = state_dict["agv_stats"]
        elif hasattr(self, "agv_stats"):
            del self.agv_stats

        g_state = state_dict["grid_state"]
        self.grid.positions_xy[:] = g_state["positions_xy"]
        self.grid.finishes_xy[:] = g_state["finishes_xy"]
        if isinstance(self.grid.is_active, dict):
            if isinstance(g_state["is_active"], dict):
                self.grid.is_active.update(g_state["is_active"])
            else:
                for i, v in enumerate(g_state["is_active"]):
                    self.grid.is_active[i] = bool(v)
        else:
            self.grid.is_active[:] = g_state["is_active"]

        self.hash_machines = self.create_hash_machines()
        self.hash_operations = self.create_hash_operations()

    def job_all_done(self):
        return all([job.is_completed for job in self.jobs])

    def _check_physical_precondition(self, task: RoutingTask) -> bool:
        """
        检查任务的物理前置条件是否满足。
        对于 JSSP，主要是检查前置工序是否 FINISHED。
        """
        # 第一道工序，无前置约束，直接通过
        if task.op_id == 0:
            return True

        # 获取 Job 信息
        # 注意：self.jobs 里的状态是由 machine_process 实时更新的
        job = next((job for job in self.jobs if job.job_id == task.job_id), None)
        if job is None:
            return False

        # 边界检查
        if task.op_id > 0 and task.op_id < len(job.ops):
            prev_op = job.ops[task.op_id - 1]
            # 只有前置工序彻底完成了，物料才存在，才能开始运输
            if prev_op.status == "FINISHED":
                return True
        else:
            # 异常 op_id
            return False

        return False

    def _deliver(self, agent_idx: int, transfer) -> bool:
        """AGV 卸货: 将物料放入目标机器的 input_queue"""
        # 用 transfer.destination 查找机器，不用 finishes_xy
        # 因为 pogema on_target="restart" 会自动将 finishes_xy 改为随机目标
        target_machine = self.hash_machines.get(transfer.destination, None)

        if target_machine is None:
            if self.debug_mode:
                print(
                    f"[WARNING] Agent {agent_idx} arrived at "
                    f"{transfer.destination} but no machine found."
                )
            return False

        # Metrics 记录
        transfer.finish_time = self.env_timeline
        hash_op = (transfer.job_id, transfer.op_id)
        real_op = self.hash_operations[hash_op]
        real_op.arrive_machine_at = self.env_timeline
        real_op.assigned_machine = target_machine.id

        # 解析 machine_options_with_time 获取机器实际加工时间
        if real_op.machine_options_with_time:
            for mid, pt in real_op.machine_options_with_time:
                if mid == target_machine.id:
                    real_op.nominal_proc_time = pt
                    real_op.proc_time = pt
                    real_op.sampled_proc_time = None
                    real_op.processing_time_distribution = None
                    break
        # 如果 machine_options_with_time 为空，保持原 proc_time 不变

        # 放入机器的 input_queue
        if not hasattr(target_machine, "input_queue"):
            target_machine.input_queue = []
        target_machine.input_queue.append(real_op)
        target_machine.urgent_reservations.discard((transfer.job_id, transfer.op_id))

        # 激活休眠机器
        if target_machine.id not in [m.id for m in self.activated_machines]:
            self.activated_machines.append(target_machine)

        # 记录任务完成
        self.agv_finished_tasks[agent_idx].append(transfer)
        if transfer in self.active_transfers:
            self.active_transfers.remove(transfer)
        self.agv_current_task[agent_idx] = None
        return True
