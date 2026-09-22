"""Rolling-horizon genetic scheduler for visible DFJSP-T state only."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Mapping

from experiment.algorithm_platform.models import (
    ArtifactKind,
    ArtifactRef,
    DecisionRequest,
    DecisionResponse,
    DecisionStatus,
    Feedback,
    RunContext,
)

from .domain import DFJSPT_URGENT_PRIORITY
from .rules import _machine_load, _minimum_processing_time, _ready_operations


OperationKey = tuple[int, int]


@dataclass(slots=True)
class _Individual:
    order: list[OperationKey]
    machines: dict[OperationKey, int]


@dataclass(frozen=True, slots=True)
class _Evaluation:
    objective: tuple[float, float, float]
    completion: Mapping[OperationKey, float]


class RollingHorizonGA:
    """Re-optimize a bounded visible horizon whenever an operation is ready."""

    def __init__(self, parameters: Mapping[str, object]):
        self._parameters = dict(parameters)
        self._population_size = int(parameters.get("population_size", 48))
        self._generations = int(parameters.get("generations", 40))
        self._horizon_operations = int(parameters.get("horizon_operations", 32))
        self._lookahead_per_job = int(parameters.get("lookahead_per_job", 4))
        self._dispatch_batch_size = int(parameters.get("dispatch_batch_size", 1))
        self._crossover_rate = float(parameters.get("crossover_rate", 0.85))
        self._mutation_rate = float(parameters.get("mutation_rate", 0.15))
        self._tournament_size = int(parameters.get("tournament_size", 3))
        self._elite_size = int(parameters.get("elite_size", 2))
        if self._population_size < 4:
            raise ValueError("population_size must be at least 4")
        if self._generations < 1:
            raise ValueError("generations must be positive")
        if self._horizon_operations < 1 or self._lookahead_per_job < 1:
            raise ValueError("rolling horizon limits must be positive")
        if self._dispatch_batch_size < 1:
            raise ValueError("dispatch_batch_size must be positive")
        if not 0.0 <= self._crossover_rate <= 1.0:
            raise ValueError("crossover_rate must be between zero and one")
        if not 0.0 <= self._mutation_rate <= 1.0:
            raise ValueError("mutation_rate must be between zero and one")
        if not 2 <= self._tournament_size <= self._population_size:
            raise ValueError("tournament_size is outside the population")
        if not 1 <= self._elite_size < self._population_size:
            raise ValueError("elite_size is outside the population")

        self._context: RunContext | None = None
        self._rng: random.Random | None = None
        self._dispatched: set[OperationKey] = set()
        self._decisions: list[dict[str, object]] = []
        self._search_reports: list[dict[str, object]] = []
        self._feedback: list[dict[str, object]] = []
        self._idle_decisions = 0

    def initialize(self, context: RunContext) -> None:
        self._context = context
        self._rng = random.Random(context.run.seeds.algorithm)
        self._dispatched.clear()
        self._decisions.clear()
        self._search_reports.clear()
        self._feedback.clear()
        self._idle_decisions = 0

    def decide(
        self,
        request: DecisionRequest[Mapping[str, object]],
    ) -> DecisionResponse[dict[str, object]]:
        if self._rng is None:
            raise RuntimeError("rolling-horizon GA was not initialized")
        ready = _ready_operations(request.observation, self._dispatched)
        if not ready:
            self._idle_decisions += 1
            return DecisionResponse(
                request_id=request.request_id,
                status=DecisionStatus.FEASIBLE,
                action={"dispatches": []},
                plan={"horizon": [], "selected": []},
                diagnostics={
                    "ready_operations": 0,
                    "idle_decisions": self._idle_decisions,
                },
            )

        horizon = _build_horizon(
            request.observation,
            ready,
            self._horizon_operations,
            self._lookahead_per_job,
        )
        jobs, operations = _indexed_problem(request.observation, horizon)
        population = self._initial_population(horizon, jobs, operations)
        generation_best: list[tuple[float, float, float]] = []
        evaluations: list[_Evaluation] = []

        for _ in range(self._generations):
            evaluations = [
                _evaluate_individual(
                    individual,
                    request.observation,
                    jobs,
                    operations,
                )
                for individual in population
            ]
            ranking = sorted(
                range(len(population)),
                key=lambda index: evaluations[index].objective,
            )
            generation_best.append(evaluations[ranking[0]].objective)
            next_population = [
                _clone(population[index])
                for index in ranking[: self._elite_size]
            ]
            while len(next_population) < self._population_size:
                first = self._tournament(population, evaluations)
                second = self._tournament(population, evaluations)
                child = (
                    self._crossover(first, second)
                    if self._rng.random() < self._crossover_rate
                    else _clone(first)
                )
                self._mutate(child, operations)
                next_population.append(child)
            population = next_population

        evaluations = [
            _evaluate_individual(
                individual,
                request.observation,
                jobs,
                operations,
            )
            for individual in population
        ]
        best_index = min(
            range(len(population)),
            key=lambda index: evaluations[index].objective,
        )
        best = population[best_index]
        best_evaluation = evaluations[best_index]
        ready_keys = {
            (int(job["job_id"]), int(operation["op_id"]))
            for job, operation in ready
        }
        selected_keys = [
            key for key in best.order if key in ready_keys
        ][: self._dispatch_batch_size]
        dispatches = [
            {
                "job_id": key[0],
                "op_id": key[1],
                "machine_id": best.machines[key],
            }
            for key in selected_keys
        ]
        for dispatch in dispatches:
            key = (dispatch["job_id"], dispatch["op_id"])
            self._dispatched.add(key)
            self._decisions.append(
                {
                    "simulation_time": float(request.simulation_time),
                    "state_version": int(request.state_version),
                    "dispatch": dispatch,
                    "predicted_completion": best_evaluation.completion[key],
                    "predicted_objective": best_evaluation.objective,
                }
            )

        search_report = {
            "simulation_time": float(request.simulation_time),
            "state_version": int(request.state_version),
            "horizon": [
                {"job_id": job_id, "op_id": operation_id}
                for job_id, operation_id in horizon
            ],
            "generation_best": generation_best,
            "best_objective": best_evaluation.objective,
            "selected": dispatches,
        }
        self._search_reports.append(search_report)
        return DecisionResponse(
            request_id=request.request_id,
            status=DecisionStatus.FEASIBLE,
            action={"dispatches": dispatches},
            plan={
                "horizon": search_report["horizon"],
                "selected": dispatches,
                "predicted_objective": best_evaluation.objective,
            },
            objective_values=best_evaluation.objective[:2],
            diagnostics={
                "population_size": self._population_size,
                "generations": self._generations,
                "horizon_operations": len(horizon),
                "ready_operations": len(ready),
                "dispatched_operations": len(dispatches),
            },
        )

    def observe(
        self,
        feedback: Feedback[dict[str, object], Mapping[str, object]],
    ) -> None:
        proposed = feedback.proposed_action or {}
        if proposed.get("dispatches"):
            self._feedback.append(
                {
                    "request_id": feedback.request_id,
                    "accepted": feedback.accepted,
                    "simulation_time": float(feedback.simulation_time),
                    "state_version": int(feedback.state_version),
                    "metrics": dict(feedback.metrics),
                }
            )

    def finalize(self) -> tuple[ArtifactRef, ...]:
        if self._context is None:
            raise RuntimeError("rolling-horizon GA was not initialized")
        solution = self._context.artifact_publisher.publish_json(
            {
                "schema_version": 1,
                "algorithm": self._context.run.algorithm.algorithm_id,
                "parameters": self._parameters,
                "dispatches": self._decisions,
                "feedback": self._feedback,
                "idle_decisions": self._idle_decisions,
            },
            kind=ArtifactKind.SOLUTION,
            metadata={
                "domain": "dfjsp_t",
                "solution_type": "rolling_horizon_dispatch_trace",
            },
        )
        report = self._context.artifact_publisher.publish_json(
            {
                "schema_version": 1,
                "objective": ["C_max_E", "C_max", "total_tardiness"],
                "searches": self._search_reports,
            },
            kind=ArtifactKind.REPORT,
            metadata={
                "domain": "dfjsp_t",
                "report_type": "rolling_horizon_ga_diagnostics",
            },
        )
        return solution, report

    def _initial_population(
        self,
        horizon: list[OperationKey],
        jobs: Mapping[int, Mapping[str, object]],
        operations: Mapping[OperationKey, Mapping[str, object]],
    ) -> list[_Individual]:
        assert self._rng is not None
        heuristic_order = sorted(
            horizon,
            key=lambda key: (
                0
                if int(jobs[key[0]]["priority"]) >= DFJSPT_URGENT_PRIORITY
                else 1,
                math.inf
                if jobs[key[0]]["due"] is None
                else float(jobs[key[0]]["due"]),
                _minimum_processing_time(operations[key]),
                key,
            ),
        )
        population = [
            _Individual(
                order=list(heuristic_order),
                machines={
                    key: min(
                        operations[key]["machine_options"],
                        key=lambda option: (
                            float(option["processing_time"]),
                            int(option["machine_id"]),
                        ),
                    )["machine_id"]
                    for key in horizon
                },
            )
        ]
        while len(population) < self._population_size:
            order = list(horizon)
            self._rng.shuffle(order)
            machines = {
                key: int(
                    self._rng.choice(operations[key]["machine_options"])[
                        "machine_id"
                    ]
                )
                for key in horizon
            }
            population.append(_Individual(order=order, machines=machines))
        return population

    def _tournament(
        self,
        population: list[_Individual],
        evaluations: list[_Evaluation],
    ) -> _Individual:
        assert self._rng is not None
        participants = self._rng.sample(
            range(len(population)),
            self._tournament_size,
        )
        winner = min(
            participants,
            key=lambda index: evaluations[index].objective,
        )
        return population[winner]

    def _crossover(
        self,
        first: _Individual,
        second: _Individual,
    ) -> _Individual:
        assert self._rng is not None
        if len(first.order) < 2:
            return _clone(first)
        start, end = sorted(self._rng.sample(range(len(first.order)), 2))
        end += 1
        segment = first.order[start:end]
        remainder = [key for key in second.order if key not in segment]
        order = remainder[:start] + segment + remainder[start:]
        machines = {
            key: (
                first.machines[key]
                if self._rng.random() < 0.5
                else second.machines[key]
            )
            for key in order
        }
        return _Individual(order=order, machines=machines)

    def _mutate(
        self,
        individual: _Individual,
        operations: Mapping[OperationKey, Mapping[str, object]],
    ) -> None:
        assert self._rng is not None
        if len(individual.order) > 1 and self._rng.random() < self._mutation_rate:
            first, second = self._rng.sample(range(len(individual.order)), 2)
            individual.order[first], individual.order[second] = (
                individual.order[second],
                individual.order[first],
            )
        if self._rng.random() < self._mutation_rate:
            key = self._rng.choice(individual.order)
            individual.machines[key] = int(
                self._rng.choice(operations[key]["machine_options"])["machine_id"]
            )


def _build_horizon(
    observation: Mapping[str, object],
    ready: list[tuple[Mapping[str, object], Mapping[str, object]]],
    horizon_operations: int,
    lookahead_per_job: int,
) -> list[OperationKey]:
    ready_by_job = {
        int(job["job_id"]): int(operation["op_id"])
        for job, operation in ready
    }
    jobs = sorted(
        (
            job
            for job in observation["jobs"]
            if int(job["job_id"]) in ready_by_job
        ),
        key=lambda job: (
            0
            if int(job["priority"]) >= DFJSPT_URGENT_PRIORITY
            else 1,
            math.inf if job["due"] is None else float(job["due"]),
            int(job["job_id"]),
        ),
    )
    horizon = []
    for depth in range(lookahead_per_job):
        for job in jobs:
            operation_id = ready_by_job[int(job["job_id"])] + depth
            if operation_id >= len(job["operations"]):
                continue
            operation = job["operations"][operation_id]
            if operation["status"] == "FINISHED":
                continue
            horizon.append((int(job["job_id"]), operation_id))
            if len(horizon) >= horizon_operations:
                return horizon
    return horizon


def _indexed_problem(
    observation: Mapping[str, object],
    horizon: list[OperationKey],
) -> tuple[
    dict[int, Mapping[str, object]],
    dict[OperationKey, Mapping[str, object]],
]:
    horizon_set = set(horizon)
    jobs = {int(job["job_id"]): job for job in observation["jobs"]}
    operations = {
        (job_id, int(operation["op_id"])): operation
        for job_id, job in jobs.items()
        for operation in job["operations"]
        if (job_id, int(operation["op_id"])) in horizon_set
    }
    return jobs, operations


def _evaluate_individual(
    individual: _Individual,
    observation: Mapping[str, object],
    jobs: Mapping[int, Mapping[str, object]],
    operations: Mapping[OperationKey, Mapping[str, object]],
) -> _Evaluation:
    now = float(observation["simulation_time"])
    machine_rows = {
        int(machine["machine_id"]): machine
        for machine in observation["machines"]
    }
    machine_available = {
        machine_id: now + _machine_load(machine, observation["planning_observation"]["failure_priors"]["machine_failure"]["repair_time"])
        for machine_id, machine in machine_rows.items()
    }
    unscheduled = set(individual.order)
    job_available = {job_id: now for job_id in jobs}
    previous_machine: dict[int, int | None] = {}
    for job_id, job in jobs.items():
        first_horizon_op = min(
            (operation_id for candidate_job, operation_id in unscheduled if candidate_job == job_id),
            default=0,
        )
        if first_horizon_op > 0:
            previous_machine[job_id] = job["operations"][first_horizon_op - 1][
                "assigned_machine"
            ]
        else:
            previous_machine[job_id] = None

    completion: dict[OperationKey, float] = {}
    while unscheduled:
        key = next(
            candidate
            for candidate in individual.order
            if candidate in unscheduled
            and (candidate[0], candidate[1] - 1) not in unscheduled
        )
        job_id, operation_id = key
        operation = operations[key]
        machine_id = int(individual.machines[key])
        processing_time = next(
            float(option["processing_time"])
            for option in operation["machine_options"]
            if int(option["machine_id"]) == machine_id
        )
        source_machine = previous_machine[job_id]
        travel_time = 0.0
        if source_machine is not None:
            source = machine_rows[int(source_machine)]["location"]
            destination = machine_rows[machine_id]["location"]
            travel_time = float(
                abs(int(source[0]) - int(destination[0]))
                + abs(int(source[1]) - int(destination[1]))
            )
        start = max(
            machine_available[machine_id],
            job_available[job_id] + travel_time,
        )
        finish = start + processing_time
        machine_available[machine_id] = finish
        job_available[job_id] = finish
        previous_machine[job_id] = machine_id
        completion[key] = finish
        unscheduled.remove(key)

    job_completion = {
        job_id: max(
            (finish for key, finish in completion.items() if key[0] == job_id),
            default=now,
        )
        for job_id in jobs
    }
    urgent_completion = [
        finish
        for job_id, finish in job_completion.items()
        if int(jobs[job_id]["priority"]) >= DFJSPT_URGENT_PRIORITY
    ]
    makespan = max(job_completion.values(), default=now)
    urgent_makespan = max(urgent_completion, default=0.0)
    tardiness = sum(
        max(0.0, finish - float(jobs[job_id]["due"]))
        for job_id, finish in job_completion.items()
        if jobs[job_id]["due"] is not None
    )
    return _Evaluation(
        objective=(urgent_makespan, makespan, tardiness),
        completion=completion,
    )


def _clone(individual: _Individual) -> _Individual:
    return _Individual(
        order=list(individual.order),
        machines=dict(individual.machines),
    )
