"""Immutable artifact publication, verification, and file-locked storage."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from xlm.artifacts.manifest import ArtifactFile, ArtifactManifest, validate_manifest_path
from xlm.core.paths import ArtifactPaths


def compute_file_sha256(path: Path) -> str:
    """Compute SHA-256 checksum of a file in bounded memory chunks."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


class ArtifactStore:
    """Manages immutable artifact publishing and validation with file locking."""

    def __init__(self, paths: ArtifactPaths) -> None:
        self.paths = paths
        self.staging_dir = self.paths.root / ".staging"
        self.locks_dir = self.paths.root / ".locks"

    def _ensure_internal_dirs(self) -> None:
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)

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
    ) -> Path:
        """Atomically publish a completed artifact with manifest and checksums."""
        self._ensure_internal_dirs()
        lock_file = self.locks_dir / f"{artifact_id}.lock"
        dest_dir = self.paths.root / kind / artifact_id

        with FileLock(str(lock_file), timeout=10.0):
            # Check destination under the lock for idempotence or conflict
            if dest_dir.exists() and (dest_dir / "_COMPLETED").exists():
                # Verify existing artifact
                try:
                    self.verify_artifact(dest_dir)
                    # Check whether the publication request matches the existing artifact
                    existing_manifest = self.load_manifest(dest_dir)
                    if (
                        existing_manifest.artifact_id == artifact_id
                        and existing_manifest.resolved_config_hash == resolved_config_hash
                    ):
                        # Idempotent success: return existing artifact directory
                        return dest_dir
                except Exception:  # noqa: BLE001
                    pass
                raise ValueError(
                    f"Artifact conflict: artifact '{artifact_id}' already exists at {dest_dir} with conflicting content"
                )

            # Create private staging directory on destination filesystem
            staging_id = f"{artifact_id}_{uuid.uuid4().hex[:8]}"
            stage_path = self.staging_dir / staging_id
            stage_path.mkdir(parents=True, exist_ok=True)

            try:
                manifest_files: list[ArtifactFile] = []

                # Write payload files
                for rel_name, content in files.items():
                    target_file = validate_manifest_path(stage_path, rel_name)
                    target_file.parent.mkdir(parents=True, exist_ok=True)

                    if isinstance(content, Path):
                        shutil.copy2(content, target_file)
                    elif isinstance(content, bytes):
                        target_file.write_bytes(content)
                    elif isinstance(content, str):
                        target_file.write_text(content, encoding="utf-8")
                    else:
                        raise TypeError(f"Unsupported content type for file '{rel_name}': {type(content)}")

                    file_size = target_file.stat().st_size
                    file_sha = compute_file_sha256(target_file)
                    manifest_files.append(
                        ArtifactFile(path=rel_name, size_bytes=file_size, sha256=file_sha)
                    )

                # Write manifest
                now_str = datetime.now(timezone.utc).isoformat()
                manifest = ArtifactManifest(
                    schema_version=1,
                    artifact_id=artifact_id,
                    kind=kind,
                    status="completed",
                    created_at=now_str,
                    producer_code_hash=producer_code_hash,
                    dependency_hash=dependency_hash,
                    resolved_config_hash=resolved_config_hash,
                    input_artifact_ids=input_artifact_ids or [],
                    files=manifest_files,
                    metadata=metadata or {},
                )

                manifest_path = stage_path / "manifest.json"
                manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

                # Write completion marker
                completion_marker = stage_path / "_COMPLETED"
                completion_marker.write_text(f"COMPLETED at {now_str}\n", encoding="utf-8")

                # Atomic rename from staging to final destination
                dest_dir.parent.mkdir(parents=True, exist_ok=True)
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)
                stage_path.rename(dest_dir)

                return dest_dir

            except Exception:
                # Cleanup staging on failure
                if stage_path.exists():
                    shutil.rmtree(stage_path, ignore_errors=True)
                raise

    def load_manifest(self, artifact_dir: Path) -> ArtifactManifest:
        """Load and parse artifact manifest."""
        manifest_file = artifact_dir / "manifest.json"
        if not manifest_file.exists():
            raise FileNotFoundError(f"Manifest not found in artifact directory: {artifact_dir}")
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError(f"Unsupported manifest schema_version: {data.get('schema_version')}")
        return ArtifactManifest.model_validate(data)

    def verify_artifact(self, artifact_dir: Path) -> ArtifactManifest:
        """Verify artifact completeness, manifest schema, and file checksums."""
        if not artifact_dir.is_dir():
            raise FileNotFoundError(f"Artifact directory not found: {artifact_dir}")

        marker = artifact_dir / "_COMPLETED"
        if not marker.exists():
            raise ValueError(
                f"Artifact at '{artifact_dir}' is incomplete (missing '_COMPLETED' publication marker)"
            )

        manifest = self.load_manifest(artifact_dir)
        if manifest.status != "completed":
            raise ValueError(f"Artifact status is '{manifest.status}', expected 'completed'")

        for f_entry in manifest.files:
            target_path = validate_manifest_path(artifact_dir, f_entry.path)
            if not target_path.is_file():
                raise FileNotFoundError(
                    f"Corrupt artifact: expected file '{f_entry.path}' not found at {target_path}"
                )

            actual_size = target_path.stat().st_size
            if actual_size != f_entry.size_bytes:
                raise ValueError(
                    f"Corrupt artifact: file '{f_entry.path}' size mismatch "
                    f"(expected {f_entry.size_bytes} bytes, got {actual_size})"
                )

            actual_sha = compute_file_sha256(target_path)
            if actual_sha != f_entry.sha256:
                raise ValueError(
                    f"Corrupt artifact: file '{f_entry.path}' checksum mismatch "
                    f"(expected {f_entry.sha256}, got {actual_sha})"
                )

        return manifest
