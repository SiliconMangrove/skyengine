"""Event-aware heterogeneous state encoding for RACE-FMS."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence

import numpy as np

from .coupling import COUPLING_FEATURE_NAMES


ENTITY_DIMS = {
    "jobs": 11,
    "operations": 15,
    "machines": 13,
    "tasks": 13,
    "agvs": 14,
    "events": 10,
}

RELATION_NAMES = (
    "job_operation",
    "operation_precedence",
    "operation_machine",
    "task_job",
    "task_machine",
    "agv_task",
)


def _clip01(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _ratio01(value: float) -> float:
    value = max(0.0, float(value))
    return value / (1.0 + value)


def _empty_edges() -> np.ndarray:
    return np.zeros((2, 0), dtype=np.int64)


@dataclass(frozen=True)
class RaceState:
    nodes: Dict[str, np.ndarray]
    edges: Dict[str, np.ndarray]
    action_masks: Dict[str, np.ndarray]
    coupling: Dict[str, float]
    coupling_vector: np.ndarray
    epochs: Dict[str, int]
    events: tuple[Mapping[str, Any], ...]
    timeline: int
    metadata: Dict[str, Any]

    def validate(self) -> None:
        for name, dim in ENTITY_DIMS.items():
            values = self.nodes.get(name)
            if values is None or values.ndim != 2 or values.shape[1] != dim:
                raise ValueError(f"invalid {name} node matrix; expected (*, {dim})")
            if not np.isfinite(values).all():
                raise ValueError(f"{name} node matrix contains non-finite values")
        for name in RELATION_NAMES:
            edge = self.edges.get(name)
            if edge is None or edge.ndim != 2 or edge.shape[0] != 2:
                raise ValueError(f"invalid relation matrix: {name}")
        production_mask = self.action_masks.get("production")
        if production_mask is None or production_mask.shape != (self.edges["operation_machine"].shape[1],):
            raise ValueError("invalid production action mask")
        logistics_mask = self.action_masks.get("logistics")
        expected_logistics = self.nodes["tasks"].shape[0] * self.nodes["agvs"].shape[0]
        if logistics_mask is None or logistics_mask.shape != (expected_logistics,):
            raise ValueError("invalid logistics action mask")
        if self.coupling_vector.shape != (len(COUPLING_FEATURE_NAMES),):
            raise ValueError("invalid coupling feature vector")


class RaceStateEncoder:
    """Encode current SkyEngine observations without changing legacy features."""

    EVENT_TYPES = {
        "machine_breakdown": 0,
        "machine_recovery": 0,
        "agv_breakdown": 1,
        "agv_recovery": 1,
        "temporary_obstacle": 2,
        "temporary_obstacle_cleared": 2,
        "urgent_job_arrival": 3,
        "job_replan_started": 3,
    }

    def __init__(self):
        self._map_epoch = None
        self._distance_cache: dict[tuple, float] = {}

    def encode(self, obs: Mapping[str, Any]) -> RaceState:
        job_obs = obs.get("job_observation") or {}
        task_obs = obs.get("task_observation") or {}
        jobs = list(job_obs.get("jobs") or task_obs.get("jobs") or [])
        machines = list(task_obs.get("machines") or job_obs.get("machines") or [])
        tasks = [*(task_obs.get("pending_transfers") or []), *(task_obs.get("active_transfers") or [])]
        agents = list(task_obs.get("agents") or [])
        events = tuple(task_obs.get("events") or ())
        timeline = int(task_obs.get("env_timeline", 0) or 0)
        epochs = {
            name: int(task_obs.get(name, 0) or 0)
            for name in ("event_epoch", "map_epoch", "machine_epoch", "agv_epoch", "job_epoch")
        }

        if epochs["map_epoch"] != self._map_epoch:
            self._distance_cache.clear()
            self._map_epoch = epochs["map_epoch"]

        grid_h = max(1, int(task_obs.get("grid_height", 1) or 1))
        grid_w = max(1, int(task_obs.get("grid_width", 1) or 1))
        obstacles = task_obs.get("obstacle_grid")
        obstacle_array = np.asarray(obstacles) if obstacles is not None else None

        job_index = {getattr(job, "job_id", idx): idx for idx, job in enumerate(jobs)}
        machine_index = {getattr(machine, "id", idx): idx for idx, machine in enumerate(machines)}
        machine_by_location = {
            tuple(getattr(machine, "location", (-1, -1))): idx
            for idx, machine in enumerate(machines)
        }
        task_index = {getattr(task, "task_id", idx): idx for idx, task in enumerate(tasks)}

        operation_rows, operation_lookup, job_op_edges, precedence_edges, op_machine_edges, production_mask = (
            self._encode_operations(jobs, machines, machine_index, timeline)
        )
        job_rows = self._encode_jobs(jobs, timeline)
        machine_rows = self._encode_machines(machines, timeline, grid_w, grid_h)
        task_rows, task_job_edges, task_machine_edges = self._encode_tasks(
            tasks, jobs, job_index, machine_by_location, timeline, grid_w, grid_h, agents
        )
        agv_rows, agv_task_edges = self._encode_agvs(
            agents, task_index, timeline, grid_w, grid_h
        )
        event_rows = self._encode_events(events, timeline, len(machines), len(agents), len(jobs))

        coupling = self._coupling_features(
            jobs=jobs,
            machines=machines,
            tasks=tasks,
            agents=agents,
            events=events,
            timeline=timeline,
            prev_metrics=task_obs.get("prev_metrics") or {},
            obstacle_grid=obstacle_array,
        )
        coupling_vector = np.asarray(
            [coupling[name] for name in COUPLING_FEATURE_NAMES], dtype=np.float32
        )
        operation_keys = [
            (int(getattr(job, "job_id", job_index)), int(getattr(op, "op_id", op_index)))
            for job_index, job in enumerate(jobs)
            for op_index, op in enumerate(getattr(job, "ops", []))
        ]
        metadata = {
            "operation_keys": operation_keys,
            "machine_ids": [int(getattr(machine, "id", index)) for index, machine in enumerate(machines)],
            "task_ids": [int(getattr(task, "task_id", index)) for index, task in enumerate(tasks)],
            "agv_ids": [int(getattr(agent, "id", index)) for index, agent in enumerate(agents)],
            "operation_machine_edges": [tuple(map(int, edge)) for edge in op_machine_edges],
            "coupling_vector": coupling_vector.tolist(),
            "coupling": coupling,
        }
        state = RaceState(
            nodes={
                "jobs": self._rows(job_rows, ENTITY_DIMS["jobs"]),
                "operations": self._rows(operation_rows, ENTITY_DIMS["operations"]),
                "machines": self._rows(machine_rows, ENTITY_DIMS["machines"]),
                "tasks": self._rows(task_rows, ENTITY_DIMS["tasks"]),
                "agvs": self._rows(agv_rows, ENTITY_DIMS["agvs"]),
                "events": self._rows(event_rows, ENTITY_DIMS["events"]),
            },
            edges={
                "job_operation": self._edges(job_op_edges),
                "operation_precedence": self._edges(precedence_edges),
                "operation_machine": self._edges(op_machine_edges),
                "task_job": self._edges(task_job_edges),
                "task_machine": self._edges(task_machine_edges),
                "agv_task": self._edges(agv_task_edges),
            },
            action_masks={
                "production": np.asarray(production_mask, dtype=np.bool_),
                "logistics": self._logistics_mask(tasks, agents),
            },
            coupling=coupling,
            coupling_vector=coupling_vector,
            epochs=epochs,
            events=events,
            timeline=timeline,
            metadata=metadata,
        )
        state.validate()
        moves = task_obs.get("move_deltas")
        if moves is not None:
            route_mask = np.zeros((len(agents), len(moves)), dtype=np.bool_)
            for agent_index, agent in enumerate(agents):
                x, y = agent.pos
                for move_index, (dx, dy) in enumerate(moves):
                    nx, ny = x + dx, y + dy
                    route_mask[agent_index, move_index] = (
                        (dx == 0 and dy == 0)
                        or (agent.status == "OK" and agent.task_phase not in {"PICKING", "DROPPING"}
                            and 0 <= nx < grid_h and 0 <= ny < grid_w and obstacle_array[nx, ny] == 0)
                    )
            state.action_masks["route"] = route_mask
        return state

    @staticmethod
    def _rows(rows: Sequence[Sequence[float]], dim: int) -> np.ndarray:
        if not rows:
            return np.zeros((0, dim), dtype=np.float32)
        return np.asarray(rows, dtype=np.float32).reshape(-1, dim)

    @staticmethod
    def _edges(edges: Sequence[tuple[int, int]]) -> np.ndarray:
        if not edges:
            return _empty_edges()
        return np.asarray(edges, dtype=np.int64).T

    @staticmethod
    def _job_remaining(job) -> float:
        return sum(
            float(getattr(op, "remaining_proc_time", None) or getattr(op, "nominal_proc_time", None) or getattr(op, "proc_time", 0) or 0)
            for op in getattr(job, "ops", [])
            if getattr(op, "status", "PENDING") not in {"FINISHED"}
        )

    @classmethod
    def _due_pressure(cls, job, timeline: int) -> float:
        due = getattr(job, "due", None)
        if due is None or float(due) < 0:
            return 0.0
        remaining = max(1.0, cls._job_remaining(job))
        slack = float(due) - float(timeline) - remaining
        return _clip01(1.0 - max(0.0, slack) / (2.0 * remaining))

    def _encode_jobs(self, jobs, timeline: int) -> list[list[float]]:
        rows = []
        for job in jobs:
            ops = list(getattr(job, "ops", []))
            counts = {
                status: sum(1 for op in ops if getattr(op, "status", "PENDING") == status)
                for status in ("PENDING", "PROCESSING", "SUSPENDED", "FINISHED")
            }
            total = max(1, len(ops))
            priority = float(getattr(job, "priority", 0) or 0)
            rows.append([
                len(ops) / 20.0,
                counts["PENDING"] / total,
                counts["PROCESSING"] / total,
                counts["SUSPENDED"] / total,
                counts["FINISHED"] / total,
                _ratio01(float(getattr(job, "release", 0) or 0) / max(timeline, 1)),
                self._due_pressure(job, timeline),
                _ratio01(priority / 100.0),
                1.0 if priority >= 100 else 0.0,
                1.0 if getattr(job, "is_completed", False) else 0.0,
                _ratio01(self._job_remaining(job) / 20.0),
            ])
        return rows

    def _encode_operations(self, jobs, machines, machine_index, timeline: int):
        rows = []
        lookup = {}
        job_edges = []
        precedence = []
        machine_edges = []
        production_mask = []
        machine_available = {
            getattr(machine, "id", idx): getattr(machine, "status", "OK") == "OK"
            for idx, machine in enumerate(machines)
        }
        for j_idx, job in enumerate(jobs):
            previous = None
            due_pressure = self._due_pressure(job, timeline)
            for op in getattr(job, "ops", []):
                op_idx = len(rows)
                lookup[(getattr(job, "job_id", j_idx), getattr(op, "op_id", op_idx))] = op_idx
                job_edges.append((j_idx, op_idx))
                if previous is not None:
                    precedence.append((previous, op_idx))
                previous = op_idx
                status = str(getattr(op, "status", "PENDING"))
                status_vec = [float(status == name) for name in ("PENDING", "PROCESSING", "SUSPENDED", "FINISHED")]
                nominal = float(getattr(op, "nominal_proc_time", None) or getattr(op, "proc_time", 0) or 0)
                sampled = getattr(op, "sampled_proc_time", None)
                actual = float(sampled if sampled is not None else getattr(op, "proc_time", nominal) or nominal)
                remaining = getattr(op, "remaining_proc_time", None)
                if remaining is None:
                    remaining = max(0.0, nominal - op.processed_time)
                remaining = float(remaining if remaining is not None else (0.0 if status == "FINISHED" else actual))
                deviation = (abs(actual - nominal) if status == "FINISHED" else max(0.0, op.processed_time - nominal)) / max(nominal, 1.0)
                options = list(getattr(op, "machine_options", None) or [])
                if not options:
                    options = [mid for mid, _ in (getattr(op, "machine_options_with_time", None) or [])]
                for mid in options:
                    if mid in machine_index:
                        machine_edges.append((op_idx, machine_index[mid]))
                        production_mask.append(
                            status == "PENDING" and not op.transfer_requested and op.assigned_machine is None
                            and (op.op_id == 0 or job.ops[op.op_id - 1].status == "FINISHED")
                            and machine_available.get(mid, False)
                        )
                assigned = getattr(op, "assigned_machine", None)
                rows.append(status_vec + [
                    _ratio01(nominal / 10.0),
                    _ratio01(actual / 10.0),
                    _ratio01(remaining / 10.0),
                    _ratio01(float(getattr(op, "release", 0) or 0) / max(timeline, 1)),
                    due_pressure,
                    _ratio01(float(getattr(op, "priority", 0) or 0) / 100.0),
                    _ratio01(len(options) / 3.0),
                    1.0 if assigned is not None else 0.0,
                    _ratio01(deviation),
                    1.0 if status == "PENDING" else 0.0,
                    _ratio01(float(getattr(op, "preemption_count", 0) or 0)),
                ])
        return rows, lookup, job_edges, precedence, machine_edges, production_mask

    @staticmethod
    def _logistics_mask(tasks, agents) -> np.ndarray:
        if not tasks or not agents:
            return np.zeros((0,), dtype=np.bool_)
        return np.asarray([
            getattr(agent, "status", "OK") == "OK"
            and getattr(agent, "current_task", None) is None
            and task.assigned_agent_id is None
            for task in tasks
            for agent in agents
        ], dtype=np.bool_)

    def _encode_machines(self, machines, timeline: int, grid_w: int, grid_h: int):
        rows = []
        for machine in machines:
            x, y = tuple(getattr(machine, "location", (0, 0)))
            status = str(getattr(machine, "status", "OK"))
            current = getattr(machine, "current_op", None)
            current_nominal = float(getattr(current, "nominal_proc_time", 0) or 0) if current else 0.0
            current_actual = float(current.processed_time) if current else 0.0
            deviation = max(0.0, current_actual - current_nominal) / max(current_nominal, 1.0)
            rows.append([
                x / max(grid_w - 1, 1), y / max(grid_h - 1, 1),
                float(status == "OK"), float(status == "DOWN"), float(status == "MAINTENANCE"),
                _ratio01(float(getattr(machine, "down_elapsed", 0) or 0) / 10.0),
                1.0 if current is not None else 0.0,
                _ratio01(len(getattr(machine, "input_queue", []) or [])),
                _ratio01(float(getattr(machine, "total_work_time", 0) or 0) / max(timeline, 1)),
                _ratio01(float(getattr(machine, "processed_ops_count", 0) or 0) / 10.0),
                _ratio01(current_actual / 10.0),
                _ratio01(deviation),
                len(machine.buffer_jobs) / machine.buffer_capacity,
            ])
        return rows

    def _encode_tasks(self, tasks, jobs, job_index, machine_by_location, timeline, grid_w, grid_h, agents):
        rows, job_edges, machine_edges = [], [], []
        jobs_by_id = {getattr(job, "job_id", idx): job for idx, job in enumerate(jobs)}
        endpoint_counts: dict[tuple, int] = {}
        for task in tasks:
            if getattr(task, "destination", None) is not None:
                dest = tuple(task.destination)
                endpoint_counts[dest] = endpoint_counts.get(dest, 0) + 1
        for idx, task in enumerate(tasks):
            sx, sy = tuple(getattr(task, "source", (0, 0)) or (0, 0))
            destination = getattr(task, "destination", None)
            dx, dy = tuple(destination) if destination is not None else (-1, -1)
            job = jobs_by_id.get(getattr(task, "job_id", None))
            job_edges.append((idx, job_index[getattr(task, "job_id")])) if getattr(task, "job_id", None) in job_index else None
            if destination is not None and tuple(destination) in machine_by_location:
                machine_edges.append((idx, machine_by_location[tuple(destination)]))
            create = float(getattr(task, "create_time", -1) or -1)
            if create < 0:
                create = float(getattr(task, "ready_time", timeline) or timeline)
            rows.append([
                sx / max(grid_w - 1, 1), sy / max(grid_h - 1, 1),
                dx / max(grid_w - 1, 1), dy / max(grid_h - 1, 1),
                1.0 if destination is not None else 0.0,
                1.0 if getattr(task, "pickup_required", True) else 0.0,
                _ratio01(max(0.0, timeline - create) / 10.0),
                _ratio01(float(getattr(task, "priority", 0) or 0) / 100.0),
                1.0 if getattr(task, "assigned_agent_id", None) is not None else 0.0,
                _ratio01(len(getattr(task, "candidate_machines", []) or [])),
                self._due_pressure(job, timeline) if job is not None else 0.0,
                _ratio01(endpoint_counts.get(tuple(destination), 0) - 1) if destination is not None else 0.0,
                _ratio01(len(agents) / max(len(tasks), 1)),
            ])
        return rows, job_edges, machine_edges

    def _encode_agvs(self, agents, task_index, timeline, grid_w, grid_h):
        rows, edges = [], []
        for idx, agent in enumerate(agents):
            x, y = tuple(getattr(agent, "pos", (0, 0)) or (0, 0))
            status = str(getattr(agent, "status", "OK"))
            phase = str(getattr(agent, "task_phase", "IDLE")).upper()
            task = getattr(agent, "current_task", None)
            task_id = getattr(task, "task_id", None) if task is not None else None
            if task_id in task_index:
                edges.append((idx, task_index[task_id]))
            rows.append([
                x / max(grid_w - 1, 1), y / max(grid_h - 1, 1),
                float(status == "OK"), float(status == "DOWN"),
                _ratio01(float(getattr(agent, "down_elapsed", 0) or 0) / 10.0),
                1.0 if task is None else 0.0,
                float("PICKUP" in phase and "TO_" not in phase),
                float("TO_PICKUP" in phase),
                float("TO_DROPOFF" in phase),
                float("DROPP" in phase),
                1.0 if getattr(agent, "loaded", False) else 0.0,
                _ratio01(float(getattr(agent, "total_loaded_time", 0) or 0) / max(timeline, 1)),
                _ratio01(float(getattr(agent, "handling_remaining", 0) or 0) / 5.0),
                _ratio01(float(getattr(agent, "total_task_waiting_time", 0) or 0) / max(timeline, 1)),
            ])
        return rows, edges

    def _encode_events(self, events, timeline: int, n_machines: int, n_agvs: int, n_jobs: int):
        rows = []
        for event in events:
            etype = str(event.get("type", ""))
            kind = self.EVENT_TYPES.get(etype, 4)
            kind_vec = [float(kind == idx) for idx in range(5)]
            payload = event.get("payload") or event
            duration = float(payload.get("elapsed_processing", 0) or 0)
            affected_id = payload.get("machine_id", payload.get("agv_id", payload.get("job_id", 0)))
            denominator = max(n_machines, n_agvs, n_jobs, 1)
            level = str(event.get("level", "info"))
            rows.append(kind_vec + [
                _ratio01(max(0, timeline - int(event.get("step", timeline) or timeline)) / 5.0),
                _ratio01(duration / 10.0),
                _ratio01(float(affected_id or 0) / denominator),
                float(level in {"warning", "error", "critical"}),
                1.0 if "recovery" in etype or "cleared" in etype else 0.0,
            ])
        return rows

    def _distance(self, obstacles: np.ndarray | None, source, destination) -> float:
        if source is None or destination is None:
            return 0.0
        source, destination = tuple(source), tuple(destination)
        if obstacles is None or obstacles.ndim != 2:
            return float(abs(source[0] - destination[0]) + abs(source[1] - destination[1]))
        key = (source, destination)
        if key in self._distance_cache:
            return self._distance_cache[key]
        h, w = obstacles.shape
        queue = deque([(source[0], source[1], 0)])
        seen = {source}
        result = 0.0
        while queue:
            x, y, distance = queue.popleft()
            if (x, y) == destination:
                result = float(distance)
                break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < h and 0 <= ny < w and obstacles[nx, ny] == 0 and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    queue.append((nx, ny, distance + 1))
        self._distance_cache[key] = result
        return result

    def _coupling_features(self, *, jobs, machines, tasks, agents, events, timeline, prev_metrics, obstacle_grid):
        nominal_times = [
            float(getattr(op, "nominal_proc_time", None) or getattr(op, "proc_time", 0) or 0)
            for job in jobs for op in getattr(job, "ops", [])
            if getattr(op, "status", "PENDING") != "FINISHED"
        ]
        travel = [
            self._distance(obstacle_grid, getattr(task, "source", None), getattr(task, "destination", None))
            for task in tasks if getattr(task, "destination", None) is not None
        ]
        mean_process = float(np.mean(nominal_times)) if nominal_times else 1.0
        mean_transport = float(np.mean(travel)) if travel else 0.0
        raw_transport_ratio = mean_transport / max(mean_process, 1e-6)

        active = sum(1 for agent in agents if getattr(agent, "current_task", None) is not None)
        down_agvs = sum(1 for agent in agents if getattr(agent, "status", "OK") != "OK")
        down_machines = sum(1 for machine in machines if getattr(machine, "status", "OK") != "OK")
        agv_load = (active + down_agvs) / max(len(agents), 1)

        starvation = float(prev_metrics.get("machine_waiting_for_inbound_transfer_ratio", 0.0) or 0.0)
        if not prev_metrics:
            destinations = {tuple(task.destination) for task in tasks if getattr(task, "destination", None) is not None}
            starving = sum(
                1 for machine in machines
                if getattr(machine, "current_op", None) is None
                and not (getattr(machine, "input_queue", []) or [])
                and tuple(getattr(machine, "location", (-1, -1))) in destinations
            )
            starvation = starving / max(len(machines), 1)

        due_pressure = float(np.mean([self._due_pressure(job, timeline) for job in jobs])) if jobs else 0.0
        queue_pressure = len(tasks) / max(len(agents), 1)
        transport_delay = max(0.0, float(prev_metrics.get("transport_delay_ratio", 0.0) or 0.0))
        swap_pressure = float(prev_metrics.get("swap_conflict_count", 0.0) or 0.0) / max(len(agents), 1)
        congestion = max(_ratio01(transport_delay), _clip01(swap_pressure))

        uncertainty_values = []
        for job in jobs:
            for op in getattr(job, "ops", []):
                nominal = float(getattr(op, "nominal_proc_time", None) or 0)
                sampled = getattr(op, "sampled_proc_time", None)
                if nominal > 0 and sampled is not None:
                    uncertainty_values.append(abs(float(sampled) - nominal) / nominal)
        uncertainty = float(np.mean(uncertainty_values)) if uncertainty_values else 0.0

        total_resources = max(len(machines) + len(agents) + len(jobs), 1)
        affected_ids = set()
        for event in events:
            payload = event.get("payload") or event
            for key in ("machine_id", "agv_id", "job_id", "cell"):
                if key in payload:
                    affected_ids.add((key, str(payload[key])))
        affected_fraction = len(affected_ids) / total_resources
        event_pressure = min(1.0, len(events) / max(total_resources, 1))
        resource_pressure = (down_machines + down_agvs) / max(len(machines) + len(agents), 1)

        return {
            "transport_processing_ratio": _ratio01(raw_transport_ratio),
            "agv_load": _clip01(agv_load),
            "starvation_risk": _clip01(starvation),
            "due_pressure": _clip01(due_pressure),
            "queue_pressure": _ratio01(queue_pressure),
            "congestion_pressure": _clip01(congestion),
            "disturbance_pressure": _clip01(max(event_pressure, resource_pressure)),
            "processing_uncertainty": _ratio01(uncertainty),
            "affected_fraction": _clip01(affected_fraction),
            "raw_transport_processing_ratio": raw_transport_ratio,
        }
