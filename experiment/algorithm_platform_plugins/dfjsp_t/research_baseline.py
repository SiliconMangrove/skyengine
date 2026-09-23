"""MA + PIBT research baseline, adapted to observable finite-buffer DFJSP-T.

He et al. (2022), doi:10.20965/jaciii.2022.p0974: operation / machine / AGV
chromosomes, genetic search and variable-neighborhood improvement. This is an
online adaptation, not a verbatim reproduction of their battery model or results.
PIBT handles grid conflicts; buffer_safety supplies the extra material admission
constraints absent from the cited scheduling model. No training corpus is used.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Mapping

from experiment.algorithm_platform.models import (
    ArtifactKind, ArtifactRef, DecisionRequest, DecisionResponse,
    DecisionStatus, Feedback, RunContext,
)
from .buffer_safety import buffer_claims, safe_to_admit
from .pibt import PIBTRouter

Key = tuple[int, int]


@dataclass
class Schedule:
    order: list[Key]
    machines: dict[Key, int]
    vehicles: dict[Key, int]

    def copy(self) -> Schedule:
        return Schedule(list(self.order), dict(self.machines), dict(self.vehicles))


class MemeticPIBTPolicy:
    def __init__(self, parameters: Mapping[str, object]):
        self.parameters: dict = dict(parameters)
        self.population_size: int = int(parameters.get("population_size", 48))
        self.generations: int = int(parameters.get("generations", 20))
        self.local_search_steps: int = int(parameters.get("local_search_steps", 12))
        self.horizon_operations: int = int(parameters.get("horizon_operations", 32))
        self.lookahead: int = int(parameters.get("lookahead_per_job", 4))
        self.dispatch_batch_size: int = int(parameters.get("dispatch_batch_size", 4))
        self.replan_interval: int = int(parameters.get("replan_interval", 8))
        self.context: RunContext | None = None
        self.rng: random.Random = random.Random(0)
        self.router: PIBTRouter = PIBTRouter(0)
        self.plan: Schedule = Schedule([], {}, {})
        self.preferred_vehicles: dict[Key, int] = {}
        self.next_replan: float = 0
        self.reports: list[dict] = []
        self.last_finished: int = 0
        self.last_progress: float = 0

    def initialize(self, context: RunContext) -> None:
        self.context = context
        self.rng = random.Random(context.run.seeds.algorithm)
        self.router = PIBTRouter(context.run.seeds.algorithm)
        self.plan = Schedule([], {}, {})
        self.preferred_vehicles.clear()
        self.reports.clear()
        self.next_replan = self.last_progress = 0
        self.last_finished = 0

    def decide(self, request: DecisionRequest[Mapping[str, object]]) -> DecisionResponse[dict]:
        observed: dict = request.observation["planning_observation"]
        state: dict = {**observed, "jobs": [{**job, "ops": [dict(op) for op in job["ops"]]}
                                          for job in observed["jobs"]]}
        self.router.update_grid(state)
        jobs: dict[int, dict] = {job["job_id"]: job for job in state["jobs"]}
        machines: dict[int, dict] = {m["id"]: m for m in state["machines"]}
        by_location: dict[tuple, dict] = {tuple(m["location"]): m for m in state["machines"]}
        ready: dict[Key, dict] = {}
        finished: int = 0
        for jid, job in jobs.items():
            finished += sum(op["status"] == "FINISHED" for op in job["ops"])
            if job["carrier_id"] is not None or job["transport_task_id"] is not None:
                continue
            for op in job["ops"]:
                if op["status"] == "FINISHED":
                    continue
                source: tuple = tuple(job["material_location"])
                material_ready: bool = (op["op_id"] == 0 or source not in by_location
                                        or jid in by_location[source]["buffer_jobs"])
                if op["status"] == "PENDING" and op["assigned_machine"] is None and material_ready:
                    ready[(jid, op["op_id"])] = op
                break
        if finished != self.last_finished:
            self.last_finished = finished
            self.last_progress = request.simulation_time

        healthy: list[int] = [agent["id"] for agent in state["agents"] if agent["status"] == "OK"]
        if ready and healthy and request.simulation_time >= self.next_replan:
            self.plan, score = self._search(state, ready, healthy)
            self.next_replan = request.simulation_time + self.replan_interval
            self.reports.append({"step": request.simulation_time, "predicted_makespan": score[0],
                                 "predicted_flow_sum": score[1], "horizon_operations": len(self.plan.order)})

        claims: dict[int, set[int]] = buffer_claims(state)
        production: list[dict] = []
        rejected: int = 0
        for key in self.plan.order:
            if key not in ready:
                continue
            jid, oid = key
            mid: int = self.plan.machines[key]
            if machines[mid]["status"] != "OK":
                continue
            if not safe_to_admit(state, claims, jid, oid, mid):
                rejected += 1
                continue
            production.append({"job_id": jid, "op_id": oid, "machine_id": mid})
            claims[mid].add(jid)
            jobs[jid]["ops"][oid]["assigned_machine"] = mid
            self.preferred_vehicles[key] = self.plan.vehicles[key]
            if len(production) >= self.dispatch_batch_size:
                break

        logistics: list[dict] = self._assign(state)
        assigned: set[int] = {item["agv_id"] for item in logistics}
        route: list[dict] = self.router.plan(state, assigned)
        return DecisionResponse(
            request_id=request.request_id, status=DecisionStatus.FEASIBLE,
            action={"native_action": {"production": production, "logistics": logistics, "route": route}},
            diagnostics={"ready_operations": len(ready), "admission_deferred": rejected,
                         "completed_operations": finished,
                         "steps_since_operation_progress": request.simulation_time - self.last_progress,
                         "research_baseline": "MA-PIBT with finite-buffer safety admission"},
        )

    def _assign(self, state: dict) -> list[dict]:
        idle: dict[int, dict] = {a["id"]: a for a in state["agents"]
                                 if a["current_task"] is None and a["status"] == "OK"}
        ready_ids: set[int] = set(state["ready_task_ids"])
        tasks: list[dict] = sorted((task for task in state["tasks"] if task["task_id"] in ready_ids),
                                  key=lambda t: (t["priority"] < 200, t["kind"] != "finished_goods",
                                                 t["create_time"], t["task_id"]))
        result: list[dict] = []
        for task in tasks:
            if not idle:
                break
            target: tuple = tuple(task["source"])
            reachable: list[int] = [aid for aid, agent in idle.items()
                                    if math.isfinite(self.router.distance(tuple(agent["pos"]), target))]
            if not reachable:
                continue
            preferred: int | None = self.preferred_vehicles.get((task["job_id"], task["op_id"]))
            aid: int = min(reachable, key=lambda i: (i != preferred,
                                                    self.router.distance(tuple(idle[i]["pos"]), target), i))
            result.append({"agv_id": aid, "task_id": task["task_id"]})
            del idle[aid]
        return result

    def _search(self, state: dict, ready: dict[Key, dict], vehicles: list[int]) -> tuple[Schedule, tuple]:
        jobs: dict[int, dict] = {job["job_id"]: job for job in state["jobs"]}
        # Prioritize existing WIP in the finite horizon to avoid starving its drain.
        roots: list[Key] = sorted(ready, key=lambda key: (jobs[key[0]]["priority"] < 200,
                                                         key[1] == 0, key))
        keys: list[Key] = []
        for depth in range(self.lookahead):
            for jid, oid in roots:
                index: int = oid + depth
                if index < len(jobs[jid]["ops"]) and len(keys) < self.horizon_operations:
                    keys.append((jid, index))
        ops: dict[Key, dict] = {key: jobs[key[0]]["ops"][key[1]] for key in keys}
        first: Schedule = Schedule(keys, {}, {})
        for key, op in ops.items():
            options: list = op["machine_options_with_time"]
            first.machines[key] = min(options, key=lambda option: (option[1], option[0]))[0]
            first.vehicles[key] = vehicles[len(first.vehicles) % len(vehicles)]
        population: list[Schedule] = [first]
        while len(population) < self.population_size:
            candidate: Schedule = first.copy()
            self.rng.shuffle(candidate.order)
            candidate.machines = {key: self.rng.choice(op["machine_options_with_time"])[0] for key, op in ops.items()}
            candidate.vehicles = {key: self.rng.choice(vehicles) for key in keys}
            population.append(candidate)

        def evaluate(plan: Schedule) -> tuple[float, float]:
            return self._decode(plan, state, ops)

        scored: list[tuple[tuple, Schedule]] = [(evaluate(plan), plan) for plan in population]
        for _ in range(self.generations):
            scored.sort(key=lambda item: item[0])
            next_population: list[Schedule] = [scored[0][1].copy(), scored[1][1].copy()]
            # VNS improves the elite by changing OS, MS, and AS in turn.
            elite: Schedule = next_population[0]
            best_score: tuple = scored[0][0]
            for step in range(self.local_search_steps):
                neighbor: Schedule = elite.copy()
                self._mutate(neighbor, ops, vehicles, step % 3)
                score: tuple = evaluate(neighbor)
                if score < best_score:
                    elite, best_score = neighbor, score
            next_population[0] = elite
            while len(next_population) < self.population_size:
                parents: list[Schedule] = [min(self.rng.sample(scored, 3), key=lambda item: item[0])[1] for _ in range(2)]
                retained_jobs: set[int] = {jid for jid in {key[0] for key in keys} if self.rng.random() < .5}
                remainder: list[Key] = [key for key in parents[1].order if key[0] not in retained_jobs]
                cursor: int = 0
                order: list[Key] = []
                for key in parents[0].order:
                    if key[0] in retained_jobs:
                        order.append(key)
                    else:
                        order.append(remainder[cursor])
                        cursor += 1
                child: Schedule = Schedule(order, {}, {})
                for key in keys:
                    child.machines[key] = parents[self.rng.randrange(2)].machines[key]
                    child.vehicles[key] = parents[self.rng.randrange(2)].vehicles[key]
                self._mutate(child, ops, vehicles, self.rng.randrange(3))
                next_population.append(child)
            scored = [(evaluate(plan), plan) for plan in next_population]
        best_score, best = min(scored, key=lambda item: item[0])
        return best, best_score

    def _mutate(self, plan: Schedule, ops: dict, vehicles: list[int], neighborhood: int) -> None:
        key: Key = self.rng.choice(plan.order)
        if neighborhood == 0:
            plan.order.remove(key)
            plan.order.insert(self.rng.randrange(len(plan.order) + 1), key)
        elif neighborhood == 1:
            plan.machines[key] = self.rng.choice(ops[key]["machine_options_with_time"])[0]
        else:
            plan.vehicles[key] = self.rng.choice(vehicles)

    def _decode(self, plan: Schedule, state: dict, ops: dict) -> tuple[float, float]:
        now: float = float(state["time"])
        jobs: dict[int, dict] = {job["job_id"]: job for job in state["jobs"]}
        all_ops: dict[Key, dict] = {(job["job_id"], op["op_id"]): op for job in state["jobs"] for op in job["ops"]}
        machines: dict[int, dict] = {m["id"]: m for m in state["machines"]}
        machine_time: dict[int, float] = {}
        for mid, machine in machines.items():
            workload: list = [*machine["input_queue"], *machine["suspended_ops"]]
            if machine["current_op"] is not None:
                workload.append(machine["current_op"])
            remaining: float = sum(float(all_ops[tuple(key)]["remaining_proc_time"] or 0) for key in workload)
            repair: float = (0 if machine["status"] == "OK" else _expected_repair_remaining(
                state["failure_priors"]["machine_failure"]["repair_time"], machine["down_elapsed"]))
            machine_time[mid] = now + remaining + repair
        vehicle_time: dict[int, float] = {}
        vehicle_pos: dict[int, tuple] = {}
        pickup: int = state["pickup_steps"]
        dropoff: int = state["dropoff_steps"]
        for agent in state["agents"]:
            aid: int = agent["id"]
            pos: tuple = tuple(agent["pos"])
            available: float = now
            task: dict | None = agent["current_task"]
            if task is not None:
                source: tuple = tuple(task["source"])
                destination: tuple = tuple(task["destination"])
                if not agent["loaded"]:
                    available += self.router.distance(pos, source) + (agent["handling_remaining"] if agent["task_phase"] == "PICKING" else pickup)
                    pos = source
                available += self.router.distance(pos, destination) + (agent["handling_remaining"] if agent["task_phase"] == "DROPPING" else dropoff)
                pos = destination
            vehicle_time[aid], vehicle_pos[aid] = available, pos
        job_time: dict[int, float] = {jid: now for jid in jobs}
        job_pos: dict[int, tuple] = {jid: tuple(job["material_location"] or job["raw_material_source"]) for jid, job in jobs.items()}
        pending: set[Key] = set(plan.order)
        while pending:
            key: Key = next(key for key in plan.order if key in pending and (key[0], key[1] - 1) not in pending)
            jid, _ = key
            mid: int = plan.machines[key]
            aid: int = plan.vehicles[key]
            target: tuple = tuple(machines[mid]["location"])
            arrival: float = job_time[jid]
            if job_pos[jid] != target:
                departure: float = max(job_time[jid], vehicle_time[aid] + self.router.distance(vehicle_pos[aid], job_pos[jid]))
                arrival = departure + pickup + self.router.distance(job_pos[jid], target) + dropoff
                vehicle_time[aid], vehicle_pos[aid] = arrival, target
            duration: float = float(dict(ops[key]["machine_options_with_time"])[mid])
            end: float = max(arrival, machine_time[mid]) + duration
            machine_time[mid] = job_time[jid] = end
            job_pos[jid] = target
            pending.remove(key)
        # Include shipping in the objective, as the simulator finishes at delivery.
        for jid in {key[0] for key in plan.order}:
            if jobs[jid]["ops"][-1]["op_id"] == max(key[1] for key in plan.order if key[0] == jid):
                sink: tuple = tuple(jobs[jid]["finished_goods_destination"])
                choices: list[tuple] = [(max(job_time[jid], vehicle_time[aid] + self.router.distance(vehicle_pos[aid], job_pos[jid]))
                                         + pickup + self.router.distance(job_pos[jid], sink) + dropoff, aid)
                                        for aid in set(plan.vehicles.values())]
                finish, aid = min(choices)
                vehicle_time[aid], vehicle_pos[aid], job_time[jid] = finish, sink, finish
        selected_times: list[float] = [job_time[jid] for jid in {key[0] for key in plan.order}]
        return max(selected_times), sum(selected_times)

    def observe(self, feedback: Feedback) -> None:
        if not feedback.accepted:
            self.next_replan = 0

    def finalize(self) -> tuple[ArtifactRef, ...]:
        return (self.context.artifact_publisher.publish_json(
            {"algorithm": "memetic_pibt", "parameters": self.parameters, "searches": self.reports,
             "sources": ["https://doi.org/10.20965/jaciii.2022.p0974", "https://kei18.github.io/pibt2/"],
             "adaptations": ["rolling visible horizon", "finite-buffer safe admission", "PIBT grid routing"],
             "reproduction": "adapted implementation; not the paper's original experimental configuration"},
            kind=ArtifactKind.REPORT, metadata={"domain": "dfjsp_t", "report_type": "research_baseline"}),)


def _expected_repair_remaining(prior: Mapping[str, object] | int | None, elapsed: int) -> float:
    """E[D - elapsed | D > elapsed] for the injector's public integer law."""
    if prior is None or isinstance(prior, int):
        return float(max(1, (1 if prior is None else prior) - elapsed))
    kind: str = str(prior.get("dist", "fixed")).lower()
    if kind in {"uniform", "discrete_uniform"}:
        low, high = int(prior.get("low", 1)), int(prior.get("high", 1))
        lower: int = max(1, elapsed + 1, low)
        if high >= lower:
            return (lower + high) / 2 - elapsed
    elif kind in {"triangular", "discrete_triangular"}:
        low: int = int(prior.get("low", 1))
        high: int = int(prior.get("high", low))
        mode: int = int(prior.get("mode", low))
        if high == low:
            return float(max(1, low - elapsed))
        mass: float = 0.0
        residual: float = 0.0
        for duration in range(max(1, elapsed + 1, low), high + 1):
            cdf: list[float] = []
            for boundary in (float("-inf") if duration == 1 else duration - .5, duration + .5):
                if boundary <= low:
                    cdf.append(0.0)
                elif boundary >= high:
                    cdf.append(1.0)
                elif boundary < mode:
                    cdf.append((boundary - low) ** 2 / ((high - low) * (mode - low)))
                else:
                    cdf.append(1 - (high - boundary) ** 2 / ((high - low) * (high - mode)))
            probability: float = cdf[1] - cdf[0]
            mass += probability
            residual += probability * (duration - elapsed)
        if mass > 0:
            return residual / mass
    else:
        # Fixed and unrecognized kinds follow ExceptionInjector._sample_duration.
        return float(max(1, int(prior.get("value", 1)) - elapsed))
    # An outage beyond the public support cannot expose its hidden recovery date.
    # Reassess on the next simulator tick until an observable recovery arrives.
    return 1.0
