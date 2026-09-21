"""Immutable durable repository for reusable experiment definitions."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from .config import experiment_spec_from_mapping, experiment_spec_to_mapping
from .models import ExperimentSpec, _DeepFrozen


@dataclass(frozen=True, slots=True)
class StoredExperiment(_DeepFrozen):
    experiment_id: str
    digest: str
    created_at: datetime
    spec: ExperimentSpec


class ExperimentRepository:
    """Persist each experiment identifier once with content integrity."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def put(self, spec: ExperimentSpec) -> StoredExperiment:
        _validate_identifier(spec.experiment_id)
        public_spec = experiment_spec_to_mapping(spec)
        digest = _digest(public_spec)
        path = self._path(spec.experiment_id)
        with self._lock:
            if path.exists():
                stored = self.get(spec.experiment_id)
                if stored.digest != digest:
                    raise FileExistsError(
                        "experiment identifiers are immutable; create a new "
                        "experiment_id for changed content"
                    )
                return stored
            created_at = datetime.now(timezone.utc)
            payload = {
                "schema_version": 1,
                "experiment_id": spec.experiment_id,
                "digest": digest,
                "created_at": created_at.isoformat(),
                "spec": public_spec,
            }
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.root,
                prefix=f".{spec.experiment_id}.",
                suffix=".tmp",
            )
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            ) as stream:
                json.dump(
                    payload,
                    stream,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary_name, path)
            finally:
                Path(temporary_name).unlink(missing_ok=True)
        return StoredExperiment(
            experiment_id=spec.experiment_id,
            digest=digest,
            created_at=created_at,
            spec=spec,
        )

    def get(self, experiment_id: str) -> StoredExperiment:
        _validate_identifier(experiment_id)
        path = self._path(experiment_id)
        if not path.is_file():
            raise KeyError(f"unknown experiment: {experiment_id}")
        with self._lock, path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        spec_value = value["spec"]
        actual_digest = _digest(spec_value)
        expected_digest = str(value["digest"])
        if actual_digest != expected_digest:
            raise OSError(f"experiment definition is corrupted: {experiment_id}")
        spec = experiment_spec_from_mapping(spec_value)
        if spec.experiment_id != experiment_id:
            raise OSError(f"experiment identity is corrupted: {experiment_id}")
        return StoredExperiment(
            experiment_id=experiment_id,
            digest=expected_digest,
            created_at=datetime.fromisoformat(str(value["created_at"])),
            spec=spec,
        )

    def list(self) -> tuple[StoredExperiment, ...]:
        return tuple(
            sorted(
                (self.get(path.stem) for path in self.root.glob("*.json")),
                key=lambda item: item.created_at,
                reverse=True,
            )
        )

    def _path(self, experiment_id: str) -> Path:
        return self.root / f"{experiment_id}.json"


def _digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _validate_identifier(value: str) -> None:
    if value in {"", ".", ".."} or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        for character in value
    ):
        raise ValueError(
            "experiment_id may contain only letters, digits, dash, underscore, and dot"
        )
