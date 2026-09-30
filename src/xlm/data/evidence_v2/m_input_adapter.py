"""Checked derived-input adapter: Phase-D Arm-M output -> frozen sweep input.

The frozen selector evaluator (``scripts/essential_web_selector_sweep.py``)
reads the absolute Parquet row from ``_xlm_acquisition.row_index``; the
verified Phase-D M output stores the same identity under
``_xlm_acquisition.row``. This module builds a separate, explicitly marked
``derived_analysis_input`` view that adds ``row_index`` (equal to ``row``)
and changes nothing else: every record, its order, ``eai_taxonomy``,
``quality_signals`` and all Phase-D provenance fields are preserved.

It reads only the hash-bound M JSONL, its M acquisition manifest and the
Phase-D receipt, all opened read-only. It never fabricates a legacy
bundle, execution receipt, plan hash, acquisition ID or transport history:
the evaluator binding it offers is a derived view carrying only the facts
Phase D actually recorded. Any count, identity, order, hash or shape
deviation refuses with no output.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical

ADAPTER_ID = "essential-web-phase-d-m-derived-input-adapter"
ADAPTER_VERSION = "1"
OUTPUT_KIND = "derived_analysis_input"
LOCATOR_FIELD = "_xlm_acquisition"
ROW_INDEX_FIELD = "row_index"
MARKER_FIELD = "analysis_input_kind"
FROZEN_FILES = 8
ROWS_PER_FILE = 512
FROZEN_RECORDS = FROZEN_FILES * ROWS_PER_FILE
PROJECTION = ("eai_taxonomy", "quality_signals")
M_MANIFEST_KIND = "essential_web_v41_phase_d_m_acquisition_manifest"
RECEIPT_KIND = "essential_web_v41_phase_d_phase_d_receipt"
M_OUTPUT_NAME = "m_selected_metadata.jsonl"
M_MANIFEST_NAME = "m_acquisition_manifest.json"

_SOURCE_LOCATOR_KEYS = frozenset(
    {"m_ordinal", "repository", "revision", "row", "row_group", "row_in_group", "source_file"}
)
_RECORD_KEYS = frozenset({LOCATOR_FIELD, *PROJECTION})
_IDENTITY_KEYS = ("policy_digest", "selection_digest", "scientific_namespace")
NOT_INVENTED = (
    "legacy bundle.json",
    "legacy execution.json",
    "plan hashes",
    "historical requests",
    "historical execution receipts",
    "old acquisition IDs",
    "old transport history",
)


class AdapterError(ValueError):
    """Any count, identity, order, hash or shape deviation: refuse."""


@dataclass(frozen=True)
class AdaptedInput:
    """Derived payload bytes plus the manifest binding them to their source."""

    payload: bytes
    manifest: dict[str, Any]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def adapter_code_sha256() -> str:
    """SHA-256 of this module's exact working-file bytes."""
    return _sha(Path(__file__).read_bytes())


def _read_capped(path: Path, cap: int, what: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise AdapterError(f"cannot stat {what} '{path}': {exc}") from exc
    if size > cap:
        raise AdapterError(f"{what} is {size} bytes, cap is {cap}")
    with path.open("rb") as stream:
        return stream.read()


def _load_json(raw: bytes, what: str) -> dict[str, Any]:
    try:
        body = canonical.loads_bytes_strict(raw)
    except canonical.CanonicalError as exc:
        raise AdapterError(f"{what} is not strict canonical JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise AdapterError(f"{what} is not a JSON object")
    if canonical.self_digest(body) != body.get("digest"):
        raise AdapterError(f"{what} self-digest mismatch")
    return body


def _exact_int(value: Any, what: str) -> int:
    if type(value) is not int:
        raise AdapterError(f"{what} must be a JSON integer, found {type(value).__name__}")
    return value


def _check_manifest(
    manifest: Mapping[str, Any], preparation: Mapping[str, Any], source_sha256: str, size: int
) -> list[dict[str, Any]]:
    """Cross-check the M manifest against the frozen preparation; return files."""
    if manifest.get("kind") != M_MANIFEST_KIND:
        raise AdapterError(f"unsupported M manifest kind {manifest.get('kind')!r}")
    if manifest.get("status") != "COMPLETE":
        raise AdapterError("M manifest is not COMPLETE")
    if manifest.get("locator_field") != LOCATOR_FIELD:
        raise AdapterError("M manifest locator field is not supported")
    if list(manifest.get("projection", [])) != list(PROJECTION):
        raise AdapterError("M manifest projection is not supported")
    if list(preparation.get("input", {}).get("projection", [])) != list(PROJECTION):
        raise AdapterError("preparation projection is not supported")
    for key in _IDENTITY_KEYS:
        if manifest.get(key) != preparation.get(key):
            raise AdapterError(f"M manifest and preparation disagree on '{key}'")
    if manifest.get("plan_digest") != preparation.get("phase_d_plan_digest"):
        raise AdapterError("M manifest and preparation disagree on the Phase-D plan digest")
    output = manifest.get("output", {}).get(M_OUTPUT_NAME)
    if not isinstance(output, Mapping):
        raise AdapterError("M manifest carries no output binding")
    if output.get("sha256") != source_sha256 or output.get("bytes") != size:
        raise AdapterError("source hash mismatch: M manifest output binding differs")
    if manifest.get("records") != FROZEN_RECORDS:
        raise AdapterError(f"M manifest declares {manifest.get('records')!r} records")
    files = manifest.get("files")
    windows = preparation.get("windows")
    if not isinstance(files, list) or len(files) != FROZEN_FILES:
        raise AdapterError(f"M manifest must hold exactly {FROZEN_FILES} files")
    if not isinstance(windows, list) or len(windows) != FROZEN_FILES:
        raise AdapterError(f"preparation must hold exactly {FROZEN_FILES} windows")
    seen: set[str] = set()
    for ordinal, (entry, window) in enumerate(zip(files, windows, strict=True)):
        name = entry.get("file")
        if not isinstance(name, str) or name in seen:
            raise AdapterError(f"M manifest file {name!r} is missing or repeated")
        seen.add(name)
        start, stop = (_exact_int(v, "window bound") for v in entry.get("window", [None, None]))
        if (
            entry.get("ordinal") != ordinal
            or entry.get("records") != ROWS_PER_FILE
            or stop - start != ROWS_PER_FILE
            or window.get("file") != name
            or window.get("start") != start
            or window.get("stop") != stop
            or window.get("records") != ROWS_PER_FILE
        ):
            raise AdapterError(f"file {ordinal} window differs from the frozen preparation")
        first = _exact_int(entry.get("row_group_first_row"), "row_group_first_row")
        rows = _exact_int(entry.get("row_group_rows"), "row_group_rows")
        _exact_int(entry.get("row_group"), "row_group")
        if not first <= start < stop <= first + rows:
            raise AdapterError(f"file {ordinal} window leaves its row group")
    return [dict(entry) for entry in files]


def _check_receipt(
    receipt: Mapping[str, Any],
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    source_sha256: str,
    size: int,
) -> None:
    if receipt.get("kind") != RECEIPT_KIND:
        raise AdapterError(f"unsupported receipt kind {receipt.get('kind')!r}")
    if receipt.get("status") != "COMPLETE" or receipt.get("run_status") != "COMPLETE":
        raise AdapterError("Phase-D receipt is not COMPLETE")
    arm = receipt.get("arms", {}).get("M", {})
    if arm.get("status") != "COMPLETE":
        raise AdapterError("Phase-D receipt arm M is not COMPLETE")
    for key in (*_IDENTITY_KEYS, "plan_digest"):
        if receipt.get(key) != manifest.get(key):
            raise AdapterError(f"receipt and M manifest disagree on '{key}'")
    outputs = receipt.get("outputs", {})
    expected = {
        M_OUTPUT_NAME: {"bytes": size, "sha256": source_sha256},
        M_MANIFEST_NAME: {"bytes": len(manifest_raw), "sha256": _sha(manifest_raw)},
    }
    for name, binding in expected.items():
        found = outputs.get(name)
        if not isinstance(found, Mapping) or found.get("arm") != "M":
            raise AdapterError(f"receipt carries no arm-M output binding for {name}")
        if found.get("bytes") != binding["bytes"] or found.get("sha256") != binding["sha256"]:
            raise AdapterError(f"source hash mismatch: receipt binding for {name} differs")


def _adapt_line(
    raw: bytes, number: int, entry: Mapping[str, Any], expected_row: int, source: Mapping[str, Any]
) -> tuple[bytes, list[Any], bytes]:
    """Validate one source line; return (derived line, identity, metadata bytes)."""
    if not raw.endswith(b"\n"):
        raise AdapterError(f"line {number}: unsupported input shape (no trailing newline)")
    body = raw[:-1]
    try:
        record = canonical.loads_bytes_strict(body)
    except canonical.CanonicalError as exc:
        raise AdapterError(f"line {number}: unsupported input shape: {exc}") from exc
    if not isinstance(record, dict) or set(record) != _RECORD_KEYS:
        raise AdapterError(f"line {number}: unsupported input shape (top-level fields)")
    if canonical.canonical_bytes(record) != body:
        raise AdapterError(f"line {number}: unsupported input shape (not canonical JSON)")
    locator = record[LOCATOR_FIELD]
    if not isinstance(locator, dict) or "row" not in locator:
        raise AdapterError(f"line {number}: missing row identity")
    if set(locator) != _SOURCE_LOCATOR_KEYS:
        raise AdapterError(f"line {number}: unsupported input shape (locator fields)")
    row = _exact_int(locator["row"], f"line {number}: row")
    if (
        locator["source_file"] != entry["file"]
        or locator["repository"] != source.get("repository")
        or locator["revision"] != source.get("revision")
    ):
        raise AdapterError(f"line {number}: foreign row (file/repository/revision)")
    if row != expected_row:
        raise AdapterError(
            f"line {number}: order drift: row {row} where frozen window expects {expected_row}"
        )
    if (
        locator["m_ordinal"] != entry["ordinal"]
        or locator["row_group"] != entry["row_group"]
        or locator["row_in_group"] != row - entry["row_group_first_row"]
    ):
        raise AdapterError(f"line {number}: locator provenance disagrees with the M manifest")
    for field in PROJECTION:
        if not isinstance(record[field], dict):
            raise AdapterError(f"line {number}: unsupported input shape ({field})")
    derived = dict(record)
    derived[LOCATOR_FIELD] = {**locator, ROW_INDEX_FIELD: row, MARKER_FIELD: OUTPUT_KIND}
    identity = [
        locator["source_file"],
        row,
        locator["row_group"],
        locator["row_in_group"],
        locator["m_ordinal"],
    ]
    metadata = canonical.canonical_bytes({field: record[field] for field in PROJECTION})
    return canonical.canonical_bytes(derived) + b"\n", identity, metadata


def adapt_m_input(
    records_path: Path,
    manifest_path: Path,
    receipt_path: Path,
    preparation: Mapping[str, Any],
) -> AdaptedInput:
    """Build the derived analysis input in memory; refuse on any deviation."""
    caps = preparation.get("analysis_caps")
    declared = preparation.get("input")
    if not isinstance(caps, Mapping) or not isinstance(declared, Mapping):
        raise AdapterError("preparation lacks analysis caps or input binding")
    source_raw = _read_capped(records_path, int(caps["max_input_bytes"]), "M source JSONL")
    source_sha256 = _sha(source_raw)
    if source_sha256 != declared.get("sha256") or len(source_raw) != declared.get("bytes"):
        raise AdapterError("source hash mismatch: M JSONL differs from the frozen preparation")
    manifest_raw = _read_capped(manifest_path, 4 * 1024 * 1024, "M manifest")
    expected_manifest = declared.get("manifest", {})
    if _sha(manifest_raw) != expected_manifest.get("sha256"):
        raise AdapterError("source hash mismatch: M manifest differs from the frozen preparation")
    manifest = _load_json(manifest_raw, "M manifest")
    receipt_raw = _read_capped(receipt_path, 4 * 1024 * 1024, "Phase-D receipt")
    receipt = _load_json(receipt_raw, "Phase-D receipt")
    files = _check_manifest(manifest, preparation, source_sha256, len(source_raw))
    _check_receipt(receipt, manifest, manifest_raw, source_sha256, len(source_raw))
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        raise AdapterError("M manifest carries no source identity")

    lines = source_raw.splitlines(keepends=True)
    if len(lines) != FROZEN_RECORDS or declared.get("records") != FROZEN_RECORDS:
        raise AdapterError(f"count {len(lines)} != {FROZEN_RECORDS} records")
    out: list[bytes] = []
    identities: list[list[Any]] = []
    seen: set[tuple[str, int]] = set()
    metadata_hash = hashlib.sha256()
    per_file: list[dict[str, Any]] = []
    for ordinal, entry in enumerate(files):
        chunk = lines[ordinal * ROWS_PER_FILE : (ordinal + 1) * ROWS_PER_FILE]
        if _sha(b"".join(chunk)) != entry.get("records_sha256"):
            raise AdapterError(f"source hash mismatch: file {ordinal} records differ")
        start = entry["window"][0]
        derived_chunk: list[bytes] = []
        for offset, raw in enumerate(chunk):
            number = ordinal * ROWS_PER_FILE + offset + 1
            if len(raw) > int(caps["max_line_bytes"]):
                raise AdapterError(f"line {number} exceeds the line cap")
            line, identity, metadata = _adapt_line(raw, number, entry, start + offset, source)
            key = (str(identity[0]), int(identity[1]))
            if key in seen:
                raise AdapterError(f"line {number}: duplicate row identity {key}")
            seen.add(key)
            derived_chunk.append(line)
            identities.append(identity)
            metadata_hash.update(metadata + b"\n")
        out.extend(derived_chunk)
        per_file.append(
            {
                "ordinal": ordinal,
                "file": entry["file"],
                "crawl": crawl_of(entry["file"]),
                "window": list(entry["window"]),
                "row_group": entry["row_group"],
                "records": len(derived_chunk),
                "source_records_sha256": entry["records_sha256"],
                "derived_records_sha256": _sha(b"".join(derived_chunk)),
            }
        )
    if len(seen) != FROZEN_RECORDS:
        raise AdapterError(f"count {len(seen)} != {FROZEN_RECORDS} distinct row identities")
    payload = b"".join(out)
    body: dict[str, Any] = {
        "kind": OUTPUT_KIND,
        "adapter": {
            "id": ADAPTER_ID,
            "version": ADAPTER_VERSION,
            "code_sha256": adapter_code_sha256(),
        },
        "mapping": f"{LOCATOR_FIELD}.row -> {LOCATOR_FIELD}.{ROW_INDEX_FIELD} (added; row kept)",
        "marker": f"{LOCATOR_FIELD}.{MARKER_FIELD} = {OUTPUT_KIND}",
        "not_invented": list(NOT_INVENTED),
        "source": {
            "records": {"bytes": len(source_raw), "sha256": source_sha256},
            "m_manifest": {
                "bytes": len(manifest_raw),
                "sha256": _sha(manifest_raw),
                "digest": manifest["digest"],
            },
            "phase_d_receipt": {
                "bytes": len(receipt_raw),
                "sha256": _sha(receipt_raw),
                "digest": receipt["digest"],
            },
            "synthetic": manifest.get("synthetic"),
            "repository": source.get("repository"),
            "revision": source.get("revision"),
        },
        "identity": {
            **{key: manifest[key] for key in _IDENTITY_KEYS},
            "phase_d_plan_digest": manifest["plan_digest"],
        },
        "projection": list(PROJECTION),
        "output": {"bytes": len(payload), "sha256": _sha(payload)},
        "records": len(out),
        "row_identity_digest": canonical.digest(identities),
        "row_identity_fields": ["source_file", "row", "row_group", "row_in_group", "m_ordinal"],
        "metadata_digest": metadata_hash.hexdigest(),
        "files": per_file,
    }
    body["digest"] = canonical.self_digest(body)
    return AdaptedInput(payload=payload, manifest=body)


def crawl_of(source_file: str) -> str:
    """Crawl label from the frozen ``data/crawl=.../`` path segment."""
    parts = [part for part in source_file.split("/") if part.startswith("crawl=")]
    if len(parts) != 1:
        raise AdapterError(f"source file {source_file!r} carries no unique crawl segment")
    return parts[0]


def metadata_digest_of(payload: bytes) -> str:
    """Recompute the ordered metadata digest from any JSONL payload."""
    digest = hashlib.sha256()
    for raw in payload.splitlines():
        record = canonical.loads_bytes_strict(raw)
        digest.update(canonical.canonical_bytes({f: record[f] for f in PROJECTION}) + b"\n")
    return digest.hexdigest()


def evaluator_binding(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Derived view of the binding facts the frozen ``run_sweep`` consumes.

    ``run_sweep`` reads ``crawls``, ``files`` (crawl and row range),
    ``repository``, ``total_records`` and, under the evaluator's ``bundle``
    key, the revision plus combined payload hash/size. Only those facts are
    supplied, all derived from the adapter manifest; no digest, plan hash
    or execution receipt is present, so the frozen legacy ``load_binding``
    would refuse this view rather than mistake it for a historical bundle.
    """
    if manifest.get("kind") != OUTPUT_KIND:
        raise AdapterError("binding requires a derived_analysis_input manifest")
    files: Sequence[Mapping[str, Any]] = manifest["files"]
    return {
        "bundle": {
            "kind": OUTPUT_KIND,
            "revision": manifest["source"]["revision"],
            "combined_sha256": manifest["output"]["sha256"],
            "combined_bytes": manifest["output"]["bytes"],
        },
        "repository": manifest["source"]["repository"],
        "files": {
            entry["file"]: {
                "crawl": entry["crawl"],
                "stratum": entry["ordinal"],
                "start": entry["window"][0],
                "stop": entry["window"][1],
            }
            for entry in files
        },
        "crawls": [entry["crawl"] for entry in files],
        "total_records": manifest["records"],
    }
