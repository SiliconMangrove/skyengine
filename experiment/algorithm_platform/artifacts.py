"""Local content-addressed storage for immutable experiment artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from .models import ArtifactKind, ArtifactRef, _DeepFrozen
from .serialization import to_jsonable


@dataclass(frozen=True, slots=True)
class ArtifactManifest(_DeepFrozen):
    """Immutable named collection of content-addressed artifacts."""

    manifest_id: str
    created_at: datetime
    artifacts: tuple[ArtifactRef, ...]
    metadata: Mapping[str, object]


class LocalArtifactStore:
    """Store immutable blobs by SHA-256 and publish explicit manifests."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.objects_dir = self.root / "objects" / "sha256"
        self.manifests_dir = self.root / "manifests"
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)

    def put_bytes(
        self,
        data: bytes,
        *,
        kind: ArtifactKind,
        media_type: str,
        metadata: Mapping[str, object] | None = None,
    ) -> ArtifactRef:
        """Persist bytes and return their content-addressed reference."""

        digest_hex = hashlib.sha256(data).hexdigest()
        destination = self._object_path(digest_hex)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=destination.parent,
                prefix=f".{digest_hex}.",
                suffix=".tmp",
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, destination)
        self._assert_object(destination, digest_hex, len(data))

        return self._reference(
            digest_hex=digest_hex,
            size_bytes=len(data),
            kind=kind,
            media_type=media_type,
            metadata=metadata,
        )

    def put_file(
        self,
        source: str | Path,
        *,
        kind: ArtifactKind,
        media_type: str,
        metadata: Mapping[str, object] | None = None,
    ) -> ArtifactRef:
        """Stream a file into the store without loading it entirely in memory."""

        source_path = Path(source).resolve(strict=True)
        digest_hex, size_bytes = self._hash_file(source_path)
        destination = self._object_path(digest_hex)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=destination.parent,
                prefix=f".{digest_hex}.",
                suffix=".tmp",
            )
            with source_path.open("rb") as input_stream, os.fdopen(
                descriptor, "wb"
            ) as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
            os.replace(temporary_name, destination)
        self._assert_object(destination, digest_hex, size_bytes)

        return self._reference(
            digest_hex=digest_hex,
            size_bytes=size_bytes,
            kind=kind,
            media_type=media_type,
            metadata=metadata,
        )

    def put_json(
        self,
        value: object,
        *,
        kind: ArtifactKind,
        metadata: Mapping[str, object] | None = None,
    ) -> ArtifactRef:
        """Store canonical UTF-8 JSON as an artifact."""

        data = json.dumps(
            to_jsonable(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return self.put_bytes(
            data,
            kind=kind,
            media_type="application/json",
            metadata=metadata,
        )

    def resolve(self, artifact: ArtifactRef) -> Path:
        """Resolve the local object path for a reference."""

        digest_hex = self._digest_hex(artifact.digest)
        path = self._object_path(digest_hex)
        if not path.is_file():
            raise FileNotFoundError(f"artifact object does not exist: {artifact.digest}")
        return path

    def resolve_digest(self, digest: str) -> Path:
        """Resolve a SHA-256 object without requiring its manifest metadata."""

        digest_hex = self._digest_hex(digest)
        path = self._object_path(digest_hex)
        if not path.is_file():
            raise FileNotFoundError(f"artifact object does not exist: {digest}")
        return path

    def open(self, artifact: ArtifactRef) -> BinaryIO:
        """Open an artifact for read-only binary access."""

        return self.resolve(artifact).open("rb")

    def read_bytes(self, artifact: ArtifactRef) -> bytes:
        return self.resolve(artifact).read_bytes()

    def verify(self, artifact: ArtifactRef) -> bool:
        """Verify both size and digest of a stored artifact."""

        path = self.resolve(artifact)
        digest_hex, size_bytes = self._hash_file(path)
        return (
            size_bytes == artifact.size_bytes
            and f"sha256:{digest_hex}" == artifact.digest
        )

    def write_manifest(
        self,
        manifest_id: str,
        artifacts: Sequence[ArtifactRef],
        *,
        metadata: Mapping[str, object] | None = None,
        created_at: datetime | None = None,
    ) -> ArtifactManifest:
        """Atomically publish a new immutable artifact manifest."""

        path = self._manifest_path(manifest_id)
        if path.exists():
            raise FileExistsError(f"artifact manifest already exists: {manifest_id}")
        for artifact in artifacts:
            if not self.verify(artifact):
                raise OSError(f"artifact failed integrity verification: {artifact.digest}")

        manifest = ArtifactManifest(
            manifest_id=manifest_id,
            created_at=created_at or datetime.now(timezone.utc),
            artifacts=tuple(artifacts),
            metadata={} if metadata is None else dict(metadata),
        )
        payload = _manifest_to_dict(manifest)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.manifests_dir,
            prefix=f".{manifest_id}.",
            suffix=".tmp",
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
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
        return manifest

    def read_manifest(self, manifest_id: str) -> ArtifactManifest:
        path = self._manifest_path(manifest_id)
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        artifacts = tuple(_artifact_from_dict(item) for item in value["artifacts"])
        return ArtifactManifest(
            manifest_id=str(value["manifest_id"]),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            artifacts=artifacts,
            metadata=dict(value["metadata"]),
        )

    def iter_manifests(self) -> Iterator[ArtifactManifest]:
        """Yield manifests in stable identifier order."""

        for path in sorted(self.manifests_dir.glob("*.json")):
            yield self.read_manifest(path.stem)

    def _reference(
        self,
        *,
        digest_hex: str,
        size_bytes: int,
        kind: ArtifactKind,
        media_type: str,
        metadata: Mapping[str, object] | None,
    ) -> ArtifactRef:
        return ArtifactRef(
            digest=f"sha256:{digest_hex}",
            kind=kind,
            media_type=media_type,
            size_bytes=size_bytes,
            uri=f"artifact://sha256/{digest_hex}",
            metadata={} if metadata is None else dict(metadata),
        )

    def _object_path(self, digest_hex: str) -> Path:
        return self.objects_dir / digest_hex[:2] / digest_hex[2:]

    def _manifest_path(self, manifest_id: str) -> Path:
        if not manifest_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
            for character in manifest_id
        ):
            raise ValueError(
                "manifest_id may contain only letters, digits, dash, underscore, and dot"
            )
        return self.manifests_dir / f"{manifest_id}.json"

    @staticmethod
    def _digest_hex(digest: str) -> str:
        algorithm, separator, digest_hex = digest.partition(":")
        if algorithm != "sha256" or separator != ":" or len(digest_hex) != 64:
            raise ValueError(f"unsupported artifact digest: {digest}")
        int(digest_hex, 16)
        return digest_hex.lower()

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size_bytes = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size_bytes += len(chunk)
        return digest.hexdigest(), size_bytes

    def _assert_object(
        self,
        path: Path,
        expected_digest: str,
        expected_size: int,
    ) -> None:
        actual_digest, actual_size = self._hash_file(path)
        if actual_digest != expected_digest or actual_size != expected_size:
            raise OSError(f"content-addressed object is corrupted: {path}")


class RunArtifactPublisher:
    """Run-scoped publisher that attaches immutable provenance metadata."""

    def __init__(
        self,
        store: LocalArtifactStore,
        provenance: Mapping[str, object],
    ) -> None:
        self._store = store
        self._provenance = dict(provenance)

    def publish_bytes(
        self,
        data: bytes,
        *,
        kind: ArtifactKind,
        media_type: str,
        metadata: Mapping[str, object] | None = None,
    ) -> ArtifactRef:
        return self._store.put_bytes(
            data,
            kind=kind,
            media_type=media_type,
            metadata=self._metadata(metadata),
        )

    def publish_file(
        self,
        source: str | Path,
        *,
        kind: ArtifactKind,
        media_type: str,
        metadata: Mapping[str, object] | None = None,
    ) -> ArtifactRef:
        return self._store.put_file(
            source,
            kind=kind,
            media_type=media_type,
            metadata=self._metadata(metadata),
        )

    def publish_json(
        self,
        value: object,
        *,
        kind: ArtifactKind,
        metadata: Mapping[str, object] | None = None,
    ) -> ArtifactRef:
        return self._store.put_json(
            value,
            kind=kind,
            metadata=self._metadata(metadata),
        )

    def _metadata(
        self,
        metadata: Mapping[str, object] | None,
    ) -> dict[str, object]:
        return {
            **({} if metadata is None else metadata),
            **self._provenance,
        }


def _manifest_to_dict(manifest: ArtifactManifest) -> dict[str, object]:
    return dict(to_jsonable(manifest))


def _artifact_from_dict(value: Mapping[str, object]) -> ArtifactRef:
    return ArtifactRef(
        digest=str(value["digest"]),
        kind=ArtifactKind(str(value["kind"])),
        media_type=str(value["media_type"]),
        size_bytes=int(value["size_bytes"]),
        uri=str(value["uri"]),
        metadata=dict(value["metadata"]),
    )
