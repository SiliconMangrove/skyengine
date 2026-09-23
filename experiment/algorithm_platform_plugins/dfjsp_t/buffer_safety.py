"""Finite-buffer admission for the research baseline, using a safe drain order.

This is a factory adaptation of the Banker's safety test, not a MAPF theorem.
A claim includes material in service and committed inbound material so that a
machine's output and a travelling job cannot consume the same last buffer slot.
Reference: https://www.cs.utexas.edu/~EWD/transcriptions/EWD06xx/EWD623.html
"""
from __future__ import annotations


def buffer_claims(state: dict) -> dict[int, set[int]]:
    claims: dict[int, set[int]] = {}
    by_location: dict[tuple, int] = {}
    for machine in state["machines"]:
        mid: int = machine["id"]
        by_location[tuple(machine["location"])] = mid
        claims[mid] = set(machine["buffer_jobs"])
        if machine["current_op"] is not None:
            claims[mid].add(machine["current_op"][0])
        claims[mid].update(key[0] for key in machine["suspended_ops"])
    for task in state["tasks"]:
        if task["kind"] == "operation":
            claims[by_location[tuple(task["destination"])]].add(task["job_id"])
    return claims


def safe_to_admit(state: dict, claims: dict[int, set[int]], job_id: int,
                  op_id: int, machine_id: int) -> bool:
    """Can every admitted job finish in some serial order after this commitment?

    While one job drains, other jobs retain all their claims. Each unfinished
    operation must have a machine with space excluding this job's own claims.
    A draining job holds at most one processing slot and one destination slot;
    its source slot is released by pickup. This is deliberately conservative.
    Transport reachability and temporary failures are handled separately.
    """
    capacities: dict[int, int] = {m["id"]: m["buffer_capacity"] for m in state["machines"]}
    trial: dict[int, set[int]] = {mid: set(owners) for mid, owners in claims.items()}
    trial[machine_id].add(job_id)
    if any(len(owners) > capacities[mid] for mid, owners in trial.items()):
        return False
    jobs: dict[int, dict] = {job["job_id"]: job for job in state["jobs"]}
    remaining: set[int] = set().union(*trial.values())
    while remaining:
        drained: bool = False
        for jid in sorted(remaining):
            free: set[int] = {mid for mid, owners in trial.items()
                              if len(owners - {jid}) < capacities[mid]}
            possible: bool = True
            for op in jobs[jid]["ops"]:
                if op["status"] == "FINISHED":
                    continue
                selected: int | None = (machine_id if (jid, op["op_id"]) == (job_id, op_id)
                                        else op["assigned_machine"])
                options: set[int] = ({selected} if selected is not None
                                     else {int(mid) for mid, _ in op["machine_options_with_time"]})
                if not options.intersection(free):
                    possible = False
                    break
            if possible:
                for owners in trial.values():
                    owners.discard(jid)
                remaining.remove(jid)
                drained = True
                break
        if not drained:
            return False
    return True
