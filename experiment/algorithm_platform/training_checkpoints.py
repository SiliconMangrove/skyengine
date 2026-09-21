"""Atomic run-local checkpoints with replaceable best and retained step snapshots."""

from __future__ import annotations

import io
import json
import os
import threading
import zipfile
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse
from urllib.request import url2pathname

_checkpoint_lock = threading.RLock()


def checkpoint_directory(workspace_uri: str) -> Path:
    return Path(url2pathname(urlparse(workspace_uri).path)) / "checkpoints"


def save_training_checkpoint(directory: Path, name: str, payload: Mapping[str, object],
                             metadata: Mapping[str, object]) -> dict[str, object]:
    import torch

    buffer = io.BytesIO()
    torch.save(dict(payload), buffer)
    record = {**metadata, "name": name, "size_bytes": buffer.tell()}
    directory.mkdir(parents=True, exist_ok=True)
    # Metadata and weights live in one archive so a restart cannot expose
    # metadata belonging to a different version of best.
    with _checkpoint_lock:
        temporary = directory / f".{name}.tmp"
        with temporary.open("wb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("metadata.json", json.dumps(record, ensure_ascii=False))
                archive.writestr("checkpoint.pt", buffer.getbuffer())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / f"{name}.ckpt")
    return record


def read_training_checkpoint(path: Path) -> tuple[dict[str, object], bytes]:
    with _checkpoint_lock, zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("metadata.json")), archive.read("checkpoint.pt")


def list_training_checkpoints(directory: Path) -> list[dict[str, object]]:
    with _checkpoint_lock:
        records: list[dict[str, object]] = []
        for path in sorted(directory.glob("*.ckpt")):
            with zipfile.ZipFile(path) as archive:
                records.append(json.loads(archive.read("metadata.json")))
        return records
