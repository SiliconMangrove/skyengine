"""Append-only event streams for replay, diagnostics, and state verification."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from .models import EventRecord, EventType
from .serialization import to_jsonable


def hash_state(state: object) -> str:
    """Return a stable SHA-256 digest for a JSON-serializable domain state."""

    encoded = json.dumps(
        to_jsonable(state),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def event_to_dict(event: EventRecord) -> dict[str, object]:
    """Convert an event record to its canonical JSON representation."""

    return dict(to_jsonable(event))


def event_from_dict(value: Mapping[str, object]) -> EventRecord:
    """Construct an event record from the canonical JSON representation."""

    wall_time = datetime.fromisoformat(str(value["wall_time"]))
    return EventRecord(
        sequence=int(value["sequence"]),
        event_type=EventType(str(value["event_type"])),
        execution_id=str(value["execution_id"]),
        run_id=str(value["run_id"]),
        wall_time=wall_time,
        simulation_time=(
            None
            if value.get("simulation_time") is None
            else float(value["simulation_time"])
        ),
        state_hash=(
            None if value.get("state_hash") is None else str(value["state_hash"])
        ),
        payload=dict(value.get("payload", {})),
    )


class EventSink(ABC):
    """A single-run append-only destination with strict sequence ordering."""

    def __init__(self) -> None:
        self._last_sequence = -1
        self._execution_id: str | None = None
        self._run_id: str | None = None
        self._lock = threading.Lock()

    @property
    def last_sequence(self) -> int:
        return self._last_sequence

    @property
    def run_id(self) -> str | None:
        return self._run_id

    @property
    def execution_id(self) -> str | None:
        return self._execution_id

    def append(self, event: EventRecord) -> None:
        """Append exactly the next event in this sink's run."""

        with self._lock:
            self._validate_next(event)
            self._write(event)
            self._accept(event)

    def close(self) -> None:
        """Release resources held by the sink."""

    def __enter__(self) -> EventSink:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _validate_next(self, event: EventRecord) -> None:
        expected = self._last_sequence + 1
        if event.sequence != expected:
            raise ValueError(
                f"event sequence must be {expected}, received {event.sequence}"
            )
        if self._run_id is not None and event.run_id != self._run_id:
            raise ValueError(
                f"event sink belongs to run {self._run_id}, received {event.run_id}"
            )
        if (
            self._execution_id is not None
            and event.execution_id != self._execution_id
        ):
            raise ValueError(
                "event sink belongs to execution "
                f"{self._execution_id}, received {event.execution_id}"
            )

    def _accept(self, event: EventRecord) -> None:
        self._last_sequence = event.sequence
        self._execution_id = event.execution_id
        self._run_id = event.run_id

    @abstractmethod
    def _write(self, event: EventRecord) -> None:
        raise NotImplementedError


class InMemoryEventSink(EventSink):
    """Append-only sink retained in memory for runtime consumers."""

    def __init__(self, initial_events: Sequence[EventRecord] = ()) -> None:
        super().__init__()
        self._events: list[EventRecord] = []
        for event in initial_events:
            self.append(event)

    @property
    def events(self) -> tuple[EventRecord, ...]:
        return tuple(self._events)

    def __iter__(self) -> Iterator[EventRecord]:
        return iter(self._events)

    def _write(self, event: EventRecord) -> None:
        self._events.append(event)


class JsonlEventSink(EventSink):
    """Durable append-only JSONL sink that can resume an existing log."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            for event in read_events(self.path):
                self._validate_next(event)
                self._accept(event)
        self._stream: IO[str] = self.path.open("a", encoding="utf-8", newline="\n")

    def close(self) -> None:
        self._stream.close()

    def _write(self, event: EventRecord) -> None:
        line = json.dumps(
            event_to_dict(event),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        self._stream.write(line + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())


class EventRecorder:
    """Assign sequence numbers and emit timestamped events to a sink."""

    def __init__(self, execution_id: str, run_id: str, sink: EventSink) -> None:
        if sink.execution_id is not None and sink.execution_id != execution_id:
            raise ValueError(
                "event sink belongs to execution "
                f"{sink.execution_id}, not requested execution {execution_id}"
            )
        if sink.run_id is not None and sink.run_id != run_id:
            raise ValueError(
                f"event sink belongs to run {sink.run_id}, not requested run {run_id}"
            )
        self.execution_id = execution_id
        self.run_id = run_id
        self.sink = sink
        self._next_sequence = sink.last_sequence + 1
        self._lock = threading.Lock()

    def emit(
        self,
        event_type: EventType,
        payload: Mapping[str, object] | None = None,
        *,
        simulation_time: float | None = None,
        state_hash: str | None = None,
        wall_time: datetime | None = None,
    ) -> EventRecord:
        """Create and append the next record for this run."""

        with self._lock:
            event = EventRecord(
                sequence=self._next_sequence,
                event_type=event_type,
                execution_id=self.execution_id,
                run_id=self.run_id,
                wall_time=wall_time or datetime.now(timezone.utc),
                simulation_time=simulation_time,
                state_hash=state_hash,
                payload={} if payload is None else dict(payload),
            )
            self.sink.append(event)
            self._next_sequence += 1
            return event

    def emit_state(
        self,
        event_type: EventType,
        state: object,
        payload: Mapping[str, object] | None = None,
        *,
        simulation_time: float | None = None,
        wall_time: datetime | None = None,
    ) -> EventRecord:
        """Emit an event carrying the canonical hash of the supplied state."""

        return self.emit(
            event_type,
            payload,
            simulation_time=simulation_time,
            state_hash=hash_state(state),
            wall_time=wall_time,
        )


def read_events(path: str | Path) -> Iterator[EventRecord]:
    """Stream records from a canonical event JSONL file in stored order."""

    with Path(path).open("r", encoding="utf-8") as stream:
        for line in stream:
            yield event_from_dict(json.loads(line))

