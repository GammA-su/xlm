"""Versioned declared publication identity and portable artifact path validation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_FILES = 10_000
RESERVED_FILES = frozenset({"manifest.json", "_completed"})
INTERNAL_KINDS = frozenset({"ledger", "locks", "staging", "tmp", "runs"})
_DEVICE = re.compile(r"^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)", re.I)


def validate_component(value: str) -> str:
    """One portable component, before filesystem operations on any platform."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or value in (".", "..")
        or value.startswith(".")
        or value.endswith((".", " "))
        or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in value)
        or _DEVICE.match(value)
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ValueError(f"Invalid artifact path component: {value!r}")
    return value


def canonical_payload_path(value: str) -> str:
    """Reject noncanonical/ambiguous paths, including foreign-platform escapes."""
    if not isinstance(value, str) or not value or PureWindowsPath(value).drive:
        raise ValueError(f"Path traversal detected or invalid payload path: {value!r}")
    if "\\" in value or value.startswith("/"):
        raise ValueError(f"Path traversal detected or noncanonical payload path: {value!r}")
    parts = value.split("/")
    if len(parts) > 32 or len(value) > 2048:
        raise ValueError("Artifact payload path exceeds length/depth limit")
    for part in parts:
        try:
            validate_component(part)
        except ValueError as exc:
            raise ValueError(f"Path traversal detected or invalid payload path: {value!r}") from exc
    if parts[0].casefold() in RESERVED_FILES:
        raise ValueError(f"Reserved publication path: {value!r}")
    return value


def validate_file_set(names: list[str]) -> None:
    if not names or len(names) > MAX_FILES:
        raise ValueError(f"Artifact must have 1..{MAX_FILES} payload files")
    seen: set[str] = set()
    directories: set[str] = set()
    spelling: dict[str, str] = {}
    for name in sorted(names):
        key = canonical_payload_path(name).casefold()
        parts = name.split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            folded = prefix.casefold()
            if folded in spelling and spelling[folded] != prefix:
                raise ValueError(f"Colliding artifact paths: {name!r}")
            spelling[folded] = prefix
        parents = ["/".join(key.split("/")[:i]) for i in range(1, len(key.split("/")))]
        if key in seen or key in directories or any(p in seen for p in parents):
            raise ValueError(f"Colliding artifact paths: {name!r}")
        seen.add(key)
        directories.update(parents)
        if len(seen) + len(directories) > 2 * MAX_FILES:
            raise ValueError("Artifact payload tree exceeds entry limit")


def bounded_children(directory: Path) -> Iterator[Path]:
    """Bound directory discovery without materializing an unrestricted list."""
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_FILES:
                raise ValueError("Artifact directory enumeration exceeds entry limit")
            yield Path(entry.path)


def ensure_plain_path(path: Path) -> None:
    """Reject existing links/junctions in every ancestor, including dangling links."""
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError(f"Artifact path contains a symlink/junction: {part}")


def comparable_resolved(path: Path) -> Path:
    """Resolve for containment comparison, independent of path existence.

    On Windows, ``Path.resolve()`` returns an extended-length (``\\\\?\\``)
    form when the path can be opened and the plain form otherwise, so two
    resolutions of nearby paths can disagree on form when files are created
    concurrently. Stripping that prefix keeps the comparison deterministic.
    Containment stays sound: link/junction rejection happens separately in
    ``ensure_plain_path``, and no separators survive component validation.
    """
    text = str(path.resolve())
    if text.startswith("\\\\?\\"):
        stripped = text[len("\\\\?\\") :]
        if stripped.startswith("UNC\\"):
            stripped = "\\\\" + stripped[len("UNC\\") :]
        text = stripped
    return Path(text)


def validate_manifest_path(artifact_dir: Path, rel_path: str) -> Path:
    canonical_payload_path(rel_path)
    target = artifact_dir / rel_path
    ensure_plain_path(target)
    if not comparable_resolved(target).is_relative_to(comparable_resolved(artifact_dir)):
        raise ValueError(f"Path traversal detected: {rel_path!r}")
    return target


def canonical_json(value: Any) -> bytes:
    """Bound structure/strings before canonical encoding; never allow NaN or code."""
    remaining = MAX_MANIFEST_BYTES
    nodes = 0

    def check(item: Any, depth: int) -> None:
        nonlocal remaining, nodes
        nodes += 1
        if depth > 32 or nodes > 100_000 or remaining < 0:
            raise ValueError("Artifact metadata exceeds bounded manifest limits")
        if isinstance(item, str):
            remaining -= len(item)
        elif isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("Artifact metadata keys must be strings")
                check(key, depth + 1)
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
        elif item is not None and type(item) not in (int, float, bool):
            raise ValueError(f"Artifact metadata is not JSON data: {type(item).__name__}")
        if remaining < 0:
            raise ValueError("Artifact metadata exceeds bounded manifest limits")

    check(value, 0)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ValueError("Artifact manifest exceeds byte limit")
    return encoded


def identity_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


class ArtifactFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ArtifactManifest(BaseModel):
    """v1 integrity remains legacy; v2 binds declared request and payload separately."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1, 2] = 1
    artifact_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    status: Literal["pending", "completed"] = "completed"
    created_at: str
    producer_code_hash: str = Field(min_length=8)
    dependency_hash: str = Field(min_length=8)
    resolved_config_hash: str = Field(min_length=8)
    input_artifact_ids: list[str] = Field(default_factory=list)
    files: list[ArtifactFile]
    metadata: dict[str, Any] = Field(default_factory=dict)
    serializer_version: str | None = None
    input_manifest_hashes: dict[str, str] = Field(default_factory=dict)
    cosmetic_metadata: dict[str, Any] = Field(default_factory=dict)
    production_key: str | None = None
    content_hash: str | None = None

    @model_validator(mode="after")
    def validate_structure(self) -> ArtifactManifest:
        validate_component(self.artifact_id)
        validate_component(self.kind)
        if self.kind.casefold() in INTERNAL_KINDS:
            raise ValueError(f"Reserved artifact kind: {self.kind}")
        validate_file_set([entry.path for entry in self.files])
        if len(set(self.input_artifact_ids)) != len(self.input_artifact_ids):
            raise ValueError("Duplicate input artifact IDs")
        if not set(self.input_manifest_hashes).issubset(self.input_artifact_ids):
            raise ValueError("Input manifest fingerprints must name declared input IDs")
        if any(not re.fullmatch(r"[0-9a-f]{64}", h) for h in self.input_manifest_hashes.values()):
            raise ValueError("Invalid input manifest fingerprint")
        if self.schema_version == 1:
            if any(
                (
                    self.serializer_version,
                    self.production_key,
                    self.content_hash,
                    self.input_manifest_hashes,
                    self.cosmetic_metadata,
                )
            ):
                raise ValueError("Legacy schema cannot carry v2 identity claims")
        elif not self.serializer_version:
            raise ValueError("v2 artifact requires a declared serializer version")
        canonical_json(self.model_dump())
        return self

    def production_identity(self) -> dict[str, Any]:
        return {
            "identity_version": 2,
            "kind": self.kind,
            "schema_version": self.schema_version,
            "serializer_version": self.serializer_version,
            "producer_code_hash": self.producer_code_hash,
            "dependency_hash": self.dependency_hash,
            "resolved_config_hash": self.resolved_config_hash,
            "input_artifact_ids": self.input_artifact_ids,
            "input_manifest_hashes": self.input_manifest_hashes,
            "metadata": self.metadata,
        }

    def output_identity(self) -> list[dict[str, Any]]:
        return [f.model_dump() for f in sorted(self.files, key=lambda f: f.path)]

    @property
    def identity_scope(self) -> str:
        return "declared-request-v2" if self.schema_version == 2 else "legacy-checksum-only"

    @property
    def unresolved_input_artifact_ids(self) -> list[str]:
        return [key for key in self.input_artifact_ids if key not in self.input_manifest_hashes]

    def check_identity(self) -> None:
        if self.schema_version == 2 and (
            self.production_key != identity_digest(self.production_identity())
            or self.content_hash != identity_digest(self.output_identity())
        ):
            raise ValueError("Corrupt artifact publication identity digest")
