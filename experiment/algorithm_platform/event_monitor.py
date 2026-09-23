"""Incremental HTTP monitoring of append-only execution event files."""

from __future__ import annotations

import json
import uuid
from contextlib import ExitStack
from bisect import bisect_left
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
    # Keep disk locations, not a second in-memory copy of the entire event log.
    lines: list[tuple[Path, int, int, int]] = field(default_factory=list)
    summary: dict[tuple[str, str, str, str], EventRecord] = field(default_factory=dict)
    training: dict[str, dict] = field(default_factory=dict)
    by_level: dict[int, list[int]] = field(default_factory=lambda: {level: [] for level in range(4)})


def _severity(row: dict) -> int:
    payload: dict = row["payload"]
    level: str = str(row.get("level", payload.get("level", ""))).lower()
    if payload.get("failure") or payload.get("status") == "failed" or level in {"error", "critical", "fatal"}:
        return 3
    if payload.get("status") in {"cancelled", "cancel_requested", "timed_out"} or level in {"warn", "warning"}:
        return 2
    if level in {"debug", "info"}:
        return int(level == "info")
    return int(payload.get("phase") not in {"collecting", "evaluating", "update_progress", "episode_started", "episode_completed"}
               and row["event_type"] not in {"environment_reset", "observation_published", "decision_requested", "candidate_found",
                                             "decision_returned", "action_validated", "action_executed", "state_changed",
                                             "metric_updated", "snapshot_created", "algorithm_event", "domain_event"})


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

        additions: list[tuple[dict, tuple[Path, int, int, int]]] = []
        for path, stat in stats.items():
            offset = state.streams.get(path, (0, 0, 0))[2]
            if stat.st_size > offset:
                with path.open("rb") as stream:
                    stream.seek(offset)
                    # Read only the size observed before opening; an incomplete
                    # final record is left for the next request.
                    data = stream.read(stat.st_size - offset)
                complete = data.rfind(b"\n") + 1
                position: int = offset
                for line in data[:complete].splitlines(keepends=True):
                    row: dict = json.loads(line)
                    additions.append((row, (path, position, len(line), _severity(row))))
                    position += len(line)
                offset += complete
            state.streams[path] = (stat.st_dev, stat.st_ino, offset)

        additions.sort(key=lambda item: (
            item[0]["wall_time"], item[0]["execution_id"],
            item[0]["run_id"], item[0]["sequence"],
        ))
        for row, line in additions:
            for severity in range(line[3] + 1):
                state.by_level[severity].append(len(state.lines))
            state.lines.append(line)
            event_type = str(row["event_type"])
            if event_type in _SUMMARY_TYPES:
                payload: dict[str, object] = row["payload"]
                key = (str(row["execution_id"]), str(row["run_id"]),
                       event_type, str(payload.get("candidate_id", "")))
                previous = state.summary.get(key)
                if previous is None or int(row["sequence"]) > previous.sequence:
                    state.summary[key] = event_from_dict(row)
            run_key: str = f'{row["execution_id"]}/{row["run_id"]}'
            if event_type in {"training_started", "training_progress"}:
                state.training.setdefault(run_key, {"key": run_key, "label": run_key,
                                                     "updates": [], "barriers": [], "stages": {}})
            if run_key in state.training:
                run: dict = state.training[run_key]
                payload = row["payload"]
                phase: str = str(payload.get("phase", ""))
                if phase == "update_completed" and payload["trainer"]["updated"]:
                    run["updates"].append({"sequence": row["sequence"], "payload": payload})
                if phase == "barrier_completed":
                    run["barriers"].append({"sequence": row["sequence"], "payload": payload})
                stage: str = ("sampling" if phase in {"episode_started", "collecting", "sampling_completed"}
                              else "learner" if phase in {"updating", "update_progress", "update_completed"}
                              else "evaluation" if phase in {"evaluating", "evaluation_completed", "evaluation_draining"}
                              else "other")
                brief: dict = {"event_type": event_type, "message": payload.get("message", ""),
                               "status": payload.get("status"), "sequence": row["sequence"]}
                run["stages"][stage] = brief
                if phase not in {"evaluating", "evaluation_queued", "evaluation_completed", "checkpoint_saved"}:
                    run["stages"]["overall"] = brief

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

    def training(self, execution_id: str, paths: tuple[Path, ...]) -> bytes:
        with self._lock:
            state = self._refresh(execution_id, paths)
            return json.dumps({"generation": state.generation, "runs": list(state.training.values())},
                              ensure_ascii=False).encode("utf-8")

    def page(self, execution_id: str, paths: tuple[Path, ...],
             cursor: str | None, limit: int, *, tail: bool = False,
             before: str | None = None, level: str = "debug") -> bytes:
        with self._lock:
            state = self._refresh(execution_id, paths)
            generation, _, index = (cursor or "").partition(":")
            reset = generation != state.generation
            start = 0 if reset else int(index)
            if start > len(state.lines):
                reset, start = True, 0
            severity: int = {"debug": 0, "info": 1, "warning": 2, "error": 3}[level]
            end: int = len(state.lines)
            if before is not None:
                before_generation, _, before_index = before.partition(":")
                end = min(int(before_index), end) if before_generation == state.generation else end
            eligible: list[int] = state.by_level[severity]
            left: int = bisect_left(eligible, start)
            right: int = bisect_left(eligible, end)
            indices: list[int] = (eligible[max(left, right - limit):right] if tail or before is not None
                                  else eligible[left:min(left + limit, right)])
            if not tail and before is None and indices:
                end = indices[-1] + 1
            first: int = indices[0] if indices else end
            with ExitStack() as stack:
                streams = {path: stack.enter_context(path.open("rb")) for path in {state.lines[i][0] for i in indices}}
                lines: list[bytes] = []
                for index in indices:
                    path, offset, size, _ = state.lines[index]
                    streams[path].seek(offset)
                    lines.append(streams[path].read(size).rstrip(b"\r\n"))
            metadata = json.dumps({
                "execution_id": execution_id,
                "count": len(indices),
                "total_count": len(state.lines),
                "next_cursor": f"{state.generation}:{end}",
                "reset": reset,
                "has_more": end < len(state.lines),
                "before_cursor": f"{state.generation}:{first}",
                "has_older": bool(eligible) and eligible[0] < first,
            }).encode("utf-8")
            return metadata[:-1] + b',"items":[' + b",".join(lines) + b"]}"
