"""JSONL 数据集读取、分片和实例规范化。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


class JSONLDataset:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        with self.path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError(f"{self.path}:{line_no} 不是对象")
                yield item

    def shard(self, rank: int, world_size: int) -> Iterator[dict[str, Any]]:
        if world_size <= 0 or not 0 <= rank < world_size:
            raise ValueError("rank/world_size 无效")
        for index, item in enumerate(self):
            if index % world_size == rank:
                yield item

    def count(self) -> int:
        return sum(1 for _ in self)
