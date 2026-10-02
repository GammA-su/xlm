"""Translate certified real-source evidence into C04 admission evidence, offline.

Production admission (:class:`~xlm.data.sources.admission.AdmissionGate`)
needs a ``ProbeEvidenceRecord`` that is ``accessible``, real, pinned to an
exact revision and carries a verified ``ViewSchema`` and a probe fingerprint.
The generic ``xlm data probe --live`` cannot produce one for most sources: it
builds its schema only from Hub card data, which never holds a schema, so it
records ``partial`` / ``adapter_contract_unassigned``. Several sources were
nevertheless certified on real rows by other, source-specific tools.

This module is the bridge. It reads that immutable evidence, re-derives every
admission fact from it, re-runs the registered adapter on the real rows, and
publishes the result as the next probe-evidence attempt together with a
self-digested bridge receipt. Nothing is downloaded and nothing is assumed:

- the source pin (repository, revision, view, adapter) comes from the catalog
  and the Mix-01 view registry and must agree with every input;
- the schema comes from the certified input, with its basis named;
- the adapter must read every real row (or reject it by policy), reproduce any
  documents digest the input already recorded, and fit the schema;
- observed file identities (path, length, strong ETag) come from completed
  real fetch journals at the pinned revision.

Two certified input kinds exist:

``ultrax-schema-probe-v1``
    ``scripts/ultrax_schema_probe.py`` receipt plus its saved real rows. The
    schema is the datasets ``Features`` of the pinned Parquet Arrow schema.
``xlm-calibration-fetch-v1``
    a completed real ``xlm data fetch`` calibration (plan, journal, selected
    records, adaptation summary). The schema is observed from the projected
    columns of the real rows and is labeled as such.

A source keeps its own pin, adapter, schema and reviews; evidence of one
source can never satisfy another (the pin binds source, view and revision).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xlm.artifacts.store import ArtifactStore
from xlm.data.acquisition.plan import AcquisitionPlan
from xlm.data.adapters.columns import columns_for
from xlm.data.adapters.mix01_adapters import AdapterError, RecordRejectedError
from xlm.data.adapters.registry import ADAPTERS_BY_ID, adapter_code_modules
from xlm.data.adapters.rejections import serialize_document
from xlm.data.evidence_v2 import canonical
from xlm.data.sources.admission import (
    attempt_artifact_id,
    latest_attempt,
    load_probe_evidence,
    next_attempt,
)
from xlm.data.sources.catalog import DatasetCatalogDraft
from xlm.data.sources.mix01 import Mix01ViewRegistry, component_admission_views
from xlm.data.sources.policy import is_denied_source
from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome
from xlm.data.sources.schema import FieldDescriptor, ViewSchema, compute_probe_fingerprint

BRIDGE_KIND = "certified_probe_bridge_receipt"
BRIDGE_VERSION = 1
PRODUCER = "xlm.data.sources.certified_evidence/1"
RECEIPT_FILENAME = "certified_bridge_receipt.json"
EVIDENCE_FILENAME = "probe_evidence.json"
ULTRAX_PROBE = "ultrax-schema-probe-v1"
CALIBRATION_FETCH = "xlm-calibration-fetch-v1"
#: Named schema bases; the basis is part of the fingerprinted schema.
BASIS_FEATURES = "hf_features_of_pinned_parquet_arrow_schema"
BASIS_BUILDER = "hf_builder_features_at_pinned_revision"
BASIS_OBSERVED = "observed_projected_rows_of_real_fetch"
MIB = 1024**2
MAX_JSON_BYTES = MIB
MAX_ROWS_BYTES = 64 * MIB
MAX_ROWS = 20_000
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
#: The hf-datasets scalar spelling ``Value('dtype')``; dtypes are Arrow type names.
_VALUE = re.compile(r"^Value\('([a-z0-9_]+)'\)$")
#: Primitive Arrow types a ``Value`` may name in admitted evidence.
_SCALARS = frozenset(
    {
        "string",
        "large_string",
        "bool",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "float",
        "double",
        "float32",
        "float64",
    }
)
NOT_ESTABLISHED = (
    "production yield, cost or row counts of any other file",
    "benchmark contamination status (C05 screening has not run)",
    "license or provenance approval (an operator review decides)",
    "non-nullability: nullable=true means no non-null guarantee was observed",
)


class BridgeRefusal(ValueError):
    """The certified evidence cannot become admission evidence; nothing is published."""


# ---------------------------------------------------------------------- pin


@dataclass(frozen=True)
class SourcePin:
    """The exact identity admission evidence must carry, from catalog and registry."""

    source_id: str
    view_id: str
    component_id: str
    provider: str
    repository: str
    revision: str
    adapter_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "view_id": self.view_id,
            "component_id": self.component_id,
            "provider": self.provider,
            "repository": self.repository,
            "revision": self.revision,
            "adapter_id": self.adapter_id,
        }


def resolve_pin(
    catalog: DatasetCatalogDraft,
    registry: Mix01ViewRegistry,
    source_id: str,
    view_id: str,
    adapter_id: str,
) -> SourcePin:
    """Bind source, view, revision and adapter; any disagreement refuses."""
    if source_id == "essential_web":
        raise BridgeRefusal("Essential-Web admission evidence comes from its own bootstrap")
    candidate = catalog.get_source(source_id)
    if candidate is None:
        raise BridgeRefusal(f"source '{source_id}' is not in the catalog")
    if is_denied_source(candidate.repository):
        raise BridgeRefusal(f"repository '{candidate.repository}' is denied by XLM policy")
    if candidate.provider != "huggingface":
        raise BridgeRefusal("only pinned Hugging Face sources are supported")
    specs = [
        spec
        for spec in registry.views
        if spec.source_id == source_id and view_id in component_admission_views(spec)
    ]
    if len(specs) != 1:
        raise BridgeRefusal(
            f"view '{view_id}' of '{source_id}' must belong to exactly one Mix-01 component "
            f"(found {len(specs)})"
        )
    spec = specs[0]
    if spec.repository != candidate.repository:
        raise BridgeRefusal("registry and catalog repositories differ")
    revision = spec.observed_revision or ""
    if not SHA_RE.fullmatch(revision):
        raise BridgeRefusal("the registry carries no exact 40-hex revision pin for this view")
    if candidate.revision is not None and candidate.revision != revision:
        raise BridgeRefusal("catalog and registry revision pins differ")
    if adapter_id not in (spec.adapter_id, *spec.extra_adapter_ids):
        raise BridgeRefusal(f"adapter '{adapter_id}' is not an adapter of '{spec.component_id}'")
    if adapter_id not in ADAPTERS_BY_ID:
        raise BridgeRefusal(f"adapter '{adapter_id}' is not registered")
    return SourcePin(
        source_id=source_id,
        view_id=view_id,
        component_id=spec.component_id,
        provider=candidate.provider,
        repository=candidate.repository,
        revision=revision,
        adapter_id=adapter_id,
    )


# ------------------------------------------------------------------- inputs


def _object(data: bytes, what: str, limit: int = MAX_JSON_BYTES) -> dict[str, Any]:
    if len(data) > limit:
        raise BridgeRefusal(f"{what} exceeds its bounded size")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BridgeRefusal(f"{what} is not UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BridgeRefusal(f"{what} is not a JSON object")
    return value


def _jsonl(data: bytes, what: str) -> list[dict[str, Any]]:
    """Strict probe/fetch output: UTF-8 without BOM, LF only, one object per line."""
    if len(data) > MAX_ROWS_BYTES:
        raise BridgeRefusal(f"{what} exceeds its bounded size")
    if data.startswith(b"\xef\xbb\xbf") or b"\r" in data:
        raise BridgeRefusal(f"{what} is not the tool's own output (BOM or CR present)")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(data.split(b"\n"), start=1):
        if not line:
            continue
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise BridgeRefusal(f"{what} line {number} is not JSON") from exc
        if not isinstance(value, dict):
            raise BridgeRefusal(f"{what} line {number} is not an object")
        rows.append(value)
        if len(rows) > MAX_ROWS:
            raise BridgeRefusal(f"{what} holds more than {MAX_ROWS} rows")
    if not rows:
        raise BridgeRefusal(f"{what} holds no row")
    return rows


def _identity(data: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


@dataclass(frozen=True)
class CalibrationEvidence:
    """The four files of one completed real calibration fetch and its adaptation."""

    plan: bytes
    journal: bytes
    records: bytes
    summary: bytes


@dataclass
class CertifiedFacts:
    """What the certified inputs establish, before the adapter is re-run."""

    kind: str
    schema: dict[str, FieldDescriptor]
    schema_basis: str
    declared_license: str | None
    license_caveat: str | None
    split: str | None
    observed_at: str | None
    #: ``(source_file, source_row, record)`` of every real row to certify against.
    rows: list[tuple[str, int, dict[str, Any]]] = field(default_factory=list)
    #: Documents digests an earlier real adaptation recorded, keyed by row-set name.
    recorded_documents: dict[str, tuple[int, int, str]] = field(default_factory=dict)
    row_sets: dict[str, tuple[int, int]] = field(default_factory=dict)
    observed_files: list[dict[str, Any]] = field(default_factory=list)
    inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    probe: dict[str, Any] = field(default_factory=dict)


def _features_schema(types: Mapping[str, Any]) -> dict[str, FieldDescriptor]:
    """Arrow scalar fields from hf-datasets ``Value('dtype')`` spellings.

    ``datasets`` derives these features from the Arrow schema of the pinned
    Parquet files; a ``Value`` dtype name is the Arrow type name. Features carry
    no non-null constraint, so every field is recorded nullable.
    """
    fields: dict[str, FieldDescriptor] = {}
    for name, spelled in sorted(types.items()):
        match = _VALUE.fullmatch(str(spelled))
        if match is None or match[1] not in _SCALARS:
            raise BridgeRefusal(f"field '{name}' has an unsupported feature type")
        fields[name] = FieldDescriptor(name=name, type_name=match[1], nullable=True)
    if not fields:
        raise BridgeRefusal("receipt declares no schema field")
    return fields


def _stats(values: Sequence[int]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


def _same_stats(recorded: Any, computed: Mapping[str, Any]) -> bool:
    if not isinstance(recorded, dict) or set(recorded) != set(computed):
        return False
    for key, value in computed.items():
        other = recorded[key]
        if isinstance(value, float) or isinstance(other, float):
            if not isinstance(other, (int, float)) or not math.isclose(other, value, rel_tol=1e-12):
                return False
        elif other != value:
            return False
    return True


def translate_ultrax_probe(
    pin: SourcePin, receipt_bytes: bytes, sample_bytes: bytes
) -> CertifiedFacts:
    """Facts of an ``ultrax-schema-probe-v1`` receipt, bound to its saved real rows.

    Every row statistic the probe recorded is recomputed from the sample, so a
    sample can only pass with the receipt it was saved with.
    """
    receipt = _object(receipt_bytes, "probe receipt")
    if receipt.get("probe") != ULTRAX_PROBE or receipt.get("probe_version") != 1:
        raise BridgeRefusal("not an ultrax-schema-probe-v1 receipt")
    revision = receipt.get("revision_sha")
    if (receipt.get("repository"), revision) != (pin.repository, pin.revision):
        raise BridgeRefusal("probe receipt repository/revision differs from the pinned source")
    if receipt.get("config") != pin.view_id or receipt.get("expected_config") != pin.view_id:
        raise BridgeRefusal("probe receipt config differs from the admitted view")
    if receipt.get("config_verified") is not True or receipt.get("schema_match") is not True:
        raise BridgeRefusal("probe receipt did not verify its config and schema")
    aliases = receipt.get("tested_aliases")
    observed = aliases.get(pin.repository) if isinstance(aliases, dict) else None
    if (
        not isinstance(observed, dict)
        or observed.get("accessible") is not True
        or observed.get("sha") != pin.revision
    ):
        raise BridgeRefusal("probe receipt does not record the live pinned resolution")
    budgets = receipt.get("budgets")
    if not isinstance(budgets, dict) or budgets.get("full_download") is not False:
        raise BridgeRefusal("probe receipt does not record its bounded live budget")
    basis = {"streaming_features": BASIS_FEATURES, "builder_info": BASIS_BUILDER}.get(
        str((receipt.get("schema_evidence") or {}).get("source"))
    )
    if basis is None:
        raise BridgeRefusal("probe schema evidence is not metadata of the pinned files")
    types = receipt.get("field_types")
    if not isinstance(types, dict):
        raise BridgeRefusal("probe receipt has no field types")
    schema = _features_schema(types)
    names = receipt.get("field_names")
    if not isinstance(names, list) or sorted(names) != sorted(schema):
        raise BridgeRefusal("probe field names differ from its typed schema")
    expected = receipt.get("expected_fields")
    if not isinstance(expected, list) or not set(expected) <= set(schema):
        raise BridgeRefusal("probe receipt lacks an expected field")
    if receipt.get("extra_fields") not in ([], None):
        raise BridgeRefusal("probe receipt reports unexpected extra fields")

    rows = _jsonl(sample_bytes, "probe sample")
    split = receipt.get("split")
    if not isinstance(split, str) or not split:
        raise BridgeRefusal("probe receipt has no split")
    locator = f"hf-stream://{pin.repository}@{pin.revision}/{pin.view_id}/{split}"
    if receipt.get("rows_sampled") != len(rows):
        raise BridgeRefusal("probe sample row count differs from the receipt")
    seen: set[int] = set()
    certified: list[tuple[str, int, dict[str, Any]]] = []
    for row in rows:
        source_row = row.get("_cert_source_row")
        if (
            row.get("_cert_source_file") != locator
            or row.get("_cert_revision") != pin.revision
            or not isinstance(source_row, int)
            or isinstance(source_row, bool)
            or source_row < 0
            or source_row in seen
        ):
            raise BridgeRefusal("probe sample row locator differs from the receipt")
        seen.add(source_row)
        upstream = {k: v for k, v in row.items() if not k.startswith("_cert_")}
        if set(upstream) != set(schema):
            raise BridgeRefusal("probe sample row fields differ from the receipt schema")
        certified.append((locator, source_row, upstream))
    presence = receipt.get("observed_field_presence")
    if presence != {name: len(rows) for name in schema}:
        raise BridgeRefusal("probe field presence differs from the sample")
    uids = [str(r.get("uid")) for _, _, r in certified]
    digest = hashlib.sha256("|".join(sorted(uids)).encode("utf-8")).hexdigest()
    if (receipt.get("uid_format") or {}).get("sorted_sample_sha256") != digest:
        raise BridgeRefusal("probe uid digest differs from the sample")
    for column in ("cleaned_content", "raw_content"):
        lengths = [len(r[column]) for _, _, r in certified if isinstance(r.get(column), str)]
        if not _same_stats(receipt.get(f"{column}_lengths"), _stats(lengths)):
            raise BridgeRefusal(f"probe {column} statistics differ from the sample")
    empty = sum(1 for _, _, r in certified if not str(r.get("cleaned_content") or "").strip())
    if receipt.get("cleaned_content_empty_count") != empty:
        raise BridgeRefusal("probe empty-text count differs from the sample")
    labels: dict[str, int] = {}
    for _, _, r in certified:
        labels[str(r.get("source"))] = labels.get(str(r.get("source")), 0) + 1
    if receipt.get("source_values") != labels:
        raise BridgeRefusal("probe source labels differ from the sample")
    license_tag = receipt.get("declared_license")
    if not isinstance(license_tag, str) or observed.get("license") != license_tag:
        raise BridgeRefusal("probe license is not the one the live resolution declared")
    return CertifiedFacts(
        kind=ULTRAX_PROBE,
        schema=schema,
        schema_basis=basis,
        declared_license=license_tag,
        license_caveat=receipt.get("license_caveat")
        if isinstance(receipt.get("license_caveat"), str)
        else None,
        split=split,
        observed_at=str(receipt.get("observed_at")) if receipt.get("observed_at") else None,
        rows=certified,
        row_sets={"probe_sample": (0, len(certified))},
        inputs={"probe_receipt": _identity(receipt_bytes), "probe_sample": _identity(sample_bytes)},
        probe={
            "kind": ULTRAX_PROBE,
            "observed_at": receipt.get("observed_at"),
            "schema_evidence": receipt.get("schema_evidence"),
            "rows_sampled": len(certified),
            "configs_observed": receipt.get("configs_observed"),
            "row_source_labels": labels,
        },
    )


def _calibration_plan(pin: SourcePin, evidence: CalibrationEvidence) -> AcquisitionPlan:
    plan = AcquisitionPlan.model_validate(_object(evidence.plan, "calibration plan"))
    if not plan.plan_hash or plan.plan_hash != plan.compute_behavioral_hash():
        raise BridgeRefusal("calibration plan hash does not verify")
    if (plan.source_id, plan.view_id, plan.provider, plan.repository, plan.revision) != (
        pin.source_id,
        pin.view_id,
        pin.provider,
        pin.repository,
        pin.revision,
    ):
        raise BridgeRefusal("calibration plan belongs to another source, view or revision")
    return plan


def calibration_files(pin: SourcePin, evidence: CalibrationEvidence) -> list[dict[str, Any]]:
    """Real file identities (path, length, strong ETag) a completed calibration observed."""
    plan = _calibration_plan(pin, evidence)
    journal = _object(evidence.journal, "calibration journal", 8 * MIB)
    if (journal.get("plan_id"), journal.get("plan_hash")) != (plan.plan_id, plan.plan_hash):
        raise BridgeRefusal("calibration journal belongs to another plan")
    if journal.get("status") != "COMPLETED":
        raise BridgeRefusal("calibration fetch did not complete")
    validators = journal.get("source_validators")
    if not isinstance(validators, dict) or not validators:
        raise BridgeRefusal("calibration journal observed no source file")
    files: list[dict[str, Any]] = []
    for path, value in sorted(validators.items()):
        if path not in plan.selected_files or not isinstance(value, dict):
            raise BridgeRefusal("calibration validator names a file outside its plan")
        etag, length = value.get("etag"), value.get("length")
        if (
            not isinstance(etag, str)
            or not etag
            or etag.startswith("W/")
            or not isinstance(length, int)
            or isinstance(length, bool)
            or length < 12
        ):
            raise BridgeRefusal("calibration validator is not a strong ETag with a length")
        files.append(
            {
                "path": path,
                "length": length,
                "etag": etag,
                "evidence": "completed real calibration fetch journal",
                "plan_hash": plan.plan_hash,
            }
        )
    return files


def translate_calibration(
    pin: SourcePin, evidence: CalibrationEvidence, facts: CertifiedFacts | None = None
) -> CertifiedFacts:
    """Add (or, without ``facts``, derive everything from) one real calibration fetch."""
    plan = _calibration_plan(pin, evidence)
    files = calibration_files(pin, evidence)
    journal = _object(evidence.journal, "calibration journal", 8 * MIB)
    progress = (journal.get("file_progress") or {}).get("selected_records.jsonl") or {}
    if progress.get("content_sha256") != hashlib.sha256(evidence.records).hexdigest():
        raise BridgeRefusal("calibration records are not the bytes the fetch journal verified")
    rows = _jsonl(evidence.records, "calibration records")
    if journal.get("records_acquired") != len(rows) or progress.get("record_count") != len(rows):
        raise BridgeRefusal("calibration record count differs from the journal")
    etags = {f["path"]: f["etag"] for f in files}
    accepted_hashes = plan.accepted_selection_hashes()
    certified: list[tuple[str, int, dict[str, Any]]] = []
    for number, row in enumerate(rows, start=1):
        locator = row.get("_xlm_acquisition")
        if not isinstance(locator, dict):
            raise BridgeRefusal(f"calibration record {number} carries no acquisition locator")
        source_file, row_index = locator.get("source_file"), locator.get("row_index")
        if (
            locator.get("source_id") != pin.source_id
            or locator.get("repository") != pin.repository
            or locator.get("revision") != pin.revision
            or source_file not in etags
            or locator.get("etag") != etags[str(source_file)]
            or locator.get("selection_hash") not in accepted_hashes
            or not isinstance(row_index, int)
            or isinstance(row_index, bool)
            or row_index < 0
        ):
            raise BridgeRefusal(f"calibration record {number} locator differs from its plan")
        certified.append((str(source_file), row_index, row))
    summary = _object(evidence.summary, "adaptation summary")
    documents = summary.get("documents") or {}
    if (
        summary.get("plan_hash") != plan.plan_hash
        or summary.get("adapter_id") != pin.adapter_id
        or summary.get("source_revision") != pin.revision
        or summary.get("total_input_records") != len(rows)
        or not isinstance(documents.get("sha256"), str)
    ):
        raise BridgeRefusal("adaptation summary belongs to another plan, adapter or revision")
    start = len(facts.rows) if facts is not None else 0
    if facts is None:
        projected = plan.projected_fields
        if not projected:
            raise BridgeRefusal("a schema from observed rows needs an explicit plan projection")
        facts = CertifiedFacts(
            kind=CALIBRATION_FETCH,
            schema=_observed_schema([r for _, _, r in certified], projected),
            schema_basis=BASIS_OBSERVED,
            declared_license=None,
            license_caveat=None,
            split=None,
            observed_at=str(journal.get("updated_at")) if journal.get("updated_at") else None,
            probe={"kind": CALIBRATION_FETCH, "plan_hash": plan.plan_hash},
        )
    facts.rows.extend(certified)
    facts.row_sets["calibration_records"] = (start, start + len(certified))
    facts.recorded_documents["calibration_records"] = (
        int(summary.get("accepted_records", -1)),
        int(summary.get("rejected_records", -1)),
        str(documents["sha256"]),
    )
    known = {f["path"] for f in facts.observed_files}
    facts.observed_files.extend(f for f in files if f["path"] not in known)
    facts.inputs.update(
        {
            "calibration_plan": _identity(evidence.plan),
            "calibration_journal": _identity(evidence.journal),
            "calibration_records": _identity(evidence.records),
            "calibration_summary": _identity(evidence.summary),
        }
    )
    return facts


def generic_probe_record(
    store: ArtifactStore, source_id: str, view_id: str
) -> tuple[ProbeEvidenceRecord, str, str] | None:
    """The newest verified non-bridge probe attempt: ``(record, artifact id, file sha256)``.

    That is the real ``xlm data probe --live`` metadata record (Hub API at the
    pinned revision). It cannot admit (``partial``), but it is real observed
    evidence of the declared license and the resolved revision.
    """
    base = _base_id(source_id, view_id)
    for attempt in range(latest_attempt(store, "probe_evidence", base), 0, -1):
        artifact = attempt_artifact_id(base, attempt)
        directory = store.paths.root / "probe_evidence" / artifact
        if (directory / RECEIPT_FILENAME).exists() or not (directory / "_COMPLETED").is_file():
            continue
        store.verify_artifact(directory)
        data = (directory / EVIDENCE_FILENAME).read_bytes()
        record = ProbeEvidenceRecord.model_validate(_object(data, "stored probe evidence"))
        return record, artifact, hashlib.sha256(data).hexdigest()
    return None


def translate_store_probe(
    pin: SourcePin,
    facts: CertifiedFacts,
    record: ProbeEvidenceRecord,
    artifact_id: str,
    file_sha256: str,
) -> CertifiedFacts:
    """Bind the earlier real metadata probe: same source, view, revision; its license."""
    if record.evidence_type != EvidenceType.REAL_OBSERVED:
        raise BridgeRefusal("the stored metadata probe is not real observed evidence")
    if (
        record.source_id,
        record.view_id,
        record.provider,
        record.repository,
        record.immutable_revision,
    ) != (pin.source_id, pin.view_id, pin.provider, pin.repository, pin.revision):
        raise BridgeRefusal("the stored metadata probe belongs to another source or revision")
    if record.outcome not in (ProbeOutcome.PARTIAL, ProbeOutcome.ACCESSIBLE) or record.is_gated:
        raise BridgeRefusal(f"the stored metadata probe outcome is '{record.outcome.value}'")
    declared = (record.declared_license or "").strip().lower()
    if not declared:
        raise BridgeRefusal("the stored metadata probe declares no license")
    if facts.declared_license is not None and facts.declared_license.strip().lower() != declared:
        raise BridgeRefusal("certified evidence and metadata probe declare different licenses")
    facts.declared_license = declared
    facts.inputs["metadata_probe"] = {
        "artifact_id": artifact_id,
        "sha256": file_sha256,
        "outcome": record.outcome.value,
        "observed_timestamp": record.observed_timestamp,
    }
    return facts


def _observed_type(values: Iterable[Any]) -> tuple[str, dict[str, FieldDescriptor]]:
    kinds: set[str] = set()
    nested: dict[str, list[Any]] = {}
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            kinds.add("bool")
        elif isinstance(value, int):
            kinds.add("int64")
        elif isinstance(value, float):
            kinds.add("double")
        elif isinstance(value, str):
            kinds.add("string")
        elif isinstance(value, list):
            kinds.add("list")
        elif isinstance(value, dict):
            kinds.add("struct")
            for key, child in value.items():
                nested.setdefault(str(key), []).append(child)
        else:
            raise BridgeRefusal("observed value has an unsupported JSON type")
    if kinds == {"int64", "double"}:
        kinds = {"double"}
    if len(kinds) > 1:
        raise BridgeRefusal(f"observed column mixes types {sorted(kinds)}")
    kind = kinds.pop() if kinds else "null"
    children: dict[str, FieldDescriptor] = {}
    for name, child in sorted(nested.items()):
        child_kind, grandchildren = _observed_type(child)
        children[name] = FieldDescriptor(
            name=name, type_name=child_kind, nested_fields=grandchildren
        )
    return kind, children


def _observed_schema(
    rows: Sequence[Mapping[str, Any]], projected: Sequence[str]
) -> dict[str, FieldDescriptor]:
    """Types of the projected columns over every real row; absent columns refuse."""
    fields: dict[str, FieldDescriptor] = {}
    for name in sorted(projected):
        if not any(name in row for row in rows):
            raise BridgeRefusal(f"projected column '{name}' never appears in the real rows")
        kind, nested = _observed_type(row.get(name) for row in rows)
        fields[name] = FieldDescriptor(name=name, type_name=kind, nested_fields=nested)
    return fields


# -------------------------------------------------------------- certification


def adapter_code_identity(adapter_id: str) -> dict[str, str]:
    """SHA-256 of the sources defining ``adapter_id`` (LF-normalized), by module name.

    The frozen adapter and column-contract modules for every adapter, plus a
    versioned adapter's own module (:func:`adapter_code_modules`), so a
    corrected adapter contract changes only its own adapter's identity.
    """
    identity: dict[str, str] = {}
    for module in adapter_code_modules(adapter_id):
        source = inspect.getsourcefile(module)
        if source is None:
            raise BridgeRefusal("adapter source file is not available")
        data = Path(source).read_bytes().replace(b"\r\n", b"\n")
        identity[f"{module.__name__}"] = hashlib.sha256(data).hexdigest()
    return identity


def view_schema(pin: SourcePin, facts: CertifiedFacts) -> ViewSchema:
    return ViewSchema(
        view_id=pin.view_id,
        fields=dict(facts.schema),
        raw_schema_type=facts.schema_basis,
        is_nested=any(f.nested_fields for f in facts.schema.values()),
    )


def certify_adapter(pin: SourcePin, facts: CertifiedFacts, schema: ViewSchema) -> dict[str, Any]:
    """Run the registered adapter on every real row; refuse anything it cannot read.

    A policy rejection is counted. A missing or mistyped field, a document of
    another source or revision, non-deterministic output, or a recorded
    documents digest the adapter no longer reproduces all refuse.
    """
    factory: Any = ADAPTERS_BY_ID[pin.adapter_id]
    adapter = factory()
    contract = adapter.contract()
    ok, missing = contract.evaluate_against_schema(schema)
    if not ok:
        raise BridgeRefusal(f"schema lacks adapter fields: {missing}")
    try:
        projection = list(columns_for(pin.adapter_id, pin.view_id))
    except ValueError as exc:
        raise BridgeRefusal(str(exc)) from exc
    absent = [name for name in projection if name not in schema.fields]
    if absent:
        raise BridgeRefusal(f"schema lacks the adapter's projected columns: {absent}")
    sets: dict[str, dict[str, Any]] = {}
    for name, (start, stop) in facts.row_sets.items():
        digest = hashlib.sha256()
        accepted = 0
        codes: dict[str, int] = {}
        canonical_bytes = 0
        for index in range(start, stop):
            source_file, source_row, record = facts.rows[index]
            try:
                document = adapter.adapt(
                    record,
                    source_file=source_file,
                    source_row=source_row,
                    source_revision=pin.revision,
                )
            except RecordRejectedError as exc:
                codes[type(exc).__name__] = codes.get(type(exc).__name__, 0) + 1
                continue
            except AdapterError as exc:
                raise BridgeRefusal(
                    f"{name} row {index - start + 1}: adapter cannot read the real row "
                    f"({type(exc).__name__})"
                ) from exc
            again = adapter.adapt(
                record, source_file=source_file, source_row=source_row, source_revision=pin.revision
            )
            if (
                document.source_id != pin.source_id
                or document.source_revision != pin.revision
                or document.source_file != source_file
                or document.source_row != source_row
                or again.to_dict() != document.to_dict()
            ):
                raise BridgeRefusal(f"{name} row {index - start + 1}: adapter output is not bound")
            digest.update(serialize_document(document).encode("utf-8") + b"\n")
            accepted += 1
            canonical_bytes += document.utf8_byte_count
        rejected = sum(codes.values())
        recorded = facts.recorded_documents.get(name)
        if recorded is not None and recorded != (accepted, rejected, digest.hexdigest()):
            raise BridgeRefusal(f"adapter no longer reproduces the recorded {name} documents")
        sets[name] = {
            "rows": stop - start,
            "accepted": accepted,
            "rejected": rejected,
            "rejection_counts_by_code": dict(sorted(codes.items())),
            "canonical_bytes": canonical_bytes,
            "documents_sha256": digest.hexdigest(),
            "reproduces_recorded_documents": recorded is not None,
        }
    if sum(s["accepted"] for s in sets.values()) < 1:
        raise BridgeRefusal("the adapter accepted no real row")
    return {
        "adapter_id": pin.adapter_id,
        "contract": contract.model_dump(),
        "projected_columns": projection,
        "code_sha256": adapter_code_identity(pin.adapter_id),
        "row_sets": sets,
    }


# ------------------------------------------------------------------ receipt


def _with_digest(body: dict[str, Any]) -> dict[str, Any]:
    body.pop("digest", None)
    body["digest"] = canonical.digest(body)
    return body


def build_bridge(
    pin: SourcePin, facts: CertifiedFacts, *, evidence_type: EvidenceType
) -> tuple[dict[str, Any], ProbeEvidenceRecord]:
    """The bridge receipt and the admission evidence record it justifies."""
    if not facts.declared_license:
        raise BridgeRefusal("no real evidence declares the license; bind the metadata probe")
    if not facts.observed_files:
        raise BridgeRefusal("no real fetch observed a file identity; bind a calibration fetch")
    schema = view_schema(pin, facts)
    certification = certify_adapter(pin, facts, schema)
    inventory = [{"path": f["path"], "size": f["length"]} for f in facts.observed_files]
    fingerprint = compute_probe_fingerprint(
        provider=pin.provider,
        repository=pin.repository,
        immutable_revision=pin.revision,
        view_id=pin.view_id,
        schema_dict=schema.to_canonical_dict(),
        file_inventory=inventory,
    )
    receipt = _with_digest(
        {
            "kind": BRIDGE_KIND,
            "version": BRIDGE_VERSION,
            "producer": PRODUCER,
            "role": "translate certified real-source evidence into C04 admission evidence; "
            "offline, no network, no re-probe",
            "evidence_type": evidence_type.value,
            "source": pin.as_dict(),
            "certification": {
                "kind": facts.kind,
                "inputs": dict(sorted(facts.inputs.items())),
                "probe": facts.probe,
                "split": facts.split,
                "observed_at": facts.observed_at,
            },
            "schema": schema.to_canonical_dict(),
            "schema_basis": facts.schema_basis,
            "schema_digest": canonical.digest(schema.to_canonical_dict()),
            "observed_files": facts.observed_files,
            "adapter": certification,
            "declared_license": facts.declared_license,
            "license_caveat": facts.license_caveat,
            "probe_fingerprint": fingerprint,
            "not_established": list(NOT_ESTABLISHED),
        }
    )
    record = ProbeEvidenceRecord(
        source_id=pin.source_id,
        view_id=pin.view_id,
        provider=pin.provider,
        repository=pin.repository,
        immutable_revision=pin.revision,
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=evidence_type,
        probe_fingerprint=fingerprint,
        observed_timestamp=facts.observed_at or "",
        observed_files_count=len(inventory),
        verified_schema=schema,
        declared_license=facts.declared_license,
        resource_metrics={
            "producer": PRODUCER,
            "bridge_receipt_digest": receipt["digest"],
            "certification_kind": facts.kind,
            "network_requests": 0,
            "measurement": "offline translation of earlier real evidence; no transfer",
        },
        reason="translated from certified real-source evidence by the admission bridge; "
        "schema basis " + facts.schema_basis,
    )
    return receipt, record


def check_receipt(receipt: Mapping[str, Any]) -> None:
    """Refuse an altered, foreign or unsupported bridge receipt."""
    body = dict(receipt)
    if body.pop("digest", None) != canonical.digest(body):
        raise BridgeRefusal("bridge receipt digest does not verify")
    if receipt.get("kind") != BRIDGE_KIND or receipt.get("version") != BRIDGE_VERSION:
        raise BridgeRefusal("not a version-1 certified bridge receipt")


def record_from_receipt(receipt: Mapping[str, Any]) -> ProbeEvidenceRecord:
    """Rebuild the evidence record a receipt justifies (used to verify publications)."""
    check_receipt(receipt)
    source = receipt["source"]
    schema = ViewSchema.model_validate(receipt["schema"])
    return ProbeEvidenceRecord(
        source_id=source["source_id"],
        view_id=source["view_id"],
        provider=source["provider"],
        repository=source["repository"],
        immutable_revision=source["revision"],
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=EvidenceType(receipt["evidence_type"]),
        probe_fingerprint=receipt["probe_fingerprint"],
        observed_timestamp=receipt["certification"]["observed_at"] or "",
        observed_files_count=len(receipt["observed_files"]),
        verified_schema=schema,
        declared_license=receipt["declared_license"],
        resource_metrics={
            "producer": PRODUCER,
            "bridge_receipt_digest": receipt["digest"],
            "certification_kind": receipt["certification"]["kind"],
            "network_requests": 0,
            "measurement": "offline translation of earlier real evidence; no transfer",
        },
        reason="translated from certified real-source evidence by the admission bridge; "
        "schema basis " + str(receipt["schema_basis"]),
    )


# -------------------------------------------------------------- publication


def _base_id(source_id: str, view_id: str) -> str:
    return f"probe_{source_id}_{view_id}"


def stored_bridge(store: ArtifactStore, source_id: str, view_id: str) -> dict[str, Any] | None:
    """The bridge receipt of the latest probe-evidence attempt; None when it is not a bridge."""
    attempt = latest_attempt(store, "probe_evidence", _base_id(source_id, view_id))
    if attempt == 0:
        return None
    directory = (
        store.paths.root
        / "probe_evidence"
        / attempt_artifact_id(_base_id(source_id, view_id), attempt)
    )
    path = directory / RECEIPT_FILENAME
    if not path.is_file():
        return None
    store.verify_artifact(directory)
    receipt = _object(path.read_bytes(), "stored bridge receipt")
    check_receipt(receipt)
    return receipt


def publish_bridge(
    store: ArtifactStore, receipt: Mapping[str, Any], record: ProbeEvidenceRecord
) -> tuple[str, bool]:
    """Publish evidence + receipt as the next probe attempt; ``(dir, published)``.

    Idempotent for an identical receipt. Earlier attempts (the old ``partial``
    record) are never replaced; the newer attempt supersedes them.
    """
    check_receipt(receipt)
    if record.model_dump() != record_from_receipt(receipt).model_dump():
        raise BridgeRefusal("evidence record does not match its bridge receipt")
    if record.evidence_type != EvidenceType.REAL_OBSERVED:
        raise BridgeRefusal("authored or synthetic evidence is never published as admission")
    base = _base_id(record.source_id, record.view_id)
    current = stored_bridge(store, record.source_id, record.view_id)
    if current is not None and current["digest"] == receipt["digest"]:
        attempt = latest_attempt(store, "probe_evidence", base)
        return str(store.paths.root / "probe_evidence" / attempt_artifact_id(base, attempt)), False
    attempt = next_attempt(store, "probe_evidence", base)
    published = store.publish_artifact(
        artifact_id=attempt_artifact_id(base, attempt),
        kind="probe_evidence",
        files={
            EVIDENCE_FILENAME: json.dumps(record.to_canonical_dict(), indent=2).encode("utf-8"),
            RECEIPT_FILENAME: (json.dumps(dict(receipt), indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        },
        producer_code_hash=hashlib.sha256(PRODUCER.encode()).hexdigest()[:16],
        dependency_hash=hashlib.sha256(b"uv.lock").hexdigest()[:16],
        resolved_config_hash=record.probe_fingerprint or "unverified_config",
        metadata={
            "source_id": record.source_id,
            "view_id": record.view_id,
            "outcome": record.outcome.value,
            "evidence_type": record.evidence_type.value,
            "bridge_receipt_digest": receipt["digest"],
        },
    )
    loaded = load_probe_evidence(record.source_id, record.view_id, store)
    if loaded is None or loaded.probe_fingerprint != record.probe_fingerprint:
        raise BridgeRefusal("published evidence does not load back as the latest attempt")
    return str(published), True


def verify_current(
    store: ArtifactStore,
    pin: SourcePin,
    rebuild: Callable[[], tuple[dict[str, Any], ProbeEvidenceRecord]] | None = None,
) -> dict[str, Any]:
    """The stored bridge still binds this pin, this adapter code and (optionally) its inputs.

    ``rebuild`` re-translates the original inputs; the result must reproduce the
    stored receipt digest exactly. Any drift (revision, schema, adapter code,
    inputs) refuses.
    """
    receipt = stored_bridge(store, pin.source_id, pin.view_id)
    if receipt is None:
        raise BridgeRefusal("the latest probe evidence of this view is not a bridge publication")
    if receipt["source"] != pin.as_dict():
        raise BridgeRefusal("stored bridge binds another source, view, revision or adapter")
    if receipt["adapter"]["code_sha256"] != adapter_code_identity(pin.adapter_id):
        raise BridgeRefusal("adapter code changed since the evidence was bridged")
    evidence = load_probe_evidence(pin.source_id, pin.view_id, store)
    if evidence is None or evidence.model_dump() != record_from_receipt(receipt).model_dump():
        raise BridgeRefusal("stored evidence record differs from its bridge receipt")
    if rebuild is not None and rebuild()[0]["digest"] != receipt["digest"]:
        raise BridgeRefusal("the certified inputs no longer reproduce the stored bridge receipt")
    return receipt


def inputs_outside(paths: Iterable[Path], checkout: Path) -> None:
    """Authored fixtures live in the checkout; real operator evidence never does."""
    root = checkout.resolve()
    for path in paths:
        resolved = path.resolve()
        if resolved == root or resolved.is_relative_to(root):
            raise BridgeRefusal(
                f"'{path}' lies inside the code checkout; authored fixtures are not real evidence"
            )


def is_digest(value: Any) -> bool:
    return isinstance(value, str) and DIGEST_RE.fullmatch(value) is not None
