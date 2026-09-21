"""Environment-owned edits to unexecuted plans; executed material movement is immutable."""
from __future__ import annotations


class ReschedulingActions:
    def residual_problem(self) -> dict:
        """Expose a residual scheduling problem using observable estimates only."""
        now: float = float(self.env_timeline)
        locked: set[tuple[int, int]] = {(job.job_id, op.op_id) for job in self.jobs for op in job.ops
                                      if op.status in {"PROCESSING", "SUSPENDED", "FINISHED"}}
        locked.update((task.job_id, task.op_id) for agent_id, task in enumerate(self.agv_current_task)
                      if task is not None and self.agv_loaded[agent_id])
        ready: list[float] = [now] * len(self.machines)
        estimates: dict[tuple[int, int], float] = {}
        for machine in self.machines:
            current = machine.current_op
            if current is not None:
                remaining: float = max(0.0, float(current.nominal_proc_time or current.proc_time) - current.processed_time)
                ready[machine.id] += remaining
                estimates[(current.job_id, current.op_id)] = ready[machine.id]
        jobs: list[dict] = []
        for job in self.jobs:
            pending = [op for op in job.ops if (job.job_id, op.op_id) not in locked]
            if not pending:
                continue
            first = pending[0]
            source = self.hash_machines.get(job.material_location)
            jobs.append({"job_id": job.job_id, "priority": job.priority, "due": job.due,
                         "request_id": job.request_id, "ready_time": estimates.get((job.job_id, first.op_id - 1), now),
                         "from_machine": source.id if source is not None else -1,
                         "material_location": job.material_location, "carrier_id": job.carrier_id,
                         "operations": [{"op_id": op.op_id,
                                         "alternatives": [{"machine": mid, "processing": duration}
                                                          for mid, duration in op.machine_options_with_time]} for op in pending]})
        return {"current_step": now, "machines": len(self.machines), "machine_ready_times": ready,
                "machine_status": [machine.status for machine in self.machines], "jobs": jobs}

    def apply_reschedule(self, action: dict) -> None:
        if not action:
            return
        cancelled: set[tuple[int, int]] = {tuple(map(int, key)) for key in action.get("cancel_operations", [])}
        release: set[int] = {int(value) for value in action.get("release_agvs", [])}
        queues: dict[int, list[tuple[int, int]]] = {
            int(machine): [tuple(map(int, key)) for key in keys]
            for machine, keys in action.get("machine_queues", {}).items()
        }
        # Validate the whole rescheduling transaction before changing ownership.
        for key in cancelled:
            operation = self.hash_operations[key]
            if operation.status != "PENDING":
                raise ValueError("only unstarted operations may be cancelled")
        for agent_id, task in enumerate(self.agv_current_task):
            if task is not None and (task.job_id, task.op_id) in cancelled:
                release.add(agent_id)
        for agent_id in release:
            if not 0 <= agent_id < self.grid_config.num_agents or self.agv_current_task[agent_id] is None:
                raise ValueError("release_agvs requires an AGV with a current task")
            if self.agv_loaded[agent_id]:
                raise ValueError("a loaded workpiece must remain on its current AGV")
        for machine_id, keys in queues.items():
            current: set = {(op.job_id, op.op_id) for op in self.machines[machine_id].input_queue} - cancelled
            if len(keys) != len(set(keys)) or set(keys) != current:
                raise ValueError("machine_queues must be a permutation of its remaining input queue")

        for agent_id in release:
            task = self.agv_current_task[agent_id]
            job = next(job for job in self.jobs if job.job_id == task.job_id)
            job.transport_task_id = None
            task.assigned_agent_id = None
            task.assign_time = task.pickup_start_time = -1
            self.agv_current_task[agent_id] = None
            self.agv_task_phase[agent_id] = "IDLE"
            self.agv_handling_remaining[agent_id] = 0
            self.grid.finishes_xy[agent_id] = self.grid.positions_xy[agent_id]
            self.active_transfers.remove(task)
            if (task.job_id, task.op_id) not in cancelled:
                self.transfers_to_assign.append(task)
        for name in ("pending_transfers", "buffered_tasks", "transfers_to_assign"):
            setattr(self, name, [task for task in getattr(self, name) if (task.job_id, task.op_id) not in cancelled])
        for machine in self.machines:
            machine.input_queue = [op for op in machine.input_queue if (op.job_id, op.op_id) not in cancelled]
            machine.urgent_reservations.difference_update(cancelled)
        for key in cancelled:
            operation = self.hash_operations[key]
            operation.transfer_requested = False
            operation.assigned_machine = operation.assigned_robot = operation.assigned_node = None
            operation.arrive_machine_at = -1
        for machine_id, keys in queues.items():
            self.machines[machine_id].input_queue = [self.hash_operations[key] for key in keys]
        self.reschedule_count += 1
        self.reassigned_operation_count += len(cancelled)
        self.reassigned_transport_count += len(release)
        self.emit_event("reschedule_applied", {"cancel_operations": sorted(cancelled),
                        "release_agvs": sorted(release), "machine_queues": queues})

    def release_failed_unloaded_transfers(self) -> int:
        agents: list[int] = [agent_id for agent_id, task in enumerate(self.agv_current_task)
                            if task is not None and self.agv_status[agent_id] != "OK" and not self.agv_loaded[agent_id]]
        if agents:
            self.apply_reschedule({"release_agvs": agents})
        return len(agents)
