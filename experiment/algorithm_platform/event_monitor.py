"""Incremental HTTP monitoring of append-only execution event files."""

from __future__ import annotations

import json
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

from .events import event_from_dict
from .models import EventRecord


_SUMMARY_TYPES = {
    "run_started", "run_finished", "metric_updated",
    "candidate_proposed", "candidate_evaluated", "benchmark_finished",
}


@dataclass
class _ExecutionEvents:
    generation: str = field(default_factory=lambda: uuid.uuid4().hex)
    # Path -> (device, inode, consumed byte offset).
    streams: dict[Path, tuple[int, int, int]] = field(default_factory=dict)
    lines: list[bytes] = field(default_factory=list)
    summary: dict[tuple[str, str, str, str], EventRecord] = field(default_factory=dict)


class ExecutionEventMonitor:
    """Share file offsets and small run summaries across monitor endpoints."""

    def __init__(self, max_executions: int = 4) -> None:
        self._entries: OrderedDict[str, _ExecutionEvents] = OrderedDict()
        self._lock = RLock()
        self._max_executions = max_executions

    def discard(self, execution_id: str) -> None:
        with self._lock:
            self._entries.pop(execution_id, None)

    def _refresh(self, execution_id: str, paths: tuple[Path, ...]) -> _ExecutionEvents:
        state = self._entries.get(execution_id)
        if state is None:
            state = _ExecutionEvents()
        stats = {path: path.stat() for path in paths}
        if any(
            path not in stats
            or (stats[path].st_dev, stats[path].st_ino) != (device, inode)
            or stats[path].st_size < offset
            for path, (device, inode, offset) in state.streams.items()
        ):
            state = _ExecutionEvents()

        additions: list[tuple[dict[str, object], bytes]] = []
        for path, stat in stats.items():
            offset = state.streams.get(path, (0, 0, 0))[2]
            if stat.st_size > offset:
                with path.open("rb") as stream:
                    stream.seek(offset)
                    # Read only the size observed before opening; an incomplete
                    # final record is left for the next request.
                    data = stream.read(stat.st_size - offset)
                complete = data.rfind(b"\n") + 1
                for line in data[:complete].splitlines():
                    additions.append((json.loads(line), line))
                offset += complete
            state.streams[path] = (stat.st_dev, stat.st_ino, offset)

        additions.sort(key=lambda item: (
            item[0]["wall_time"], item[0]["execution_id"],
            item[0]["run_id"], item[0]["sequence"],
        ))
        for row, line in additions:
            state.lines.append(line)
            event_type = str(row["event_type"])
            if event_type in _SUMMARY_TYPES:
                payload: dict[str, object] = row["payload"]
                key = (str(row["execution_id"]), str(row["run_id"]),
                       event_type, str(payload.get("candidate_id", "")))
                previous = state.summary.get(key)
                if previous is None or int(row["sequence"]) > previous.sequence:
                    state.summary[key] = event_from_dict(row)

        self._entries[execution_id] = state
        self._entries.move_to_end(execution_id)
        while len(self._entries) > self._max_executions:
            self._entries.popitem(last=False)
        return state

    def summary(self, execution_id: str, paths: tuple[Path, ...]) -> tuple[EventRecord, ...]:
        with self._lock:
            state = self._refresh(execution_id, paths)
            return tuple(sorted(state.summary.values(), key=lambda event: (
                event.execution_id, event.run_id, event.sequence,
            )))

    def page(self, execution_id: str, paths: tuple[Path, ...],
             cursor: str | None, limit: int) -> bytes:
        with self._lock:
            state = self._refresh(execution_id, paths)
            generation, _, index = (cursor or "").partition(":")
            reset = generation != state.generation
            start = 0 if reset else int(index)
            if start > len(state.lines):
                reset, start = True, 0
            end = min(start + limit, len(state.lines))
            metadata = json.dumps({
                "execution_id": execution_id,
                "count": end - start,
                "total_count": len(state.lines),
                "next_cursor": f"{state.generation}:{end}",
                "reset": reset,
                "has_more": end < len(state.lines),
            }).encode("utf-8")
            # Stored JSON is already encoded. Avoid rebuilding and recursively
            # serializing every historical EventRecord on every poll.
            return metadata[:-1] + b',"items":[' + b",".join(state.lines[start:end]) + b"]}"
