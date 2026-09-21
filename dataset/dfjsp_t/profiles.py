"""训练集和 benchmark 的规模/难度配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    jobs: tuple[int, int]
    machines: tuple[int, int]
    agvs: tuple[int, int]
    ops: tuple[int, int]
    flexibility: tuple[float, float]
    proc_time: tuple[int, int]
    map_size: tuple[int, int]
    disturbance: str
    bottleneck: str = "none"


PROFILES = {
    "train": Profile("train", (10, 50), (5, 20), (2, 8), (3, 10), (0.25, 0.65), (3, 20), (16, 30), "random"),
    "validation": Profile("validation", (10, 50), (5, 20), (2, 8), (3, 10), (0.25, 0.65), (3, 20), (16, 30), "random"),
    "normal": Profile("normal", (20, 40), (8, 16), (4, 8), (4, 8), (0.35, 0.60), (5, 20), (20, 28), "none"),
    "transport_bottleneck": Profile("transport_bottleneck", (25, 45), (8, 16), (2, 3), (5, 9), (0.45, 0.75), (5, 18), (24, 32), "none", "transport"),
    "machine_bottleneck": Profile("machine_bottleneck", (25, 45), (8, 16), (4, 7), (5, 9), (0.20, 0.45), (5, 22), (20, 28), "none", "machine"),
    "failure": Profile("failure", (20, 40), (8, 16), (3, 6), (4, 8), (0.35, 0.65), (5, 24), (20, 28), "failure"),
    "composite": Profile("composite", (35, 70), (10, 20), (2, 4), (6, 10), (0.55, 0.90), (5, 28), (24, 34), "failure", "composite"),
}


def profile_for(split: str, profile: str | None = None) -> Profile:
    key = profile or split
    if key == "pressure_suite":
        raise ValueError("pressure_suite 由多个 profile 组合，不能直接生成单个实例")
    try:
        return PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"未知生成 profile: {key}; 可选 {sorted(PROFILES)}") from exc
