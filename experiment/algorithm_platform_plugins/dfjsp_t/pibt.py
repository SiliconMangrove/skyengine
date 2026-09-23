"""PIBT (Okumura et al., IJCAI 2019 / AIJ 2022) with factory stop constraints.

Independent implementation of priority inheritance and backtracking. Finite-time
arrival guarantees from the paper require its graph assumptions; frozen handling
vehicles and dynamic road closures do not automatically satisfy those assumptions.
"""
from __future__ import annotations

from collections import deque
import math
import random

Coord = tuple[int, int]


class PIBTRouter:
    def __init__(self, seed: int):
        self.rng: random.Random = random.Random(seed)
        self.grid: tuple[tuple[int, ...], ...] = ()
        self.moves: tuple[Coord, ...] = ()
        self.distances: dict[Coord, dict[Coord, int]] = {}
        self.age: dict[int, int] = {}
        self.tasks: dict[int, tuple] = {}

    def update_grid(self, state: dict) -> None:
        grid: tuple[tuple[int, ...], ...] = tuple(tuple(row) for row in state["grid"])
        if grid != self.grid:
            self.grid = grid
            self.distances.clear()
        self.moves = tuple(tuple(move) for move in state["moves"])

    def neighbors(self, pos: Coord) -> list[Coord]:
        height: int = len(self.grid)
        width: int = len(self.grid[0])
        return [(pos[0] + dx, pos[1] + dy) for dx, dy in self.moves
                if (dx or dy) and 0 <= pos[0] + dx < height and 0 <= pos[1] + dy < width
                and not self.grid[pos[0] + dx][pos[1] + dy]]

    def distance(self, source: Coord, target: Coord) -> float:
        if target not in self.distances:
            distances: dict[Coord, int] = {target: 0}
            queue: deque[Coord] = deque([target])
            while queue:
                pos: Coord = queue.popleft()
                for neighbor in self.neighbors(pos):
                    if neighbor not in distances:
                        distances[neighbor] = distances[pos] + 1
                        queue.append(neighbor)
            self.distances[target] = distances
        return float(self.distances[target].get(source, math.inf))

    def plan(self, state: dict, assigned: set[int]) -> list[dict]:
        self.update_grid(state)
        agents: dict[int, dict] = {agent["id"]: agent for agent in state["agents"]}
        positions: dict[int, Coord] = {aid: tuple(agent["pos"]) for aid, agent in agents.items()}
        occupied: dict[Coord, int] = {pos: aid for aid, pos in positions.items()}
        machines: dict[Coord, dict] = {tuple(m["location"]): m for m in state["machines"]}
        endpoints: set[Coord] = set(machines)
        for job in state["jobs"]:
            endpoints.update((tuple(job["raw_material_source"]), tuple(job["finished_goods_destination"])))
        goals: dict[int, Coord] = {}
        forbidden: dict[int, set[Coord]] = {}
        frozen: set[int] = set(assigned)
        for aid, agent in agents.items():
            task: dict | None = agent["current_task"]
            phase: str = agent["task_phase"]
            signature: tuple = () if task is None else (task["task_id"], phase)
            self.age[aid] = self.age.get(aid, 0) + 1 if self.tasks.get(aid) == signature and task else 0
            self.tasks[aid] = signature
            pos: Coord = positions[aid]
            target: Coord = pos
            forbidden[aid] = set()
            if agent["status"] != "OK" or phase in {"PICKING", "DROPPING"}:
                frozen.add(aid)
            if task is not None:
                target = tuple(task["source"] if phase in {"TO_PICKUP", "PICKING"} else task["destination"])
                blocked: bool = (phase == "TO_DROPOFF" and task["kind"] == "operation"
                                 and len(machines[target]["buffer_jobs"]) >= machines[target]["buffer_capacity"])
                if blocked:
                    forbidden[aid].add(target)
                    target = pos
                elif pos == target:
                    # task_step starts handling before agent movement.
                    frozen.add(aid)
            if aid not in frozen and target == pos and pos in endpoints:
                parking: list[Coord] = [cell for cell in self._reachable_parking(pos, endpoints)
                                       if cell not in occupied]
                if parking:
                    target = parking[0]
            goals[aid] = target
        chosen: dict[int, Coord] = {aid: positions[aid] for aid in frozen}
        reserved: set[Coord] = set(chosen.values())

        def inherit(aid: int, parent: int | None = None) -> bool:
            candidates: list[Coord] = [positions[aid], *self.neighbors(positions[aid])]
            self.rng.shuffle(candidates)
            candidates.sort(key=lambda cell: (self.distance(cell, goals[aid]), cell in occupied))
            for cell in candidates:
                if cell in reserved or cell in forbidden[aid] or (parent is not None and cell == positions[parent]):
                    continue
                other: int | None = occupied.get(cell)
                if other is not None and other != aid and chosen.get(other) == positions[aid]:
                    continue
                chosen[aid] = cell
                reserved.add(cell)
                if other is not None and other not in chosen and not inherit(other, aid):
                    continue
                return True
            chosen[aid] = positions[aid]
            reserved.add(positions[aid])
            return False

        priorities: list[int] = sorted(agents, key=lambda aid: (
            self.age[aid] if agents[aid]["current_task"] else -1, -aid), reverse=True)
        for aid in priorities:
            if aid not in chosen:
                inherit(aid)
        move_index: dict[Coord, int] = {move: index for index, move in enumerate(self.moves)}
        return [{"agv_id": aid, "path": [move_index[(chosen[aid][0] - pos[0], chosen[aid][1] - pos[1])]]}
                for aid, pos in positions.items()]

    def _reachable_parking(self, start: Coord, endpoints: set[Coord]) -> list[Coord]:
        queue: deque[Coord] = deque([start])
        seen: set[Coord] = {start}
        parking: list[Coord] = []
        while queue:
            pos: Coord = queue.popleft()
            if pos not in endpoints and len(self.neighbors(pos)) >= 2:
                parking.append(pos)
            for neighbor in self.neighbors(pos):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        return parking
