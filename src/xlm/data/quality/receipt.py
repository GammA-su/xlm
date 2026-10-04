"""Strict, self-digested completion receipt (schema 3) and its validation.

The receipt is the ONLY completion signal: an aggregate artifact without a receipt
that passes :func:`validate_receipt` is never a result. Validation is exact (field
set, kind, schema version, status, phase, bindings, identities, artifact hashes,
effective resource envelope, measured execution facts, self-digest) and SEMANTIC:
the envelope must agree with the implementation constants and the binding, and with
what the run measured (peak process-tree RSS, supervised elapsed time, minimum free
space, worker setting and tasks in flight, largest audited row, output bytes). A
re-digested receipt that claims a smaller envelope than its own execution therefore
refuses. It establishes internal consistency, not authenticity: ``report``
additionally re-derives every artifact, the output byte total, the review strata and
the largest row from the committed units and re-hashes the sources.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.scan import QualityError, read_bounded

RECEIPT_KIND = "xlm_quality_audit_receipt"
RECEIPT_SCHEMA = 3
MAX_RECEIPT_BYTES = 64 * 1024**2
RECEIPT_KEYS = frozenset(
    {
        "kind",
        "schema_version",
        "status",
        "phase",
        "audit_phase",
        "corpus_modified",
        "actions_executed",
        "binding",
        "binding_digest",
        "input_manifest",
        "detector_policy",
        "implementation",
        "overlay",
        "source_files",
        "artifacts",
        "result_digest",
        "envelope",
        "producer_envelopes",
        "execution",
        "digest",
    }
)
ENVELOPE_KEYS = frozenset(
    {
        "workers",
        "queue_tasks",
        "max_rss_bytes",
        "free_reserve_bytes",
        "max_output_bytes",
        "max_document_bytes",
        "deadline_seconds",
        "chunk_bytes",
        "verify_threads",
        "max_pending_commits",
        "supervisor_interval_seconds",
        "publication_margin_seconds",
        "review_per_stratum",
    }
)
SOURCE_KEYS = frozenset({"path", "documents_sha256", "file_bytes", "documents"})
EXECUTION_INTS = (
    "files_scanned",
    "files_resumed",
    "scanned_documents",
    "scanned_file_bytes",
    "workers",
    "peak_tasks_in_flight",
    "peak_process_tree_rss_bytes",
    "supervisor_samples",
    "output_bytes_before_receipt",
    "max_document_bytes_observed",
)
EXECUTION_FLOATS = ("wall_seconds", "scan_seconds", "file_mb_per_s", "documents_per_s")
EXECUTION_NOTE = "execution facts are operational and excluded from result_digest"
EXECUTION_KEYS = frozenset({*EXECUTION_INTS, *EXECUTION_FLOATS, "free_space", "note"})
FREE_SPACE_KEYS = frozenset({"reserve_bytes", "min_observed_free_bytes"})
ARTIFACT_KEYS = frozenset({"bytes", "sha256", "records"})


def _require(condition: bool, what: str) -> None:
    if not condition:
        raise QualityError(f"receipt invalid: {what}")


def _sha(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= set("0123456789abcdef")


def check_envelope(envelope: Any) -> None:
    """Reconstruct the strict typed envelope (ranges and cross-field rules)."""
    from xlm.data.quality.envelope import EnvelopeError, validate_envelope

    _require(isinstance(envelope, dict) and set(envelope) == ENVELOPE_KEYS, "envelope schema")
    try:
        validate_envelope(envelope)
    except EnvelopeError:
        raise QualityError("receipt invalid: operational envelope") from None


def implementation_constants() -> dict[str, Any]:
    """Envelope fields fixed by this implementation (not operator choices)."""
    from xlm.data.quality.policy import REVIEW_PER_STRATUM
    from xlm.data.quality.runner import (
        MAX_PENDING_COMMITS,
        PUBLICATION_MARGIN,
        SUPERVISOR_INTERVAL,
        VERIFY_THREADS,
    )
    from xlm.data.quality.scan import CHUNK_BYTES

    return {
        "chunk_bytes": CHUNK_BYTES,
        "verify_threads": VERIFY_THREADS,
        "max_pending_commits": MAX_PENDING_COMMITS,
        "supervisor_interval_seconds": SUPERVISOR_INTERVAL,
        "publication_margin_seconds": PUBLICATION_MARGIN,
        "review_per_stratum": REVIEW_PER_STRATUM,
    }


def _check_bound_envelope(envelope: Mapping[str, Any], binding: Mapping[str, Any]) -> None:
    """An envelope used by this audit: implementation constants and binding agree."""
    check_envelope(envelope)
    for name, value in implementation_constants().items():
        _require(envelope[name] == value, f"envelope {name} differs from the implementation")
    _require(envelope["chunk_bytes"] == binding["chunk_bytes"], "envelope chunk vs binding")
    _require(
        envelope["max_document_bytes"] == binding["line_ceiling"],
        "envelope document ceiling differs from the audit binding",
    )


def _check_execution(execution: Any) -> dict[str, Any]:
    _require(isinstance(execution, dict) and set(execution) == EXECUTION_KEYS, "execution schema")
    for name in EXECUTION_INTS:
        _require(type(execution[name]) is int and execution[name] >= 0, f"execution {name}")
    for name in EXECUTION_FLOATS:
        value = execution[name]
        _require(type(value) is float and 0 <= value < float("inf"), f"execution {name}")
    _require(execution["note"] == EXECUTION_NOTE, "execution note")
    volumes = execution["free_space"]
    _require(isinstance(volumes, list) and len(volumes) > 0, "execution free space")
    for volume in volumes:
        _require(
            isinstance(volume, dict)
            and set(volume) == FREE_SPACE_KEYS
            and all(type(volume[k]) is int and volume[k] >= 0 for k in FREE_SPACE_KEYS),
            "execution free space schema",
        )
    checked: dict[str, Any] = execution
    return checked


def check_execution_envelope(receipt: Mapping[str, Any], receipt_bytes: int | None) -> None:
    """The effective envelope against what the run MEASURED (and the binding)."""
    binding = receipt["binding"]
    envelope = receipt["envelope"]
    execution = _check_execution(receipt["execution"])
    totals = binding["input_manifest"]
    _check_bound_envelope(envelope, binding)
    producers = receipt["producer_envelopes"]
    for producer in producers:
        _check_bound_envelope(producer, binding)
    _require(
        producers == sorted(producers, key=canonical.canonical_bytes)
        and len({canonical.canonical_bytes(p) for p in producers}) == len(producers),
        "producer envelopes are not a sorted set",
    )
    scanned, resumed = execution["files_scanned"], execution["files_resumed"]
    _require(scanned + resumed == totals["files"], "scanned + resumed files != manifest files")
    if resumed == 0:
        _require(producers == [envelope], "a fresh run's units must carry its own envelope")
    elif scanned > 0:
        _require(envelope in producers, "this run's envelope is missing from its units")
    _require(execution["scanned_documents"] <= totals["documents"], "scanned documents")
    _require(execution["scanned_file_bytes"] <= totals["file_bytes"], "scanned file bytes")
    # Worker setting and queue bound actually used.
    _require(execution["workers"] == envelope["workers"], "execution workers != envelope")
    in_flight = execution["peak_tasks_in_flight"]
    _require(in_flight <= envelope["queue_tasks"], "tasks in flight exceed the queue bound")
    _require((in_flight >= 1) == (scanned > 0), "tasks in flight vs files scanned")
    # Process-tree RSS, deadline, free-space reserve: measured facts within the envelope.
    peak = execution["peak_process_tree_rss_bytes"]
    _require(execution["supervisor_samples"] >= 1, "no supervisor measurement")
    _require(0 < peak <= envelope["max_rss_bytes"], "measured peak RSS exceeds the RSS ceiling")
    _require(
        execution["wall_seconds"] <= envelope["deadline_seconds"],
        "supervised elapsed time exceeds the deadline",
    )
    _require(execution["scan_seconds"] <= execution["wall_seconds"], "scan time vs elapsed time")
    for volume in execution["free_space"]:
        _require(
            volume["reserve_bytes"] == envelope["free_reserve_bytes"],
            "free-space reserve differs from the envelope",
        )
        _require(
            volume["min_observed_free_bytes"] >= volume["reserve_bytes"],
            "minimum observed free space is below the reserve",
        )
    # Largest audited row.
    observed = execution["max_document_bytes_observed"]
    _require(observed <= envelope["max_document_bytes"], "largest row exceeds the document ceiling")
    _require((observed > 0) == (totals["documents"] > 0), "largest row vs documents")
    # Output bytes (claimed total; ``report`` re-derives it from the files themselves).
    published = sum(entry["bytes"] for entry in receipt["artifacts"].values())
    before = execution["output_bytes_before_receipt"]
    _require(before >= published, "output bytes below the published artifact bytes")
    total = before + (receipt_bytes or 0)
    _require(total <= envelope["max_output_bytes"], "output bytes exceed the output ceiling")


def validate_receipt(
    receipt: Any, artifact_names: Sequence[str], receipt_bytes: int | None = None
) -> dict[str, Any]:
    """Exact schema-3 receipt validation including the semantic envelope checks;
    ``receipt_bytes`` (the receipt file's own size) counts toward the output ceiling."""
    _require(isinstance(receipt, dict) and set(receipt) == RECEIPT_KEYS, "field set")
    _require(receipt["kind"] == RECEIPT_KIND, "kind")
    _require(receipt["schema_version"] == RECEIPT_SCHEMA, "schema version")
    _require(receipt["status"] == "COMPLETE", "status is not COMPLETE")
    _require(receipt["phase"] == "COMPLETE", "phase is not COMPLETE")
    _require(receipt["audit_phase"] == "A_READ_ONLY_AUDIT", "audit phase")
    _require(receipt["corpus_modified"] is False, "corpus_modified")
    _require(receipt["actions_executed"] == [], "actions_executed")
    _require(receipt["digest"] == canonical.self_digest(receipt), "self-digest")
    binding = receipt["binding"]
    _require(isinstance(binding, dict), "binding")
    _require(binding.get("digest") == canonical.self_digest(binding), "binding digest")
    _require(receipt["binding_digest"] == binding["digest"], "binding digest reference")
    manifest = receipt["input_manifest"]
    _require(
        isinstance(manifest, dict)
        and set(manifest) == {"path", "digest", "file_sha256"}
        and manifest["digest"] == binding["input_manifest"]["digest"]
        and manifest["file_sha256"] == binding["input_manifest"]["file_sha256"]
        and type(manifest["path"]) is str,
        "input manifest binding",
    )
    _require(receipt["detector_policy"] == binding["detector_policy"], "detector identity")
    implementation = receipt["implementation"]
    _require(
        isinstance(implementation, dict)
        and set(implementation) == {"code_commit", "code_identity", "dependency_sha256"}
        and implementation["code_identity"] == binding["implementation"]["code_identity"]
        and implementation["dependency_sha256"] == binding["implementation"]["dependency_sha256"],
        "implementation identity",
    )
    _require(receipt["overlay"] == binding["overlay"], "overlay identity")
    sources = receipt["source_files"]
    _require(
        isinstance(sources, list) and len(sources) == binding["input_manifest"]["files"],
        "source count",
    )
    for source in sources:
        _require(isinstance(source, dict) and set(source) == SOURCE_KEYS, "source identity schema")
        _require(_sha(source["documents_sha256"]), "source SHA-256")
    artifacts = receipt["artifacts"]
    _require(isinstance(artifacts, dict) and set(artifacts) == set(artifact_names), "artifact set")
    for entry in artifacts.values():
        _require(isinstance(entry, dict) and set(entry) == ARTIFACT_KEYS, "artifact schema")
        _require(_sha(entry["sha256"]) and type(entry["bytes"]) is int, "artifact hash")
    expected = canonical.digest({name: artifacts[name]["sha256"] for name in sorted(artifacts)})
    _require(receipt["result_digest"] == expected, "result digest")
    check_envelope(receipt["envelope"])
    envelopes = receipt["producer_envelopes"]
    _require(isinstance(envelopes, list) and len(envelopes) > 0, "producer envelopes")
    for envelope in envelopes:
        check_envelope(envelope)
    try:
        check_execution_envelope(receipt, receipt_bytes)
    except (KeyError, TypeError):
        raise QualityError("receipt invalid: binding or execution schema") from None
    validated: dict[str, Any] = receipt
    return validated


def load_receipt(output: Path, artifact_names: Sequence[str], receipt_file: str) -> dict[str, Any]:
    path = output / receipt_file
    if not path.exists():
        raise QualityError("no completion receipt: the audit is incomplete (never a result)")
    raw = read_bounded(path, MAX_RECEIPT_BYTES, "receipt")
    try:
        body = canonical.loads_bytes_strict(raw)
    except ValueError as exc:
        if isinstance(exc, QualityError):
            raise
        raise QualityError("receipt invalid: not strict canonical JSON") from None
    return validate_receipt(body, artifact_names, len(raw))


def build_receipt(
    *,
    binding: Mapping[str, Any],
    manifest_path: Path,
    implementation: Mapping[str, str],
    sources: list[dict[str, Any]],
    artifacts: Mapping[str, bytes],
    result_digest: str,
    envelope: Mapping[str, Any],
    producer_envelopes: list[dict[str, Any]],
    execution: Mapping[str, Any],
) -> bytes:
    import hashlib

    body: dict[str, Any] = {
        "kind": RECEIPT_KIND,
        "schema_version": RECEIPT_SCHEMA,
        "status": "COMPLETE",
        "phase": "COMPLETE",
        "audit_phase": "A_READ_ONLY_AUDIT",
        "corpus_modified": False,
        "actions_executed": [],
        "binding": dict(binding),
        "binding_digest": binding["digest"],
        "input_manifest": {
            "path": manifest_path.resolve().as_posix(),
            "digest": binding["input_manifest"]["digest"],
            "file_sha256": binding["input_manifest"]["file_sha256"],
        },
        "detector_policy": binding["detector_policy"],
        "implementation": {
            "code_commit": implementation["code_commit"],
            "code_identity": implementation["code_identity"],
            "dependency_sha256": implementation["dependency_sha256"],
        },
        "overlay": binding["overlay"],
        "source_files": sources,
        "artifacts": {
            name: {
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "records": data.count(b"\n"),
            }
            for name, data in sorted(artifacts.items())
        },
        "result_digest": result_digest,
        "envelope": dict(envelope),
        "producer_envelopes": producer_envelopes,
        "execution": dict(execution),
    }
    body["digest"] = canonical.digest(body)
    return canonical.canonical_bytes(body)
