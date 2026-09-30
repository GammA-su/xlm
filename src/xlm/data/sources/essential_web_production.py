"""Essential-Web production selection: reproduction, dry plan and readiness.

Offline checks around the frozen B-normal production selector
(:mod:`xlm.data.adapters.essential_web_selector`):

- :func:`reproduce_b_normal` runs the three production adapters' admission
  over a metadata-only evidence replicate and compares the five final
  counts, overall and per crawl, with the frozen sweep artifacts;
- :func:`build_dry_plan` lays out the production selection stages without
  running, authorizing or sizing anything that was not measured;
- :func:`evaluate_readiness` reports what still blocks bulk acquisition.

Nothing here downloads, probes, admits or fetches. Rows that carry
``text`` are refused: reproduction is metadata-only by construction.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.adapters.columns import columns_for
from xlm.data.sources.admission import (
    AdmissionDecision,
    AdmissionGate,
    attempt_artifact_id,
    latest_attempt,
)
from xlm.data.sources.mix01 import Mix01ViewRegistry
from xlm.data.sources.prober import ProbeEvidenceRecord

ADAPTER_ID = "essential_web_bnormal"
SOURCE_ID = "essential_web"
REPOSITORY = "EssentialAI/essential-web-v1.0"
REPLICATE_ROWS = 4096
ROWS_PER_CRAWL = 512
MAX_LINE_BYTES = 1048576
DRY_PLAN_KIND = "essential_web_production_selection_dry_plan"
READINESS_KEYS = (
    "selector_frozen",
    "selector_integration_ok",
    "source_revision_ok",
    "production_admission_ok",
    "inventory_ready",
    "acquisition_plan_ready",
)
UNKNOWN = "UNKNOWN (not measured; not zero)"


class ProductionCheckError(ValueError):
    """Any input, identity or accounting deviation: refuse."""


class _Admission(Protocol):
    component: str

    def selector_final(self, record: Mapping[str, Any]) -> str: ...


def expected_b_normal(
    summary: Mapping[str, Any], per_crawl: Mapping[str, Any]
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """B-normal final counts, overall and per crawl, from frozen sweep artifacts."""
    total = dict(summary["combos"][selector.CONDITION]["final"])
    crawls = {
        crawl: dict(per_crawl[crawl]["combos"][selector.CONDITION]["final"])
        for crawl in summary["crawls"]
    }
    return total, crawls


def _zeroed(counter: Counter[str]) -> dict[str, int]:
    return {final: counter[final] for final in selector.FINAL_COMPONENTS}


def reproduce_b_normal(
    payload: bytes,
    crawl_of: Mapping[str, str],
    adapters: Sequence[_Admission],
    expected_total: Mapping[str, int],
    expected_per_crawl: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    """Run production admission over one metadata replicate; compare with the frozen counts."""
    if sorted(adapter.component for adapter in adapters) != sorted(selector.ADMITTED_COMPONENTS):
        raise ProductionCheckError("reproduction needs exactly one adapter per component")
    total: Counter[str] = Counter()
    per_crawl: dict[str, Counter[str]] = {crawl: Counter() for crawl in expected_per_crawl}
    seen: set[tuple[str, int]] = set()
    overlaps = 0
    rows = 0
    for line_number, raw in enumerate(payload.splitlines(), start=1):
        if len(raw) > MAX_LINE_BYTES:
            raise ProductionCheckError(f"line {line_number} exceeds the line cap")
        record = json.loads(raw)
        if not isinstance(record, dict):
            raise ProductionCheckError(f"line {line_number}: record is not an object")
        if "text" in record:
            raise ProductionCheckError(f"line {line_number}: text content refused")
        locator = record.get("_xlm_acquisition")
        if not isinstance(locator, Mapping):
            raise ProductionCheckError(f"line {line_number}: missing acquisition locator")
        source_file, row_index = locator.get("source_file"), locator.get("row_index")
        if source_file not in crawl_of or type(row_index) is not int:
            raise ProductionCheckError(f"line {line_number}: row is outside the bound replicate")
        if locator.get("revision") != selector.SOURCE_REVISION:
            raise ProductionCheckError(f"line {line_number}: locator revision is not the pin")
        key = (str(source_file), row_index)
        if key in seen:
            raise ProductionCheckError(f"duplicate row identity {key}")
        seen.add(key)
        finals = {adapter.component: adapter.selector_final(record) for adapter in adapters}
        if len(set(finals.values())) != 1:
            raise ProductionCheckError(f"line {line_number}: adapters disagree on the final")
        final = next(iter(finals.values()))
        if final not in selector.FINAL_COMPONENTS:
            raise ProductionCheckError(f"line {line_number}: unknown final '{final}'")
        # A row is admitted by the adapter whose own component equals its final.
        admitted_by = [component for component, value in finals.items() if value == component]
        overlaps += len(admitted_by) > 1
        if bool(admitted_by) != (final in selector.ADMITTED_COMPONENTS):
            raise ProductionCheckError(f"line {line_number}: admission and final disagree")
        total[final] += 1
        per_crawl[crawl_of[str(source_file)]][final] += 1
        rows += 1
    observed_total = _zeroed(total)
    observed_crawls = {crawl: _zeroed(counter) for crawl, counter in per_crawl.items()}
    mismatches = [
        f"{scope}: observed {observed} != frozen {dict(expected)}"
        for scope, observed, expected in (
            ("all", observed_total, expected_total),
            *(
                (crawl, observed_crawls[crawl], expected_per_crawl[crawl])
                for crawl in expected_per_crawl
            ),
        )
        if observed != dict(expected)
    ]
    conserved = (
        rows == REPLICATE_ROWS
        and sum(observed_total.values()) == REPLICATE_ROWS
        and all(sum(row.values()) == ROWS_PER_CRAWL for row in observed_crawls.values())
    )
    return {
        "condition": selector.CONDITION,
        "rows": rows,
        "final": observed_total,
        "per_crawl": observed_crawls,
        "frozen_final": dict(expected_total),
        "sum": sum(observed_total.values()),
        "conserved": conserved,
        "rows_admitted_by_more_than_one_component": overlaps,
        "mismatches": mismatches,
        "match": not mismatches and conserved and overlaps == 0,
        "admission_path": "EssentialWebSelectedAdapter.selector_final (frozen evaluator)",
        "canonical_rendering_exercised": False,
        "canonical_rendering_note": "the replicate is metadata-only (no text), so admission "
        "is exercised and document rendering is not",
    }


def essential_views(registry: Mix01ViewRegistry) -> list[Any]:
    views = [view for view in registry.views if view.source_id == SOURCE_ID]
    if sorted(view.component_id for view in views) != sorted(selector.ADMITTED_COMPONENTS):
        raise ProductionCheckError("registry does not hold exactly the three Essential views")
    return sorted(views, key=lambda view: selector.ADMITTED_COMPONENTS.index(view.component_id))


def _share(count: int, denominator: int) -> float:
    return round(100.0 * count / denominator, 4)


def _admission_counts(
    freeze: Mapping[str, Any], component: str
) -> dict[str, dict[str, int | float]]:
    key = component.removeprefix("essential_")
    dev = freeze["counts"]["development"][selector.CONDITION][key]
    m = freeze["counts"]["m"][selector.CONDITION][key]
    out: dict[str, dict[str, int | float]] = {}
    for name, count, rows in (
        ("development", dev, REPLICATE_ROWS),
        ("m", m, REPLICATE_ROWS),
        ("both_replicates", dev + m, 2 * REPLICATE_ROWS),
    ):
        out[name] = {"admitted_rows": count, "of_rows": rows, "share_percent": _share(count, rows)}
    return out


def build_dry_plan(
    *,
    freeze: Mapping[str, Any],
    registry: Mix01ViewRegistry,
    weights: Mapping[str, float],
    quotas: Mapping[str, Any],
    limits: Mapping[str, Any],
) -> dict[str, Any]:
    """Dry production-selection plan: stages and bindings only; nothing is run or sized."""
    units = []
    for view in essential_views(registry):
        component = view.component_id
        units.append(
            {
                "component": component,
                "source_id": view.source_id,
                "view_id": component,
                "repository": view.repository,
                "revision": view.observed_revision,
                "adapter_id": view.adapter_id,
                "adapter_config": component,
                "adapter_spec": f"{view.adapter_id}:{component}",
                "projected_columns": list(columns_for(view.adapter_id, component)),
                "mix01_weight": weights[component],
                "final_quota_tokens": quotas["final_quotas"][component],
                "first_pass_quota_tokens": quotas["first_pass_headroom_quotas"][component],
                "observed_admission": _admission_counts(freeze, component),
                "tokens_per_admitted_row": UNKNOWN,
                "transferred_bytes_per_scanned_row": UNKNOWN,
            }
        )
    prefix = "uv run --offline --locked --extra cpu xlm data"
    stages = [
        {
            "stage": "probe",
            "network": True,
            "command": "uv run --locked --extra cpu xlm data probe --catalog "
            "manifests/datasets.catalog.yaml --source essential_web --view <component> --live "
            "--budget-mib <MIB> --probe-id <ID> --json",
            "note": "one per view; the stored essential_science probe is budget_exhausted",
        },
        {
            "stage": "inventory",
            "network": False,
            "command": "uv run --offline --locked --extra cpu python scripts/mix01_inventory.py "
            "freeze --source essential_web --repo EssentialAI/essential-web-v1.0 --revision "
            f"{selector.SOURCE_REVISION} --seed 20260918 --files <candidates.txt> --sizes "
            "<sizes.json> --output <DATA>/inventories/essential_web.inventory.json",
            "note": "one inventory shared by the three views",
        },
        {
            "stage": "calibrate",
            "network": True,
            "command": "sample-blocks, plan (pilot caps), fetch, verify, then one adapt per "
            "component with --adapter essential_web_bnormal --adapter-config <component> "
            "--on-reject record",
            "note": "one raw fetch feeds three adapts; the sample must be large enough for "
            "the sparse science slice",
        },
        {
            "stage": "plan",
            "network": False,
            "command": f"{prefix} plan --source essential_web --view <component> --catalog "
            "manifests/datasets.catalog.yaml --files <ORDERED_PREFIX_CSV> --mode "
            "selected_records --row-ranges <rows.json> --adapter-spec "
            "essential_web_bnormal:<component> --seed 20260918 --max-bytes <BYTES> "
            "--max-records <RECORDS> --max-output-disk <BYTES> --output <plan.json>",
            "note": "STOP after the plan hash is shown; no authorization is given here",
        },
        {
            "stage": "admit",
            "network": False,
            "command": f"{prefix} admit --source essential_web --view <component> --adapter "
            'essential_web_bnormal --notes "<operator review notes>" --decision approve '
            "--license-review approved --benchmark-risk clean",
            "note": "operator decision only; one per view",
        },
        {
            "stage": "fetch_verify",
            "network": True,
            "command": "uv run --locked --extra cpu xlm data fetch --plan <plan.json> "
            "--output-dir <raw> --scratch-dir <scratch>; then xlm data verify",
            "note": "the fetch gate re-verifies probe evidence and the admission decision",
        },
        {
            "stage": "adapt",
            "network": False,
            "command": f"{prefix} adapt --plan <plan.json> --adapter essential_web_bnormal "
            "--adapter-config <component> --input <raw>/selected_records.jsonl --output-dir "
            "<canonical>/<component> --on-reject record --max-input-bytes <BYTES> "
            "--output-shard-bytes <BYTES>",
            "note": "frozen B-normal admission happens here; rejection codes give the "
            "rejected, unassigned and other-component counts",
        },
    ]
    for stage in stages:
        stage["status"] = "NOT RUN"
    return {
        "kind": DRY_PLAN_KIND,
        "version": 1,
        "status": "DRY: no network, no acquisition, no authorization",
        "selector": selector.selector_identity(),
        "t_semantic_review": freeze["t_arm"]["status"],
        "source": {"repository": REPOSITORY, "revision": selector.SOURCE_REVISION},
        "mixture": {
            "registry_id": registry.registry_id,
            "weights_changed": False,
            "quota_id": quotas["quota_id"],
        },
        "units": units,
        "non_admitted_finals": ["unassigned", "rejected"],
        "stages": stages,
        "existing_limits": dict(limits),
        "observations": [
            "The three components share the same raw rows; each row is admitted by at most "
            "one component, following the frozen precedence science, practical, prose.",
            "Science is the sparse slice while its quota equals the practical quota and is "
            "twice the prose quota, so science bounds the rows that must be scanned.",
            "Admission shares are descriptive counts over clustered 512-row windows, not "
            "rates with uncertainty.",
        ],
        "unknown_costs": [
            "tokens per admitted row (no text was measured on the production path)",
            "transferred bytes per scanned row with the text column projected",
            "rows and files needed per component",
        ],
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_bytes().decode("utf-8"))


def _latest_record(xlm_home: Path, kind: str, base_id: str) -> Path:
    """Record file of the newest attempt, as the fetch gate would resolve it."""
    store = ArtifactStore(ArtifactPaths(root=xlm_home))
    attempt = max(1, latest_attempt(store, kind, base_id))
    return xlm_home / kind / attempt_artifact_id(base_id, attempt) / f"{kind}.json"


def _admission_check(xlm_home: Path | None, views: Sequence[Any]) -> list[str]:
    if xlm_home is None:
        return ["no operator store was given, so no admission evidence exists to read"]
    reasons: list[str] = []
    for view in views:
        name = view.component_id
        evidence_path = _latest_record(xlm_home, "probe_evidence", f"probe_{SOURCE_ID}_{name}")
        decision_path = _latest_record(
            xlm_home, "admission_decision", f"admission_{SOURCE_ID}_{name}"
        )
        if not evidence_path.is_file():
            reasons.append(f"{name}: no probe evidence in the operator store")
            continue
        evidence = ProbeEvidenceRecord.model_validate(_load_json(evidence_path))
        decision = (
            AdmissionDecision.model_validate(_load_json(decision_path))
            if decision_path.is_file()
            else None
        )
        gate = AdmissionGate.evaluate(evidence, decision)
        if not gate.admitted:
            reasons.append(f"{name}: {gate.status.value}: " + "; ".join(gate.reasons))
            continue
        assert decision is not None
        if decision.immutable_revision != selector.SOURCE_REVISION:
            reasons.append(f"{name}: admission decision is not bound to the pinned revision")
        if decision.adapter_id != ADAPTER_ID:
            reasons.append(f"{name}: admission decision names adapter '{decision.adapter_id}'")
    return reasons


def _inventory_check(inventory_path: Path | None) -> list[str]:
    if inventory_path is None or not inventory_path.is_file():
        return ["no frozen production inventory for essential_web"]
    inventory = _load_json(inventory_path)
    reasons = []
    if inventory.get("source_id") != SOURCE_ID or inventory.get("repository") != REPOSITORY:
        reasons.append("inventory is not for essential_web")
    if inventory.get("revision") != selector.SOURCE_REVISION:
        reasons.append("inventory is not bound to the pinned revision")
    if not inventory.get("inventory_digest") or not inventory.get("file_count"):
        reasons.append("inventory has no digest or no files")
    return reasons


def _plan_check(calibration_path: Path | None, inventory_ok: bool) -> list[str]:
    reasons = []
    sources: Mapping[str, Any] = {}
    if calibration_path is not None and calibration_path.is_file():
        sources = _load_json(calibration_path).get("sources", {})
    missing = [name for name in selector.ADMITTED_COMPONENTS if name not in sources]
    if missing:
        reasons.append(f"no calibration measurement under the frozen selector for {missing}")
    empty = [
        name
        for name in selector.ADMITTED_COMPONENTS
        if name in sources and not sources[name].get("accepted_records")
    ]
    if empty:
        reasons.append(f"calibration accepted no rows for {empty}")
    if not inventory_ok:
        reasons.append("no frozen inventory to order production files from")
    return reasons


def evaluate_readiness(
    *,
    freeze: Mapping[str, Any],
    registry: Mix01ViewRegistry,
    reproductions: Mapping[str, Mapping[str, Any] | None],
    xlm_home: Path | None,
    inventory_path: Path | None,
    calibration_path: Path | None,
) -> dict[str, Any]:
    """Readiness vector for Essential-Web bulk acquisition, with explicit reasons."""
    views = essential_views(registry)
    identity = selector.selector_identity()
    reasons: dict[str, list[str]] = {key: [] for key in READINESS_KEYS}

    if freeze.get("status") != "FROZEN" or freeze.get("freeze_digest") != selector.FREEZE_DIGEST:
        reasons["selector_frozen"].append("freeze is not the frozen fast-track freeze")
    if freeze["production_selector"]["condition"] != selector.CONDITION:
        reasons["selector_frozen"].append("freeze does not select B-normal")
    if freeze["production_selector"]["policy_digest"] != selector.POLICY_DIGEST:
        reasons["selector_frozen"].append("freeze policy digest differs from the selector")

    for view in views:
        bound = view.upstream_selector.get("frozen_selector", {})
        if view.adapter_id != ADAPTER_ID:
            reasons["selector_integration_ok"].append(
                f"{view.component_id} binds adapter '{view.adapter_id}'"
            )
        drift = [key for key, value in identity.items() if bound.get(key) != value]
        if drift:
            reasons["selector_integration_ok"].append(
                f"{view.component_id} registry selector binding differs: {drift}"
            )
        if view.observed_revision != selector.SOURCE_REVISION or view.repository != REPOSITORY:
            reasons["source_revision_ok"].append(f"{view.component_id} is not the pinned source")
    for name, result in reproductions.items():
        if result is None:
            reasons["selector_integration_ok"].append(f"{name} reproduction was not run")
        elif result.get("match") is not True:
            reasons["selector_integration_ok"].append(f"{name} reproduction does not match")
    if freeze["source"]["revision"] != selector.SOURCE_REVISION:
        reasons["source_revision_ok"].append("freeze source revision differs from the pin")

    reasons["production_admission_ok"] = _admission_check(xlm_home, views)
    reasons["inventory_ready"] = _inventory_check(inventory_path)
    reasons["acquisition_plan_ready"] = _plan_check(
        calibration_path, not reasons["inventory_ready"]
    )
    vector = {key: not reasons[key] for key in READINESS_KEYS}
    return {
        "kind": "essential_web_production_readiness",
        "selector": identity,
        "vector": vector,
        "reasons": {key: reasons[key] for key in READINESS_KEYS if reasons[key]},
        "ready_for_bulk_acquisition": all(vector.values()),
        "admission_inspection": "read-only file inspection; the fetch gate re-verifies "
        "through the artifact store",
    }
