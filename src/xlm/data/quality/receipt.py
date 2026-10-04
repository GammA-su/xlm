"""Strict, self-digested completion receipt (schema 2) and its validation.

The receipt is the ONLY completion signal: an aggregate artifact without a receipt
that passes :func:`validate_receipt` is never a result. Validation is exact (field
set, kind, schema version, status, phase, bindings, identities, artifact hashes,
effective resource envelope, self-digest). It establishes internal consistency, not
authenticity: ``report`` additionally re-derives every artifact from the committed
units and re-hashes the sources against the frozen manifest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.scan import QualityError, read_bounded

RECEIPT_KIND = "xlm_quality_audit_receipt"
RECEIPT_SCHEMA = 2
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
ARTIFACT_KEYS = frozenset({"bytes", "sha256", "records"})


def _require(condition: bool, what: str) -> None:
    if not condition:
        raise QualityError(f"receipt invalid: {what}")


def _sha(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= set("0123456789abcdef")


def check_envelope(envelope: Any) -> None:
    _require(isinstance(envelope, dict) and set(envelope) == ENVELOPE_KEYS, "envelope schema")
    for key, value in envelope.items():
        _require(type(value) in (int, float) and value >= 0, f"envelope {key}")


def validate_receipt(receipt: Any, artifact_names: Sequence[str]) -> dict[str, Any]:
    """Exact schema-2 receipt validation; returns the receipt."""
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
    _require(isinstance(receipt["execution"], dict), "execution")
    validated: dict[str, Any] = receipt
    return validated


def load_receipt(output: Path, artifact_names: Sequence[str], receipt_file: str) -> dict[str, Any]:
    path = output / receipt_file
    if not path.exists():
        raise QualityError("no completion receipt: the audit is incomplete (never a result)")
    try:
        body = canonical.loads_bytes_strict(read_bounded(path, MAX_RECEIPT_BYTES, "receipt"))
    except ValueError as exc:
        if isinstance(exc, QualityError):
            raise
        raise QualityError("receipt invalid: not strict canonical JSON") from None
    return validate_receipt(body, artifact_names)


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
