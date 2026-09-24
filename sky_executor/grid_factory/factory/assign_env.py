from pogema.envs import PogemaLifeLong, GridConfig
import random
from typing import Optional, List, Dict, Union
from collections import deque
from sky_executor.grid_factory.factory.Utils.structure import (
    Machine,
    Job,
    RoutingTask,
    AGV,
    Operation,
)
from sky_executor.grid_factory.factory.Utils.processing_time import ProcessingTimeSampler
from .rescheduling import ReschedulingActions
import pickle, json


AGV_PHASE_IDLE = "IDLE"
AGV_PHASE_TO_PICKUP = "TO_PICKUP"
AGV_PHASE_PICKING = "PICKING"
AGV_PHASE_TO_DROPOFF = "TO_DROPOFF"
AGV_PHASE_DROPPING = "DROPPING"
HANDLING_PHASES = {AGV_PHASE_PICKING, AGV_PHASE_DROPPING}


class PogemaLifeLongWithAssign(ReschedulingActions, PogemaLifeLong):
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
        self.padding_init_request = False
        self._episode_seed: int = int(grid_config.seed or 0)
        self._rng = random.Random(self._episode_seed)
        self.processing_time_sampler = ProcessingTimeSampler.from_config(processing_time_config)
        handling_config = material_handling_config or {}
        self.pickup_dwell_steps = int(handling_config.get("pickup_dwell_steps", 2))
        self.dropoff_dwell_steps = int(handling_config.get("dropoff_dwell_steps", 2))
        raw_source = handling_config.get("raw_material_source")
        self.raw_material_source = tuple(raw_source) if raw_source is not None else None
        sink = handling_config.get("finished_goods_destination")
        self.finished_goods_destination = tuple(sink) if sink is not None else None
        self.buffer_capacity: int = int(handling_config.get("buffer_capacity", 4))
        self.machine_buffer_capacities: dict = handling_config.get("machine_buffer_capacities", {})
        self.reschedule_count: int = 0
        self.reassigned_operation_count: int = 0
        self.reassigned_transport_count: int = 0
        self._shipping_sequence: int = -1

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
        self.agv_down_elapsed: list[int] = [0] * self.grid_config.num_agents
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
        self._pending_events: list[dict] = []
        self._in_step: bool = False
        self.event_metrics = {}

        # 初始化统计信息
        self.agv_down_elapsed = [0] * self.grid_config.num_agents
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
                    down_elapsed=self.agv_down_elapsed[idx],
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

        self._episode_seed = int(self.grid_config.seed or 0) if seed is None else int(seed)
        self._rng = random.Random(self._episode_seed)
        self.processing_time_sampler.reset(self._episode_seed)
        super().reset(seed, return_info, options)

        for idx in range(self.grid_config.num_agents):
            if self.padding_init_request and self.grid_config.possible_targets_xy:
                self.grid.finishes_xy[idx] = self._to_internal_xy(
                    self._rng.choice(self.grid_config.possible_targets_xy)
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
        self.agv_down_elapsed = [0] * self.grid_config.num_agents
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
            m.down_elapsed = 0
            m.buffer_capacity = int(self.machine_buffer_capacities.get(str(m.id), self.buffer_capacity))
            if m.buffer_capacity < 1:
                raise ValueError("machine buffer capacity must be positive")
            m.buffer_jobs = set()
            m.buffer_blocked_steps = 0

        self.hash_machines = self.create_hash_machines()

        obs = {
            "machines": self.machines,
            "pending_transfers": self.pending_transfers,
            "agents": self.get_agv_info(),
        }
        infos = {"num_machines": len(self.machines)}
        return obs, infos

    def job_reset(self, jobs: list[Job]):
        self._all_jobs = jobs if jobs else []
        self.jobs = [job for job in self._all_jobs if float(getattr(job, "release", 0.0) or 0.0) <= 0.0]
        self._future_jobs = [job for job in self._all_jobs if job not in self.jobs]
        self.pending_transfers.clear()
        self.active_transfers.clear()
        self.transfers_to_assign.clear()
        self.buffered_tasks.clear()
        self.reschedule_count = self.reassigned_operation_count = self.reassigned_transport_count = 0
        self._shipping_sequence = -1
        reachable: set[tuple[int, int]] = self._require_connected_layout()
        # Observation padding is outside the factory and cannot host material stations.
        offset: int = self.grid_config.obs_radius or 0
        cells: list[tuple[int, int]] = [self._to_public_xy((x, y))
            for x in range(offset, self.grid.obstacles.shape[0] - offset)
            for y in range(offset, self.grid.obstacles.shape[1] - offset)
            if self._to_public_xy((x, y)) in reachable and self._to_public_xy((x, y)) not in self.hash_machines]
        if not cells:
            raise ValueError("layout needs a traversable material station outside the machines")
        source: tuple[int, int] = self.raw_material_source if self.raw_material_source is not None else cells[0]
        destination: tuple[int, int] = self.finished_goods_destination if self.finished_goods_destination is not None else cells[-1]
        self.raw_material_source = source
        self.finished_goods_destination = destination
        for job in self._all_jobs:
            job.raw_material_source = job.raw_material_source or source
            job.finished_goods_destination = job.finished_goods_destination or destination
            job.material_location = job.raw_material_source
            job.carrier_id = job.transport_task_id = None
            job.delivered = False
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
                op.processed_time = 0.0
                op.transfer_requested = False
                op.deviation_reported = False
        self.hash_operations = self.create_hash_operations()
        self.event_epoch = 0
        self.map_epoch = 0
        self.machine_epoch = 0
        self.agv_epoch = 0
        self.job_epoch = 0
        self.last_events = []
        self._pending_events = []
        self._in_step = False
        self.event_metrics = {}

        obs = {"jobs": self.jobs, "machines": self.machines}
        infos = {"num_jobs": len(self.jobs)}
        return obs, infos

    def _release_due_jobs(self, timeline: float) -> None:
        due = [job for job in self._future_jobs if float(getattr(job, "release", 0.0) or 0.0) <= float(timeline)]
        if not due:
            return
        self._future_jobs = [job for job in self._future_jobs if job not in due]
        self.jobs.extend(due)
        for job in due:
            for operation in job.ops:
                self.hash_operations[(job.job_id, operation.op_id)] = operation
        self.job_epoch += 1
        for job in due:
            self.emit_event("urgent_job_arrival" if job.priority >= 200 else "job_release", {"job_id": job.job_id})

    def machine_process(self):
        for machine in list(self.activated_machines):
            if machine.status != "OK":
                continue
            if machine.current_op is not None and machine.current_op.status == "FINISHED":
                self._release_finished_operation(machine)
                continue
            if machine.current_op is None:
                if machine.urgent_reservations and not any(op.priority >= 200 for op in machine.input_queue):
                    continue
                if machine.input_queue:
                    index: int = max(range(len(machine.input_queue)), key=lambda i: machine.input_queue[i].priority)
                    operation = machine.input_queue.pop(index)
                elif machine.suspended_ops:
                    operation = machine.suspended_ops.pop(0)
                else:
                    self.activated_machines.remove(machine)
                    continue
                machine.current_op = operation
                machine.buffer_jobs.discard(operation.job_id)
                self.machine_process_time[machine.id] = 0.0
                if operation.remaining_proc_time is not None:
                    operation.proc_time = operation.remaining_proc_time
                    operation.remaining_proc_time = None
                else:
                    operation.proc_time, operation.processing_time_distribution = self.processing_time_sampler.sample_for_operation(
                        operation, machine.id, operation.nominal_proc_time or operation.proc_time)
                    operation.sampled_proc_time = operation.proc_time
                if operation.start_process_at < 0:
                    operation.start_process_at = self.env_timeline
                operation.status = "PROCESSING"
                self.emit_event("operation_started", {"job_id": operation.job_id, "op_id": operation.op_id, "machine_id": machine.id})
            operation = machine.current_op
            service: float = min(1.0, operation.proc_time - self.machine_process_time[machine.id])
            self.machine_process_time[machine.id] += service
            operation.processed_time += service
            machine.total_work_time += service
            if self.machine_process_time[machine.id] >= operation.proc_time:
                operation.finish_process_at = self.env_timeline + 1
                operation.status = "FINISHED"
                operation.accumulated_process_time = operation.processed_time
                machine.processed_ops_count += 1
                machine.history_ops.append((operation.job_id, operation.op_id, operation.start_process_at, operation.finish_process_at))
                self.emit_event("operation_completed", {"job_id": operation.job_id, "op_id": operation.op_id, "machine_id": machine.id}, step=self.env_timeline + 1)
                self._release_finished_operation(machine)
            elif operation.processed_time >= float(operation.nominal_proc_time or 0) and not operation.deviation_reported:
                operation.deviation_reported = True
                self.emit_event("processing_time_deviation", {"job_id": operation.job_id, "op_id": operation.op_id,
                                "elapsed_processing": operation.processed_time}, step=self.env_timeline + 1)

    def _release_finished_operation(self, machine: Machine) -> None:
        operation: Operation = machine.current_op
        if len(machine.buffer_jobs) >= machine.buffer_capacity:
            machine.buffer_blocked_steps += 1
            return
        machine.buffer_jobs.add(operation.job_id)
        machine.current_op = None
        self.machine_process_time[machine.id] = 0.0
        job: Job = next(job for job in self.jobs if job.job_id == operation.job_id)
        if operation.op_id == len(job.ops) - 1:
            task = RoutingTask(task_id=self._shipping_sequence, job_id=job.job_id, op_id=len(job.ops),
                               kind="finished_goods", source=job.material_location,
                               destination=job.finished_goods_destination, ready_time=operation.finish_process_at,
                               priority=job.priority, create_time=operation.finish_process_at)
            self._shipping_sequence -= 1
            self.buffered_tasks.append(task)

    def _resolve_transfer(self, value: dict) -> RoutingTask:
        fields: dict = dict(value)
        destination_id = fields.pop("destination_machine_id", None)
        source_id = fields.pop("source_machine_id", None)
        if destination_id is not None:
            fields["destination"] = self.machines[int(destination_id)].location
        if source_id is not None:
            fields["source"] = self.machines[int(source_id)].location
        return RoutingTask(**fields)

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
        self._release_due_jobs(self.env_timeline)
        actions = actions or {}
        requests: list[RoutingTask] = [self._resolve_transfer(item) if isinstance(item, dict) else item
                                      for item in actions.get("transfer_requests", [])]
        existing_ids: set[int] = {task.task_id for task in [*self.buffered_tasks, *self.transfers_to_assign, *self.active_transfers]}
        selected: set[tuple[int, int]] = set()
        for task in requests:
            key: tuple[int, int] = (task.job_id, task.op_id)
            if task.kind != "operation" or task.task_id < 0 or task.task_id in existing_ids or key in selected:
                raise ValueError("duplicate or invalid production transfer")
            operation: Operation = self.hash_operations[key]
            machine = self.hash_machines.get(task.destination)
            eligible: set[int] = set(operation.machine_options or [mid for mid, _ in operation.machine_options_with_time])
            if machine is None or machine.id not in eligible:
                raise ValueError("transfer destination is not an eligible machine")
            if operation.status != "PENDING" or operation.transfer_requested or operation.assigned_machine is not None:
                raise ValueError("operation already started or has a committed transfer")
            selected.add(key)
            existing_ids.add(task.task_id)
        for task in requests:
            operation = self.hash_operations[(task.job_id, task.op_id)]
            operation.transfer_requested = True
            operation.assigned_machine = self.hash_machines[task.destination].id
            task.create_time = self.env_timeline
            self.buffered_tasks.append(task)
            self._reserve_for_urgent_transfer(task)
        self.pending_transfers = []
        return {"jobs": self.jobs, "machines": self.machines}, {}, {"job_done": self.job_all_done()}, {}, {}

    def _prepare_transfer(self, task: RoutingTask) -> RoutingTask:
        job: Job = next(job for job in self.jobs if job.job_id == task.job_id)
        if job.material_location is not None:
            task.source = job.material_location
        task.pickup_required = True
        return task

    def _set_agv_phase(self, agent_idx: int, phase: str, remaining: int = 0) -> None:
        self.agv_task_phase[agent_idx] = phase
        self.agv_handling_remaining[agent_idx] = max(0, int(remaining))

    def _set_agv_target(self, agent_idx: int, target) -> None:
        if target is not None:
            self.grid.finishes_xy[agent_idx] = self._to_internal_xy(target)

    def _finish_pickup(self, agent_idx: int, transfer: RoutingTask) -> None:
        job: Job = next(job for job in self.jobs if job.job_id == transfer.job_id)
        source_machine = self.hash_machines.get(job.material_location)
        if source_machine is not None:
            source_machine.buffer_jobs.discard(job.job_id)
        job.material_location = None
        job.carrier_id = agent_idx
        transfer.pickup_finish_time = self.env_timeline
        self.agv_loaded[agent_idx] = True
        self._set_agv_phase(agent_idx, AGV_PHASE_TO_DROPOFF)
        self._set_agv_target(agent_idx, transfer.destination)
        self.emit_event("transport_picked_up", {"job_id": job.job_id, "task_id": transfer.task_id, "agv_id": agent_idx})

    def _finish_dropoff(self, agent_idx: int, transfer: RoutingTask) -> bool:
        if not self._deliver(agent_idx, transfer):
            # Waiting for buffer space is not physical handling. Allow the
            # loaded vehicle to yield the shared port to an outgoing pickup.
            self._set_agv_phase(agent_idx, AGV_PHASE_TO_DROPOFF)
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

            if phase == AGV_PHASE_DROPPING and self.agv_handling_remaining[agent_idx] == 0:
                self._finish_dropoff(agent_idx, transfer)
                return
            if phase == AGV_PHASE_TO_DROPOFF:
                if position != self._to_internal_xy(transfer.destination):
                    return
                if transfer.kind == "operation":
                    machine: Machine = self.hash_machines[transfer.destination]
                    if len(machine.buffer_jobs) >= machine.buffer_capacity:
                        machine.buffer_blocked_steps += 1
                        return
                transfer.dropoff_start_time = self.env_timeline
                if self.dropoff_dwell_steps > 0:
                    self._set_agv_phase(agent_idx, AGV_PHASE_DROPPING, self.dropoff_dwell_steps)
                    self._set_agv_target(agent_idx, transfer.destination)
                    return
                self._set_agv_phase(agent_idx, AGV_PHASE_DROPPING)
                self._finish_dropoff(agent_idx, transfer)
            return

    def _assign_transfer(self, agent_idx: int, transfer: RoutingTask) -> None:
        transfer = self._prepare_transfer(transfer)
        job: Job = next(job for job in self.jobs if job.job_id == transfer.job_id)
        job.transport_task_id = transfer.task_id
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
        self._assigned_this_step.clear()
        ready: list[RoutingTask] = []
        waiting: list[RoutingTask] = []
        for task in self.buffered_tasks:
            self._prepare_transfer(task)
            if self._check_physical_precondition(task):
                if task.kind == "operation" and task.source == task.destination:
                    operation: Operation = self.hash_operations[(task.job_id, task.op_id)]
                    machine: Machine = self.hash_machines[task.destination]
                    if task.job_id not in machine.buffer_jobs and len(machine.buffer_jobs) >= machine.buffer_capacity:
                        waiting.append(task)
                        continue
                    machine.buffer_jobs.add(task.job_id)
                    operation.arrive_machine_at = self.env_timeline
                    self._set_operation_machine(operation, machine)
                    machine.input_queue.append(operation)
                    machine.urgent_reservations.discard((task.job_id, task.op_id))
                    if machine not in self.activated_machines:
                        self.activated_machines.append(machine)
                else:
                    ready.append(task)
            else:
                waiting.append(task)
        self.buffered_tasks = waiting
        self.transfers_to_assign.extend(ready)
        assignments: dict = action.get("assignments", {})
        tasks: dict[int, RoutingTask] = {task.task_id: task for task in self.transfers_to_assign}
        selected: set[int] = set()
        jobs: set[int] = set()
        accepted: list[tuple[int, RoutingTask]] = []
        for agent_id, value in assignments.items():
            agent_id = int(agent_id)
            if value is None:
                continue
            task_id: int = int(value["task_id"] if isinstance(value, dict) else value.task_id if isinstance(value, RoutingTask) else value)
            if not 0 <= agent_id < self.grid_config.num_agents or task_id not in tasks:
                raise ValueError("assignment references an unknown AGV or a non-ready task")
            task = tasks[task_id]
            if task_id in selected or task.job_id in jobs or self.agv_current_task[agent_id] is not None:
                raise ValueError("transport tasks, workpieces and AGVs must have a single owner")
            selected.add(task_id)
            jobs.add(task.job_id)
            if self.agv_status[agent_id] != "OK":
                self.emit_event("assignment_deferred", {"agv_id": agent_id, "task_id": task_id, "reason": "agv_down"})
                continue
            accepted.append((agent_id, task))
        for agent_id, task in accepted:
            self._assign_transfer(agent_id, task)
            self.transfers_to_assign.remove(task)
        self.machine_process()
        for agent_id in range(self.grid_config.num_agents):
            if self.agv_status[agent_id] == "OK" and self.agv_current_task[agent_id] is not None:
                self._settle_arrival(agent_id)
        return {}, [], [False] * self.grid_config.num_agents, [False] * self.grid_config.num_agents, []

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

        self.env_timeline += 1
        for idx, phase in enumerate(phase_at_step_start):
            self._advance_handling(idx, phase)
            if self.agv_status[idx] == "OK":
                self._settle_arrival(idx)
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
            "all_jobs": self._all_jobs, "future_jobs": self._future_jobs,
            "episode_seed": self._episode_seed, "random_state": self._rng.getstate(),
            "shipping_sequence": self._shipping_sequence,
            "pending_events": self._pending_events,
            "reschedule_count": self.reschedule_count,
            "reassigned_operation_count": self.reassigned_operation_count,
            "reassigned_transport_count": self.reassigned_transport_count,
            "agv_down_elapsed": self.agv_down_elapsed,
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
        self._all_jobs = state_dict["all_jobs"]
        self._future_jobs = state_dict["future_jobs"]
        self._episode_seed = state_dict["episode_seed"]
        self._rng.setstate(state_dict["random_state"])
        self.processing_time_sampler.reset(self._episode_seed)
        self._shipping_sequence = state_dict["shipping_sequence"]
        self._pending_events = state_dict["pending_events"]
        self._in_step = False
        self.reschedule_count = state_dict["reschedule_count"]
        self.reassigned_operation_count = state_dict["reassigned_operation_count"]
        self.reassigned_transport_count = state_dict["reassigned_transport_count"]
        self.agv_down_elapsed = state_dict["agv_down_elapsed"]
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
        return bool(getattr(self, "_all_jobs", self.jobs)) and all(job.is_completed for job in getattr(self, "_all_jobs", self.jobs))

    def _check_physical_precondition(self, task: RoutingTask) -> bool:
        job: Job = next(job for job in self.jobs if job.job_id == task.job_id)
        if task.ready_time > self.env_timeline or job.carrier_id is not None or job.transport_task_id is not None:
            return False
        if task.op_id > 0 and job.ops[task.op_id - 1].status != "FINISHED":
            return False
        source_machine = self.hash_machines.get(job.material_location)
        at_raw_source: bool = task.op_id == 0 and job.ops[0].arrive_machine_at < 0 and job.material_location == job.raw_material_source
        return at_raw_source or source_machine is None or job.job_id in source_machine.buffer_jobs

    def _deliver(self, agent_idx: int, transfer: RoutingTask) -> bool:
        job: Job = next(job for job in self.jobs if job.job_id == transfer.job_id)
        if transfer.kind == "operation":
            machine: Machine = self.hash_machines[transfer.destination]
            if len(machine.buffer_jobs) >= machine.buffer_capacity:
                machine.buffer_blocked_steps += 1
                return False
            operation: Operation = self.hash_operations[(transfer.job_id, transfer.op_id)]
            operation.arrive_machine_at = self.env_timeline
            self._set_operation_machine(operation, machine)
            machine.input_queue.append(operation)
            machine.buffer_jobs.add(job.job_id)
            machine.urgent_reservations.discard((transfer.job_id, transfer.op_id))
            if machine not in self.activated_machines:
                self.activated_machines.append(machine)
        else:
            job.delivered = True
            job.completion_time = self.env_timeline
            self.emit_event("job_completed", {"job_id": job.job_id})
        transfer.finish_time = self.env_timeline
        job.material_location = transfer.destination
        job.carrier_id = job.transport_task_id = None
        self.agv_finished_tasks[agent_idx].append(transfer)
        self.active_transfers.remove(transfer)
        self.agv_current_task[agent_idx] = None
        self.emit_event("transport_completed", {"job_id": job.job_id, "task_id": transfer.task_id, "agv_id": agent_idx})
        return True

    def _set_operation_machine(self, operation: Operation, machine: Machine) -> None:
        operation.assigned_machine = machine.id
        nominal: float = dict(operation.machine_options_with_time)[machine.id]
        operation.nominal_proc_time = operation.proc_time = nominal
        operation.sampled_proc_time = None
        operation.processing_time_distribution = None

    def emit_event(self, event_type: str, payload: dict, *, step: int | None = None) -> None:
        self.event_epoch += 1
        event: dict = {"type": event_type, "step": self.env_timeline if step is None else step,
                       "event_epoch": self.event_epoch, "payload": payload}
        self.last_events.append(event)
        if not self._in_step:
            self._pending_events.append(event)

    def _require_connected_layout(self) -> set[tuple[int, int]]:
        endpoints: set[tuple[int, int]] = {machine.location for machine in self.machines}
        endpoints.update(self._to_public_xy(pos) for pos in self.grid.positions_xy)
        endpoints.update(job.raw_material_source for job in self._all_jobs if job.raw_material_source is not None)
        endpoints.update(job.finished_goods_destination for job in self._all_jobs if job.finished_goods_destination is not None)
        endpoints.update(pos for pos in (self.raw_material_source, self.finished_goods_destination) if pos is not None)
        cells: set[tuple[int, int]] = {self._to_internal_xy(pos) for pos in endpoints}
        grid = self.grid.obstacles
        for x, y in cells:
            if not (0 <= x < grid.shape[0] and 0 <= y < grid.shape[1]) or grid[x, y] != 0:
                raise ValueError("material stations, machines and AGVs must lie on traversable cells")
        start: tuple[int, int] = min(cells)
        seen: set = {start}
        queue = deque([start])
        while queue:
            x, y = queue.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                neighbour = (x + dx, y + dy)
                if (0 <= neighbour[0] < grid.shape[0] and 0 <= neighbour[1] < grid.shape[1]
                        and grid[neighbour] == 0 and neighbour not in seen):
                    seen.add(neighbour)
                    queue.append(neighbour)
        if not cells <= seen:
            unreachable: list[tuple[int, int]] = sorted(self._to_public_xy(pos) for pos in cells - seen)
            raise ValueError(f"工厂机器、AGV 和物料站必须位于同一四邻接连通区域；与 {self._to_public_xy(start)} 不连通的位置：{unreachable}")
        return {self._to_public_xy(pos) for pos in seen}
