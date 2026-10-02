"""Physical file formats of the Mix-01 whole-file source transport.

The transport moves opaque bytes (resumable stream, SHA-256, durable hashed
copy); only identity labels, scratch names and local decoding depend on the
file format. The format is the file name's exact suffix: ``.parquet`` or
``.jsonl.gz``. Anything else is refused, never guessed.

Parquet keeps its historical identity kind, representation and scratch names
byte for byte (``source_parquet`` is frozen by the Essential-Web campaign), so
existing plans, partial downloads and durable sidecars are untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xlm.data.acquisition import jsonl_gz
from xlm.data.acquisition import source_parquet as sp

PARQUET = "parquet"
JSONL_GZ = jsonl_gz.FORMAT
#: Representation of the retained raw artifact, also the identity sidecar kind.
REPRESENTATIONS = {
    PARQUET: sp.IDENTITY_KIND,
    JSONL_GZ: "verified_source_jsonl_gz",
}
#: Raw-artifact contract a sealed unit of each format is published under.
RAW_CONTRACTS = {
    PARQUET: "mix01-source-raw-artifact-v1",
    JSONL_GZ: "mix01-source-raw-artifact-jsonl-gz-v1",
}


class SourceFormatError(ValueError):
    """A source file name has no supported format, or a durable identity is not its own."""


def source_format(name: str) -> str:
    """The exact format of a repository-relative source file name."""
    if name.endswith(".parquet"):
        return PARQUET
    if name.endswith(jsonl_gz.SUFFIX):
        return JSONL_GZ
    raise SourceFormatError(f"'{name}' is neither .parquet nor .jsonl.gz")


def partial_name(key: str, source_file: str) -> str:
    """Scratch name of a unit's partial download (Parquet keeps its historical name)."""
    suffix = "parquet" if source_format(source_file) == PARQUET else "jsonl.gz"
    return f"{key}.{suffix}.part"


def identity_record_for(
    identity: sp.SourceIdentity, *, source_file: str, repository: str, revision: str
) -> dict[str, Any]:
    """The durable identity sidecar of a verified source file of either format."""
    record = sp.identity_record(
        identity, source_file=source_file, repository=repository, revision=revision
    )
    record["kind"] = REPRESENTATIONS[source_format(source_file)]
    return record


def load_durable(destination: Path, source_file: str) -> dict[str, Any] | None:
    """A retained durable source and its sidecar, rehashed; None when absent.

    Parquet delegates to the frozen loader unchanged. Other formats apply the
    same rules to their own sidecar kind.
    """
    kind = source_format(source_file)
    if kind == PARQUET:
        return sp.load_durable_source(destination)
    sidecar = sp.identity_path(destination)
    if not destination.exists() or not sidecar.exists():
        return None
    if sidecar.stat().st_size > 1024 * 1024:
        raise SourceFormatError(f"'{sidecar.name}' exceeds its bounded size")
    record = json.loads(sidecar.read_bytes().decode("utf-8"))
    if (
        not isinstance(record, dict)
        or record.get("kind") != REPRESENTATIONS[kind]
        or record.get("version") != sp.IDENTITY_VERSION
    ):
        raise SourceFormatError(f"'{sidecar.name}' is not a verified {kind} identity")
    if sp.file_sha256(destination) != (record.get("sha256"), record.get("length")):
        raise SourceFormatError(f"durable '{destination.name}' no longer matches its identity")
    return record
