"""Versioned deterministic sharded JSONL datasets plus streaming readers.

A sharded dataset is an explicit manifest (``dataset-manifest.json``) plus
``shard-00000.jsonl`` … ``shard-N.jsonl`` files holding the same logical
record stream as one concatenated JSONL. Shard boundaries depend only on
source record order, the configured target size, and serialized canonical
size — never on timing, workers, or completion order. A single document
larger than the target occupies one oversize shard; documents are never
split.

Publication is crash-safe by construction: every shard is staged to a
temporary file, hashed/counted in the single streaming pass, fsynced, and
atomically renamed; the manifest is written only after every declared
shard is re-verified by reread, so no manifest ever references an
incomplete shard. A crash leaves staging temps and/or orphan finals with
no manifest; rerunning the same deterministic command regenerates
byte-identical outputs. Resume-by-reuse is intentionally NOT wired into
callers here (fail-closed restart instead); :meth:`ShardedJsonlWriter.adopt`
provides the verified-prefix primitive for later stages.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xlm.data.acquisition.records import StreamingJsonlWriter

#: Manifest contract version. Bump explicitly if the schema changes.
SHARD_MANIFEST_VERSION = 1

#: Manifest filename inside a sharded dataset directory.
MANIFEST_FILENAME = "dataset-manifest.json"

#: Shard filename pattern: ``shard-00000.jsonl`` (zero-padded ordinals sort
#: lexicographically in shard order).
SHARD_FILENAME_RE = re.compile(r"^shard-(\d{5})\.jsonl$")

#: Cap for loading a manifest document (shard entries are small; 64 MiB
#: covers ~300k shards and keeps hostile manifests bounded).
MANIFEST_MAX_BYTES = 64 * 1024 * 1024


def shard_filename(ordinal: int) -> str:
    """Deterministic shard filename for a zero-based ordinal."""
    if ordinal < 0 or ordinal > 99999:
        raise ValueError(f"shard ordinal out of range: {ordinal}")
    return f"shard-{ordinal:05d}.jsonl"


@dataclass(frozen=True)
class ShardEntry:
    """One verified shard: identity plus independent integrity metadata."""

    ordinal: int
    path: str
    doc_count: int
    byte_count: int
    sha256: str
    oversize: bool = False
    first_doc_id: str | None = None
    last_doc_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "path": self.path,
            "doc_count": self.doc_count,
            "byte_count": self.byte_count,
            "sha256": self.sha256,
            "oversize": self.oversize,
            "first_doc_id": self.first_doc_id,
            "last_doc_id": self.last_doc_id,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ShardEntry:
        try:
            return ShardEntry(
                ordinal=int(data["ordinal"]),
                path=str(data["path"]),
                doc_count=int(data["doc_count"]),
                byte_count=int(data["byte_count"]),
                sha256=str(data["sha256"]),
                oversize=bool(data.get("oversize", False)),
                first_doc_id=data.get("first_doc_id"),
                last_doc_id=data.get("last_doc_id"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed shard entry: {exc}") from exc


@dataclass(frozen=True)
class ShardManifest:
    """Deterministic manifest for one sharded dataset (no timestamps)."""

    schema_version: int = SHARD_MANIFEST_VERSION
    artifact_type: str = "canonical_documents_sharded"
    dataset_id: str = ""
    source_artifact: dict[str, Any] = field(default_factory=dict)
    producer: dict[str, Any] = field(default_factory=dict)
    shard_target_bytes: int = 0
    shards: tuple[ShardEntry, ...] = ()
    total_documents: int = 0
    total_bytes: int = 0
    aggregate_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_type": self.artifact_type,
            "dataset_id": self.dataset_id,
            "source_artifact": dict(self.source_artifact),
            "producer": dict(self.producer),
            "shard_target_bytes": self.shard_target_bytes,
            "shards": [entry.to_dict() for entry in self.shards],
            "total_documents": self.total_documents,
            "total_bytes": self.total_bytes,
            "aggregate_sha256": self.aggregate_sha256,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ShardManifest:
        try:
            if data.get("schema_version") != SHARD_MANIFEST_VERSION:
                raise ValueError(
                    f"unsupported shard manifest version {data.get('schema_version')!r}"
                )
            shards = tuple(ShardEntry.from_dict(entry) for entry in data.get("shards", []))
            return ShardManifest(
                schema_version=int(data["schema_version"]),
                artifact_type=str(data.get("artifact_type", "")),
                dataset_id=str(data.get("dataset_id", "")),
                source_artifact=dict(data.get("source_artifact", {})),
                producer=dict(data.get("producer", {})),
                shard_target_bytes=int(data.get("shard_target_bytes", 0)),
                shards=shards,
                total_documents=int(data.get("total_documents", 0)),
                total_bytes=int(data.get("total_bytes", 0)),
                aggregate_sha256=str(data.get("aggregate_sha256", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed shard manifest: {exc}") from exc


def aggregate_identity(shards: list[ShardEntry]) -> str:
    """Aggregate SHA-256 over per-shard identities in ordinal order."""
    digest = hashlib.sha256()
    for entry in sorted(shards, key=lambda item: item.ordinal):
        digest.update(
            f"{entry.ordinal}:{entry.path}:{entry.sha256}:{entry.doc_count}:"
            f"{entry.byte_count}\n".encode()
        )
    return digest.hexdigest()


def _safe_shard_path(root: Path, name: str) -> Path:
    """Resolve a manifest shard path, refusing traversal outside the root."""
    match = SHARD_FILENAME_RE.fullmatch(name)
    if not match:
        raise ValueError(f"shard path is not a dataset shard: {name!r}")
    resolved = (root / name).resolve()
    if resolved != root.resolve() / name:
        raise ValueError(f"shard path escapes the dataset root: {name!r}")
    return root / name


def load_manifest(path: Path) -> ShardManifest:
    """Load and structurally validate a manifest document (not its shards)."""
    if not path.is_file():
        raise FileNotFoundError(f"shard manifest not found: {path}")
    if path.stat().st_size > MANIFEST_MAX_BYTES:
        raise ValueError(f"shard manifest exceeds {MANIFEST_MAX_BYTES} bytes: {path}")
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"malformed shard manifest {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"malformed shard manifest {path.name}: not an object")
    manifest = ShardManifest.from_dict(data)
    _check_manifest_structure(manifest)
    return manifest


def _check_manifest_structure(manifest: ShardManifest) -> None:
    ordinals = [entry.ordinal for entry in manifest.shards]
    if sorted(ordinals) != list(range(len(ordinals))):
        raise ValueError("shard ordinals must be dense zero-based without gaps or duplicates")
    paths = [entry.path for entry in manifest.shards]
    if len(set(paths)) != len(paths):
        raise ValueError("duplicate shard filenames in manifest")
    for entry in manifest.shards:
        if SHARD_FILENAME_RE.fullmatch(entry.path) is None:
            raise ValueError(f"shard path is not a dataset shard: {entry.path!r}")
        if entry.doc_count < 0 or entry.byte_count < 0:
            raise ValueError(f"negative shard size for {entry.path!r}")
        if len(entry.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in entry.sha256
        ):
            raise ValueError(f"shard {entry.path!r} lacks a SHA-256 hex digest")
    if sum(entry.doc_count for entry in manifest.shards) != manifest.total_documents:
        raise ValueError("manifest total_documents does not match shard counts")
    if sum(entry.byte_count for entry in manifest.shards) != manifest.total_bytes:
        raise ValueError("manifest total_bytes does not match shard sizes")
    if aggregate_identity(list(manifest.shards)) != manifest.aggregate_sha256:
        raise ValueError("manifest aggregate identity does not match shard entries")


def verify_manifest(root: Path, manifest: ShardManifest) -> ShardManifest:
    """Strictly verify every declared shard against disk (sizes, hashes, counts).

    Rereading is verification, not production: producers must accumulate this
    information during writing, never reread merely to learn it.
    """
    _check_manifest_structure(manifest)
    for entry in manifest.shards:
        path = _safe_shard_path(root, entry.path)
        if not path.is_file():
            raise ValueError(f"manifest shard missing: {entry.path!r}")
        digest = hashlib.sha256()
        count = 0
        size = 0
        with path.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
                size += len(block)
                count += block.count(b"\n")
        if size != entry.byte_count:
            raise ValueError(
                f"shard {entry.path!r} byte count {size} != manifest {entry.byte_count}"
            )
        if digest.hexdigest() != entry.sha256:
            raise ValueError(f"shard {entry.path!r} SHA-256 mismatch")
        if count != entry.doc_count:
            raise ValueError(
                f"shard {entry.path!r} holds {count} lines, manifest says {entry.doc_count}"
            )
    return manifest


def iter_shard_records(
    root: Path, manifest: ShardManifest
) -> Iterator[tuple[dict[str, Any], str, int, int]]:
    """Yield ``(record, shard_path, ordinal, global_seq)`` in shard order."""
    sequence = 0
    for entry in sorted(manifest.shards, key=lambda item: item.ordinal):
        path = _safe_shard_path(root, entry.path)
        with path.open("rb") as stream:
            pending = bytearray()
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                pending += chunk
                *lines, remainder = bytes(pending).split(b"\n")
                pending = bytearray(remainder)
                for raw in lines:
                    if not raw.strip():
                        continue
                    yield json.loads(raw.decode()), entry.path, entry.ordinal, sequence
                    sequence += 1
            if pending.strip():
                yield (
                    json.loads(bytes(pending).decode("utf-8")),
                    entry.path,
                    entry.ordinal,
                    sequence,
                )
                sequence += 1


def iter_single_records(path: Path) -> Iterator[tuple[dict[str, Any], str, int, int]]:
    """Yield ``(record, shard_id, ordinal, seq)`` from one legacy JSONL file."""
    shard_id = path.name
    sequence = 0
    with path.open("rb") as stream:
        pending = bytearray()
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            pending += chunk
            *lines, remainder = bytes(pending).split(b"\n")
            pending = bytearray(remainder)
            for raw in lines:
                if not raw.strip():
                    continue
                yield json.loads(raw.decode()), shard_id, 0, sequence
                sequence += 1
        if pending.strip():
            yield json.loads(bytes(pending).decode()), shard_id, 0, sequence
            sequence += 1


def iter_input_blocks(
    source: Path, *, chunk_bytes: int = 65536
) -> Iterator[tuple[bytes, str, int]]:
    """Yield ``(chunk, shard_id, ordinal)`` bytes for a legacy file or manifest dir.

    Detection is explicit by input type/path, never by content guessing.
    Manifests are structurally validated upfront; shards stream in ordinal
    order. A manifest directory streams the concatenation of its shards,
    which is byte-identical to the equivalent single file when shards hold
    no blank lines (writers never emit any).
    """
    if chunk_bytes < 1:
        raise ValueError("input chunk bytes must be positive")
    if source.is_dir():
        manifest_path = source / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise ValueError(
                f"directory input has no {MANIFEST_FILENAME}: {source}; refusing to guess shards"
            )
        manifest = load_manifest(manifest_path)
        for entry in sorted(manifest.shards, key=lambda item: item.ordinal):
            path = _safe_shard_path(source, entry.path)
            with path.open("rb") as stream:
                while True:
                    chunk = stream.read(chunk_bytes)
                    if not chunk:
                        break
                    yield chunk, entry.path, entry.ordinal
        return
    if source.is_file() and source.suffix == ".jsonl":
        with source.open("rb") as stream:
            while True:
                chunk = stream.read(chunk_bytes)
                if not chunk:
                    break
                yield chunk, source.name, 0
        return
    raise ValueError(f"unsupported adapt input (need .jsonl file or manifest dir): {source}")


def input_byte_size(source: Path) -> int:
    """Upfront input size without reading content (stat or manifest totals)."""
    if source.is_dir():
        manifest_path = source / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise ValueError(
                f"directory input has no {MANIFEST_FILENAME}: {source}; refusing to guess shards"
            )
        return load_manifest(manifest_path).total_bytes
    if source.is_file() and source.suffix == ".jsonl":
        return source.stat().st_size
    raise ValueError(f"unsupported adapt input (need .jsonl file or manifest dir): {source}")


class ShardedJsonlWriter:
    """Deterministic size-sharded JSONL writer with atomic per-shard publication.

    Lines are routed to ``shard-00000.jsonl`` … in call order; a shard rolls
    once it reaches ``target_shard_bytes`` (the in-flight line always
    finishes first, so one oversize document yields one flagged oversize
    shard rather than a split document). Each shard is staged, hashed,
    counted, fsynced, and atomically renamed on roll; the manifest is
    written only after every declared shard is re-verified. :meth:`adopt`
    exposes the verified-prefix primitive for later resume work.
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        dataset_id: str,
        target_shard_bytes: int,
        source_artifact: dict[str, Any] | None = None,
        producer: dict[str, Any] | None = None,
    ) -> None:
        if target_shard_bytes < 1:
            raise ValueError("shard target bytes must be positive")
        self._directory = output_dir
        self._dataset_id = dataset_id
        self._target = target_shard_bytes
        self._source_artifact = dict(source_artifact or {})
        self._producer = dict(producer or {})
        manifest_path = output_dir / MANIFEST_FILENAME
        if manifest_path.exists():
            raise FileExistsError(
                f"refusing to overwrite published dataset manifest '{manifest_path}'; "
                "use a fresh output dir"
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        self._entries: list[ShardEntry] = []
        self._writer: StreamingJsonlWriter | None = None
        self._current_first: str | None = None
        self._current_last: str | None = None
        self._current_temp: Path | None = None
        self._abandoned = False
        self._flush_seconds = 0.0
        self._peak_rss = 0

    def _roll_shard(self) -> None:
        """Atomically publish the current shard and record its entry."""
        assert self._writer is not None and self._current_temp is not None
        writer, temporary = self._writer, self._current_temp
        writer.close()
        self._flush_seconds += writer.flush_seconds
        self._peak_rss = max(self._peak_rss, writer.peak_rss_bytes)
        final = self._directory / shard_filename(len(self._entries))
        os.replace(temporary, final)
        oversize = writer.size > self._target and writer.count <= 1
        self._entries.append(
            ShardEntry(
                ordinal=len(self._entries),
                path=final.name,
                doc_count=writer.count,
                byte_count=writer.size,
                sha256=writer.digest.hexdigest(),
                oversize=oversize,
                first_doc_id=self._current_first,
                last_doc_id=self._current_last,
            )
        )
        self._writer = None
        self._current_temp = None
        self._current_first = None
        self._current_last = None

    def write_line(self, line: str, doc_id: str | None) -> None:
        """Append one serialized record line (without trailing newline)."""
        if self._abandoned:
            raise ValueError("writer was abandoned")
        if len(self._entries) >= 100000:
            raise ValueError("shard count exceeds the 100000-shard safety bound")
        if self._writer is None:
            temporary = (
                self._directory / f"{shard_filename(len(self._entries))}.{uuid.uuid4().hex}.tmp"
            )
            self._current_temp = temporary
            self._writer = StreamingJsonlWriter(temporary)
        assert self._writer is not None
        data = line.encode("utf-8") + b"\n"
        if self._writer.count > 0 and self._writer.size >= self._target:
            self._roll_shard()
            temporary = (
                self._directory / f"{shard_filename(len(self._entries))}.{uuid.uuid4().hex}.tmp"
            )
            self._current_temp = temporary
            self._writer = StreamingJsonlWriter(temporary)
        self._writer.write_line(data)
        if self._current_first is None:
            self._current_first = doc_id
        self._current_last = doc_id

    def adopt(self, manifest: ShardManifest) -> int:
        """Adopt a verified prefix of a caller-supplied manifest (P27B primitive).

        Every adopted shard is re-verified against disk (ordinal, path,
        bytes, SHA-256, count); the first mismatch raises without adopting
        anything further. Returns the adopted document count. Unverified
        tails are never adopted, duplicated, skipped, or shifted: adoption
        stops at the first gap.
        """
        if self._writer is not None or self._entries:
            raise ValueError("adopt into a fresh writer before any writes")
        adopted = 0
        staged: list[ShardEntry] = []
        for entry in sorted(manifest.shards, key=lambda item: item.ordinal):
            if entry.ordinal != len(staged):
                raise ValueError(f"cannot adopt shard ordinal {entry.ordinal} out of sequence")
            path = _safe_shard_path(self._directory, entry.path)
            if not path.is_file():
                raise ValueError(f"adopted shard missing: {entry.path!r}")
            digest = hashlib.sha256()
            count = 0
            size = 0
            with path.open("rb") as stream:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    size += len(block)
                    count += block.count(b"\n")
            if size != entry.byte_count or digest.hexdigest() != entry.sha256:
                raise ValueError(f"adopted shard {entry.path!r} failed verification")
            if count != entry.doc_count:
                raise ValueError(f"adopted shard {entry.path!r} failed verification")
            staged.append(entry)
            adopted += entry.doc_count
        self._entries.extend(staged)
        return adopted

    @property
    def entries(self) -> list[ShardEntry]:
        return list(self._entries)

    @property
    def flush_seconds(self) -> float:
        total = self._flush_seconds
        if self._writer is not None:
            total += self._writer.flush_seconds
        return total

    @property
    def peak_rss_bytes(self) -> int:
        peak = self._peak_rss
        if self._writer is not None:
            peak = max(peak, self._writer.peak_rss_bytes)
        return peak

    def finish(self) -> ShardManifest:
        """Publish the open shard (if any), verify everything, publish manifest last."""
        if self._abandoned:
            raise ValueError("writer was abandoned")
        if self._writer is not None:
            self._roll_shard()
        manifest = ShardManifest(
            schema_version=SHARD_MANIFEST_VERSION,
            artifact_type="canonical_documents_sharded",
            dataset_id=self._dataset_id,
            source_artifact=self._source_artifact,
            producer=self._producer,
            shard_target_bytes=self._target,
            shards=tuple(self._entries),
            total_documents=sum(entry.doc_count for entry in self._entries),
            total_bytes=sum(entry.byte_count for entry in self._entries),
            aggregate_sha256=aggregate_identity(self._entries),
        )
        verify_manifest(self._directory, manifest)
        temporary = self._directory / f"{MANIFEST_FILENAME}.{uuid.uuid4().hex}.tmp"
        try:
            payload = (json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n").encode()
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._directory / MANIFEST_FILENAME)
        finally:
            temporary.unlink(missing_ok=True)
        return manifest

    def abandon(self) -> None:
        """Discard staging state without publishing; finals already rolled stay."""
        self._abandoned = True
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
        if self._current_temp is not None:
            self._current_temp.unlink(missing_ok=True)
            self._current_temp = None
