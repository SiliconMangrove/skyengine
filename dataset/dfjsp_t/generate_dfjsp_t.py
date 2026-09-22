"""可复现 DFJSP-T 训练集、验证集和分层 benchmark 生成器。"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Iterable

from .disturbances import build_disturbances
from .jobs import generate_jobs
from .profiles import PROFILES, Profile, profile_for
from .schema import canonical_hash
from .topology import generate_topology
from .validate_schema import validate_instance


def _existing_hashes(output: Path, reference: Path | None) -> set[str]:
    paths = list(output.parent.glob("*/manifest.json"))
    if reference:
        paths.append(reference)
    hashes = set()
    own_manifest = output / "manifest.json"
    for path in paths:
        if path.resolve() == own_manifest.resolve():
            continue
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        hashes.update(item.get("canonical_hash") for item in data.get("instances", []) if item.get("canonical_hash"))
    return hashes


def make_instance(seed: int, split: str, profile: Profile, ordinal: int) -> dict:
    rng = random.Random(seed)
    jobs = rng.randint(*profile.jobs)
    machines = rng.randint(*profile.machines)
    agvs = rng.randint(*profile.agvs)
    size = rng.randint(*profile.map_size)
    topology = generate_topology(rng, machines, size, profile.bottleneck)
    map_rows: list[str] = topology["map"].splitlines()
    reserved: set[tuple[int, int]] = {tuple(machine["location"]) for machine in topology["machines"].values()}
    reserved.update((tuple(topology["depot"]), tuple(topology["product"])))
    # Fill distinct parking cells column by column, without wrapping vehicles
    # back onto occupied starting positions on small maps.
    parking_cells: list[tuple[int, int]] = [
        (x, y) for x in range(1, topology["gridWidth"] - 1)
        for y in (*range(1, topology["gridHeight"] - 1, 2), *range(2, topology["gridHeight"] - 1, 2))
        if map_rows[y][x] != "#" and (x, y) not in reserved
    ]
    if agvs > len(parking_cells):
        raise ValueError(f"地图只有 {len(parking_cells)} 个可用停车格，无法放置 {agvs} 台 AGV")
    dynamic_rate = 0.45 if split == "benchmark" or profile.bottleneck == "composite" else 0.25
    urgent_rate = 0.16 if split == "benchmark" or profile.bottleneck in {"machine", "composite"} else 0.10
    job_list = generate_jobs(rng, jobs, machines, profile.ops, profile.flexibility, profile.proc_time, profile.bottleneck, dynamic_rate, urgent_rate)
    max_steps = max(500, sum(len(job["operations"]) for job in job_list) * 25)
    processing, exceptions = build_disturbances(rng, profile.disturbance, machines, agvs, max_steps)
    instance = {
        "schema_version": 1,
        "instance_id": f"dfjspt-{split}-{profile.name}-{seed:010d}-{ordinal:05d}",
        "split": split,
        "seed": seed,
        "profile": profile.name,
        "topology": topology,
        "agvs": [{"id": index, "name": f"AGV-{index}", "initialLocation": list(parking_cells[index]), "velocity": 1.0, "capacity": 1, "status": "IDLE"} for index in range(agvs)],
        "jobs": {"job_list": job_list},
        "material_handling_config": {"raw_material_source": topology["depot"], "pickup_dwell_steps": 1, "dropoff_dwell_steps": 1},
        "processing_time_config": processing,
        "exception_config": exceptions,
        "simulation_control": {"max_steps": max_steps},
    }
    instance["canonical_hash"] = canonical_hash(instance)
    validate_instance(instance)
    return instance


def _write_jsonl(path: Path, instances: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for instance in instances:
            handle.write(json.dumps(instance, ensure_ascii=False, separators=(",", ":")) + "\n")


def generate(split: str, count: int, seed: int, output: Path, profile_name: str | None, reference: Path | None) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    if profile_name == "pressure_suite":
        profiles = [PROFILES[name] for name in ("normal", "transport_bottleneck", "machine_bottleneck", "failure", "composite")]
        per_profile = count // len(profiles)
        requests = [(profile, per_profile) for profile in profiles]
        requests[-1] = (requests[-1][0], count - sum(item[1] for item in requests[:-1]))
    else:
        requests = [(profile_for(split, profile_name), count)]
    seen = _existing_hashes(output, reference)
    instances = []
    ordinal = 0
    for profile, requested in requests:
        made = 0
        attempt = 0
        while made < requested:
            instance_seed = seed + ordinal * 1009 + attempt
            instance = make_instance(instance_seed, split, profile, ordinal)
            attempt += 1
            digest = instance["canonical_hash"]
            if digest in seen:
                continue
            seen.add(digest)
            instances.append(instance)
            made += 1
            ordinal += 1
    jsonl_name = "benchmark.jsonl" if split == "benchmark" else f"{split}.jsonl"
    _write_jsonl(output / jsonl_name, instances)
    manifest = {
        "schema_version": 1,
        "generator": "dfjsp_t.generate_dfjsp_t",
        "generator_version": "1.0",
        "split": split,
        "base_seed": seed,
        "seed_namespace": f"{split}:{seed}",
        "count": len(instances),
        "profiles": {
            profile.name: {
                "jobs": list(profile.jobs), "machines": list(profile.machines),
                "agvs": list(profile.agvs), "operations": list(profile.ops),
                "flexibility": list(profile.flexibility), "processing_time": list(profile.proc_time),
                "map_size": list(profile.map_size), "disturbance": profile.disturbance,
                "bottleneck": profile.bottleneck,
            }
            for profile, _ in requests
        },
        "difficulty_distribution": {profile.name: sum(item["profile"] == profile.name for item in instances) for profile, _ in requests},
        "instances": [{"instance_id": item["instance_id"], "seed": item["seed"], "profile": item["profile"], "canonical_hash": item["canonical_hash"]} for item in instances],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"split": split, "count": len(instances), "profiles": {profile.name: sum(item["profile"] == profile.name for item in instances) for profile, _ in requests}}
    (output / "statistics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("train", "validation", "benchmark"), required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", default=None, help="训练/验证默认使用 split；benchmark 可使用 pressure_suite")
    parser.add_argument("--reference-manifest", type=Path, default=None)
    args = parser.parse_args()
    if args.count <= 0:
        raise ValueError("count 必须为正数")
    print(json.dumps(generate(args.split, args.count, args.seed, args.output, args.profile, args.reference_manifest), ensure_ascii=False))


if __name__ == "__main__":
    main()
