"""Exact component/allocation accounting against existing frozen Mix-01 quotas."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.cleaned import is_cleaned
from xlm.data.exclusion.gates import C05View, MembershipGate
from xlm.data.exclusion.inputs import COMPONENTS, read_metadata
from xlm.data.exclusion.policy import C05Error
from xlm.data.tokens import TokenShardReader

LABELS = ("component", "view", "upstream_component")


def view_requirements(view: C05View, quotas: Path, ifm_split: Path) -> dict[str, Any]:
    """Frozen requirements of a verified C05 view: its membership, its verified provenance."""
    return frozen_requirements(
        view.input_manifest, quotas, ifm_split, provenance=view.requirements_manifest()
    )


def frozen_requirements(
    manifest: Mapping[str, Any],
    quotas: Path,
    ifm_split: Path,
    *,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read existing quota decisions, never create/amend/substitute an allocation.

    Allocations come from ``manifest`` (the C05 input manifest). Source/adapter/quota
    bindings come from ``provenance``: the same manifest, or, for a cleaned manifest,
    its original manifest as verified by ``cleaned.requirements_manifest``.
    """
    if manifest.get("digest") != canonical.self_digest(manifest):
        raise C05Error("quota input manifest changed")
    if provenance is None or provenance.get("digest") == manifest["digest"]:
        if is_cleaned(manifest):
            raise C05Error("a cleaned manifest needs its verified original for quota provenance")
        provenance = manifest
    elif (
        provenance.get("digest") != canonical.self_digest(provenance)
        or not is_cleaned(manifest)
        or manifest["lineage"].get("original_input_manifest_digest") != provenance["digest"]
        or [[f[k] for k in LABELS] for f in manifest["files"]]
        != [[f.get(k) for k in LABELS] for f in provenance["files"]]
    ):
        raise C05Error("quota provenance is not this cleaned manifest's original")
    raw = quotas.read_bytes()
    if len(raw) > 1024 * 1024:
        raise C05Error("quota metadata size ceiling")
    quota_sha = hashlib.sha256(raw).hexdigest()
    table = yaml.safe_load(raw)
    finals = table["final_quotas"]
    total = table["final_valid_targets"]
    # The real table declares 6,000,000,000; sealed sources bind its SHA-256 below,
    # so a substituted table cannot satisfy the production manifest.
    if set(finals) != COMPONENTS or type(total) is not int or sum(finals.values()) != total:
        raise C05Error("frozen Mix-01 quota coverage/total changed")
    sources = {s["source_key"]: s for s in provenance["sources"]}
    for source in sources.values():
        for binding in source.get("adapter_binding", {}).values():
            if binding.get("quotas_sha256") != quota_sha:
                raise C05Error("quota file differs from sealed source requirement")
    split = read_metadata(ifm_split)
    if (
        split.get("quotas_sha256") != quota_sha
        or split.get("component_id") != "ifm_behaviors_general_planning"
    ):
        raise C05Error("IFM split quota binding mismatch")
    for source in (sources["ifm_general"], sources["ifm_planning"]):
        for binding in source["adapter_binding"].values():
            if binding["requirement_split"]["split_digest"] != split["digest"]:
                raise C05Error("IFM split differs from sealed source")
    common = [b["component_split"] for b in sources["common_pile"]["adapter_binding"].values()]
    if not common or any(c != common[0] for c in common):
        raise C05Error("Common Pile allocation changed across source plans")
    cp = common[0]
    if cp.get("digest") != canonical.self_digest(cp):
        raise C05Error("Common Pile split digest mismatch")
    result: dict[str, int] = {}
    for f in manifest["files"]:
        component, view, upstream = f["component"], f["view"], f.get("upstream_component")
        if component == "ifm_behaviors_general_planning":
            quota = split["views"][view]["final_tokens"]
        elif component == "common_pile_prose":
            quota = cp["components"][upstream]["final_tokens"]
        else:
            quota = finals[component]
        key = canonical.canonical_bytes([component, view, upstream]).decode()
        result[key] = quota
    by_component: dict[str, int] = {}
    for key, quota in result.items():
        component = canonical.loads_strict(key)[0]
        by_component[component] = by_component.get(component, 0) + quota
    if by_component != finals or any(type(q) is not int or q <= 0 for q in result.values()):
        raise C05Error("allocation quotas do not reproduce frozen component totals")
    return {
        "quota_sha256": quota_sha,
        "ifm_split_digest": split["digest"],
        "common_pile_split_digest": cp["digest"],
        "allocations": result,
        "valid_target_quota": total,
        "final_quotas": dict(sorted(finals.items())),
        "tokenizer_vocab_size": table["tokenizer_vocab_size"],
    }


def quota_report(
    gate: MembershipGate, requirements: Mapping[str, Any], shards: list[Path]
) -> dict[str, Any]:
    """Aggregate only verified unique token offsets; never equate bytes/4 to tokens."""
    if gate.mode != "protected":
        raise C05Error("quota reporting requires protected kept membership")
    exact: dict[str, int] = {}
    counts: dict[str, int] = {}
    tokenizer: str | None = None
    gate.db.execute("DELETE FROM seen")
    for path in shards:
        gate.verify_token_shard(path, reset_seen=False)
        reader = TokenShardReader(path)
        if tokenizer is not None and reader.manifest.tokenizer_hash != tokenizer:
            raise C05Error("exact counts use different tokenizers")
        tokenizer = reader.manifest.tokenizer_hash
        for offset in reader.iter_document_offsets():
            allocation = gate.db.execute(
                "SELECT component,view,upstream FROM membership WHERE id=?", (offset["doc_id"],)
            ).fetchone()
            key = canonical.canonical_bytes(allocation).decode()
            if key not in requirements["allocations"]:
                raise C05Error("unexpected token allocation")
            counts[key] = counts.get(key, 0) + 1
            exact[key] = exact.get(key, 0) + int(offset["valid_targets"])
    available = gate.completion["allocations"]
    report: dict[str, Any] = {}
    for key, quota in requirements["allocations"].items():
        # Missing or partial counts never imply that a component is sufficient.
        component_counts = available.get(key, {"kept": 0, "train_bytes": 0})
        training_rows = gate.db.execute(
            "SELECT COUNT(*) FROM membership WHERE component=? AND view=? "
            "AND upstream IS ? AND split='train'",
            tuple(canonical.loads_strict(key)),
        ).fetchone()[0]
        complete = counts.get(key, 0) == training_rows and bool(shards)
        tokens = exact.get(key, 0) if complete else None
        report[key] = {
            "kept_documents": component_counts["kept"],
            "training_documents": training_rows,
            "canonical_train_bytes": component_counts["train_bytes"],
            "exact_valid_targets": tokens,
            "counted_documents": counts.get(key, 0),
            "quota": quota,
            "deficit": max(0, quota - tokens) if tokens is not None else None,
            "surplus": max(0, tokens - quota) if tokens is not None else None,
            "status": "NOT_COUNTED"
            if tokens is None
            else "DEFICIT"
            if tokens < quota
            else "SUFFICIENT",
        }
    return {
        "kind": "c05_quota_report_v1",
        "completion_digest": gate.receipt_digest,
        "requirements_digest": canonical.digest(requirements),
        "tokenizer": tokenizer,
        "allocations": report,
        "all_sufficient": all(r["status"] == "SUFFICIENT" for r in report.values()),
        "top_up_rule": "same component/allocation only; separate acquisition authorization "
        "and renewed global C05 required",
    }
