"""Admission of a Phase-C cleaned-corpus manifest into a FRESH C05 plan (read-only).

A cleaned manifest (``xlm_cleaned_input_manifest``, status
``CANDIDATE_REQUIRES_INDEPENDENT_AUDIT_AND_C05``) cannot be rebuilt from source seals
the way ``c05_global_input_manifest`` is, and it carries no source identities. It is
admitted only through this layer, which never writes the manifest, the corpus or any
cleaning/audit artifact. ``admit`` requires, in this order:

1. the manifest itself: strict canonical JSON, self-digest equal to the operator's
   ``expect_manifest_digest`` pin, cleaned kind, candidate status, totals and
   components recomputed exactly from its files;
2. the ORIGINAL manifest named by its lineage (digest and raw SHA-256), with exactly
   one original file per cleaned file in the same order (``cleaned_from`` identity and
   source key/component/view/upstream equal). It supplies the source identities and
   seals that the C05 scan checks on every row;
3. the production cleaning state: valid receipt, VERIFIED verification record, both
   pinned by the operator, verified totals equal to the manifest's, and the manifest
   bytes re-derived from that verified state byte for byte;
4. the independent quality audit of THIS manifest, verified from its own recorded
   identities and on-disk artifacts (it may come from another commit): manifest
   digest/raw SHA-256/kind/totals, no kept overlay, data root, per-file source
   identities equal to the manifest's, result digest pinned by the operator;
5. the saved stdout of that audit's ``report`` command: ``verified`` and
   ``sources_rehashed`` both true, same result digest and artifact count.

The admission record says what it is: permission to PLAN a fresh C05 over this
manifest. It is not a C05 result; C05 is NOT RUN until a fresh plan is authorized,
run and its signed completion verified. ``plan`` re-derives the record from the
recorded evidence paths and refuses unless it is identical.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import ExecutionPlan, InputAdmission, InputFile
from xlm.data.exclusion.policy import C05Error

ADMISSION_KIND = "c05_cleaned_input_admission_v1"
ADMISSION_STATUS = "ADMITTED_FOR_FRESH_C05_PLANNING"
ADMISSION_C05 = (
    "NOT RUN: admission is not a C05 result; a fresh plan, authorization, run and "
    "verified signed completion are still required"
)
CLEANED_STATUS = "CANDIDATE_REQUIRES_INDEPENDENT_AUDIT_AND_C05"
# Cleaned kind -> (plan mode, the original manifest kind it must descend from).
CLEANED_KINDS = {
    "xlm_cleaned_input_manifest": ("protected", "c05_global_input_manifest"),
    "authored_cleaned_input": ("authored", "authored_c05_input"),
}
MAX_MANIFEST_BYTES = 8 * 1024**2
MAX_RECORD_BYTES = 1024**2
MAX_REPORT_BYTES = 64 * 1024
MAX_FILES = 10_000
COUNTS = ("documents", "canonical_bytes", "file_bytes")
IDENTITY = ("path", "documents_sha256", "file_bytes", "canonical_bytes", "documents")
LABELS = ("source_key", "component", "view", "upstream_component")
FILE_KEYS = frozenset({*IDENTITY, *LABELS, "cleaned_from"})
REPORT_KEYS = frozenset({"artifacts", "result_digest", "sources_rehashed", "verified"})
PINS = (
    "expect_manifest_digest",
    "expect_production_result_digest",
    "expect_production_receipt_digest",
    "expect_verification_digest",
    "expect_audit_result_digest",
)
_SHA = re.compile(r"[0-9a-f]{64}")
_PLAN_NAME = re.compile(r"p(\d{4,})\.json")


def _require(condition: bool, what: str) -> None:
    if not condition:
        raise C05Error("cleaned-manifest admission refused: " + what)


def is_cleaned(manifest: Mapping[str, Any]) -> bool:
    return manifest.get("kind") in CLEANED_KINDS


@dataclass(frozen=True)
class Evidence:
    """Evidence paths plus the operator's pins (exact digests reviewed out of band)."""

    manifest: Path
    original_manifest: Path
    cleaning_state: Path
    audit_output: Path
    audit_report: Path
    expect_manifest_digest: str
    expect_production_result_digest: str
    expect_production_receipt_digest: str
    expect_verification_digest: str
    expect_audit_result_digest: str

    def check(self) -> None:
        for name in PINS:
            _require(bool(_SHA.fullmatch(getattr(self, name))), f"{name} is not a SHA-256")

    def record(self) -> dict[str, Any]:
        return {
            "manifest": _posix(self.manifest),
            "original_manifest": _posix(self.original_manifest),
            "cleaning_state": _posix(self.cleaning_state),
            "audit_output": _posix(self.audit_output),
            "audit_report": _posix(self.audit_report),
            **{name: getattr(self, name) for name in PINS},
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> Evidence:
        evidence: Any = record.get("evidence")
        _require(
            isinstance(evidence, dict)
            and set(evidence)
            == {
                "manifest",
                "original_manifest",
                "cleaning_state",
                "audit_output",
                "audit_report",
                *PINS,
            }
            and all(type(v) is str for v in evidence.values()),
            "admission evidence schema",
        )
        return cls(
            manifest=Path(evidence["manifest"]),
            original_manifest=Path(evidence["original_manifest"]),
            cleaning_state=Path(evidence["cleaning_state"]),
            audit_output=Path(evidence["audit_output"]),
            audit_report=Path(evidence["audit_report"]),
            **{name: evidence[name] for name in PINS},
        )


def _posix(path: Path) -> str:
    return Path(path).resolve().as_posix()


def _read(path: Path, limit: int, what: str) -> bytes:
    path = Path(path)
    _require(path.is_file() and not path.is_symlink(), f"{what} is missing")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    _require(len(raw) <= limit, f"{what} exceeds its size bound")
    return raw


def _strict(raw: bytes, what: str) -> dict[str, Any]:
    try:
        body = canonical.loads_bytes_strict(raw)
    except ValueError:
        raise C05Error(f"cleaned-manifest admission refused: {what} is not strict JSON") from None
    _require(isinstance(body, dict), f"{what} is not an object")
    _require(body.get("digest") == canonical.self_digest(body), f"{what} self-digest")
    result: dict[str, Any] = body
    return result


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _check_manifest(body: Mapping[str, Any], pin: str) -> tuple[str, str]:
    """Cleaned-manifest schema and arithmetic; returns (mode, original kind)."""
    _require(body["digest"] == pin, "manifest digest differs from the operator pin")
    _require(body.get("kind") in CLEANED_KINDS, "not a cleaned input manifest kind")
    _require(body.get("version") == 1, "manifest version")
    _require(body.get("status") == CLEANED_STATUS, "manifest status")
    _require(body.get("training_permitted") is False, "manifest training flag")
    _require(type(body.get("data_root")) is str and bool(body["data_root"]), "data root")
    files: Any = body.get("files")
    _require(isinstance(files, list) and 0 < len(files) <= MAX_FILES, "file list bound")
    for entry in files:
        _require(isinstance(entry, dict) and set(entry) == FILE_KEYS, "file entry schema")
        _require(all(_count(entry[k]) for k in COUNTS), "file counts")
        _require(bool(_SHA.fullmatch(str(entry["documents_sha256"]))), "file SHA-256")
        source = entry["cleaned_from"]
        _require(isinstance(source, dict) and set(source) == set(IDENTITY), "cleaned_from")
    _require(len({f["path"] for f in files}) == len(files), "duplicate cleaned file")
    totals = {k: sum(int(f[k]) for f in files) for k in COUNTS}
    _require(body.get("totals") == totals, "manifest totals differ from its files")
    components = {
        name: {k: sum(int(f[k]) for f in files if f["component"] == name) for k in COUNTS}
        for name in sorted({f["component"] for f in files})
    }
    _require(body.get("components") == components, "manifest components differ from its files")
    _require(body.get("empty_files") == sum(1 for f in files if f["documents"] == 0), "empty files")
    lineage = body.get("lineage")
    _require(isinstance(lineage, dict), "manifest lineage")
    return CLEANED_KINDS[str(body["kind"])]


def _check_original(
    cleaned: Mapping[str, Any], original: Mapping[str, Any], raw: bytes, kind: str
) -> None:
    lineage = cleaned["lineage"]
    _require(original["digest"] == lineage.get("original_input_manifest_digest"), "lineage digest")
    _require(
        hashlib.sha256(raw).hexdigest() == lineage.get("original_input_manifest_file_sha256"),
        "lineage raw SHA-256",
    )
    _require(original.get("kind") == lineage.get("original_input_manifest_kind") == kind, "kind")
    _require(original["digest"] != cleaned["digest"], "cleaned manifest reuses the original")
    files: Any = cleaned["files"]
    before: Any = original.get("files")
    _require(isinstance(before, list) and len(before) == len(files), "original file count")
    sources: Any = original.get("sources")
    _require(
        isinstance(sources, list) and all(isinstance(s, dict) for s in sources),
        "original sources",
    )
    known = {s.get("source_key") for s in sources}
    for after, entry in zip(files, before, strict=True):
        _require(isinstance(entry, dict), "original file entry")
        _require(
            after["cleaned_from"] == {k: entry.get(k) for k in IDENTITY},
            "a cleaned file does not descend from the original file in the same position",
        )
        _require(all(after[k] == entry.get(k) for k in LABELS), "cleaned file labels")
        _require(type(entry.get("source_file")) is str, "original source file")
        _require(after["source_key"] in known, "original source missing")
    for source in sources:
        _require(
            isinstance(source.get("source"), dict)
            and type(source["source"].get("source_id")) is str
            and type(source["source"].get("revision")) is str
            and bool(_SHA.fullmatch(str(source.get("seal_digest")))),
            "original source identity",
        )


def _production(evidence: Evidence, manifest: Mapping[str, Any], raw: bytes) -> dict[str, Any]:
    from xlm.data.quality.production_verify import (
        derive_cleaned_manifest,
        load_production_receipt,
        load_verification,
    )

    state = Path(evidence.cleaning_state)
    receipt = load_production_receipt(state)
    verification = load_verification(state, receipt)
    _require(
        receipt["result_digest"] == evidence.expect_production_result_digest,
        "production result digest differs from the operator pin",
    )
    _require(
        receipt["digest"] == evidence.expect_production_receipt_digest,
        "production receipt digest differs from the operator pin",
    )
    _require(
        verification["digest"] == evidence.expect_verification_digest,
        "production verification digest differs from the operator pin",
    )
    totals = manifest["totals"]
    _require(
        (
            verification["files_verified"],
            verification["documents"],
            verification["canonical_bytes"],
            verification["file_bytes"],
        )
        == (
            len(manifest["files"]),
            totals["documents"],
            totals["canonical_bytes"],
            totals["file_bytes"],
        ),
        "verified production totals differ from the manifest totals",
    )
    derived = derive_cleaned_manifest(state, Path(manifest["data_root"]))
    _require(
        canonical.canonical_bytes(derived) == raw,
        "manifest bytes differ from the manifest re-derived from the verified cleaning",
    )
    checks = verification["checks"]
    return {
        "receipt_digest": receipt["digest"],
        "result_digest": receipt["result_digest"],
        "verification_digest": verification["digest"],
        "verification_status": verification["status"],
        "sources_compared_byte_for_byte": checks["sources_compared_byte_for_byte"],
        "rows_reevaluated_keep": checks["rows_reevaluated_keep"],
    }


def _decode_report(raw: bytes) -> dict[str, Any]:
    """The saved ``report`` stdout: UTF-8 (optional BOM) or BOM-marked UTF-16.

    Windows PowerShell 5.1 redirection writes UTF-16LE with a BOM; accepting it avoids
    hand-editing evidence. Exactly one JSON object; no NaN/duplicate-key leniency.
    """
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    try:
        body = canonical.loads_strict(text.strip())
    except ValueError:
        raise C05Error("cleaned-manifest admission refused: audit report is not JSON") from None
    _require(isinstance(body, dict) and set(body) == REPORT_KEYS, "audit report schema")
    result: dict[str, Any] = body
    return result


def _audit(evidence: Evidence, manifest: Mapping[str, Any], raw: bytes) -> dict[str, Any]:
    from xlm.data.quality.phase_a_history import verify_historical_audit

    audit = verify_historical_audit(Path(evidence.audit_output))
    receipt, binding = audit.receipt, audit.binding
    totals = manifest["totals"]
    bound = binding["input_manifest"]
    _require(
        receipt["input_manifest"]["digest"] == manifest["digest"] == bound["digest"],
        "the audit did not audit this manifest",
    )
    _require(
        receipt["input_manifest"]["file_sha256"]
        == bound["file_sha256"]
        == hashlib.sha256(raw).hexdigest(),
        "audit manifest raw SHA-256 differs",
    )
    _require(bound["kind"] == manifest["kind"], "audit manifest kind differs")
    _require(
        (bound["files"], bound["documents"], bound["canonical_bytes"], bound["file_bytes"])
        == (
            len(manifest["files"]),
            totals["documents"],
            totals["canonical_bytes"],
            totals["file_bytes"],
        ),
        "audit totals differ from the manifest totals",
    )
    _require(binding["overlay"] is None, "the audit used a C05 kept overlay")
    _require(
        binding["data_root"] == Path(manifest["data_root"]).resolve().as_posix(),
        "audit data root differs from the manifest data root",
    )
    _require(
        receipt["source_files"]
        == [
            {
                "path": f["path"],
                "documents_sha256": f["documents_sha256"],
                "file_bytes": f["file_bytes"],
                "documents": f["documents"],
            }
            for f in manifest["files"]
        ],
        "audit source identities differ from the manifest files",
    )
    _require(
        receipt["result_digest"] == evidence.expect_audit_result_digest,
        "audit result digest differs from the operator pin",
    )
    report_raw = _read(Path(evidence.audit_report), MAX_REPORT_BYTES, "audit report")
    report = _decode_report(report_raw)
    _require(report["verified"] is True, "the audit report is not verified")
    _require(report["sources_rehashed"] is True, "the audit report did not re-hash the sources")
    _require(report["result_digest"] == receipt["result_digest"], "audit report result digest")
    _require(report["artifacts"] == len(receipt["artifacts"]), "audit report artifact count")
    return {
        "receipt_digest": receipt["digest"],
        "binding_digest": binding["digest"],
        "result_digest": receipt["result_digest"],
        "audit_code_identity": receipt["implementation"]["code_identity"],
        "files_scanned": receipt["execution"]["files_scanned"],
        "files_resumed": receipt["execution"]["files_resumed"],
        "report_sha256": hashlib.sha256(report_raw).hexdigest(),
        "report_verified": True,
        "report_sources_rehashed": True,
    }


def admit(evidence: Evidence) -> dict[str, Any]:
    """Re-derive the admission record from the evidence (read-only; see module doc)."""
    evidence.check()
    raw = _read(Path(evidence.manifest), MAX_MANIFEST_BYTES, "cleaned manifest")
    manifest = _strict(raw, "cleaned manifest")
    _require(canonical.canonical_bytes(manifest) == raw, "cleaned manifest is not canonical bytes")
    mode, original_kind = _check_manifest(manifest, evidence.expect_manifest_digest)
    original_raw = _read(Path(evidence.original_manifest), MAX_MANIFEST_BYTES, "original manifest")
    original = _strict(original_raw, "original manifest")
    _check_original(manifest, original, original_raw, original_kind)
    production = _production(evidence, manifest, raw)
    audit = _audit(evidence, manifest, raw)
    record: dict[str, Any] = {
        "kind": ADMISSION_KIND,
        "version": 1,
        "status": ADMISSION_STATUS,
        "c05": ADMISSION_C05,
        "mode": mode,
        "input_manifest": {
            "digest": manifest["digest"],
            "file_sha256": hashlib.sha256(raw).hexdigest(),
            "kind": manifest["kind"],
            "status": manifest["status"],
            "data_root": manifest["data_root"],
            "files": len(manifest["files"]),
            **manifest["totals"],
        },
        "original_manifest": {
            "digest": original["digest"],
            "file_sha256": hashlib.sha256(original_raw).hexdigest(),
            "kind": original["kind"],
            "status": "HISTORICAL: provenance only; its C05 decisions and proofs are stale",
        },
        "production_cleaning": production,
        "quality_audit": audit,
        "evidence": evidence.record(),
    }
    record["digest"] = canonical.digest(record)
    return record


def write_admission(evidence: Evidence, output: Path) -> dict[str, Any]:
    record = admit(evidence)
    write_once(output, record)
    return record


def load_admission(path: Path) -> dict[str, Any]:
    raw = _read(Path(path), MAX_RECORD_BYTES, "admission record")
    record = _strict(raw, "admission record")
    _require(record.get("kind") == ADMISSION_KIND, "admission record kind")
    _require(record.get("status") == ADMISSION_STATUS, "admission record status")
    return record


def verify_admission(path: Path, manifest_path: Path) -> dict[str, Any]:
    """Re-derive the record from its recorded evidence; refuse unless identical."""
    record = load_admission(path)
    evidence = Evidence.from_record(record)
    _require(
        _posix(Path(manifest_path)) == _posix(evidence.manifest),
        "admission record is for another manifest path",
    )
    _require(admit(evidence) == record, "admission no longer re-derives from its evidence")
    return record


def binding(record: Mapping[str, Any]) -> InputAdmission:
    production, audit = record["production_cleaning"], record["quality_audit"]
    return InputAdmission(
        admission_digest=record["digest"],
        cleaned_manifest_file_sha256=record["input_manifest"]["file_sha256"],
        original_manifest_digest=record["original_manifest"]["digest"],
        production_receipt_digest=production["receipt_digest"],
        production_result_digest=production["result_digest"],
        verification_digest=production["verification_digest"],
        audit_receipt_digest=audit["receipt_digest"],
        audit_result_digest=audit["result_digest"],
    )


def plan_inputs(
    manifest: Mapping[str, Any], record: Mapping[str, Any]
) -> tuple[tuple[InputFile, ...], dict[str, str]]:
    """Plan files (cleaned bytes, original source identities) and source seals."""
    evidence = Evidence.from_record(record)
    raw = _read(evidence.original_manifest, MAX_MANIFEST_BYTES, "original manifest")
    original = _strict(raw, "original manifest")
    _require(
        (original["digest"], hashlib.sha256(raw).hexdigest())
        == (record["original_manifest"]["digest"], record["original_manifest"]["file_sha256"]),
        "original manifest changed since admission",
    )
    _require(manifest["digest"] == record["input_manifest"]["digest"], "manifest changed")
    _check_original(manifest, original, raw, str(original["kind"]))
    sources = {s["source_key"]: s for s in original["sources"]}
    files = tuple(
        InputFile(
            path=after["path"],
            source_key=after["source_key"],
            source_id=sources[after["source_key"]]["source"]["source_id"],
            source_revision=sources[after["source_key"]]["source"]["revision"],
            component=after["component"],
            view=after["view"],
            source_file=before["source_file"],
            documents_sha256=after["documents_sha256"],
            file_bytes=after["file_bytes"],
            canonical_bytes=after["canonical_bytes"],
            documents=after["documents"],
            upstream_component=after["upstream_component"],
        )
        for after, before in zip(manifest["files"], original["files"], strict=True)
    )
    used = {f.source_key for f in files}
    seals = {k: str(s["seal_digest"]) for k, s in sources.items() if k in used}
    return files, seals


def check_fresh_generation(
    plan_root: Path, scratch: Path, output: Path, manifest_digest: str
) -> None:
    """A cleaned-corpus plan never shares roots with another corpus generation.

    Every existing ``pNNNN.json`` in the plan root must bind this manifest, and every
    plan-digest entry in the scratch/output roots must be a plan of this plan root, so
    historical plans, work directories, membership and proofs are never touched.
    """
    identities: set[str] = set()
    if Path(plan_root).is_dir():
        for path in sorted(Path(plan_root).iterdir()):
            if _PLAN_NAME.fullmatch(path.name) is None:
                continue
            body = json.loads(_read(path, MAX_MANIFEST_BYTES, "existing plan"))
            _require(
                isinstance(body, dict) and body.get("input_manifest_digest") == manifest_digest,
                "plan root holds a plan of another corpus generation; use a fresh plan root",
            )
            identities.add(ExecutionPlan.model_validate(body).identity())
    for root, role in ((scratch, "scratch"), (output, "output")):
        if not Path(root).is_dir():
            continue
        for entry in Path(root).iterdir():
            _require(
                _SHA.fullmatch(entry.name) is None or entry.name in identities,
                f"C05 {role} root holds state of another plan generation; use a fresh root",
            )
