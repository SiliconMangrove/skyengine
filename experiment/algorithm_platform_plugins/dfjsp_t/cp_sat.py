"""OR-Tools CP-SAT nominal reference solver for complete DFJSP-T instances."""

from __future__ import annotations

from typing import Mapping

from ortools.sat.python import cp_model

from experiment.algorithm_platform.models import (
    ArtifactKind,
    ArtifactRef,
    Candidate,
    InfeasibleSolutionError,
    RunContext,
)

from .batch import BatchProblemView, build_batch_view, nominal_horizon
from .domain import DFJSPTProblem, DFJSPT_URGENT_PRIORITY


OperationKey = tuple[int, int]


class DFJSPTCPsatSolver:
    """Solve the nominal full-instance scheduling reference with CP-SAT.

    The model includes alternative machines, releases, processing precedence,
    obstacle-aware shortest transfer durations, material-handling dwell time,
    and aggregate AGV capacity. Dynamic failures and route conflicts remain in
    the online simulator and are intentionally not represented as an oracle.
    """

    def __init__(self, parameters: Mapping[str, object]):
        self._parameters = dict(parameters)
        self._time_limit_seconds = float(
            parameters.get("time_limit_seconds", 300.0)
        )
        self._num_workers = int(parameters.get("num_workers", 1))
        self._log_search_progress = bool(
            parameters.get("log_search_progress", False)
        )
        if self._time_limit_seconds <= 0.0:
            raise ValueError("time_limit_seconds must be positive")
        if self._num_workers < 1:
            raise ValueError("num_workers must be positive")
        self._context: RunContext | None = None
        self._report: dict[str, object] | None = None

    def solve(
        self,
        problem: DFJSPTProblem,
        context: RunContext,
    ) -> Candidate[dict[str, object]]:
        self._context = context
        view = build_batch_view(problem.instance)
        horizon = nominal_horizon(view)
        model = cp_model.CpModel()
        starts: dict[OperationKey, object] = {}
        ends: dict[OperationKey, object] = {}
        choices: dict[OperationKey, dict[int, object]] = {}
        processing_times: dict[tuple[OperationKey, int], int] = {}
        machine_intervals: dict[int, list[object]] = {
            machine_id: [] for machine_id in view.machine_positions
        }

        for job in view.jobs:
            job_id = int(job["job_id"])
            for source_operation in job["operations"]:
                operation_id = int(source_operation["op_id"])
                key = (job_id, operation_id)
                master_start = model.NewIntVar(
                    0,
                    horizon,
                    f"operation_{job_id}_{operation_id}_start",
                )
                master_end = model.NewIntVar(
                    0,
                    horizon,
                    f"operation_{job_id}_{operation_id}_end",
                )
                starts[key] = master_start
                ends[key] = master_end
                choices[key] = {}
                literals = []
                for machine_id_value, duration_value in source_operation[
                    "machine_options_with_time"
                ]:
                    machine_id = int(machine_id_value)
                    duration = _integer_time(
                        duration_value,
                        f"processing time for operation {key}",
                    )
                    selected = model.NewBoolVar(
                        f"operation_{job_id}_{operation_id}_machine_{machine_id}"
                    )
                    option_start = model.NewIntVar(
                        0,
                        horizon,
                        f"operation_{job_id}_{operation_id}_machine_{machine_id}_start",
                    )
                    option_end = model.NewIntVar(
                        0,
                        horizon,
                        f"operation_{job_id}_{operation_id}_machine_{machine_id}_end",
                    )
                    interval = model.NewOptionalIntervalVar(
                        option_start,
                        duration,
                        option_end,
                        selected,
                        f"operation_{job_id}_{operation_id}_machine_{machine_id}_interval",
                    )
                    model.Add(master_start == option_start).OnlyEnforceIf(selected)
                    model.Add(master_end == option_end).OnlyEnforceIf(selected)
                    choices[key][machine_id] = selected
                    processing_times[(key, machine_id)] = duration
                    machine_intervals[machine_id].append(interval)
                    literals.append(selected)
                model.AddExactlyOne(literals)

        for intervals in machine_intervals.values():
            model.AddNoOverlap(intervals)

        transfer_intervals: list[object] = []
        first_transfers: dict[tuple[OperationKey, int], tuple[object, object]] = {}
        pair_transfers: dict[
            tuple[OperationKey, int, int],
            tuple[object, object, object],
        ] = {}
        for job in view.jobs:
            job_id = int(job["job_id"])
            release = _integer_time(job.get("release", 0), f"release of job {job_id}")
            for source_operation in job["operations"]:
                operation_id = int(source_operation["op_id"])
                key = (job_id, operation_id)
                if operation_id == 0:
                    for machine_id, selected in choices[key].items():
                        duration = view.transport_times[(None, machine_id)]
                        transfer_start = model.NewIntVar(
                            0,
                            horizon,
                            f"transfer_{job_id}_{operation_id}_depot_{machine_id}_start",
                        )
                        transfer_end = model.NewIntVar(
                            0,
                            horizon,
                            f"transfer_{job_id}_{operation_id}_depot_{machine_id}_end",
                        )
                        interval = model.NewOptionalIntervalVar(
                            transfer_start,
                            duration,
                            transfer_end,
                            selected,
                            f"transfer_{job_id}_{operation_id}_depot_{machine_id}_interval",
                        )
                        model.Add(transfer_start >= release).OnlyEnforceIf(selected)
                        model.Add(starts[key] >= transfer_end).OnlyEnforceIf(selected)
                        first_transfers[(key, machine_id)] = (
                            transfer_start,
                            transfer_end,
                        )
                        transfer_intervals.append(interval)
                    continue

                previous_key = (job_id, operation_id - 1)
                for previous_machine, previous_selected in choices[
                    previous_key
                ].items():
                    for machine_id, selected in choices[key].items():
                        active = model.NewBoolVar(
                            f"transfer_{job_id}_{operation_id}_{previous_machine}_{machine_id}_active"
                        )
                        model.Add(active <= previous_selected)
                        model.Add(active <= selected)
                        model.Add(active >= previous_selected + selected - 1)
                        duration = view.transport_times[
                            (previous_machine, machine_id)
                        ]
                        transfer_start = model.NewIntVar(
                            0,
                            horizon,
                            f"transfer_{job_id}_{operation_id}_{previous_machine}_{machine_id}_start",
                        )
                        transfer_end = model.NewIntVar(
                            0,
                            horizon,
                            f"transfer_{job_id}_{operation_id}_{previous_machine}_{machine_id}_end",
                        )
                        interval = model.NewOptionalIntervalVar(
                            transfer_start,
                            duration,
                            transfer_end,
                            active,
                            f"transfer_{job_id}_{operation_id}_{previous_machine}_{machine_id}_interval",
                        )
                        model.Add(
                            transfer_start >= ends[previous_key]
                        ).OnlyEnforceIf(active)
                        model.Add(starts[key] >= transfer_end).OnlyEnforceIf(active)
                        pair_transfers[(key, previous_machine, machine_id)] = (
                            transfer_start,
                            transfer_end,
                            active,
                        )
                        transfer_intervals.append(interval)

        if transfer_intervals:
            model.AddCumulative(
                transfer_intervals,
                [1] * len(transfer_intervals),
                view.agv_capacity,
            )

        job_ends = [
            ends[(int(job["job_id"]), int(job["operations"][-1]["op_id"]))]
            for job in view.jobs
        ]
        c_max = model.NewIntVar(0, horizon, "C_max")
        model.AddMaxEquality(c_max, job_ends)
        urgent_ends = [
            ends[(int(job["job_id"]), int(job["operations"][-1]["op_id"]))]
            for job in view.jobs
            if int(job.get("priority", 0)) >= DFJSPT_URGENT_PRIORITY
        ]
        c_max_e = model.NewIntVar(0, horizon, "C_max_E")
        if urgent_ends:
            model.AddMaxEquality(c_max_e, urgent_ends)
        else:
            model.Add(c_max_e == 0)
        model.Minimize(c_max_e * (horizon + 1) + c_max)

        solver = cp_model.CpSolver()
        time_limit = self._time_limit_seconds
        if context.run.budget.wall_time_seconds is not None:
            time_limit = min(time_limit, context.run.budget.wall_time_seconds)
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_search_workers = self._num_workers
        solver.parameters.random_seed = context.run.seeds.algorithm
        solver.parameters.log_search_progress = self._log_search_progress
        status = solver.Solve(model)
        status_name = _status_name(status)
        self._report = {
            "schema_version": 1,
            "solver": "OR-Tools CP-SAT",
            "status": status_name,
            "optimal": status == cp_model.OPTIMAL,
            "time_limit_seconds": time_limit,
            "num_workers": self._num_workers,
            "wall_time_seconds": float(solver.WallTime()),
            "conflicts": int(solver.NumConflicts()),
            "branches": int(solver.NumBranches()),
            "nominal_model_limits": [
                "processing-time disturbances are evaluated only by the online simulator",
                "machine and AGV failures are evaluated only by the online simulator",
                "AGV route conflicts and deadheading are not modeled",
            ],
        }
        if status == cp_model.INFEASIBLE:
            raise InfeasibleSolutionError(
                "CP-SAT proved the nominal DFJSP-T instance infeasible",
                details={"solver_status": status_name},
            )
        if status == cp_model.UNKNOWN:
            raise TimeoutError(
                "CP-SAT reached its limit without a feasible schedule"
            )
        if status == cp_model.MODEL_INVALID:
            raise RuntimeError("CP-SAT rejected the nominal DFJSP-T model")

        selected_machine = {
            key: next(
                machine_id
                for machine_id, selected in candidates.items()
                if solver.BooleanValue(selected)
            )
            for key, candidates in choices.items()
        }
        operations = []
        transfers = []
        for job in view.jobs:
            job_id = int(job["job_id"])
            previous_machine: int | None = None
            for source_operation in job["operations"]:
                operation_id = int(source_operation["op_id"])
                key = (job_id, operation_id)
                machine_id = selected_machine[key]
                start = int(solver.Value(starts[key]))
                end = int(solver.Value(ends[key]))
                operations.append(
                    {
                        "job_id": job_id,
                        "op_id": operation_id,
                        "machine_id": machine_id,
                        "start": start,
                        "end": end,
                        "processing_time": processing_times[(key, machine_id)],
                    }
                )
                if previous_machine is None:
                    transfer_start_var, transfer_end_var = first_transfers[
                        (key, machine_id)
                    ]
                else:
                    transfer_start_var, transfer_end_var, _ = pair_transfers[
                        (key, previous_machine, machine_id)
                    ]
                transfer_start = int(solver.Value(transfer_start_var))
                transfer_end = int(solver.Value(transfer_end_var))
                transfers.append(
                    {
                        "job_id": job_id,
                        "op_id": operation_id,
                        "source_machine_id": previous_machine,
                        "destination_machine_id": machine_id,
                        "start": transfer_start,
                        "end": transfer_end,
                        "duration": transfer_end - transfer_start,
                    }
                )
                previous_machine = machine_id

        solution = {
            "schema_version": 1,
            "solver": "OR-Tools CP-SAT",
            "model": "nominal_dfjsp_t_with_aggregate_agv_capacity",
            "status": status_name,
            "operations": operations,
            "transfers": transfers,
            "predicted_objective": {
                "C_max_E": int(solver.Value(c_max_e)),
                "C_max": int(solver.Value(c_max)),
            },
        }
        self._report.update({
            "objective_value": float(solver.ObjectiveValue()),
            "best_objective_bound": float(solver.BestObjectiveBound()),
            "predicted_C_max_E": int(solver.Value(c_max_e)),
            "predicted_C_max": int(solver.Value(c_max)),
        })
        return Candidate(
            candidate_id=f"{context.run.run_id}:cp_sat",
            value=solution,
            metadata={
                "solver_status": status_name,
                "optimal": status == cp_model.OPTIMAL,
                "best_objective_bound": float(solver.BestObjectiveBound()),
            },
        )

    def finalize(self) -> tuple[ArtifactRef, ...]:
        if self._context is None or self._report is None:
            raise RuntimeError("CP-SAT solver did not produce a report")
        report = self._context.artifact_publisher.publish_json(
            self._report,
            kind=ArtifactKind.REPORT,
            metadata={
                "domain": "dfjsp_t",
                "report_type": "cp_sat_solver_diagnostics",
            },
        )
        return (report,)


def _integer_time(value: object, label: str) -> int:
    result = int(value)
    if float(value) != float(result):
        raise ValueError(f"{label} must be an integer number of simulation steps")
    return result


def _status_name(status: int) -> str:
    return {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
        cp_model.UNKNOWN: "UNKNOWN",
    }[status]
