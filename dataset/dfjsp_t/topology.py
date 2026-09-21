"""生成连通的网格、机器位置和运输瓶颈。"""

from __future__ import annotations

from collections import deque
import random


def _connected(grid: list[list[str]], points: list[tuple[int, int]]) -> bool:
    if not points:
        return True
    width, height = len(grid[0]), len(grid)
    queue = deque([points[0]])
    seen = {points[0]}
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height and grid[ny][nx] != "#" and (nx, ny) not in seen:
                seen.add((nx, ny))
                queue.append((nx, ny))
    return all(point in seen for point in points)


def generate_topology(rng: random.Random, machines: int, size: int, bottleneck: str = "none") -> dict:
    size = max(size, 12)
    grid = [["." for _ in range(size)] for _ in range(size)]
    depot = (1, size // 2)
    product = (size - 2, size // 2)
    machine_locations = []
    columns = max(2, int(machines**0.5))
    for index in range(machines):
        row, col = divmod(index, columns)
        x = 2 + int((size - 5) * (col + 1) / (columns + 1))
        y = 2 + int((size - 5) * (row + 1) / (max(1, (machines + columns - 1) // columns) + 1))
        candidate = (min(size - 2, x), min(size - 2, y))
        while candidate in machine_locations or candidate in {depot, product}:
            candidate = (candidate[0], min(size - 2, candidate[1] + 1))
        machine_locations.append(candidate)

    if bottleneck in {"transport", "composite"}:
        wall_x = size // 2
        for y in range(size):
            if y not in {size // 3, 2 * size // 3}:
                grid[y][wall_x] = "#"
        for x, y in machine_locations + [depot, product]:
            grid[y][x] = "."
    if not _connected(grid, machine_locations + [depot, product]):
        grid = [["." for _ in range(size)] for _ in range(size)]

    machines_data = {
        str(index): {
            "id": index,
            "name": f"M{index}",
            "location": list(location),
            "size": [1, 1],
            "status": "IDLE",
        }
        for index, location in enumerate(machine_locations)
    }
    return {
        "gridWidth": size,
        "gridHeight": size,
        "map": "\n".join("".join(row) for row in grid),
        "machines": machines_data,
        "depot": list(depot),
        "product": list(product),
        "waypoints": {},
    }
