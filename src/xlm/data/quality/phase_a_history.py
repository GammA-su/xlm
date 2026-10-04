"""Verifier for a COMPLETED, possibly historical Phase-A audit consumed by the freeze.

``clean-freeze-policy`` reads an audit that may come from an earlier accepted Phase-A
implementation. The accepted pre-performance implementation used 32 MiB scan chunks
and allowed workers 1/2/4/8/16. The current one uses 8 MiB chunks and adds 12
workers. The current-run verifier (:func:`xlm.data.quality.receipt.validate_receipt`,
used by ``audit``, ``report`` and ``materialize-review``) deliberately binds a
receipt's operational envelope to the CURRENT implementation constants. It is
unchanged and still refuses such a historical audit.

This module verifies the audit against its OWN recorded identities and artifacts, never
against current operational constants. It checks:

- the receipt's exact field set, kind, schema, COMPLETE status/phase, Phase-A phase and
  read-only flags, and its self-digest;
- the binding: kind, self-digest, digest reference, and exact byte identity with
  ``audit-binding.json``. The receipt's input manifest, detector policy, implementation
  identity and overlay must equal the binding's;
- the detector policy against the CURRENT detectors. Thresholds are only meaningful
  under identical detector semantics; this is a semantic identity, not an operational
  setting;
- the artifact table: the exact Phase-A artifact set, the recomputed result digest, and
  every artifact file on disk (size, SHA-256, record count);
- the recorded envelope, internally only: typed ranges, consistency with the audit's
  own binding (``chunk_bytes``, ``line_ceiling``), and the measured execution facts
  within it (workers, tasks in flight, RSS, deadline, free space, largest row, output
  bytes). The output bytes are also re-derived from the files on disk;
- the candidate YAML and ``quality-by-component.json``: kind, band, ``PROPOSAL_ONLY``,
  ``executable: false``, bindings equal to the receipt, every action null, unique rule
  ids, the expected detector set per component, and a component set equal to the
  audit's own component scopes.

The historical envelope (``chunk_bytes``, workers, queue) is preserved as provenance and
is never compared with the current implementation constants.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical
from xlm.data.quality.policy import METRICS, POLICY_VERSION, policy_identity
from xlm.data.quality.receipt import (
    MAX_RECEIPT_BYTES,
    RECEIPT_KEYS,
    RECEIPT_KIND,
    RECEIPT_SCHEMA,
    _check_execution,
    check_envelope,
)
from xlm.data.quality.report import ARTIFACTS
from xlm.data.quality.scan import (
    BINDING_FILE,
    BINDING_KIND,
    MAX_MANIFEST_BYTES,
    RECEIPT_FILE,
    QualityError,
    read_bounded,
    unit_path,
)

MAX_ARTIFACT_BYTES = 512 * 1024**2
HASH_BLOCK = 8 * 1024**2
CANDIDATE_ARTIFACT = "candidate-policy-conservative.yaml"
BY_COMPONENT_ARTIFACT = "quality-by-component.json"
AUDIT_ARTIFACT = "quality-audit.json"
FLAG_DETECTORS = frozenset({"empty", "whitespace_only"})
CANDIDATE_DETECTORS = frozenset(m.name for m in METRICS if m.candidate)
BINDING_KEYS = frozenset(
    {
        "kind",
        "input_manifest",
        "data_root",
        "detector_policy",
        "implementation",
        "overlay",
        "chunk_bytes",
        "line_ceiling",
        "review_key_sha256",
        "source_identity",
        "digest",
    }
)
MANIFEST_KEYS = frozenset(
    {"digest", "file_sha256", "kind", "mode", "files", "documents", "file_bytes", "canonical_bytes"}
)
SOURCE_KEYS = frozenset({"path", "documents_sha256", "file_bytes", "documents"})
ARTIFACT_KEYS = frozenset({"bytes", "sha256", "records"})


class HistoricalAuditError(QualityError):
    """Content-free refusal of a historical Phase-A audit."""


def _require(condition: bool, what: str) -> None:
    if not condition:
        raise HistoricalAuditError(f"Phase-A audit refused: {what}")


def _sha(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= set("0123456789abcdef")


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


@dataclass(frozen=True)
class HistoricalAudit:
    """A verified completed Phase-A audit (receipt, binding, verified artifact bytes)."""

    receipt: Mapping[str, Any]
    binding: Mapping[str, Any]
    artifacts: Mapping[str, bytes]  # verified bytes of the artifacts the freeze reads
    components: frozenset[str]


def _load_json(path: Path, limit: int, what: str) -> tuple[Any, bytes]:
    _require(path.is_file() and not path.is_symlink(), f"{what} is missing")
    raw = read_bounded(path, limit, what)
    try:
        return canonical.loads_bytes_strict(raw), raw
    except ValueError as exc:
        if isinstance(exc, QualityError):
            raise
        raise HistoricalAuditError(f"Phase-A audit refused: {what} is not strict JSON") from None


def _check_receipt_header(receipt: Any) -> None:
    _require(isinstance(receipt, dict) and set(receipt) == RECEIPT_KEYS, "receipt field set")
    _require(receipt["kind"] == RECEIPT_KIND, "receipt kind")
    _require(receipt["schema_version"] == RECEIPT_SCHEMA, "receipt schema version")
    _require(
        receipt["status"] == "COMPLETE" and receipt["phase"] == "COMPLETE",
        "receipt is not COMPLETE",
    )
    _require(receipt["audit_phase"] == "A_READ_ONLY_AUDIT", "receipt audit phase")
    _require(receipt["corpus_modified"] is False, "receipt corpus_modified")
    _require(receipt["actions_executed"] == [], "receipt actions_executed")
    _require(receipt["digest"] == canonical.self_digest(receipt), "receipt self-digest")


def _check_binding(receipt: Mapping[str, Any], output: Path) -> dict[str, Any]:
    binding = receipt["binding"]
    _require(isinstance(binding, dict) and set(binding) == BINDING_KEYS, "binding field set")
    _require(binding["kind"] == BINDING_KIND, "binding kind")
    _require(binding["digest"] == canonical.self_digest(binding), "binding self-digest")
    _require(receipt["binding_digest"] == binding["digest"], "binding digest reference")
    on_disk = read_bounded(output / BINDING_FILE, MAX_MANIFEST_BYTES, "audit binding")
    _require(on_disk == canonical.canonical_bytes(binding), "audit-binding.json differs")
    manifest = binding["input_manifest"]
    _require(isinstance(manifest, dict) and set(manifest) == MANIFEST_KEYS, "binding manifest")
    _require(_sha(manifest["digest"]) and _sha(manifest["file_sha256"]), "manifest digests")
    for name in ("files", "documents", "file_bytes", "canonical_bytes"):
        _require(_count(manifest[name]), "binding manifest totals")
    _require(manifest["mode"] in ("production", "authored"), "binding manifest mode")
    recorded = receipt["input_manifest"]
    _require(
        isinstance(recorded, dict)
        and set(recorded) == {"path", "digest", "file_sha256"}
        and recorded["digest"] == manifest["digest"]
        and recorded["file_sha256"] == manifest["file_sha256"]
        and type(recorded["path"]) is str,
        "receipt input manifest differs from the binding",
    )
    _require(receipt["detector_policy"] == binding["detector_policy"], "detector identity")
    _require(
        binding["detector_policy"] == {"version": POLICY_VERSION, "digest": policy_identity()},
        "Phase-A detector policy differs from the current detectors",
    )
    implementation = receipt["implementation"]
    bound = binding["implementation"]
    _require(
        isinstance(bound, dict)
        and set(bound) == {"code_identity", "dependency_sha256"}
        and _sha(bound["code_identity"])
        and _sha(bound["dependency_sha256"]),
        "binding implementation identity",
    )
    _require(
        isinstance(implementation, dict)
        and set(implementation) == {"code_commit", "code_identity", "dependency_sha256"}
        and type(implementation["code_commit"]) is str
        and implementation["code_identity"] == bound["code_identity"]
        and implementation["dependency_sha256"] == bound["dependency_sha256"],
        "receipt implementation identity differs from the binding",
    )
    _require(receipt["overlay"] == binding["overlay"], "overlay identity")
    for name in ("chunk_bytes", "line_ceiling"):
        _require(type(binding[name]) is int and binding[name] > 0, f"binding {name}")
    checked: dict[str, Any] = binding
    return checked


def _hash_file(path: Path, keep: bool) -> tuple[int, str, int, bytes]:
    """(size, SHA-256, newline count, the bytes when ``keep``) in one bounded pass."""
    size = path.stat().st_size
    _require(size <= MAX_ARTIFACT_BYTES, "artifact exceeds its size bound")
    digest = hashlib.sha256()
    records = 0
    kept = bytearray()
    with path.open("rb") as stream:
        while block := stream.read(HASH_BLOCK):
            digest.update(block)
            records += block.count(b"\n")
            if keep:
                kept += block
    return size, digest.hexdigest(), records, bytes(kept)


def _check_artifacts(receipt: Mapping[str, Any], output: Path) -> dict[str, bytes]:
    table = receipt["artifacts"]
    _require(isinstance(table, dict) and set(table) == set(ARTIFACTS), "artifact set")
    for entry in table.values():
        _require(
            isinstance(entry, dict)
            and set(entry) == ARTIFACT_KEYS
            and _sha(entry["sha256"])
            and _count(entry["bytes"])
            and _count(entry["records"]),
            "artifact table schema",
        )
    expected = canonical.digest({name: table[name]["sha256"] for name in sorted(table)})
    _require(receipt["result_digest"] == expected, "result digest")
    kept: dict[str, bytes] = {}
    for name in ARTIFACTS:
        path = output / name
        _require(path.is_file() and not path.is_symlink(), f"artifact {name} is missing")
        keep = name in (CANDIDATE_ARTIFACT, BY_COMPONENT_ARTIFACT, AUDIT_ARTIFACT)
        size, sha, records, data = _hash_file(path, keep)
        entry = table[name]
        _require(
            (size, sha, records) == (entry["bytes"], entry["sha256"], entry["records"]),
            f"artifact {name} differs from the receipt",
        )
        if keep:
            kept[name] = data
    return kept


def _check_envelope_internally(receipt: Mapping[str, Any], binding: Mapping[str, Any]) -> None:
    """The recorded envelope against the audit's OWN binding and measured facts only."""
    envelope = receipt["envelope"]
    producers = receipt["producer_envelopes"]
    _require(isinstance(producers, list) and len(producers) > 0, "producer envelopes")
    try:
        for item in (envelope, *producers):
            check_envelope(item)  # typed schema and ranges; no implementation constants
    except QualityError:
        raise HistoricalAuditError("Phase-A audit refused: recorded envelope schema") from None
    for item in (envelope, *producers):
        _require(item["chunk_bytes"] == binding["chunk_bytes"], "envelope chunk vs binding")
        _require(item["max_document_bytes"] == binding["line_ceiling"], "envelope document ceiling")
    _require(
        producers == sorted(producers, key=canonical.canonical_bytes)
        and len({canonical.canonical_bytes(p) for p in producers}) == len(producers),
        "producer envelopes are not a sorted set",
    )
    try:
        execution = _check_execution(receipt["execution"])
    except QualityError:
        raise HistoricalAuditError("Phase-A audit refused: execution schema") from None
    totals = binding["input_manifest"]
    scanned, resumed = execution["files_scanned"], execution["files_resumed"]
    _require(scanned + resumed == totals["files"], "scanned + resumed files != manifest files")
    if resumed == 0:
        _require(producers == [envelope], "a fresh run's units must carry its own envelope")
    elif scanned > 0:
        _require(envelope in producers, "this run's envelope is missing from its units")
    _require(execution["scanned_documents"] <= totals["documents"], "scanned documents")
    _require(execution["scanned_file_bytes"] <= totals["file_bytes"], "scanned file bytes")
    _require(execution["workers"] == envelope["workers"], "execution workers != envelope")
    in_flight = execution["peak_tasks_in_flight"]
    _require(in_flight <= envelope["queue_tasks"], "tasks in flight exceed the queue bound")
    _require((in_flight >= 1) == (scanned > 0), "tasks in flight vs files scanned")
    peak = execution["peak_process_tree_rss_bytes"]
    _require(execution["supervisor_samples"] >= 1, "no supervisor measurement")
    _require(0 < peak <= envelope["max_rss_bytes"], "measured peak RSS exceeds the ceiling")
    _require(execution["wall_seconds"] <= envelope["deadline_seconds"], "elapsed > deadline")
    _require(execution["scan_seconds"] <= execution["wall_seconds"], "scan time vs elapsed")
    for volume in execution["free_space"]:
        _require(volume["reserve_bytes"] == envelope["free_reserve_bytes"], "free reserve")
        _require(volume["min_observed_free_bytes"] >= volume["reserve_bytes"], "free space")
    observed = execution["max_document_bytes_observed"]
    _require(observed <= envelope["max_document_bytes"], "largest row exceeds the ceiling")
    _require((observed > 0) == (totals["documents"] > 0), "largest row vs documents")


def _check_output_bytes(
    receipt: Mapping[str, Any], binding: Mapping[str, Any], output: Path, receipt_bytes: int
) -> None:
    """Re-derive the recorded output bytes from the files on disk (binding, units,
    artifacts) and check them against the recorded ceiling."""
    execution, envelope = receipt["execution"], receipt["envelope"]
    units = 0
    for ordinal in range(binding["input_manifest"]["files"]):
        path = unit_path(output, ordinal)
        _require(path.is_file() and not path.is_symlink(), "a committed audit unit is missing")
        units += path.stat().st_size
    before = (output / BINDING_FILE).stat().st_size + units
    before += sum(int(entry["bytes"]) for entry in receipt["artifacts"].values())
    _require(before == execution["output_bytes_before_receipt"], "recorded output bytes")
    _require(before + receipt_bytes <= envelope["max_output_bytes"], "output ceiling")


def _check_sources(receipt: Mapping[str, Any], binding: Mapping[str, Any]) -> None:
    sources = receipt["source_files"]
    _require(
        isinstance(sources, list) and len(sources) == binding["input_manifest"]["files"],
        "source count",
    )
    for source in sources:
        _require(
            isinstance(source, dict)
            and set(source) == SOURCE_KEYS
            and _sha(source["documents_sha256"])
            and type(source["path"]) is str
            and _count(source["file_bytes"])
            and _count(source["documents"]),
            "source identity schema",
        )
    _require(
        sum(s["documents"] for s in sources) == binding["input_manifest"]["documents"]
        and sum(s["file_bytes"] for s in sources) == binding["input_manifest"]["file_bytes"],
        "source identities differ from the manifest totals",
    )


def _audit_components(artifacts: Mapping[str, bytes], receipt: Mapping[str, Any]) -> frozenset[str]:
    """Component set from the audit's own hash-verified per-component summaries."""
    try:
        by_component = canonical.loads_bytes_strict(artifacts[BY_COMPONENT_ARTIFACT])
        audit = canonical.loads_bytes_strict(artifacts[AUDIT_ARTIFACT])
    except ValueError:
        raise HistoricalAuditError("Phase-A audit refused: summary artifact is not JSON") from None
    expected_bindings = {
        "audit_binding_digest": receipt["binding_digest"],
        "input_manifest_digest": receipt["input_manifest"]["digest"],
        "detector_policy": receipt["detector_policy"],
    }
    for name, body, kind in (
        (AUDIT_ARTIFACT, audit, "xlm_quality_audit_v2"),
        (BY_COMPONENT_ARTIFACT, by_component, "xlm_quality_by_component_v1"),
    ):
        _require(
            isinstance(body, dict)
            and body.get("kind") == kind
            and body.get("corpus_modified") is False
            and body.get("bindings") == expected_bindings,
            f"{name} kind or bindings",
        )
    scopes = by_component.get("scopes")
    _require(isinstance(scopes, dict), "per-component scopes")
    components = frozenset(
        s.split(":", 1)[1] for s in scopes if type(s) is str and s.startswith("component:")
    )
    _require(bool(components) and all(components), "audit component set")
    return components


def verify_candidate(
    candidate: Any, audit: HistoricalAudit, *, kind: str, band: str
) -> dict[str, dict[str, Any]]:
    """The candidate YAML's own semantics: rules by component, every action null."""
    receipt = audit.receipt
    _require(isinstance(candidate, dict), "candidate policy")
    _require(
        candidate.get("kind") == kind
        and candidate.get("band") == band
        and candidate.get("status") == "PROPOSAL_ONLY"
        and candidate.get("executable") is False
        and candidate.get("actions_executed_in_phase_a") == [],
        "candidate policy kind/band/status/executable",
    )
    _require(
        candidate.get("bindings")
        == {
            "audit_binding_digest": receipt["binding_digest"],
            "input_manifest_digest": receipt["input_manifest"]["digest"],
            "detector_policy": receipt["detector_policy"],
        },
        "candidate policy bindings differ from the Phase-A receipt",
    )
    components = candidate.get("components")
    if not isinstance(components, dict):
        raise HistoricalAuditError("Phase-A audit refused: candidate components")
    _require(set(components) == audit.components, "candidate component set differs")
    out: dict[str, dict[str, Any]] = {}
    for component, entry in components.items():
        _require(isinstance(entry, dict) and isinstance(entry.get("rules"), list), "rules")
        found: dict[str, Any] = {}
        for rule in entry["rules"]:
            _require(isinstance(rule, dict), "candidate rule")
            detector = rule.get("detector")
            _require(
                detector in FLAG_DETECTORS | CANDIDATE_DETECTORS,
                "candidate rule has an unexpected detector",
            )
            _require(rule.get("id") == f"{component}.{detector}", "candidate rule id")
            _require(rule["id"] not in found, "duplicate candidate rule")
            _require("action" in rule and rule["action"] is None, "candidate action is not null")
            found[rule["id"]] = rule
        _require(
            {f"{component}.{d}" for d in FLAG_DETECTORS} <= set(found),
            "candidate component lacks its flag rules",
        )
        out[component] = found
    return out


def verify_historical_audit(output: Path) -> HistoricalAudit:
    """Verify a completed Phase-A output from its own recorded identities and artifacts."""
    output = Path(output)
    receipt, raw = _load_json(output / RECEIPT_FILE, MAX_RECEIPT_BYTES, "Phase-A receipt")
    try:
        _check_receipt_header(receipt)
        binding = _check_binding(receipt, output)
        _check_sources(receipt, binding)
        artifacts = _check_artifacts(receipt, output)
        _check_envelope_internally(receipt, binding)
        _check_output_bytes(receipt, binding, output, len(raw))
    except (KeyError, TypeError):
        raise HistoricalAuditError("Phase-A audit refused: receipt or binding schema") from None
    components = _audit_components(artifacts, receipt)
    return HistoricalAudit(receipt, binding, artifacts, components)
