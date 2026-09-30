"""Frozen Arm-T blinded review-package materialization (protocol sections 8, 9).

Custodian-side only. Verifies the sealed Arm-M parent and the hash-bound
Phase-D Arm-T acquisition, then builds, entirely in memory, a custodian
master ledger for every frozen locator and one reviewer package per frozen
reviewer. Review IDs and per-reviewer orders come unchanged from
``blinding``; the rubric comes unchanged from ``rubric`` and protocol
section 8. Nothing here labels, unblinds, reselects or ranks anything.

Fail-closed: a count, membership, hash, seal, leak or reuse deviation
raises ``PackageError`` before anything is written. Existing blinding
material (K, review IDs, orders) is reused exactly and never regenerated.
Selection strata and ranks are deliberately not joined here: the ledger
binds the selection manifest by hash and the join stays with the custodian
until adjudication is sealed.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import blinding, canonical, frozen, m_analysis, rubric

PACKAGE_KIND = "essential_web_t_blinded_review_package"
M_SEAL_DIGEST = "afc972ccf570a5587bf7bd3b6b8d1d728a09f2230c49f1b0a19c1c74cda0b9bc"
M_RESULT_COMMIT = "65edfd0a9e3b33b2b98cc9423223e31506db59a5"
T_PREPARATION_DIGEST = "6daa94e40463a634153de91b8123ffbf503157816d908cd575da603f949ef4b9"

REVIEWABLE = "full_text_available"
OVERSIZED = "unreviewable_full_document_due_to_size"
INVALID = "unreviewable_missing_or_invalid_text"

DOCUMENTS_NAME = "sealed/t_selected_documents.jsonl"
PROVENANCE_NAME = "sealed/t_provenance.json"
T_MANIFEST_NAME = "t_acquisition_manifest.json"
RECEIPT_NAME = "phase_d_receipt.json"

CUSTODIAN = "custodian"
KEY_PATH = "custodian/sealed/custodian_key.bin"
LEDGER_PATH = "custodian/master_ledger.json"
MAPPING_PATH = "custodian/sealed_mapping.json"
MATERIALIZATION_PATH = "custodian/materialization.json"
AUDIT_PATH = "custodian/leakage_audit.json"
MANIFEST_PATH = "custodian/package_manifest.json"
FIRST_MATERIALIZATION = "first_materialization"
REUSED_PRIOR = "reused_prior_blinding"

PUBLIC_MANIFEST = "package_manifest.json"
PUBLIC_ARTIFACT_MANIFEST = "artifact_manifest.json"

SOURCE_BYTES_MAX = 67108864
SMALL_JSON_BYTES_MAX = 4194304
RUBRIC_HEADING = b"## 8. Frozen human review rubric"
RUBRIC_END = b"## 9. "

_DOCUMENT_FIELDS = frozenset(
    {
        "locator",
        "row_group",
        "row_in_group",
        "sha256",
        "status",
        "t_ordinal",
        "text",
        "utf8_bytes",
    }
)
_PACKAGE_FIELDS = frozenset(
    {
        "kind",
        "protocol_version",
        "reviewer",
        "rubric_version",
        "key_commitment",
        "order",
        "order_digest",
        "forms",
    }
)
_PACKAGE_FORM_FIELDS = frozenset({"review_id", "text", "rubric_version", "reviewer", "labels"})
_ORDER_FIELDS = frozenset({"reviewer", "rubric_version", "items", "order", "position", "review_id"})
_RUBRIC_JSON_FIELDS = frozenset(
    {
        "rubric_version",
        "dimensions",
        "n",
        "name",
        "title",
        "levels",
        "anchors",
        "uncertain",
        "not_applicable",
        "not_applicable_dimensions",
        "not_reviewed",
        "fragmentation_defect_types",
        "form_fields",
    }
)
# Reviewer-inspectable JSON must never carry these keys, at any depth.
FORBIDDEN_KEYS: frozenset[str] = frozen.FORBIDDEN_PACKAGE_FIELDS | frozenset(
    {
        "sha256",
        "text_sha256",
        "record_sha256",
        "locator_sha256",
        "t_ordinal",
        "row",
        "row_group",
        "row_in_group",
        "repository",
        "revision",
        "file",
        "source",
        "provenance",
        "selection",
        "selection_digest",
        "policy_digest",
        "condition",
        "tier",
        "component",
        "ownership",
        "cell",
        "rank",
        "score",
        "secret",
        "key",
        "mapping",
        "status",
        "utf8_bytes",
        "taxonomy",
        "eai_taxonomy",
        "quality_signals",
        "hypothesis",
    }
)
# Generic selection/provenance vocabulary: refused outside document text,
# only counted inside it (a document may use such a word naturally).
_GENERIC_TERMS = (
    "selector",
    "locator",
    "stratum",
    "strata",
    "crawl",
    "provenance",
    "policy",
    "strict",
    "census",
    "survivor",
    "hypothesis",
    "confirmation",
    "development",
    "taxonomy",
    "fdc",
    "parquet",
    "huggingface",
    "essentialai",
    "ranking",
    "sha256",
    "hmac",
    "custodian",
)
_CONDITION_PATTERNS = (
    ("condition_label", re.compile(r"\b[ABCD]-(?:normal|strict)\b", re.IGNORECASE)),
    ("b_versus_d", re.compile(r"\bB[-_ ]?vs\.?[-_ ]?D\b", re.IGNORECASE)),
    ("ownership_label", re.compile(r"\b[BD]_(?:only_)?(?:science|practical|prose)", re.IGNORECASE)),
    ("crawl_id", re.compile(r"CC-MAIN-\d{4}-\d{2}")),
    ("source_basename", re.compile(r"train-\d{5}-of-\d{5}")),
    ("windows_drive_path", re.compile(r"\b[A-Za-z]:[\\/](?:Project|XLM)", re.IGNORECASE)),
)
_POSITION_DIGITS = 4


class PackageError(ValueError):
    """Any binding, membership, leak or reuse violation: refuse, write nothing."""


@dataclass(frozen=True)
class Pins:
    """Frozen identities the inputs must reproduce; tests supply synthetic ones."""

    m_seal_digest: str
    preparation_digest: str
    selection_sha256: str
    selection_digest: str
    repository: str
    revision: str
    source_files: tuple[str, ...]
    entries: int
    reviewable: int
    oversized: int


REAL_PINS = Pins(
    m_seal_digest=M_SEAL_DIGEST,
    preparation_digest=T_PREPARATION_DIGEST,
    selection_sha256=frozen.V21_SELECTION_SHA256,
    selection_digest=frozen.V21_SELECTION_DIGEST,
    repository=frozen.REPOSITORY,
    revision=frozen.REVISION,
    source_files=tuple(
        frozen.crawl_path(f"crawl=CC-MAIN-{suffix}", excluded)
        for suffix, _, excluded, _ in frozen.STRATA
    ),
    entries=frozen.TEXT_SELECTION_MAX,
    reviewable=117,
    oversized=1,
)


@dataclass(frozen=True)
class Sources:
    """Read-only input locations; none is ever written."""

    preparation: Path
    entry_bindings: Path
    phase_d_root: Path
    selection: Path
    m_seal_dir: Path
    protocol: Path
    rubric_code: Path


@dataclass(frozen=True)
class Entry:
    """One frozen selected locator with its acquisition facts."""

    t_ordinal: int
    locator: tuple[str, str, str, int]
    row_group: int
    row_in_group: int
    row_group_first_row: int
    operations: tuple[str, ...]
    chunk_span_half_open: tuple[int, int]
    status: str
    utf8_bytes: int
    text_sha256: str | None
    text: str | None
    record_sha256: str
    source_line: int

    @property
    def reviewable(self) -> bool:
        return self.status == REVIEWABLE


@dataclass(frozen=True)
class TSource:
    entries: tuple[Entry, ...]
    bindings: Mapping[str, Any]
    rubric_excerpt: bytes


@dataclass(frozen=True)
class Prior:
    """Previously sealed blinding material; reused exactly when present."""

    secret: bytes
    ids: Mapping[tuple[str, str, str, int], str] | None
    orders: Mapping[str, tuple[str, ...]] | None


@dataclass(frozen=True)
class Blinding:
    secret: bytes
    ids: Mapping[tuple[str, str, str, int], str]
    orders: Mapping[str, tuple[str, ...]]
    generated: bool


@dataclass(frozen=True)
class Built:
    """All package files (relative POSIX paths), the Git-safe files, and facts."""

    files: Mapping[str, bytes]
    public: Mapping[str, bytes]
    manifest: Mapping[str, Any]


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def dumps(obj: Any) -> bytes:
    """Deterministic readable JSON (sorted keys, LF, finite numbers only)."""
    return m_analysis.dumps(obj)


def _binding(raw: bytes) -> dict[str, Any]:
    return {"bytes": len(raw), "sha256": sha256_bytes(raw)}


def _read(path: Path, cap: int, what: str) -> bytes:
    """Bounded read: refuse before loading anything larger than its cap."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PackageError(f"{what} is unavailable: {exc}") from exc
    if size > cap:
        raise PackageError(f"{what} is {size} bytes, above its {cap}-byte cap")
    return path.read_bytes()


def _json(raw: bytes, what: str) -> dict[str, Any]:
    try:
        body = canonical.loads_bytes_strict(raw)
    except canonical.CanonicalError as exc:
        raise PackageError(f"{what} is not strict JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise PackageError(f"{what} is not a JSON object")
    return body


def _self_digested(raw: bytes, what: str) -> dict[str, Any]:
    body = _json(raw, what)
    if canonical.self_digest(body) != body.get("digest"):
        raise PackageError(f"{what} self-digest mismatch")
    return body


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


# --------------------------------------------------------------------------
# Parents: sealed M result, then the Arm-T acquisition it chains to.
# --------------------------------------------------------------------------


def verify_m_seal(seal_dir: Path, pins: Pins, result_commit: str) -> dict[str, Any]:
    """Recheck the sealed M package on disk and require the pinned digest."""
    try:
        seal = m_analysis.verify_seal(seal_dir)
    except (m_analysis.AnalysisError, OSError, ValueError, KeyError, TypeError) as exc:
        raise PackageError(f"M seal does not verify: {exc}") from exc
    _require(seal["seal_digest"] == pins.m_seal_digest, "M seal digest is not the sealed M result")
    _require(
        seal.get("status") == "SEALED"
        and seal.get("arm") == "M"
        and seal.get("sealed_before_other_arm_unblinding") is True
        and seal.get("other_arm_material_bound") is False,
        "M seal is not a sealed-before-T Arm-M result",
    )
    _require(
        seal.get("scientific_namespace") == frozen.PROTOCOL_VERSION
        and seal.get("selection_digest") == pins.selection_digest
        and seal.get("policy_digest") == frozen.POLICY_DIGEST
        and seal.get("source_revision") == pins.revision,
        "M seal scientific identity differs from the frozen identity",
    )
    raw = (seal_dir / m_analysis.SEAL_NAME).read_bytes()
    return {
        "seal_digest": seal["seal_digest"],
        "seal_file": _binding(raw),
        "artifact_manifest_digest": seal["artifact_manifest_digest"],
        "result_commit": result_commit,
        "phase_d_receipt_sha256": seal["source"]["phase_d_receipt"]["sha256"],
        "selector_decision": seal["selector_decision"],
        "policy_ranking": seal["policy_ranking"],
        "post_seal_rule": seal["post_seal_rule"],
        "m_artifacts_modified": False,
    }


def selection_identities(selection: Mapping[str, Any]) -> list[tuple[str, str, str, int]]:
    """Every frozen selected locator, without its stratum or rank."""
    out: list[tuple[str, str, str, int]] = []
    for cell in selection["cells"]:
        groups = [cell] if "identities" in cell else list(cell["crawls"])
        for group in groups:
            for identity in group["identities"]:
                out.append(_locator(identity, "selection identity"))
    return out


def _locator(value: Any, what: str) -> tuple[str, str, str, int]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or not all(type(part) is str for part in value[:3])
        or type(value[3]) is not int
    ):
        raise PackageError(f"{what} is not [repository, revision, file, int row]")
    return (value[0], value[1], value[2], value[3])


def rubric_excerpt(protocol_raw: bytes) -> bytes:
    """Protocol section 8, byte for byte, cross-checked against ``rubric``."""
    start = protocol_raw.find(RUBRIC_HEADING)
    end = protocol_raw.find(RUBRIC_END, start + 1)
    _require(start >= 0 and end > start, "protocol section 8 is not present")
    excerpt = protocol_raw[start:end]
    rows = [line for line in excerpt.decode("utf-8").splitlines() if re.match(r"\| \d+ ", line)]
    _require(len(rows) == len(rubric.DIMENSIONS) == 18, "rubric must hold exactly 18 dimensions")
    for dim, row in zip(rubric.DIMENSIONS, rows, strict=True):
        _require(
            row.startswith(f"| {dim['n']} {dim['title']} |")
            and all(f"`{level}`" in row for level in dim["levels"]),
            f"rubric dimension {dim['n']} differs from protocol section 8",
        )
    return excerpt


def load_sources(sources: Sources, pins: Pins, m_parent: Mapping[str, Any]) -> TSource:
    """Verify the whole Arm-T hash chain and return the frozen entries."""
    prep_raw = _read(sources.preparation, SMALL_JSON_BYTES_MAX, "T preparation manifest")
    prep = _self_digested(prep_raw, "T preparation manifest")
    _require(
        prep["digest"] == pins.preparation_digest, "T preparation is not the frozen preparation"
    )
    rules = prep.get("blinding", {})
    _require(
        prep.get("arm") == "T"
        and prep.get("entries") == pins.entries
        and prep.get("reviewable_full_text_entries") == pins.reviewable
        and prep.get("unreviewable_oversized_entries") == pins.oversized
        and prep.get("scientific_namespace") == frozen.PROTOCOL_VERSION
        and prep.get("selection_digest") == pins.selection_digest
        and prep.get("policy_digest") == frozen.POLICY_DIGEST,
        "T preparation scientific identity or counts differ from the frozen values",
    )
    _require(
        rules.get("namespace") == frozen.PROTOCOL_VERSION
        and rules.get("id_tag") == frozen.REVIEW_ID_TAG
        and rules.get("review_order_seed") == frozen.REVIEW_ORDER_SEED
        and rules.get("reviewers") == list(frozen.REVIEWERS)
        and rules.get("prohibited_fields") == sorted(frozen.FORBIDDEN_PACKAGE_FIELDS),
        "T preparation blinding rules differ from the frozen rules",
    )
    bound = prep["input"]

    receipt_raw = _read(
        sources.phase_d_root / RECEIPT_NAME, SMALL_JSON_BYTES_MAX, "Phase-D receipt"
    )
    _require(
        sha256_bytes(receipt_raw) == m_parent["phase_d_receipt_sha256"],
        "Phase-D receipt is not the receipt bound by the M seal",
    )
    receipt = _self_digested(receipt_raw, "Phase-D receipt")
    _require(
        receipt.get("run_status") == "COMPLETE"
        and receipt.get("arms", {}).get("T", {}).get("status") == "COMPLETE",
        "Phase-D Arm T is not COMPLETE",
    )
    outputs = receipt["outputs"]

    manifest_raw = _read(
        sources.phase_d_root / T_MANIFEST_NAME, SMALL_JSON_BYTES_MAX, "T acquisition manifest"
    )
    manifest = _self_digested(manifest_raw, "T acquisition manifest")
    _require(
        _binding(manifest_raw) == _bound(outputs[T_MANIFEST_NAME]),
        "source T acquisition hash mismatch: manifest differs from the Phase-D receipt",
    )
    expected_counts = {REVIEWABLE: pins.reviewable, OVERSIZED: pins.oversized, INVALID: 0}
    _require(
        manifest.get("status") == "COMPLETE"
        and manifest.get("selected_locators") == pins.entries
        and manifest.get("status_counts") == expected_counts
        and manifest.get("selection_digest") == pins.selection_digest
        and manifest.get("scientific_namespace") == frozen.PROTOCOL_VERSION
        and manifest.get("policy_digest") == frozen.POLICY_DIGEST,
        "T acquisition manifest identity or status counts differ from the frozen values",
    )

    documents_raw = _read(
        sources.phase_d_root / DOCUMENTS_NAME, SOURCE_BYTES_MAX, "T selected documents"
    )
    provenance_raw = _read(
        sources.phase_d_root / PROVENANCE_NAME, SMALL_JSON_BYTES_MAX, "T sealed provenance"
    )
    for name, raw, key in (
        (DOCUMENTS_NAME, documents_raw, "documents"),
        (PROVENANCE_NAME, provenance_raw, "provenance"),
    ):
        measured = _binding(raw)
        _require(
            measured == _bound(manifest["sealed_outputs"][name])
            and measured == _bound(outputs[name])
            and measured == _bound(bound[key]),
            f"source T acquisition hash mismatch: {name}",
        )
    provenance = _self_digested(provenance_raw, "T sealed provenance")

    selection_raw = _read(sources.selection, SMALL_JSON_BYTES_MAX, "T selection manifest")
    _require(
        sha256_bytes(selection_raw) == pins.selection_sha256
        and _binding(selection_raw) == _bound(bound["selection"]),
        "T selection manifest is not the frozen selection",
    )
    selection = _self_digested(selection_raw, "T selection manifest")
    _require(
        selection["digest"] == pins.selection_digest, "T selection digest differs from the frozen"
    )
    frozen_ids = selection_identities(selection)
    _require(
        len(frozen_ids) == len(set(frozen_ids)) == pins.entries
        and selection.get("total_selected") == pins.entries,
        "frozen T selection does not hold exactly the frozen number of distinct locators",
    )

    bindings_raw = _read(sources.entry_bindings, SMALL_JSON_BYTES_MAX, "T entry bindings")
    _require(
        _binding(bindings_raw) == _bound(bound["entry_bindings"]),
        "T entry bindings differ from the preparation",
    )
    entry_bindings = _json(bindings_raw, "T entry bindings")
    _require(
        entry_bindings.get("source_binding") == _binding(documents_raw),
        "T entry bindings describe a different documents file",
    )

    protocol_raw = _read(sources.protocol, SMALL_JSON_BYTES_MAX, "scientific protocol")
    rubric_raw = _read(sources.rubric_code, SMALL_JSON_BYTES_MAX, "rubric code")
    _require(
        _binding(protocol_raw) == _bound(prep["scientific_protocol"]),
        "scientific protocol differs from the bound protocol",
    )
    _require(_binding(rubric_raw) == _bound(prep["rubric"]), "rubric code differs from the bound")

    entries = _entries(documents_raw, provenance, entry_bindings, pins)
    _check_membership(entries, frozen_ids, pins)
    reviewable = [e for e in entries if e.reviewable]
    text_bytes = sum(e.utf8_bytes for e in reviewable)
    _require(
        text_bytes == manifest.get("retained_text_bytes") == prep.get("full_text_bytes")
        and text_bytes <= frozen.ARM_T_LIMITS["retained_text_bytes_max"],
        "retained text bytes differ from the acquisition manifest",
    )
    _require(
        len({e.text_sha256 for e in reviewable}) == len(reviewable),
        "exact-text aliases present: alias consolidation is not materialized here",
    )
    return TSource(
        entries=tuple(entries),
        bindings={
            "documents": {"name": DOCUMENTS_NAME, **_binding(documents_raw)},
            "provenance": {
                "name": PROVENANCE_NAME,
                **_binding(provenance_raw),
                "digest": provenance["digest"],
            },
            "acquisition_manifest": {
                "name": T_MANIFEST_NAME,
                **_binding(manifest_raw),
                "digest": manifest["digest"],
            },
            "phase_d_receipt": {
                "name": RECEIPT_NAME,
                **_binding(receipt_raw),
                "digest": receipt["digest"],
            },
            "selection": {**_binding(selection_raw), "digest": selection["digest"]},
            "preparation": {**_binding(prep_raw), "digest": prep["digest"]},
            "entry_bindings": _binding(bindings_raw),
            "scientific_protocol": _binding(protocol_raw),
            "rubric_code": _binding(rubric_raw),
            "phase_d_plan_digest": manifest.get("plan_digest"),
            "status_counts": expected_counts,
            "retained_text_bytes": text_bytes,
            "max_reviewable_document_bytes": max(e.utf8_bytes for e in reviewable),
            "oversized_document_bytes": [e.utf8_bytes for e in entries if not e.reviewable],
        },
        rubric_excerpt=rubric_excerpt(protocol_raw),
    )


def _bound(value: Mapping[str, Any]) -> dict[str, Any]:
    return {"bytes": value["bytes"], "sha256": value["sha256"]}


def _entries(
    documents_raw: bytes,
    provenance: Mapping[str, Any],
    entry_bindings: Mapping[str, Any],
    pins: Pins,
) -> list[Entry]:
    _require(documents_raw.endswith(b"\n"), "T documents file does not end with a newline")
    lines = documents_raw[:-1].split(b"\n")
    sealed = provenance["locators"]
    compact = entry_bindings["entries"]
    _require(
        len(lines) == len(sealed) == len(compact) == pins.entries,
        f"T source does not hold exactly {pins.entries} entries",
    )
    limit = frozen.ARM_T_LIMITS["document_bytes_max"]
    entries: list[Entry] = []
    for index, (line, prov, bind) in enumerate(zip(lines, sealed, compact, strict=True)):
        what = f"T document line {index + 1}"
        doc = _json(line, what)
        _require(set(doc) == _DOCUMENT_FIELDS, f"{what} has an unsupported field set")
        _require(canonical.canonical_bytes(doc) == line, f"{what} is not canonical JSON")
        locator = _locator(doc["locator"], f"{what} locator")
        status, text, digest, size = doc["status"], doc["text"], doc["sha256"], doc["utf8_bytes"]
        # The committed binding hashes the stored record including its newline.
        record_sha256 = sha256_bytes(line + b"\n")
        ordinal = doc["t_ordinal"]
        _require(type(ordinal) is int and type(size) is int, f"{what} has a non-integer field")
        if status == REVIEWABLE:
            _require(isinstance(text, str) and bool(text), f"{what} has no reviewable text")
            try:
                encoded = text.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise PackageError(f"{what} text is not strict UTF-8") from exc
            _require(
                len(encoded) == size and size <= limit, f"{what} byte count does not reproduce"
            )
            _require(sha256_bytes(encoded) == digest, f"{what} text hash mismatch")
        elif status == OVERSIZED:
            _require(
                text is None and digest is None and size > limit,
                f"{what} oversized entry retains text, a hash, or a reviewable length",
            )
        else:
            raise PackageError(f"{what} has unsupported acquisition status")
        _require(
            prov.get("locator") == list(locator)
            and prov.get("file") == locator[2]
            and prov.get("t_ordinal") == ordinal
            and prov.get("row_group") == doc["row_group"]
            and prov.get("row_in_group") == doc["row_in_group"]
            and prov.get("row_group_first_row", -1) + doc["row_in_group"] == locator[3]
            and prov.get("status") == status
            and prov.get("utf8_bytes") == size
            and prov.get("sha256") == digest,
            f"{what} disagrees with sealed provenance",
        )
        _require(
            bind
            == {
                "source_line": index + 1,
                "locator_sha256": canonical.digest(list(locator)),
                "status": status,
                "utf8_bytes": size,
                "text_sha256": digest,
                "record_sha256": record_sha256,
            },
            f"{what} disagrees with its committed entry binding",
        )
        span = prov["chunk_span_half_open"]
        entries.append(
            Entry(
                t_ordinal=ordinal,
                locator=locator,
                row_group=doc["row_group"],
                row_in_group=doc["row_in_group"],
                row_group_first_row=prov["row_group_first_row"],
                operations=tuple(prov["operations"]),
                chunk_span_half_open=(span[0], span[1]),
                status=status,
                utf8_bytes=size,
                text_sha256=digest,
                text=text,
                record_sha256=record_sha256,
                source_line=index + 1,
            )
        )
    return entries


def _check_membership(
    entries: Sequence[Entry], frozen_ids: Sequence[tuple[str, str, str, int]], pins: Pins
) -> None:
    locators = [e.locator for e in entries]
    _require(len(set(locators)) == len(locators), "duplicate locator in the T source")
    missing = set(frozen_ids) - set(locators)
    unexpected = set(locators) - set(frozen_ids)
    _require(not missing, f"{len(missing)} frozen locator(s) missing from the T source")
    _require(not unexpected, f"{len(unexpected)} unexpected locator(s) in the T source")
    for locator in locators:
        _require(
            locator[0] == pins.repository
            and locator[1] == pins.revision
            and locator[2] in pins.source_files,
            "locator outside the frozen repository, revision or development files",
        )
    # Acquisition order: frozen file ordinal, then ascending row within the file.
    keys = [(e.t_ordinal, e.locator[3]) for e in entries]
    _require(
        all(e.t_ordinal == pins.source_files.index(e.locator[2]) for e in entries)
        and keys == sorted(keys),
        "T source is out of frozen acquisition order",
    )
    counts = {
        REVIEWABLE: sum(1 for e in entries if e.status == REVIEWABLE),
        OVERSIZED: sum(1 for e in entries if e.status == OVERSIZED),
    }
    _require(
        len(entries) == pins.entries
        and counts[REVIEWABLE] == pins.reviewable
        and counts[OVERSIZED] == pins.oversized,
        "T status counts differ from the frozen reviewable/oversized counts",
    )


# --------------------------------------------------------------------------
# Blinding: reuse exactly when prior material exists, else first materialize.
# --------------------------------------------------------------------------


def load_prior(custodian_dir: Path) -> Prior | None:
    """Read previously sealed K, review IDs and orders from a custodian area."""
    key_path = custodian_dir / Path(KEY_PATH).relative_to(CUSTODIAN)
    ledger_path = custodian_dir / Path(LEDGER_PATH).relative_to(CUSTODIAN)
    order_paths = {r: custodian_dir / f"{r}.order.json" for r in frozen.REVIEWERS}
    others = [ledger_path, *order_paths.values()]
    if not key_path.exists():
        _require(
            not any(p.exists() for p in others),
            "prior review IDs or orders exist without their custodian key; refusing",
        )
        return None
    secret = _read(key_path, 64, "custodian key")
    try:
        blinding.check_secret(secret)
    except blinding.BlindingError as exc:
        raise PackageError(f"existing custodian key is invalid: {exc}") from exc
    ids: dict[tuple[str, str, str, int], str] | None = None
    if ledger_path.exists():
        ledger = _json(_read(ledger_path, SOURCE_BYTES_MAX, "prior ledger"), "prior ledger")
        ids = {
            _locator(item["locator"], "prior ledger locator"): item["review_id"]
            for item in ledger["entries"]
        }
    orders: dict[str, tuple[str, ...]] | None = None
    if any(p.exists() for p in order_paths.values()):
        _require(all(p.exists() for p in order_paths.values()), "prior reviewer orders incomplete")
        orders = {}
        for reviewer, path in order_paths.items():
            body = _json(_read(path, SMALL_JSON_BYTES_MAX, "prior order"), "prior order")
            orders[reviewer] = tuple(body["order"])
    return Prior(secret=secret, ids=ids, orders=orders)


def assign_blinding(
    entries: Sequence[Entry],
    prior: Prior | None,
    generate: Callable[[], bytes] = blinding.generate_secret,
) -> Blinding:
    """Frozen review IDs and orders; an existing K is never replaced."""
    secret = prior.secret if prior is not None else generate()
    try:
        ids = {e.locator: blinding.review_id(secret, list(e.locator)) for e in entries}
        _require(len(set(ids.values())) == len(ids), "review-ID collision: STOP")
        reviewable = [ids[e.locator] for e in entries if e.reviewable]
        orders = {r: tuple(blinding.order_ids(reviewable, r)) for r in frozen.REVIEWERS}
    except blinding.BlindingError as exc:
        raise PackageError(f"blinding refused: {exc}") from exc
    if prior is not None:
        _require(
            prior.ids is None or dict(prior.ids) == ids,
            "existing review IDs differ from the frozen construction; refusing to regenerate",
        )
        _require(
            prior.orders is None or dict(prior.orders) == orders,
            "existing reviewer orders differ from the frozen construction; refusing to regenerate",
        )
    return Blinding(secret=secret, ids=ids, orders=orders, generated=prior is None)


# --------------------------------------------------------------------------
# Rubric, forms and reviewer-facing files.
# --------------------------------------------------------------------------


def rubric_json() -> dict[str, Any]:
    """The frozen rubric as data, unchanged from ``rubric``."""
    return {
        "rubric_version": rubric.RUBRIC_VERSION,
        "dimensions": [
            {
                "n": d["n"],
                "name": d["name"],
                "title": d["title"],
                "levels": list(d["levels"]),
                "anchors": dict(d["anchors"]),
            }
            for d in rubric.DIMENSIONS
        ],
        "uncertain": rubric.UNCERTAIN,
        "not_applicable": rubric.NOT_APPLICABLE,
        "not_applicable_dimensions": sorted(rubric.NOT_APPLICABLE_DIMS),
        "not_reviewed": rubric.NOT_REVIEWED,
        "fragmentation_defect_types": sorted(rubric.FRAGMENTATION_DEFECT_TYPES),
        "form_fields": sorted(rubric.FORM_FIELDS),
    }


def blank_form(review_id: str | None, reviewer: str | None) -> dict[str, Any]:
    """An empty review form: no judgment field is pre-filled."""
    form: dict[str, Any] = {
        "review_id": review_id,
        "reviewer": reviewer,
        "rubric_version": rubric.RUBRIC_VERSION,
        "submitted_utc": None,
        "expertise": None,
        "confidence": None,
        "confidence_reason": None,
        "dimensions": rubric.blank_dimensions(True),
        "dimension_notes": {},
        "disposition_rationale": None,
        "known_essential_gaps": None,
        "known_material_error": None,
    }
    _require(set(form) == set(rubric.FORM_FIELDS), "blank form differs from the frozen fields")
    return form


def rubric_binding(excerpt: bytes, source: Mapping[str, Any]) -> dict[str, Any]:
    body = rubric_json()
    return {
        "rubric_version": rubric.RUBRIC_VERSION,
        "dimensions": len(rubric.DIMENSIONS),
        "dimension_names": [d["name"] for d in rubric.DIMENSIONS],
        "rubric_code": dict(source["rubric_code"]),
        "scientific_protocol": dict(source["scientific_protocol"]),
        "protocol_section_8": _binding(excerpt),
        "rubric_json": _binding(dumps(body)),
        "structure_digest": canonical.digest(body),
        "blank_form_digest": canonical.digest(blank_form(None, None)),
        "wording": "verbatim protocol section 8; no modernization or simplification",
    }


def position_name(position: int) -> str:
    return f"{position:0{_POSITION_DIGITS}d}"


_VIEW_HEAD = (
    '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta http-equiv="Content-Security-Policy" '
    "content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
    '<meta name="referrer" content="no-referrer">\n'
    "<title>Item {position}</title>\n"
    "<style>body{{margin:2em;font-family:serif}}"
    "pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit}}</style>\n"
    "</head>\n<body>\n<p>Item {position} of {total}. Review ID: {review_id}</p>\n<pre>\n"
)
_VIEW_TAIL = "</pre>\n</body>\n</html>\n"


def render_view(position: int, total: int, review_id: str, text: str) -> bytes:
    """Escaped offline rendering: no links, scripts or remote assets."""
    head = _VIEW_HEAD.format(
        position=position_name(position),
        total=position_name(total),
        review_id=html.escape(review_id),
    )
    return (head + html.escape(text, quote=True) + _VIEW_TAIL).encode("utf-8")


def view_text(raw: bytes) -> str:
    """Recover the document text from a rendered view (for validation)."""
    page = raw.decode("utf-8")
    start = page.index("<pre>\n") + len("<pre>\n")
    _require(page.endswith(_VIEW_TAIL), "view is not a rendered item page")
    return html.unescape(page[start : len(page) - len(_VIEW_TAIL)])


_README = """Essential-Web text review package for {reviewer}

Rubric version: {rubric_version}
Items to review: {items}

Files
  order.json       the order in which the items are presented; position 0001 is first
  texts/NNNN.txt   the complete, unaltered document text for position NNNN (UTF-8)
  view/NNNN.html   the same text escaped for offline reading; nothing in it is a live link
  forms/NNNN.json  the blank review form for position NNNN
  rubric.md        the review rubric, verbatim
  rubric.json      the rubric's dimension names and allowed levels in machine-readable form
  package.json     all items, in the same order, in one file

Filling in a form
  Record one allowed level for each of the 18 names under "dimensions". "uncertain" is
  allowed on every judgment and requires a short reason under "dimension_notes" for that
  name. "not_applicable" is allowed only where rubric.md lists it. Set "confidence" to
  "sufficient" or "limited"; "limited" requires "confidence_reason". A
  "disposition_rationale" is required. Put the further notes rubric.md asks for (defect
  types, subreasons, assumed prerequisites, offsets) under "dimension_notes". Do not add
  fields. Inconsistent forms are returned for clarification, never silently corrected.

Rules
  Review the entire unaltered document. Do not follow links during review. Work
  independently: no discussion of the items with, and no access to the labels of, anyone
  else reviewing them.
"""


def reviewer_files(source: TSource, blind: Blinding, reviewer: str) -> dict[str, bytes]:
    """One reviewer's complete package: opaque ID, text, rubric, blank forms."""
    items = [{"locator": list(e.locator), "text": e.text} for e in source.entries if e.reviewable]
    try:
        package = blinding.build_package(items, blind.secret, reviewer, rubric.RUBRIC_VERSION)
    except blinding.BlindingError as exc:
        raise PackageError(f"reviewer package refused: {exc}") from exc
    public, _ = blinding.split_sealed(package)
    order = tuple(public["order"])
    _require(order == blind.orders[reviewer], "helper order differs from the frozen order")
    total = len(order)
    prefix = f"{reviewer}/"
    files: dict[str, bytes] = {
        prefix + "package.json": canonical.canonical_bytes(public),
        prefix + "order.json": dumps(
            {
                "reviewer": reviewer,
                "rubric_version": rubric.RUBRIC_VERSION,
                "items": total,
                "order": [
                    {"position": index + 1, "review_id": rid} for index, rid in enumerate(order)
                ],
            }
        ),
        prefix + "rubric.md": source.rubric_excerpt,
        prefix + "rubric.json": dumps(rubric_json()),
        prefix + "README.txt": _README.format(
            reviewer=reviewer, rubric_version=rubric.RUBRIC_VERSION, items=total
        ).encode("utf-8"),
    }
    for index, form in enumerate(public["forms"]):
        name = position_name(index + 1)
        rid, text = form["review_id"], form["text"]
        files[f"{prefix}texts/{name}.txt"] = text.encode("utf-8")
        files[f"{prefix}view/{name}.html"] = render_view(index + 1, total, rid, text)
        files[f"{prefix}forms/{name}.json"] = dumps(blank_form(rid, reviewer))
    return files


def tree_digest(files: Mapping[str, bytes]) -> str:
    """Canonical digest over every path's byte length and SHA-256."""
    return canonical.digest({name: _binding(raw) for name, raw in sorted(files.items())})


def reviewer_commitment(
    files: Mapping[str, bytes], blind: Blinding, reviewer: str
) -> dict[str, Any]:
    own = {n: r for n, r in files.items() if n.startswith(f"{reviewer}/")}
    order = list(blind.orders[reviewer])
    return {
        "reviewer": reviewer,
        "reviewable_items": len(order),
        "order_digest": canonical.digest(order),
        "order_rule": (
            f"ascending H([{frozen.PROTOCOL_VERSION},{frozen.REVIEW_ORDER_PREFIX},"
            f"{frozen.REVIEW_ORDER_SEED},{reviewer},review_id]), tie by review ID"
        ),
        "package_json": _binding(own[f"{reviewer}/package.json"]),
        "files": len(own),
        "bytes": sum(len(raw) for raw in own.values()),
        "tree_digest": tree_digest(own),
    }


# --------------------------------------------------------------------------
# Custodian ledger and mapping.
# --------------------------------------------------------------------------


def master_ledger(source: TSource, blind: Blinding, m_parent: Mapping[str, Any]) -> dict[str, Any]:
    positions = {
        reviewer: {rid: index + 1 for index, rid in enumerate(order)}
        for reviewer, order in blind.orders.items()
    }
    entries = []
    for e in source.entries:
        rid = blind.ids[e.locator]
        entries.append(
            {
                "t_ordinal": e.t_ordinal,
                "locator": list(e.locator),
                "source_file": e.locator[2],
                "source_row": e.locator[3],
                "row_group": e.row_group,
                "row_in_group": e.row_in_group,
                "row_group_first_row": e.row_group_first_row,
                "acquisition": {
                    "source_line": e.source_line,
                    "record_sha256": e.record_sha256,
                    "operations": list(e.operations),
                    "chunk_span_half_open": list(e.chunk_span_half_open),
                },
                "status": e.status,
                "reviewable": e.reviewable,
                "text_sha256": e.text_sha256,
                "utf8_bytes": e.utf8_bytes,
                "review_id": rid,
                "order_position": {r: positions[r].get(rid) for r in frozen.REVIEWERS},
                "dimension_status": None if e.reviewable else rubric.NOT_REVIEWED,
            }
        )
    return {
        "kind": "essential_web_t_master_ledger",
        "access": "SEALED custodian-only; never part of a reviewer package",
        "scientific_namespace": frozen.PROTOCOL_VERSION,
        "key_commitment": blinding.key_commitment(blind.secret),
        "m_seal_digest": m_parent["seal_digest"],
        "selection": dict(source.bindings["selection"]),
        "documents": dict(source.bindings["documents"]),
        "provenance": dict(source.bindings["provenance"]),
        "counts": {
            "entries": len(entries),
            "reviewable": sum(1 for e in source.entries if e.reviewable),
            "unreviewable_oversized": sum(1 for e in source.entries if not e.reviewable),
        },
        "stratum_and_rank_join": (
            "not materialized; joined by the custodian from the hash-bound selection "
            "manifest only after adjudication is sealed"
        ),
        "entries": entries,
    }


def custodian_files(
    source: TSource, blind: Blinding, m_parent: Mapping[str, Any], mode: str
) -> dict[str, bytes]:
    commitment = blinding.key_commitment(blind.secret)
    files = {
        LEDGER_PATH: dumps(master_ledger(source, blind, m_parent)),
        MAPPING_PATH: dumps(
            {
                "kind": "essential_web_t_sealed_review_mapping",
                "access": "SEALED custodian-only; never part of a reviewer package",
                "key_commitment": commitment,
                "exact_text_aliases": 0,
                "mapping": {
                    blind.ids[e.locator]: [list(e.locator)]
                    for e in sorted(source.entries, key=lambda e: blind.ids[e.locator])
                },
            }
        ),
        MATERIALIZATION_PATH: dumps(
            {
                "kind": "essential_web_t_blinding_materialization",
                "mode": mode,
                "key_commitment": commitment,
                "selection_digest": source.bindings["selection"]["digest"],
                "reselection": False,
            }
        ),
    }
    for reviewer in frozen.REVIEWERS:
        order = list(blind.orders[reviewer])
        files[f"{CUSTODIAN}/{reviewer}.order.json"] = dumps(
            {"reviewer": reviewer, "order": order, "order_digest": canonical.digest(order)}
        )
    return files


# --------------------------------------------------------------------------
# Leakage audit and independent validation.
# --------------------------------------------------------------------------


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def _allowed_keys(name: str) -> frozenset[str]:
    dimension_names = frozenset(d["name"] for d in rubric.DIMENSIONS)
    level_names = frozenset(level for d in rubric.DIMENSIONS for level in d["levels"])
    if name == "package.json":
        return _PACKAGE_FIELDS | _PACKAGE_FORM_FIELDS
    if name == "order.json":
        return _ORDER_FIELDS
    if name == "rubric.json":
        return _RUBRIC_JSON_FIELDS | level_names
    if name.startswith("forms/"):
        return frozenset(rubric.FORM_FIELDS) | dimension_names
    raise PackageError(f"unexpected reviewer JSON file: {name}")


def _split_text(name: str, raw: bytes) -> tuple[bytes, str]:
    """(structure with document text removed, the document text)."""
    if name.startswith("texts/"):
        return b"", raw.decode("utf-8")
    if name.startswith("view/"):
        text = view_text(raw)
        page = raw.decode("utf-8")
        start = page.index("<pre>\n") + len("<pre>\n")
        shell = page[:start] + page[len(page) - len(_VIEW_TAIL) :]
        return shell.encode("utf-8"), text
    if name == "package.json":
        body = _json(raw, "reviewer package")
        text = "\n".join(form["text"] for form in body["forms"])
        blanked = {**body, "forms": [{**form, "text": ""} for form in body["forms"]]}
        return canonical.canonical_bytes(blanked), text
    return raw, ""


def forbidden_values(source: TSource, blind: Blinding, pins: Pins) -> dict[str, list[str]]:
    """Exact custodian values no reviewer file may hold, by category."""
    secret = blind.secret
    digests = {
        pins.selection_digest,
        pins.selection_sha256,
        pins.m_seal_digest,
        pins.preparation_digest,
        frozen.POLICY_DIGEST,
        frozen.FREEZE_DIGEST,
        source.bindings["documents"]["sha256"],
        source.bindings["provenance"]["sha256"],
    }
    entry_hashes: set[str] = set()
    for e in source.entries:
        entry_hashes.add(e.record_sha256)
        entry_hashes.add(canonical.digest(list(e.locator)))
        if e.text_sha256 is not None:
            entry_hashes.add(e.text_sha256)
    crawls = {part for name in pins.source_files for part in name.split("/") if "=" in part}
    return {
        "secret_key": [
            secret.hex(),
            secret.hex().upper(),
            base64.b64encode(secret).decode("ascii"),
            base64.urlsafe_b64encode(secret).decode("ascii"),
        ],
        "repository": [pins.repository],
        "revision": [pins.revision],
        "source_file": sorted(
            {*pins.source_files, *(n.rsplit("/", 1)[-1] for n in pins.source_files)}
        ),
        "crawl": sorted(crawls | {c.split("=", 1)[1] for c in crawls}),
        "locator": sorted(
            {canonical.canonical_bytes(list(e.locator)).decode("utf-8") for e in source.entries}
        ),
        "ownership_name": [name for name, _, _ in frozen.TEXT_STRATA],
        "parent_digest": sorted(digests),
        "entry_hash": sorted(entry_hashes),
        "unreviewable_review_id": [
            blind.ids[e.locator] for e in source.entries if not e.reviewable
        ],
    }


def leakage_audit(
    files: Mapping[str, bytes], source: TSource, blind: Blinding, pins: Pins
) -> dict[str, Any]:
    """Mechanical scan of every reviewer-facing file; structural hits refuse.

    Field names and exact custodian values are checked in everything a
    reviewer can open. Hits inside a document's own text are counted as a
    content-level blinding limitation (the text is never altered).
    """
    values = forbidden_values(source, blind, pins)
    structural: list[str] = []
    content: dict[str, int] = {}
    key_sets: dict[str, list[str]] = {}
    scanned = 0
    for reviewer in frozen.REVIEWERS:
        other = [r for r in frozen.REVIEWERS if r != reviewer]
        prefix = f"{reviewer}/"
        for path, raw in sorted(files.items()):
            if not path.startswith(prefix):
                continue
            scanned += 1
            name = path[len(prefix) :]
            if blind.secret in raw:
                structural.append(f"{path}: raw secret key bytes")
            if name.endswith(".json"):
                keys = _keys(_json(raw, path))
                kind = "forms/*.json" if name.startswith("forms/") else name
                key_sets[kind] = sorted(set(key_sets.get(kind, [])) | keys)
                for key in sorted(keys & FORBIDDEN_KEYS):
                    structural.append(f"{path}: forbidden field '{key}'")
                for key in sorted(keys - _allowed_keys(name)):
                    structural.append(f"{path}: field outside the allowed schema '{key}'")
            shell, text = _split_text(name, raw)
            # The fixed view header names the browser's Content-Security-Policy
            # directive; that token is markup, not selection vocabulary.
            shell_text = shell.decode("utf-8").replace("Content-Security-Policy", "")
            for category, candidates in values.items():
                for candidate in candidates:
                    if candidate in shell_text:
                        structural.append(f"{path}: {category} value")
                    if candidate in text:
                        content[category] = content.get(category, 0) + 1
            for category, pattern in _CONDITION_PATTERNS:
                if pattern.search(shell_text):
                    structural.append(f"{path}: {category} pattern")
                if pattern.search(text):
                    content[category] = content.get(category, 0) + 1
            for term in _GENERIC_TERMS:
                if re.search(rf"\b{term}\b", shell_text, re.IGNORECASE):
                    structural.append(f"{path}: generic term '{term}'")
            for name_other in other:
                if name_other in shell_text:
                    structural.append(f"{path}: names {name_other}")
    audit = {
        "kind": "essential_web_t_leakage_audit",
        "reviewer_files_scanned": scanned,
        "structural_hits": len(structural),
        "structural_hit_list": structural[:50],
        "content_level_hits": dict(sorted(content.items())),
        "content_level_note": (
            "counts of reviewer files whose unaltered document text itself contains a "
            "forbidden value or pattern; a blinding limitation, never a text edit"
        ),
        "json_field_names": key_sets,
        "forbidden_field_names": sorted(FORBIDDEN_KEYS),
        "forbidden_value_categories": {k: len(v) for k, v in sorted(values.items())},
        "forbidden_patterns": [name for name, _ in _CONDITION_PATTERNS],
        "generic_terms_refused_outside_document_text": list(_GENERIC_TERMS),
    }
    if structural:
        raise PackageError(f"reviewer package leak: {structural[:5]}")
    return audit


def validate_package(
    files: Mapping[str, bytes], source: TSource, m_parent: Mapping[str, Any], pins: Pins
) -> dict[str, Any]:
    """Re-parse the built files and recheck every readiness condition."""
    ledger = _json(files[LEDGER_PATH], "master ledger")
    rows = ledger["entries"]
    locators = [tuple(row["locator"]) for row in rows]
    reviewable = [row for row in rows if row["reviewable"]]
    unreviewable = [row for row in rows if not row["reviewable"]]
    _require(
        len(rows) == pins.entries
        and len(reviewable) == pins.reviewable
        and len(unreviewable) == pins.oversized
        and len(set(locators)) == len(locators)
        and set(locators) == {e.locator for e in source.entries},
        "master ledger membership or counts are wrong",
    )
    _require(
        all(
            row["status"] == OVERSIZED
            and row["text_sha256"] is None
            and row["order_position"] == dict.fromkeys(frozen.REVIEWERS)
            and row["dimension_status"] == rubric.NOT_REVIEWED
            for row in unreviewable
        ),
        "oversized entry is not recorded as unreviewable in the ledger",
    )
    by_id = {row["review_id"]: row for row in rows}
    _require(len(by_id) == len(rows), "duplicate review ID in the master ledger")
    unreviewable_ids = {row["review_id"] for row in unreviewable}
    memberships: dict[str, set[str]] = {}
    for reviewer in frozen.REVIEWERS:
        prefix = f"{reviewer}/"
        package = _json(files[prefix + "package.json"], f"{reviewer} package")
        forms = package["forms"]
        ids = [form["review_id"] for form in forms]
        _require(
            len(ids) == pins.reviewable and len(set(ids)) == len(ids),
            f"{reviewer} does not hold exactly the reviewable IDs once each",
        )
        _require(
            ids == blinding.order_ids(ids, reviewer) == package["order"],
            f"{reviewer} order is not the frozen order",
        )
        _require(not set(ids) & unreviewable_ids, f"{reviewer} holds an unreviewable entry")
        for index, form in enumerate(forms):
            name = position_name(index + 1)
            row = by_id.get(form["review_id"])
            _require(row is not None and row["reviewable"], f"{reviewer} holds an unknown ID")
            assert row is not None
            encoded = form["text"].encode("utf-8")
            text_file = files[f"{prefix}texts/{name}.txt"]
            _require(
                sha256_bytes(encoded) == row["text_sha256"]
                and text_file == encoded
                and view_text(files[f"{prefix}view/{name}.html"]) == form["text"]
                and row["order_position"][reviewer] == index + 1,
                f"{reviewer} item {name} text does not match the master ledger",
            )
            blank = _json(files[f"{prefix}forms/{name}.json"], "blank form")
            _require(
                form["labels"] is None
                and blank == blank_form(form["review_id"], reviewer)
                and all(v is None for v in blank["dimensions"].values()),
                f"{reviewer} item {name} form is not blank",
            )
        expected = 5 + 3 * pins.reviewable
        _require(
            sum(1 for path in files if path.startswith(prefix)) == expected,
            f"{reviewer} package holds unexpected files",
        )
        memberships[reviewer] = set(ids)
    first, second = (memberships[r] for r in frozen.REVIEWERS)
    _require(first == second, "reviewer packages differ in membership")
    _require(m_parent["seal_digest"] == pins.m_seal_digest, "M seal digest mismatch")
    return {
        "master_entries": len(rows),
        "master_reviewable": len(reviewable),
        "master_unreviewable_oversized": len(unreviewable),
        "master_duplicate_locators": 0,
        "master_missing_locators": 0,
        "reviewer_items": {r: len(memberships[r]) for r in frozen.REVIEWERS},
        "reviewer_duplicate_ids": 0,
        "reviewer_order_is_frozen_order": True,
        "reviewer_text_hashes_match_master": True,
        "same_reviewable_membership": True,
        "oversized_absent_from_reviewer_packages": True,
        "forms_blank": True,
        "m_seal_digest_matches": True,
    }


# --------------------------------------------------------------------------
# Assembly, Git-safe bindings, write and verify.
# --------------------------------------------------------------------------


def assert_public_safe(public: Mapping[str, bytes], source: TSource, blind: Blinding) -> None:
    """Git-bound files hold no text, no K, no review ID and no mapping."""
    blob = b"\n".join(public.values())
    text = blob.decode("utf-8")
    secret = blind.secret
    _require(
        secret not in blob
        and secret.hex() not in text.lower()
        and base64.b64encode(secret).decode("ascii") not in text,
        "secret key would enter the Git artifact tree",
    )
    _require(
        not any(rid in text for rid in blind.ids.values()),
        "a review ID would enter the Git artifact tree",
    )
    for e in source.entries:
        if e.text is None:
            continue
        if any(len(line) >= 32 and line in text for line in e.text.splitlines()):
            raise PackageError("document text would enter the Git artifact tree")


def build(
    source: TSource,
    blind: Blinding,
    m_parent: Mapping[str, Any],
    pins: Pins,
    *,
    mode: str,
    code: Mapping[str, Any],
    environment: Mapping[str, Any],
) -> Built:
    """Assemble every package file and every Git-safe binding in memory."""
    _require(mode in (FIRST_MATERIALIZATION, REUSED_PRIOR), "unknown materialization mode")
    files: dict[str, bytes] = {}
    for reviewer in frozen.REVIEWERS:
        files.update(reviewer_files(source, blind, reviewer))
    files.update(custodian_files(source, blind, m_parent, mode))
    for reviewer in frozen.REVIEWERS:
        own = {n: _binding(r) for n, r in sorted(files.items()) if n.startswith(f"{reviewer}/")}
        files[f"{CUSTODIAN}/{reviewer}.files.json"] = dumps(
            {"reviewer": reviewer, "files": own, "tree_digest": canonical.digest(own)}
        )
    checks = validate_package(files, source, m_parent, pins)
    audit = leakage_audit(files, source, blind, pins)
    files[AUDIT_PATH] = dumps(audit)

    commitment = blinding.key_commitment(blind.secret)
    custodian_tree = {n: r for n, r in files.items() if n.startswith(f"{CUSTODIAN}/")}
    custodian = {
        "key_commitment": commitment,
        "key_bytes": len(blind.secret),
        "materialization": mode,
        "master_ledger": _binding(files[LEDGER_PATH]),
        "sealed_mapping": _binding(files[MAPPING_PATH]),
        "files": len(custodian_tree),
        "bytes": sum(len(raw) for raw in custodian_tree.values()),
        "tree_digest": tree_digest(custodian_tree),
        "tree_scope": "custodian files except the key file and the package manifest copy",
    }
    reviewers = {r: reviewer_commitment(files, blind, r) for r in frozen.REVIEWERS}
    rubric_bound = rubric_binding(source.rubric_excerpt, source.bindings)
    payload_bytes = sum(len(raw) for raw in files.values()) + len(blind.secret)
    manifest: dict[str, Any] = {
        "kind": PACKAGE_KIND,
        "status": "SEALED",
        "arm": "T",
        "scientific_namespace": frozen.PROTOCOL_VERSION,
        "selection_digest": pins.selection_digest,
        "source_revision": pins.revision,
        "policy_digest": frozen.POLICY_DIGEST,
        "review_identity": {
            "id_tag": frozen.REVIEW_ID_TAG,
            "id_prefix": frozen.REVIEW_ID_PREFIX,
            "order_prefix": frozen.REVIEW_ORDER_PREFIX,
            "order_seed": frozen.REVIEW_ORDER_SEED,
            "reviewers": list(frozen.REVIEWERS),
            "rubric_version": rubric.RUBRIC_VERSION,
            "adjudication": "independent third adjudicator; protocol section 9 unchanged",
        },
        "blinding": {
            "key_commitment": commitment,
            "materialization": mode,
            "reselection": False,
            "ids_or_order_regenerated": False,
        },
        "counts": {
            "master_entries": checks["master_entries"],
            "reviewable": checks["master_reviewable"],
            "unreviewable_oversized": checks["master_unreviewable_oversized"],
            "missing_or_invalid": 0,
            "retained_text_bytes": source.bindings["retained_text_bytes"],
            "max_reviewable_document_bytes": source.bindings["max_reviewable_document_bytes"],
            "oversized_document_bytes": source.bindings["oversized_document_bytes"],
        },
        "m_seal_parent": dict(m_parent),
        "source": dict(source.bindings),
        "rubric": rubric_bound,
        "code": dict(code),
        "code_digest": canonical.digest(dict(code)),
        "reviewers": reviewers,
        "custodian": custodian,
        "validation": checks,
        "leakage_audit": {
            "sha256": sha256_bytes(files[AUDIT_PATH]),
            "reviewer_files_scanned": audit["reviewer_files_scanned"],
            "structural_hits": audit["structural_hits"],
            "content_level_hits": audit["content_level_hits"],
        },
        "limits": {
            "final_artifact_bytes_max": frozen.ARM_T_LIMITS["final_artifact_bytes_max"],
            "payload_bytes": payload_bytes,
        },
        "environment": dict(environment),
        "human_labels_assigned": 0,
        "unblinded": False,
        "selector_decision": "NOT MADE",
    }
    manifest["package_digest"] = canonical.digest(manifest)
    manifest_raw = dumps(manifest)
    _require(
        payload_bytes + len(manifest_raw) <= frozen.ARM_T_LIMITS["final_artifact_bytes_max"],
        "package exceeds the frozen final-artifact byte cap",
    )
    files[MANIFEST_PATH] = manifest_raw

    public: dict[str, bytes] = {
        PUBLIC_MANIFEST: manifest_raw,
        "reviewer1_commitment.json": dumps(reviewers[frozen.REVIEWERS[0]]),
        "reviewer2_commitment.json": dumps(reviewers[frozen.REVIEWERS[1]]),
        "custodian_commitment.json": dumps(custodian),
        "rubric_binding.json": dumps(rubric_bound),
        "m_seal_parent.json": dumps(dict(m_parent)),
        "leakage_audit.json": files[AUDIT_PATH],
    }
    listing: dict[str, Any] = {
        "kind": "essential_web_t_package_artifact_manifest",
        "package_digest": manifest["package_digest"],
        "artifacts": {name: _binding(raw) for name, raw in sorted(public.items())},
    }
    listing["digest"] = canonical.self_digest(listing)
    public[PUBLIC_ARTIFACT_MANIFEST] = dumps(listing)
    assert_public_safe(public, source, blind)
    return Built(files=files, public=public, manifest=manifest)


def write_key(root: Path, secret: bytes) -> None:
    """Create the key file exactly once; an existing key must be identical."""
    path = root / KEY_PATH
    if path.exists():
        _require(path.read_bytes() == secret, "existing custodian key differs; refusing")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(secret)
        stream.flush()
        os.fsync(stream.fileno())


def existing_files(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def write_package(root: Path, built: Built, secret: bytes) -> None:
    """Write the key first and the package manifest last; never overwrite."""
    allowed = {KEY_PATH, *built.files}
    present = existing_files(root)
    _require(not present - allowed, "package root holds files outside the package; refusing")
    _require(MANIFEST_PATH not in present, "package root already holds a sealed package")
    for name in sorted(present - {KEY_PATH}):
        _require(
            (root / name).read_bytes() == built.files[name],
            f"existing package file differs and is not regenerated: {name}",
        )
    write_key(root, secret)
    ordered = [n for n in sorted(built.files) if n != MANIFEST_PATH] + [MANIFEST_PATH]
    for name in ordered:
        canonical.write_atomic(root / name, built.files[name])


def verify_package(root: Path, output_dir: Path, built: Built, secret: bytes) -> None:
    """The on-disk package and Git bindings equal the recomputed ones exactly."""
    expected = {KEY_PATH, *built.files}
    present = existing_files(root)
    _require(present == expected, "package root file set differs from the sealed package")
    _require((root / KEY_PATH).read_bytes() == secret, "custodian key changed")
    for name, raw in built.files.items():
        _require((root / name).read_bytes() == raw, f"package file changed after sealing: {name}")
    for name, raw in built.public.items():
        path = output_dir / name
        _require(
            path.exists() and path.read_bytes() == raw, f"Git binding differs from package: {name}"
        )
