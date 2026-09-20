"""Immutable, bounded publication under existing per-artifact locks and staging."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from xlm.artifacts.manifest import (
    INTERNAL_KINDS,
    MAX_MANIFEST_BYTES,
    ArtifactFile,
    ArtifactManifest,
    bounded_children,
    canonical_json,
    comparable_resolved,
    ensure_plain_path,
    identity_digest,
    validate_component,
    validate_file_set,
    validate_manifest_path,
)
from xlm.core.paths import ArtifactPaths

CHUNK_BYTES = 64 * 1024
DEFAULT_MAX_PUBLICATION_BYTES = 2 * 1024**3


class ArtifactConflictError(ValueError):
    """An immutable destination cannot satisfy this publication request."""


class LegacyArtifactReuseError(ArtifactConflictError):
    """Legacy integrity is insufficient for v2 automatic request-equivalent reuse."""


def compute_file_sha256(path: Path, *, max_bytes: int | None = None) -> str:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_BYTES):
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise ValueError("Artifact hashing exceeds byte limit")
            hasher.update(chunk)
    return hasher.hexdigest()


class ArtifactStore:
    def __init__(
        self, paths: ArtifactPaths, *, max_publication_bytes: int = DEFAULT_MAX_PUBLICATION_BYTES
    ) -> None:
        if max_publication_bytes < 1:
            raise ValueError("max_publication_bytes must be positive")
        self.paths = paths
        self.staging_dir = self.paths.root / ".staging"
        self.locks_dir = self.paths.root / ".locks"
        self.max_publication_bytes = max_publication_bytes

    def _checked_destination(self, artifact_id: str, kind: str) -> Path:
        validate_component(artifact_id)
        validate_component(kind)
        if kind.casefold() in INTERNAL_KINDS:
            raise ValueError(f"Reserved artifact kind: {kind}")
        dest = self.paths.root / kind / artifact_id
        for path in (dest, self.staging_dir, self.locks_dir):
            ensure_plain_path(path)
            if not comparable_resolved(path).is_relative_to(comparable_resolved(self.paths.root)):
                raise ValueError(f"Artifact destination escapes store: {path}")
        # Reject differently cased aliases consistently on case-sensitive hosts too.
        for parent, requested in ((self.paths.root, kind), (dest.parent, artifact_id)):
            if parent.is_dir():
                for existing in bounded_children(parent):
                    if (
                        existing.name.casefold() == requested.casefold()
                        and existing.name != requested
                    ):
                        raise ArtifactConflictError(f"Artifact conflict: path alias {requested!r}")
        return dest

    def publish_artifact(
        self,
        artifact_id: str,
        kind: str,
        files: Mapping[str, Path | bytes | str],
        producer_code_hash: str,
        dependency_hash: str,
        resolved_config_hash: str,
        input_artifact_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        serializer_version: str = "xlm.artifact-files/1",
        cosmetic_metadata: dict[str, Any] | None = None,
        input_artifact_paths: Mapping[str, Path] | None = None,
    ) -> Path:
        """Compare staged actual bytes and declared provenance; never replace a destination.

        Metadata is behavioral. Only cosmetic_metadata and created_at are excluded
        from equivalence. Producer/dependency declarations are not authenticated by
        this store. Explicit input paths add verified manifest-byte fingerprints;
        IDs without paths remain declared, unresolved lineage, never fabricated hashes.
        """
        dest = self._checked_destination(artifact_id, kind)
        proposed = dict(files)
        validate_file_set(list(proposed))
        # Snapshot caller-owned metadata before side effects; reject non-JSON data.
        input_ids = list(input_artifact_ids or [])
        parents = dict(input_artifact_paths or {})
        if not set(parents).issubset(input_ids):
            raise ValueError("Input paths must correspond to declared input artifact IDs")
        parent_hashes: dict[str, str] = {}
        for parent_id, parent in parents.items():
            verified = self.verify_artifact(parent)
            if verified.artifact_id != parent_id:
                raise ValueError("Input artifact ID does not match its verified manifest")
            parent_hashes[parent_id] = compute_file_sha256(
                parent / "manifest.json", max_bytes=MAX_MANIFEST_BYTES
            )
        preliminary = ArtifactManifest(
            schema_version=2,
            artifact_id=artifact_id,
            kind=kind,
            created_at=datetime.now(UTC).isoformat(),
            producer_code_hash=producer_code_hash,
            dependency_hash=dependency_hash,
            resolved_config_hash=resolved_config_hash,
            input_artifact_ids=input_ids,
            files=[ArtifactFile(path=p, size_bytes=0, sha256="0" * 64) for p in proposed],
            metadata=json.loads(canonical_json(metadata or {})),
            serializer_version=serializer_version,
            input_manifest_hashes=parent_hashes,
            cosmetic_metadata=json.loads(canonical_json(cosmetic_metadata or {})),
        )
        estimated = 0
        for content in proposed.values():
            if isinstance(content, Path):
                ensure_plain_path(content)
                if not content.is_file():
                    raise ValueError(f"Artifact input must be a regular file: {content}")
                estimated += content.stat().st_size
            elif isinstance(content, (bytes, str)):
                estimated += len(content)  # UTF-8 upper cost checked while streaming below.
            else:
                raise TypeError(f"Unsupported artifact content type: {type(content)}")
        if estimated > self.max_publication_bytes:
            raise ValueError("Artifact publication exceeds payload byte limit")

        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        # Keep the existing global-ID lock, casefolded for portable alias contention.
        lock_key = hashlib.sha256(artifact_id.casefold().encode()).hexdigest()
        lock_path = self.locks_dir / f"{lock_key}.lock"
        ensure_plain_path(lock_path)
        with FileLock(str(lock_path), timeout=10.0):
            dest = self._checked_destination(artifact_id, kind)
            stage_path = self.staging_dir / f"{artifact_id}_{uuid.uuid4().hex}"
            stage_path.mkdir(exist_ok=False)
            try:
                manifest_files: list[ArtifactFile] = []
                used = 0
                for name, content in proposed.items():
                    target = validate_manifest_path(stage_path, name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    hasher, size = hashlib.sha256(), 0
                    with target.open("xb") as output:
                        for chunk in self._chunks(content):
                            used += len(chunk)
                            if used > self.max_publication_bytes:
                                raise ValueError("Artifact publication exceeds payload byte limit")
                            output.write(chunk)
                            hasher.update(chunk)
                            size += len(chunk)
                    manifest_files.append(
                        ArtifactFile(path=name, size_bytes=size, sha256=hasher.hexdigest())
                    )
                candidate = preliminary.model_copy(update={"files": manifest_files})
                candidate.production_key = identity_digest(candidate.production_identity())
                candidate.content_hash = identity_digest(candidate.output_identity())
                manifest_bytes = canonical_json(candidate.model_dump())

                if dest.exists():
                    try:
                        existing = self.verify_artifact(dest)
                    except (ValueError, OSError) as exc:
                        raise ArtifactConflictError(
                            "Artifact conflict: existing destination is corrupt or incomplete: "
                            f"{exc}"
                        ) from exc
                    if existing.schema_version == 1:
                        raise LegacyArtifactReuseError(
                            "Artifact conflict: legacy checksum-only artifact cannot be reused "
                            "as a v2 request; preserve it and explicitly choose a new artifact ID"
                        )
                    if (
                        existing.artifact_id != artifact_id
                        or existing.kind != kind
                        or canonical_json(existing.production_identity())
                        != canonical_json(candidate.production_identity())
                        or canonical_json(existing.output_identity())
                        != canonical_json(candidate.output_identity())
                    ):
                        raise ArtifactConflictError(
                            f"Artifact conflict: {artifact_id!r} "
                            "has different payload or provenance"
                        )
                    return dest

                (stage_path / "manifest.json").write_bytes(manifest_bytes)
                (stage_path / "_COMPLETED").write_text(
                    f"COMPLETED at {candidate.created_at}\n", encoding="utf-8"
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                self._checked_destination(artifact_id, kind)
                if dest.exists():
                    raise ArtifactConflictError(
                        "Artifact conflict: destination appeared during publish"
                    )
                stage_path.rename(dest)
                return dest
            finally:
                # Only this attempt's private, UUID-named staging is ever removed.
                ensure_plain_path(stage_path)
                if stage_path.exists():
                    if stage_path.parent.resolve() != self.staging_dir.resolve():
                        raise ValueError("Private staging escaped its owner")
                    shutil.rmtree(stage_path)

    @staticmethod
    def _chunks(content: Path | bytes | str) -> Iterator[bytes | memoryview]:
        if isinstance(content, Path):
            with content.open("rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError("Artifact source is not a regular file")
                while chunk := source.read(CHUNK_BYTES):
                    yield chunk
        elif isinstance(content, bytes):
            for offset in range(0, len(content), CHUNK_BYTES):
                yield memoryview(content)[offset : offset + CHUNK_BYTES]
        else:
            for offset in range(0, len(content), CHUNK_BYTES // 4):
                yield content[offset : offset + CHUNK_BYTES // 4].encode("utf-8")

    def load_manifest(self, artifact_dir: Path) -> ArtifactManifest:
        ensure_plain_path(artifact_dir)
        manifest_file = artifact_dir / "manifest.json"
        ensure_plain_path(manifest_file)
        if not manifest_file.is_file():
            raise FileNotFoundError(f"Manifest not found in artifact directory: {artifact_dir}")
        with manifest_file.open("rb") as source:
            raw = source.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("Artifact manifest exceeds byte limit")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema_version") not in (1, 2):
            raise ValueError("Unsupported manifest schema_version")
        return ArtifactManifest.model_validate(data)

    def verify_artifact(self, artifact_dir: Path) -> ArtifactManifest:
        ensure_plain_path(artifact_dir)
        if not artifact_dir.is_dir():
            raise FileNotFoundError(f"Artifact directory not found: {artifact_dir}")
        marker = artifact_dir / "_COMPLETED"
        ensure_plain_path(marker)
        if not marker.is_file():
            raise ValueError(f"Artifact at '{artifact_dir}' is incomplete (missing '_COMPLETED')")
        manifest = self.load_manifest(artifact_dir)
        if manifest.status != "completed":
            raise ValueError(f"Artifact status is '{manifest.status}', expected 'completed'")
        manifest.check_identity()
        if sum(entry.size_bytes for entry in manifest.files) > self.max_publication_bytes:
            raise ValueError("Artifact verification exceeds payload byte limit")
        for entry in manifest.files:
            path = validate_manifest_path(artifact_dir, entry.path)
            if not path.is_file():
                raise FileNotFoundError(f"Corrupt artifact: expected file '{entry.path}' not found")
            if path.stat().st_size != entry.size_bytes:
                raise ValueError(f"Corrupt artifact: file '{entry.path}' size mismatch")
            if compute_file_sha256(path, max_bytes=entry.size_bytes) != entry.sha256:
                raise ValueError(f"Corrupt artifact: file '{entry.path}' checksum mismatch")
        expected = {f.path for f in manifest.files} | {"manifest.json", "_COMPLETED"}
        count = 0
        for path in artifact_dir.rglob("*"):
            count += 1
            if count > 2 * 10_000 + 100:
                raise ValueError("Artifact tree exceeds bounded verification entry limit")
            ensure_plain_path(path)
            if path.is_file() and path.relative_to(artifact_dir).as_posix() not in expected:
                raise ValueError(f"Corrupt artifact: unlisted payload file {path.name!r}")
        return manifest
