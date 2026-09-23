"""Versioned precomputed-embedding artifact contract (P28-M).

Vectors live in contiguous ``.npy`` shards (never JSON); the manifest carries
every identity needed to fail closed on mismatch: schema version, source
corpus identity, model identity, dimension, dtype, normalization policy, the
ordered document IDs, per-shard hashes, and totals.

NumPy is imported lazily so this module (validation, manifests) stays usable
wherever the locked environment runs; only shard vector I/O needs it.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

EMBEDDING_MANIFEST_VERSION = 1
EMBEDDING_MANIFEST_FILENAME = "embedding-manifest.json"

_SUPPORTED_DTYPES = ("float32", "float16")


@dataclass
class EmbeddingShardRef:
    """Reference to one vector shard file."""

    path: str
    rows: int
    dim: int
    dtype: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> EmbeddingShardRef:
        return EmbeddingShardRef(
            path=str(data["path"]),
            rows=int(data["rows"]),
            dim=int(data["dim"]),
            dtype=str(data["dtype"]),
            sha256=str(data["sha256"]),
        )


@dataclass
class EmbeddingManifest:
    """Identity of a precomputed embedding artifact."""

    schema_version: int = EMBEDDING_MANIFEST_VERSION
    artifact_type: str = "embedding_shard_set"
    source_artifact: dict[str, Any] = field(default_factory=dict)
    model_identity: str = ""
    dim: int = 0
    dtype: str = "float32"
    normalized: bool = True
    truncation_policy: str = ""
    doc_ids: list[str] = field(default_factory=list)
    shards: list[EmbeddingShardRef] = field(default_factory=list)
    total_vectors: int = 0
    vector_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["shards"] = [shard.to_dict() for shard in self.shards]
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> EmbeddingManifest:
        if data.get("schema_version") != EMBEDDING_MANIFEST_VERSION:
            raise ValueError(
                f"unsupported embedding manifest version {data.get('schema_version')!r}"
            )
        return EmbeddingManifest(
            schema_version=int(data["schema_version"]),
            artifact_type=str(data.get("artifact_type", "")),
            source_artifact=dict(data.get("source_artifact", {})),
            model_identity=str(data.get("model_identity", "")),
            dim=int(data.get("dim", 0)),
            dtype=str(data.get("dtype", "float32")),
            normalized=bool(data.get("normalized", True)),
            truncation_policy=str(data.get("truncation_policy", "")),
            doc_ids=[str(item) for item in data.get("doc_ids", [])],
            shards=[EmbeddingShardRef.from_dict(item) for item in data.get("shards", [])],
            total_vectors=int(data.get("total_vectors", 0)),
            vector_bytes=int(data.get("vector_bytes", 0)),
        )

    def identity(self) -> str:
        """Stable identity of the embedding artifact (excludes mutable paths)."""
        payload = json.dumps(
            {
                "schema_version": self.schema_version,
                "source_artifact": self.source_artifact,
                "model_identity": self.model_identity,
                "dim": self.dim,
                "dtype": self.dtype,
                "normalized": self.normalized,
                "truncation_policy": self.truncation_policy,
                "doc_ids": self.doc_ids,
                "shards": [[sh.rows, sh.dim, sh.dtype, sh.sha256] for sh in self.shards],
                "total_vectors": self.total_vectors,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_numpy() -> Any:
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "NumPy is required for vector shard I/O; install numpy in the "
            "operator environment (no network installs happen here)."
        ) from exc
    return np


def write_vector_shard(vectors: Any, path: Path) -> str:
    """Write one contiguous ``.npy`` shard atomically; return its SHA-256."""
    np = _require_numpy()
    array = np.ascontiguousarray(vectors)
    if str(array.dtype) not in _SUPPORTED_DTYPES:
        raise ValueError(f"unsupported embedding dtype {array.dtype}; want {_SUPPORTED_DTYPES}")
    if array.ndim != 2:
        raise ValueError(f"embedding shards must be 2-D, got shape {array.shape}")
    temporary = path.parent / f"{path.name}.{uuid.uuid4().hex}.tmp"
    with temporary.open("wb") as stream:
        np.save(stream, array, allow_pickle=False)
        stream.flush()
        os.fsync(stream.fileno())
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    os.replace(temporary, path)
    return digest


def read_vector_shard(path: Path, *, memmap: bool = False) -> Any:
    """Read one ``.npy`` shard (memmap avoids duplicating large corpora)."""
    np = _require_numpy()
    if memmap:
        return np.load(path, allow_pickle=False, mmap_mode="r")
    with path.open("rb") as stream:
        return np.load(stream, allow_pickle=False)


def load_embedding_manifest(path: Path) -> EmbeddingManifest:
    """Load and structurally validate a manifest document (not its vectors)."""
    if not path.is_file():
        raise FileNotFoundError(f"embedding manifest not found: {path}")
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"malformed embedding manifest {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"malformed embedding manifest {path.name}: not an object")
    manifest = EmbeddingManifest.from_dict(data)
    if manifest.dtype not in _SUPPORTED_DTYPES:
        raise ValueError(f"unsupported embedding dtype {manifest.dtype!r}")
    if manifest.dim < 1:
        raise ValueError(f"embedding dim must be positive, got {manifest.dim}")
    return manifest


def validate_embedding_artifact(
    directory: Path, *, expected_source: dict[str, Any] | None = None
) -> EmbeddingManifest:
    """Fully validate an embedding artifact directory (fail closed).

    Checks schema version, dimension/dtype consistency, shard presence,
    per-shard SHA-256, row counts, ordered-ID coverage, and totals, plus an
    optional exact source-artifact match. Returns the valid manifest.
    """
    manifest = load_embedding_manifest(directory / EMBEDDING_MANIFEST_FILENAME)
    if expected_source is not None and manifest.source_artifact != expected_source:
        raise ValueError(
            "embedding artifact source mismatch: "
            f"{manifest.source_artifact} != {expected_source}; refusing to search"
        )
    total_rows = 0
    total_bytes = 0
    for shard in manifest.shards:
        if shard.dim != manifest.dim or shard.dtype != manifest.dtype:
            raise ValueError(
                f"shard {shard.path} dim/dtype {shard.dim}/{shard.dtype} disagrees "
                f"with manifest {manifest.dim}/{manifest.dtype}"
            )
        shard_path = directory / shard.path
        if not shard_path.is_file():
            raise ValueError(f"embedding shard missing: {shard.path}")
        payload = shard_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != shard.sha256:
            raise ValueError(f"embedding shard hash mismatch: {shard.path}")
        total_bytes += len(payload)
        rows = _shard_row_count(shard_path, manifest.dim, manifest.dtype)
        if rows != shard.rows:
            raise ValueError(f"shard {shard.path} holds {rows} rows, manifest says {shard.rows}")
        total_rows += rows
    if total_rows != manifest.total_vectors:
        raise ValueError(
            f"artifact holds {total_rows} vectors, manifest says {manifest.total_vectors}"
        )
    if len(manifest.doc_ids) != manifest.total_vectors:
        raise ValueError(
            f"manifest lists {len(manifest.doc_ids)} doc IDs for {manifest.total_vectors} vectors"
        )
    if len(set(manifest.doc_ids)) != len(manifest.doc_ids):
        raise ValueError("embedding manifest doc IDs are not unique")
    if total_bytes != manifest.vector_bytes and manifest.vector_bytes:
        raise ValueError(
            f"artifact holds {total_bytes} vector bytes, manifest says {manifest.vector_bytes}"
        )
    return manifest


def _shard_row_count(path: Path, dim: int, dtype: str) -> int:
    """Read (rows, dim, dtype) from a ``.npy`` header without importing NumPy.

    The v1.0 header is a short ASCII dict at a known offset; parsing it with
    the standard library keeps artifact validation usable in environments
    without NumPy (only actual vector math needs it).
    """
    import ast
    import struct

    with path.open("rb") as stream:
        magic = stream.read(6)
        if magic != b"\x93NUMPY":
            raise ValueError(f"shard {path.name} is not a .npy file")
        major, minor = stream.read(2)
        if (major, minor) != (1, 0):
            raise ValueError(f"shard {path.name} has unsupported .npy version {(major, minor)}")
        (header_len,) = struct.unpack("<H", stream.read(2))
        header = stream.read(header_len).decode("latin1")
        try:
            fields = ast.literal_eval(header)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"shard {path.name} has an unparsable .npy header") from exc
    try:
        shape = tuple(int(v) for v in fields["shape"])
        descr = str(fields["descr"])
        order = bool(fields["fortran_order"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"shard {path.name} has a malformed .npy header") from exc
    if order or len(shape) != 2 or shape[1] != dim:
        raise ValueError(
            f"shard {path.name} has shape {shape} C-order requirement unmet; "
            f"expected (*, {dim}) row-major"
        )
    expected_descr = {"float32": "<f4", "float16": "<f2"}[dtype]
    if descr != expected_descr and descr not in ("|f4", "|f2"):
        raise ValueError(f"shard {path.name} has descr {descr!r}, expected {expected_descr!r}")
    return shape[0]


def read_vector_rows_stdlib(path: Path) -> list[list[float]]:
    """Read a ``.npy`` shard payload as nested float rows without NumPy.

    Powers the pure-Python backend (and its tests) in environments without
    NumPy; vector math backends still require it. Only C-order float32 and
    float16 shards are supported.
    """
    import ast
    import struct

    with path.open("rb") as stream:
        if stream.read(6) != b"\x93NUMPY":
            raise ValueError(f"shard {path.name} is not a .npy file")
        if tuple(stream.read(2)) != (1, 0):
            raise ValueError(f"shard {path.name} has an unsupported .npy version")
        (header_len,) = struct.unpack("<H", stream.read(2))
        try:
            fields = ast.literal_eval(stream.read(header_len).decode("latin1"))
            shape = tuple(int(v) for v in fields["shape"])
            descr = str(fields["descr"])
            order = bool(fields["fortran_order"])
        except (SyntaxError, ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"shard {path.name} has a malformed .npy header") from exc
        payload = stream.read()
    if order or len(shape) != 2:
        raise ValueError(f"shard {path.name} must hold a 2-D C-order matrix")
    rows, dim = shape
    if descr in ("<f4", "=f4"):
        code, itemsize = "f", 4
    elif descr in ("<f2", "=f2"):
        code, itemsize = "e", 2
    else:
        raise ValueError(f"shard {path.name} has unsupported descr {descr!r}")
    if len(payload) != rows * dim * itemsize:
        raise ValueError(f"shard {path.name} payload size mismatch")
    values = struct.unpack(f"<{rows * dim}{code}", payload)
    return [list(values[i * dim : (i + 1) * dim]) for i in range(rows)]
