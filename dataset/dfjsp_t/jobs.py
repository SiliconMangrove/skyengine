"""生成柔性工艺路线。"""

from __future__ import annotations

import random


def generate_jobs(
    rng: random.Random,
    num_jobs: int,
    num_machines: int,
    ops_range: tuple[int, int],
    flexibility_range: tuple[float, float],
    proc_range: tuple[int, int],
    bottleneck: str,
    release_rate: float = 0.25,
    urgent_rate: float = 0.10,
) -> list[dict]:
    machine_speed = [rng.uniform(0.75, 1.35) for _ in range(num_machines)]
    jobs = []
    for job_id in range(num_jobs):
        op_count = rng.randint(*ops_range)
        operations = []
        for op_id in range(op_count):
            flexibility = rng.uniform(*flexibility_range)
            choice_count = max(1, min(num_machines, round(num_machines * flexibility)))
            if bottleneck in {"machine", "composite"} and rng.random() < 0.55:
                critical = rng.randrange(num_machines)
                candidates = {critical}
                candidates.update(rng.sample([m for m in range(num_machines) if m != critical], max(0, choice_count - 1)))
            else:
                candidates = set(rng.sample(range(num_machines), choice_count))
            base = rng.randint(*proc_range)
            options = []
            for machine_id in sorted(candidates):
                noise = rng.uniform(0.85, 1.20)
                duration = max(1, round(base * machine_speed[machine_id] * noise))
                options.append([machine_id, duration])
            operations.append({
                "op_id": op_id,
                "name": f"J{job_id}-O{op_id}",
                "machine_options_with_time": options,
            })
        release = 0 if rng.random() >= release_rate else rng.randint(1, max(2, num_jobs // 2))
        priority = 200 if rng.random() < urgent_rate else rng.randint(0, 100)
        nominal_work = sum(min(duration for _machine, duration in operation["machine_options_with_time"]) for operation in operations)
        due = release + nominal_work + rng.randint(max(5, nominal_work // 3), max(10, nominal_work * 2))
        jobs.append({
            "job_id": job_id,
            "name": f"Job-{job_id}",
            "release": release,
            "due": due,
            "priority": priority,
            "operations": operations,
        })
    return jobs
