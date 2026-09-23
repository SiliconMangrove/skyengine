"""Durable execution lifecycle records for the algorithm control plane."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Mapping

from .models import (
    ExecutionRecord,
    ExecutionStatus,
    ExperimentSpec,
    RunFailure,
    RunPurpose,
)
from .serialization import to_jsonable


_TRANSITIONS = {
    ExecutionStatus.DRAFT: {
        ExecutionStatus.COMPILED,
        ExecutionStatus.FAILED,
    },
    ExecutionStatus.COMPILED: {
        ExecutionStatus.RUNNING,
        ExecutionStatus.CANCEL_REQUESTED,
        ExecutionStatus.FAILED,
    },
    ExecutionStatus.RUNNING: {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCEL_REQUESTED,
    },
    ExecutionStatus.CANCEL_REQUESTED: {ExecutionStatus.CANCELLED},
    ExecutionStatus.SUCCEEDED: set(),
    ExecutionStatus.FAILED: set(),
    ExecutionStatus.CANCELLED: set(),
}


class ExecutionRepository:
    """Atomically persist immutable snapshots of execution state."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def create(
        self,
        execution_id: str,
        experiment: ExperimentSpec,
        *,
        purpose: RunPurpose | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> ExecutionRecord:
        _validate_execution_id(execution_id)
        now = datetime.now(timezone.utc)
        record = ExecutionRecord(
            execution_id=execution_id,
            experiment_id=experiment.experiment_id,
            purpose=experiment.purpose if purpose is None else purpose,
            status=ExecutionStatus.DRAFT,
            created_at=now,
            updated_at=now,
            metadata={} if metadata is None else dict(metadata),
        )
        with self._lock:
            if self._path(execution_id).exists():
                raise FileExistsError(
                    f"execution record already exists: {execution_id}"
                )
            self._write(record)
        return record

    def get(self, execution_id: str) -> ExecutionRecord:
        _validate_execution_id(execution_id)
        path = self._path(execution_id)
        if not path.is_file():
            raise KeyError(f"unknown execution: {execution_id}")
        with self._lock, path.open("r", encoding="utf-8") as stream:
            return _record_from_mapping(json.load(stream))

    def list(self) -> tuple[ExecutionRecord, ...]:
        with self._lock:
            records = tuple(
                _record_from_mapping(
                    json.loads(path.read_text(encoding="utf-8"))
                )
                for path in self.root.glob("*.json")
            )
        return tuple(
            sorted(records, key=lambda item: item.created_at, reverse=True)
        )

    def compiled(
        self,
        execution_id: str,
        plan_digest: str,
    ) -> ExecutionRecord:
        return self._transition(
            execution_id,
            ExecutionStatus.COMPILED,
            plan_digest=plan_digest,
        )

    def archive(self, execution_id: str, trash_root: Path) -> Path:
        """Remove a terminal record from the index without deleting its data."""
        with self._lock:
            record = self.get(execution_id)
            if record.status not in {
                ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED,
                ExecutionStatus.CANCELLED,
            }:
                raise ValueError("only terminal execution records can be deleted")
            destination = trash_root / execution_id / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            destination.mkdir(parents=True, exist_ok=False)
            return self._path(execution_id).rename(destination / f"{execution_id}.json")

    def started(self, execution_id: str) -> ExecutionRecord:
        now = datetime.now(timezone.utc)
        return self._transition(
            execution_id,
            ExecutionStatus.RUNNING,
            started_at=now,
        )

    def succeeded(
        self,
        execution_id: str,
        result_manifest_id: str,
    ) -> ExecutionRecord:
        return self._transition(
            execution_id,
            ExecutionStatus.SUCCEEDED,
            finished_at=datetime.now(timezone.utc),
            result_manifest_id=result_manifest_id,
        )

    def failed(
        self,
        execution_id: str,
        failure: RunFailure,
        result_manifest_id: str | None = None,
    ) -> ExecutionRecord:
        return self._transition(
            execution_id,
            ExecutionStatus.FAILED,
            finished_at=datetime.now(timezone.utc),
            failure=failure,
            result_manifest_id=result_manifest_id,
        )

    def request_cancel(self, execution_id: str) -> ExecutionRecord:
        return self._transition(
            execution_id,
            ExecutionStatus.CANCEL_REQUESTED,
        )

    def cancelled(self, execution_id: str) -> ExecutionRecord:
        return self._transition(
            execution_id,
            ExecutionStatus.CANCELLED,
            finished_at=datetime.now(timezone.utc),
        )

    def recover_interrupted(self) -> tuple[ExecutionRecord, ...]:
        """Close records whose worker threads cannot survive process restart."""

        recovered: list[ExecutionRecord] = []
        for record in self.list():
            if record.status is ExecutionStatus.CANCEL_REQUESTED:
                recovered.append(self.cancelled(record.execution_id))
            elif record.status in {
                ExecutionStatus.DRAFT,
                ExecutionStatus.COMPILED,
                ExecutionStatus.RUNNING,
            }:
                recovered.append(
                    self.failed(
                        record.execution_id,
                        RunFailure(
                            code="process_restarted",
                            message=(
                                "the platform process restarted before this "
                                "execution reached a terminal state"
                            ),
                            details={"recovered_status": record.status.value},
                        ),
                    )
                )
        return tuple(recovered)

    def _transition(
        self,
        execution_id: str,
        status: ExecutionStatus,
        **changes: object,
    ) -> ExecutionRecord:
        with self._lock:
            current = self.get(execution_id)
            if status not in _TRANSITIONS[current.status]:
                raise ValueError(
                    f"invalid execution transition: {current.status.value} -> "
                    f"{status.value}"
                )
            updated = replace(
                current,
                status=status,
                updated_at=datetime.now(timezone.utc),
                **changes,
            )
            self._write(updated)
            return updated

    def _write(self, record: ExecutionRecord) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.root,
            prefix=f".{record.execution_id}.",
            suffix=".tmp",
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                to_jsonable(record),
                stream,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, self._path(record.execution_id))

    def _path(self, execution_id: str) -> Path:
        return self.root / f"{execution_id}.json"


def _record_from_mapping(value: Mapping[str, object]) -> ExecutionRecord:
    failure_value = value.get("failure")
    failure = (
        None
        if failure_value is None
        else RunFailure(
            code=str(failure_value["code"]),
            message=str(failure_value["message"]),
            details=dict(failure_value.get("details", {})),
        )
    )
    return ExecutionRecord(
        execution_id=str(value["execution_id"]),
        experiment_id=str(value["experiment_id"]),
        purpose=RunPurpose(str(value["purpose"])),
        status=ExecutionStatus(str(value["status"])),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        updated_at=datetime.fromisoformat(str(value["updated_at"])),
        plan_digest=(
            None
            if value.get("plan_digest") is None
            else str(value["plan_digest"])
        ),
        started_at=(
            None
            if value.get("started_at") is None
            else datetime.fromisoformat(str(value["started_at"]))
        ),
        finished_at=(
            None
            if value.get("finished_at") is None
            else datetime.fromisoformat(str(value["finished_at"]))
        ),
        result_manifest_id=(
            None
            if value.get("result_manifest_id") is None
            else str(value["result_manifest_id"])
        ),
        failure=failure,
        metadata=dict(value.get("metadata", {})),
    )


def _validate_execution_id(value: str) -> None:
    if value in {"", ".", ".."} or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        for character in value
    ):
        raise ValueError(
            "execution_id may contain only letters, digits, dash, underscore, and dot"
        )
