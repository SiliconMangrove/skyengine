"""Shared nominal batch model, validation, and metrics for DFJSP-T."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from experiment.algorithm_platform.models import ValidationResult

from .domain import DFJSPT_URGENT_PRIORITY


OperationKey = tuple[int, int]


@dataclass(frozen=True, slots=True)
class BatchProblemView:
    jobs: tuple[Mapping[str, object], ...]
    jobs_by_id: Mapping[int, Mapping[str, object]]
    machine_positions: Mapping[int, tuple[int, int]]
    transport_times: Mapping[tuple[int | None, int], int]
    agv_capacity: int


def build_batch_view(instance: Mapping[str, object]) -> BatchProblemView:
    jobs_value = instance["jobs"]
    jobs = tuple(jobs_value["job_list"])
    jobs_by_id = {int(job["job_id"]): job for job in jobs}
    topology = instance["topology"]
    machine_positions = {
        int(machine_id): tuple(int(value) for value in machine["location"])
        for machine_id, machine in topology["machines"].items()
    }
    material = instance.get("material_handling_config", {}) or {}
    raw_source = material.get("raw_material_source")
    source_position = (
        None
        if raw_source is None
        else tuple(int(value) for value in raw_source)
    )
    pickup_dwell = int(material.get("pickup_dwell_steps", 2))
    dropoff_dwell = int(material.get("dropoff_dwell_steps", 2))
    grid = tuple(str(topology["map"]).splitlines())
    sources = set(machine_positions.values())
    if source_position is not None:
        sources.add(source_position)
    distances = {
        source: _shortest_paths(grid, source)
        for source in sources
    }
    transport_times: dict[tuple[int | None, int], int] = {}
    for destination_id, destination in machine_positions.items():
        if source_position is None:
            transport_times[(None, destination_id)] = 0
        else:
            transport_times[(None, destination_id)] = (
                distances[source_position][destination]
                + pickup_dwell
                + dropoff_dwell
            )
        for source_id, source in machine_positions.items():
            transport_times[(source_id, destination_id)] = (
                distances[source][destination]
                + pickup_dwell
                + dropoff_dwell
            )
    return BatchProblemView(
        jobs=jobs,
        jobs_by_id=MappingProxyType(jobs_by_id),
        machine_positions=MappingProxyType(machine_positions),
        transport_times=MappingProxyType(transport_times),
        agv_capacity=len(instance["agvs"]),
    )


def validate_batch_solution(
    instance: Mapping[str, object],
    solution: object,
) -> ValidationResult:
    if not isinstance(solution, Mapping):
        return ValidationResult(
            valid=False,
            violations=("solution must be an object",),
        )
    operation_values = solution.get("operations")
    transfer_values = solution.get("transfers")
    if not isinstance(operation_values, (list, tuple)):
        return ValidationResult(
            valid=False,
            violations=("solution.operations must be an array",),
        )
    if not isinstance(transfer_values, (list, tuple)):
        return ValidationResult(
            valid=False,
            violations=("solution.transfers must be an array",),
        )

    view = build_batch_view(instance)
    expected_keys = {
        (int(job["job_id"]), int(operation["op_id"]))
        for job in view.jobs
        for operation in job["operations"]
    }
    operations: dict[OperationKey, Mapping[str, object]] = {}
    transfers: dict[OperationKey, Mapping[str, object]] = {}
    violations: list[str] = []
    for index, operation in enumerate(operation_values):
        if not isinstance(operation, Mapping):
            violations.append(f"operations[{index}] must be an object")
            continue
        required = {"job_id", "op_id", "machine_id", "start", "end"}
        if not required.issubset(operation):
            violations.append(f"operations[{index}] is missing required fields")
            continue
        key = (int(operation["job_id"]), int(operation["op_id"]))
        if key in operations:
            violations.append(f"operation {key} appears more than once")
        operations[key] = operation
    for index, transfer in enumerate(transfer_values):
        if not isinstance(transfer, Mapping):
            violations.append(f"transfers[{index}] must be an object")
            continue
        required = {
            "job_id",
            "op_id",
            "source_machine_id",
            "destination_machine_id",
            "start",
            "end",
            "duration",
        }
        if not required.issubset(transfer):
            violations.append(f"transfers[{index}] is missing required fields")
            continue
        key = (int(transfer["job_id"]), int(transfer["op_id"]))
        if key in transfers:
            violations.append(f"transfer {key} appears more than once")
        transfers[key] = transfer

    missing_operations = sorted(expected_keys - set(operations))
    extra_operations = sorted(set(operations) - expected_keys)
    missing_transfers = sorted(expected_keys - set(transfers))
    extra_transfers = sorted(set(transfers) - expected_keys)
    if missing_operations:
        violations.append(f"solution is missing operations: {missing_operations}")
    if extra_operations:
        violations.append(f"solution contains unknown operations: {extra_operations}")
    if missing_transfers:
        violations.append(f"solution is missing transfers: {missing_transfers}")
    if extra_transfers:
        violations.append(f"solution contains unknown transfers: {extra_transfers}")
    if violations:
        return ValidationResult(valid=False, violations=tuple(violations))

    machine_intervals: dict[int, list[tuple[float, float, OperationKey]]] = {
        machine_id: [] for machine_id in view.machine_positions
    }
    transfer_intervals: list[tuple[float, float, OperationKey]] = []
    for job in view.jobs:
        job_id = int(job["job_id"])
        release = float(job.get("release", 0.0))
        previous_end = release
        previous_machine: int | None = None
        for source_operation in job["operations"]:
            operation_id = int(source_operation["op_id"])
            key = (job_id, operation_id)
            operation = operations[key]
            transfer = transfers[key]
            machine_id = int(operation["machine_id"])
            start = float(operation["start"])
            end = float(operation["end"])
            options = {
                int(candidate): float(duration)
                for candidate, duration in source_operation[
                    "machine_options_with_time"
                ]
            }
            if machine_id not in options:
                violations.append(f"operation {key} uses an ineligible machine")
                continue
            if start < release:
                violations.append(f"operation {key} starts before job release")
            if end <= start or abs((end - start) - options[machine_id]) > 1e-9:
                violations.append(f"operation {key} has an invalid processing interval")
            if start < previous_end:
                violations.append(f"operation {key} violates job precedence")
            machine_intervals[machine_id].append((start, end, key))

            source_machine = transfer["source_machine_id"]
            if source_machine is not None:
                source_machine = int(source_machine)
            destination_machine = int(transfer["destination_machine_id"])
            transfer_start = float(transfer["start"])
            transfer_end = float(transfer["end"])
            duration = float(transfer["duration"])
            if source_machine != previous_machine:
                violations.append(f"transfer {key} has an invalid source machine")
            if destination_machine != machine_id:
                violations.append(f"transfer {key} has an invalid destination machine")
            expected_duration = float(
                view.transport_times[(previous_machine, machine_id)]
            )
            if abs(duration - expected_duration) > 1e-9:
                violations.append(f"transfer {key} has an invalid duration")
            if abs((transfer_end - transfer_start) - duration) > 1e-9:
                violations.append(f"transfer {key} has an invalid interval")
            if transfer_start < previous_end or transfer_end > start:
                violations.append(f"transfer {key} violates material precedence")
            transfer_intervals.append((transfer_start, transfer_end, key))
            previous_end = end
            previous_machine = machine_id

    for machine_id, intervals in machine_intervals.items():
        ordered = sorted(intervals)
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] < previous[1]:
                violations.append(
                    f"machine {machine_id} overlaps operations {previous[2]} and {current[2]}"
                )

    events = []
    for start, end, key in transfer_intervals:
        if end > start:
            events.append((start, 1, key))
            events.append((end, -1, key))
    active = 0
    for _, delta, key in sorted(events, key=lambda item: (item[0], item[1])):
        active += delta
        if active > view.agv_capacity:
            violations.append(
                f"transfer {key} exceeds the available AGV capacity"
            )
            break
    return ValidationResult(valid=not violations, violations=tuple(violations))


def evaluate_batch_solution(
    instance: Mapping[str, object],
    solution: Mapping[str, object],
) -> dict[str, float]:
    view = build_batch_view(instance)
    operations = {
        (int(item["job_id"]), int(item["op_id"])): item
        for item in solution["operations"]
    }
    job_completion = {
        int(job["job_id"]): float(
            operations[
                (int(job["job_id"]), int(job["operations"][-1]["op_id"]))
            ]["end"]
        )
        for job in view.jobs
    }
    c_max = max(job_completion.values())
    urgent_completion = [
        job_completion[int(job["job_id"])]
        for job in view.jobs
        if int(job.get("priority", 0)) >= DFJSPT_URGENT_PRIORITY
    ]
    c_max_e = max(urgent_completion, default=0.0)
    total_tardiness = 0.0
    weighted_tardiness = 0.0
    for job in view.jobs:
        due = job.get("due")
        if due is None:
            continue
        tardiness = max(
            0.0,
            job_completion[int(job["job_id"])] - float(due),
        )
        total_tardiness += tardiness
        weighted_tardiness += tardiness * max(
            1.0,
            float(job.get("priority", 0)) / 100.0,
        )
    processing_time = sum(
        float(item["end"]) - float(item["start"])
        for item in solution["operations"]
    )
    transport_time = sum(
        float(item["duration"])
        for item in solution["transfers"]
    )
    return {
        "C_max": c_max,
        "C_max_E": c_max_e,
        "completed_jobs": float(len(view.jobs)),
        "unfinished_jobs": 0.0,
        "urgent_jobs": float(len(urgent_completion)),
        "unfinished_urgent_jobs": 0.0,
        "success_rate": 1.0,
        "job_completion_rate": 1.0,
        "completed_makespan": c_max,
        "full_makespan": c_max,
        "total_tardiness": total_tardiness,
        "weighted_tardiness": weighted_tardiness,
        "machine_utilization": processing_time
        / max(float(len(view.machine_positions)) * c_max, 1.0),
        "scheduled_transport_time": transport_time,
    }


def nominal_horizon(view: BatchProblemView) -> int:
    maximum_release = max(
        (int(job.get("release", 0)) for job in view.jobs),
        default=0,
    )
    processing = sum(
        max(int(duration) for _, duration in operation["machine_options_with_time"])
        for job in view.jobs
        for operation in job["operations"]
    )
    maximum_transfer = max(view.transport_times.values(), default=0)
    operation_count = sum(len(job["operations"]) for job in view.jobs)
    return maximum_release + processing + maximum_transfer * operation_count + 1


def _shortest_paths(
    grid: tuple[str, ...],
    source: tuple[int, int],
) -> dict[tuple[int, int], int]:
    width = max(len(row) for row in grid)
    height = len(grid)
    distances = {source: 0}
    queue = deque([source])
    while queue:
        x, y = queue.popleft()
        for delta_x, delta_y in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            target = (x + delta_x, y + delta_y)
            target_x, target_y = target
            if not 0 <= target_x < width or not 0 <= target_y < height:
                continue
            if target_x >= len(grid[target_y]) or grid[target_y][target_x] == "#":
                continue
            if target in distances:
                continue
            distances[target] = distances[(x, y)] + 1
            queue.append(target)
    return distances
