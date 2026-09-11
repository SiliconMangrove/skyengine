"""Shared headless Grid simulation session.

The HTTP server and batch runner use this class as their runtime boundary.  It
contains no FastAPI or timing code, so callers can drive the same environment
from a web service, a command line process, or a future training loop.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pogema import GridConfig

from sky_executor.grid_factory.factory.Component.Coordinator.coordinator import Coordinator
from sky_executor.grid_factory.factory.Utils.structure import JobConfig, MachineConfig
from sky_executor.grid_factory.factory.grid_factory_env import GridFactoryEnv


def _obstacle_map(topology: dict, machine_positions: list[tuple[int, int]], agent_positions: list[tuple[int, int]], extra_passable: list[tuple[int, int]]) -> str:
    width = int(topology.get("gridWidth", 20))
    height = int(topology.get("gridHeight", 20))
    size = max(width, height)
    blocked: set[tuple[int, int]] = set()
    for zone in topology.get("zones", []):
        if zone.get("type") != "obstacle":
            continue
        area = zone.get("area", {})
        for dy in range(int(area.get("h", 0))):
            for dx in range(int(area.get("w", 0))):
                blocked.add((int(area.get("x", 0)) + dx, int(area.get("y", 0)) + dy))
    blocked.difference_update(machine_positions, agent_positions, extra_passable)
    return "\n".join(
        "".join("#" if (x, y) in blocked else "." for y in range(size))
        for x in range(size)
    )


def _processing_config(operation: dict) -> dict | None:
    for key in ("processing_time_config", "processing_time_distribution", "processing_time_distributions"):
        value = operation.get(key)
        if isinstance(value, dict):
            return deepcopy(value)
    return None


def _material_config(raw: dict | None, topology: dict) -> dict:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError("material_handling_config must be an object")
    result = {}
    for key in ("pickup_dwell_steps", "dropoff_dwell_steps"):
        value = raw.get(key, 2)
        if type(value) is not int or value < 0:
            raise ValueError(f"material_handling_config.{key} must be a non-negative integer")
        result[key] = value
    source = raw.get("raw_material_source")
    if source is not None:
        if not isinstance(source, (list, tuple)) or len(source) != 2 or any(type(v) is not int for v in source):
            raise ValueError("material_handling_config.raw_material_source must be [x, y] integers")
        width = int(topology.get("gridWidth", 20))
        height = int(topology.get("gridHeight", 20))
        if not (0 <= source[0] < width and 0 <= source[1] < height):
            raise ValueError("material_handling_config.raw_material_source must be inside the configured map")
        result["raw_material_source"] = list(source)
    return result


def _exception_config(raw: dict | None, obs_radius: int) -> dict | None:
    if not raw:
        return raw
    normalized = deepcopy(raw)
    for event in normalized.get("schedule", []) or []:
        if event.get("type") == "temporary_obstacle" and isinstance(event.get("cell"), (list, tuple)):
            cell = event["cell"]
            if len(cell) >= 2:
                event["cell"] = [int(cell[0]) + obs_radius, int(cell[1]) + obs_radius]
    return normalized


def create_env_from_config(config: dict, mapf_algorithm: str = "astar") -> GridFactoryEnv:
    """Create the shared Grid environment from the public factory schema."""
    topology = config.get("topology", {})
    agvs = config.get("agvs", [])
    machines = topology.get("machines") or {}
    machine_values = list(machines.values())
    machine_positions = [tuple(m["location"]) for m in machine_values if "location" in m]
    agent_positions = [tuple(a["initialLocation"]) for a in agvs]
    material = _material_config(config.get("material_handling_config"), topology)
    extra = [tuple(material["raw_material_source"])] if material.get("raw_material_source") else []

    raw_map = config.get("map")
    if raw_map:
        map_text = "\n".join(str(raw_map).splitlines())
        map_width = max((len(line) for line in map_text.splitlines()), default=20)
        map_height = len(map_text.splitlines())
    else:
        map_text = _obstacle_map(topology, machine_positions, agent_positions, extra)
        map_width = int(topology.get("gridWidth", 20))
        map_height = int(topology.get("gridHeight", 20))
    num_agents = len(agent_positions) or int(config.get("num_agents", 4))

    grid_kwargs = dict(
        size=max(int(topology.get("gridWidth", map_width)), int(topology.get("gridHeight", map_height)), map_width, map_height),
        map=map_text,
        num_agents=num_agents,
        seed=int(config.get("seed", 42)),
        max_episode_steps=int((config.get("simulation_control") or {}).get("max_steps", 256)),
        obs_radius=int(config.get("obs_radius", 5)),
        on_target="restart",
        agents_xy=agent_positions or None,
        targets_xy=agent_positions or None,
    )
    if mapf_algorithm in {"mapf_gpt", "flow_rl"}:
        grid_kwargs["observation_type"] = "MAPF"
    grid = GridConfig(**grid_kwargs)

    custom_jobs = []
    durations = []
    jobs = config.get("jobs") or {}
    job_list = jobs.get("job_list", []) if isinstance(jobs, dict) else jobs
    for job in job_list:
        operations = []
        for operation in job.get("operations", []):
            options = operation.get("machine_options_with_time")
            if options is None and operation.get("machine_id") is not None and operation.get("duration") is not None:
                options = [[operation["machine_id"], operation["duration"]]]
            if not isinstance(options, list) or not options:
                raise ValueError("each operation must define machine_options_with_time")
            normalized = [(int(machine), float(duration)) for machine, duration in options]
            durations.extend(duration for _, duration in normalized)
            operations.append((normalized, normalized[0][1], _processing_config(operation)))
        custom_jobs.append(operations)

    job_config = JobConfig(
        num_jobs=len(custom_jobs),
        min_ops_per_job=min((len(job) for job in custom_jobs), default=1),
        max_ops_per_job=max((len(job) for job in custom_jobs), default=1),
        min_proc_time=min(durations, default=1),
        max_proc_time=max(durations, default=1),
        machine_choices=1,
        total_machines=len(machine_values) or int(config.get("machine_count", 5)),
        seed=int(config.get("seed", 42)),
        strategy="custom_time",
        custom_jobs=custom_jobs,
    )
    machine_count = len(machine_values) or int(config.get("machine_count", 5))
    machine_strategy = config.get("machine_strategy", "custom" if machine_positions else "random")
    machine_config = MachineConfig(
        num_machines=machine_count,
        strategy=machine_strategy,
        custom_positions=machine_positions or None,
        seed=int(config.get("seed", 42)),
    )
    if machine_positions:
        grid.possible_targets_xy = machine_positions
    return GridFactoryEnv(
        grid_config=grid,
        machine_config=machine_config,
        job_config=job_config,
        random_target=False,
        exception_config=_exception_config(config.get("exception_config"), grid.obs_radius or 0),
        processing_time_config=config.get("processing_time_config"),
        material_handling_config=material,
    )


class SimulationSession:
    """Framework-independent environment/coordinator session."""

    def __init__(self, env: GridFactoryEnv, coordinator: Coordinator):
        self.env = env
        self.coordinator = coordinator
        self.obs, self.info = self.env.reset()
        self.step_index = 0
        self.done = False
        self.last_actions = None

    @classmethod
    def from_config(
        cls,
        config: dict,
        *,
        job_solver: str = "greedy",
        route_solver: str = "astar",
        assigner: str = "nearest",
        mapf_algorithm: str | None = None,
        **kwargs,
    ):
        env = create_env_from_config(config, mapf_algorithm or route_solver)
        coordinator = Coordinator(
            job_solver=job_solver,
            route_solver=route_solver,
            assigner=assigner,
            **kwargs,
        )
        return cls(env, coordinator)

    @classmethod
    def from_environment(cls, env: GridFactoryEnv, *, job_solver: str = "greedy", route_solver: str = "astar", assigner: str = "nearest", **kwargs):
        coordinator = Coordinator(
            job_solver=job_solver,
            route_solver=route_solver,
            assigner=assigner,
            **kwargs,
        )
        return cls(env, coordinator)

    def reset(self):
        reset = getattr(self.coordinator, "reset", None)
        if callable(reset):
            reset()
        else:
            for component in (
                self.coordinator.job_solver,
                self.coordinator.route_solver,
                self.coordinator.assigner,
            ):
                reset = getattr(component, "reset", None)
                if callable(reset):
                    reset()
        self.obs, self.info = self.env.reset()
        self.step_index = 0
        self.done = False
        self.last_actions = None
        return self.obs, self.info

    def step(self, actions: dict | None = None):
        if self.done:
            raise RuntimeError("simulation session is already done")
        if actions is None:
            actions = self.coordinator.decide(self.obs)
        self.last_actions = actions
        self.obs, rewards, terminations, truncations, infos = self.env.step(actions)
        self.step_index += 1
        agent_truncated = truncations.get("agent_truncated", {})
        self.done = bool(
            terminations.get("job_done")
            or (isinstance(agent_truncated, dict) and agent_truncated.get("__all__"))
            or truncations.get("__all__")
        )
        return self.obs, rewards, terminations, truncations, infos

    def inject_exception(self, event: dict, step: int | None = None) -> dict:
        injector = getattr(self.env, "exception_injector", None)
        if injector is None:
            return {"status": "error", "message": "ExceptionInjector is unavailable"}
        return injector.inject_manual(self.env.pogema_env, self.step_index if step is None else step, event)

    def clear_exceptions(self, target: dict | None = None, step: int | None = None) -> dict:
        injector = getattr(self.env, "exception_injector", None)
        if injector is None:
            return {"status": "error", "message": "ExceptionInjector is unavailable"}
        return injector.clear_manual(
            self.env.pogema_env,
            self.step_index if step is None else step,
            target,
        )

    def insert_jobs(self, new_jobs: list, replanned_keys: set[tuple[int, int]]) -> None:
        """Install jobs and discard superseded pending transfers atomically."""
        pogema = self.env.pogema_env
        for job in new_jobs:
            pogema.jobs.append(job)
            for operation in job.ops:
                pogema.hash_operations[(job.job_id, operation.op_id)] = operation
        for attr in ("pending_transfers", "buffered_tasks", "transfers_to_assign"):
            current = list(getattr(pogema, attr, []))
            setattr(pogema, attr, [
                task for task in current
                if (task.job_id, task.op_id) not in replanned_keys
            ])
        pogema.job_epoch += 1
        self.obs["job_observation"]["jobs"] = pogema.jobs
        self.obs["job_observation"]["machines"] = pogema.machines
        self.obs["task_observation"]["pending_transfers"] = pogema.transfers_to_assign

    def state_frame(self) -> dict:
        pogema = self.env.pogema_env
        agvs = pogema.get_agv_info()
        return {
            "timestamp": f"T+{self.step_index}s",
            "grid_state": {
                "positions_xy": [list(agv.pos) for agv in agvs],
                "is_active": [agv.current_task is not None for agv in agvs],
            },
            "machines": [
                {"id": machine.id, "location": list(machine.location),
                 "current_op": (machine.current_op.job_id, machine.current_op.op_id) if machine.current_op else None}
                for machine in pogema.machines
            ],
            "active_transfers": [
                {"task_id": task.task_id, "job_id": task.job_id, "op_id": task.op_id,
                 "source": list(task.source), "destination": list(task.destination) if task.destination else None}
                for task in pogema.active_transfers
            ],
        }

    def metrics(self) -> dict:
        return self.env.metrics_hub.get_episode_summary()

    def heatmaps(self) -> dict:
        return self.env.metrics_hub.get_heatmaps()

    def close(self) -> None:
        for component in (
            self.coordinator.job_solver,
            self.coordinator.route_solver,
            self.coordinator.assigner,
        ):
            close = getattr(component, "close", None)
            if callable(close):
                close()
        close = getattr(self.env, "close", None)
        if callable(close):
            close()
