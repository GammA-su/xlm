# Requires: operator-run. Only `run` and `benchmark run` use the network.
"""Mix-01 source acquisition driver: evidence bridge, admission, planning and runs.

Every subcommand except ``run`` and ``benchmark run`` is offline. Roots come
from ``XLM_DATA_ROOT`` / ``XLM_SCRATCH_ROOT`` (``scripts/operator_storage.ps1``)
or ``--data-root`` / ``--scratch-root``; the admission store is ``XLM_HOME``.

Sequence (UltraX shown; other sources use their own key):

    evidence publish -> review show -> review record -> admit
    -> benchmark plan/authorize/run (+ range benchmark record, or a range-reach
       audit when range v1 cannot reach the whole-file population) -> policy freeze
    -> plan  (STOP: the operator reviews the printed digest)
    -> authorize --digest <digest> -> run -> status/resume-check/verify
    -> sufficiency -> plan (top-up, only if TOP_UP) -> seal
    (an authorized plan whose units fail under its own limits: plan-repair
     -> authorize -> run the repair; the failed plan is never edited)
    (a latest plan never authorized or run, whose inputs or rules changed:
     plan-supersede -> authorize the new plan; the old one is kept, never run)
    (a multi-corpus source such as Common Pile first needs the operator's
     write-once component allowlist: allowlist preview -> allowlist record ->
     mix01_inventory.py freeze --listing --allowlist; every inventory consumer
     refuses an inventory that does not bind it)

Nothing here edits a plan, renormalizes a quota or re-probes a source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.data.acquisition import component_allowlist as allow
from xlm.data.acquisition import component_calibration as cc
from xlm.data.acquisition import component_policy as cp
from xlm.data.acquisition import range_reach as reach
from xlm.data.acquisition import source_benchmark as bench
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import source_run as runner
from xlm.data.acquisition import transport_policy as policy
from xlm.data.acquisition.plan import AcquisitionPlan
from xlm.data.acquisition.sampling import SamplingRefusal, discover_layout_local
from xlm.data.acquisition.source_dashboard import ObservedScratch
from xlm.data.acquisition.source_formats import load_durable
from xlm.data.adapters.columns import columns_for
from xlm.data.sources import certified_evidence as ce
from xlm.data.sources import common_pile_evidence as cpe
from xlm.data.sources import common_pile_license as cpl
from xlm.data.sources import hf_inventory
from xlm.data.sources import mix01_admission as review
from xlm.data.sources.admission import (
    AdmissionGate,
    load_admission_decision,
    load_probe_evidence,
    next_attempt,
    resolve_verified_production_admission,
    save_admission_decision,
)
from xlm.data.sources.catalog import load_catalog
from xlm.data.sources.mix01 import load_mix01_views
from xlm.data.sources.prober import EvidenceType

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "manifests" / "datasets.catalog.yaml"
VIEWS = REPO / "recipes" / "mixtures" / "mix01_views.yaml"
QUOTAS = REPO / "recipes" / "mixtures" / "mix01_quotas_6b.yaml"
DEFAULT_PROBE_DIR = Path("D:/Project/xlm-operator-ultrax/adapter-cert-ultrax01")


@dataclass(frozen=True)
class SourceSpec:
    """Static Mix-01 facts per source key; every value is re-checked against the pin."""

    source_id: str
    view_id: str
    adapter_id: str
    calibration: str
    source_files: int | None
    certified_probe: bool = False


SOURCES = {
    "common_pile": SourceSpec("common_pile", "common_pile_prose", "common_pile", "component", None),
    "ultrax": SourceSpec(
        "ultrax_ultrafineweb", "UltraX-Ultra-FineWeb", "ultrax_ultrafineweb", "ultrax", 104, True
    ),
    "finepdfs": SourceSpec("finepdfs_edu", "eng_Latn", "finepdfs_en", "finepdfs_en", None),
    "synth": SourceSpec("synth", "default", "synth_en", "synth_en_explanations", 500),
    "wiki_rewrite": SourceSpec(
        "nemotron_specialized",
        "Nemotron-Pretraining-Wiki-Rewrite",
        "wiki_rewrite",
        "nemotron_wiki_rewrite",
        None,
    ),
    "finewiki": SourceSpec("finewiki", "en", "finewiki_en", "finewiki_en", None),
    "ifm_general": SourceSpec("ifm_behaviors", "general", "ifm_general", "ifm_general", None),
    "ifm_planning": SourceSpec("ifm_behaviors", "planning", "ifm_planning", "ifm_planning", None),
    "simple_stories": SourceSpec(
        "simple_stories", "default", "simple_stories", "simple_stories", 7
    ),
}
BLOCKED: dict[str, str] = {}
#: Sources whose repository consolidates independently licensed top-level
#: components. Production sees only an operator allowlist's components; the
#: value is the committed evidence matrix that allowlist is decided against.
ALLOWLIST_REQUIRED = {
    "common_pile": REPO
    / "docs"
    / "implementation"
    / "evidence"
    / "COMMON-PILE-PROSE-ALLOWLIST-AUDIT"
    / "license-matrix.json",
}


class DriverError(RuntimeError):
    """An operator command refused; nothing was written."""


# --------------------------------------------------------------------- inputs


def spec_of(key: str) -> SourceSpec:
    if key in BLOCKED:
        raise DriverError(f"'{key}' is blocked: {BLOCKED[key]}")
    if key not in SOURCES:
        raise DriverError(f"unknown source key '{key}'; known: {sorted(SOURCES)}")
    return SOURCES[key]


def pin_of(spec: SourceSpec) -> ce.SourcePin:
    return ce.resolve_pin(
        load_catalog(CATALOG),
        load_mix01_views(VIEWS),
        spec.source_id,
        spec.view_id,
        spec.adapter_id,
    )


def data_root(args: argparse.Namespace) -> Path:
    value = args.data_root or os.environ.get("XLM_DATA_ROOT")
    if not value:
        raise DriverError("set XLM_DATA_ROOT (scripts/operator_storage.ps1) or --data-root")
    return Path(value)


def roots_of(args: argparse.Namespace) -> runner.Roots:
    spec_of(args.source_key)
    scratch = args.scratch_root or os.environ.get("XLM_SCRATCH_ROOT")
    if not scratch:
        raise DriverError("set XLM_SCRATCH_ROOT (scripts/operator_storage.ps1) or --scratch-root")
    return runner.Roots(data_root(args), Path(scratch), args.source_key)


def store() -> ArtifactStore:
    return ArtifactStore(ArtifactPaths.from_env())


def calibration_files(args: argparse.Namespace, spec: SourceSpec) -> dict[str, Path]:
    calib = data_root(args) / "calib"
    if spec.calibration == "ultrax":
        directory = calib / "ultrax"
        plan, rows = calib / "ultrax_plan.json", calib / "ultrax_rows.evidence.json"
    else:
        directory = calib / spec.calibration
        plan, rows = directory / "plan.json", directory / "rows.evidence.json"
    journals = sorted((directory / "scratch" / "journals").glob("*.progress.json"))
    perfs = sorted((directory / "scratch" / "performance").glob("*.perf.json"))
    if len(journals) != 1 or len(perfs) != 1:
        raise DriverError(f"calibration of '{args.source_key}' needs exactly one journal and perf")
    logs = sorted((directory / "logs").glob("adapt-*.log")) if (directory / "logs").is_dir() else []
    files = {
        "plan": plan,
        "journal": journals[0],
        "perf": perfs[0],
        "rows": rows,
        "records": directory / "raw" / "selected_records.jsonl",
        "summary": directory / "canonical" / "adaptation_summary.json",
    }
    if logs:
        files["adapt_log"] = logs[-1]
    return files


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(value, dict):
        raise DriverError(f"'{path}' is not a JSON object")
    return value


def emit(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


# ------------------------------------------------------------------- evidence


def build_bridge(
    args: argparse.Namespace, spec: SourceSpec, target: ArtifactStore
) -> tuple[ce.SourcePin, dict[str, Any], Any]:
    pin = pin_of(spec)
    if spec.calibration == "component":
        inventory, _, _ = production_inventory(args, pin)
        allowlist = allow.read_json(allowlist_path(args, args.source_key))
        directories = [Path(p) for p in getattr(args, "sample_dir", [])]
        if not directories:
            directories = [
                data_root(args) / "calib" / name
                for name in ("common_pile_cal01", "common_pile_cal02")
            ]
        inputs = [
            p / name for p in directories for name in ("sample-receipt.json", "real-records.jsonl")
        ]
        if any(not p.is_file() for p in inputs):
            raise DriverError(
                "six-component certification needs --sample-dir receipts and "
                "saved rows; run the authorized calibration commands first"
            )
        ce.inputs_outside(inputs, REPO)
        facts = cpe.translate_component_samples(
            pin,
            [
                (
                    p.joinpath("sample-receipt.json").read_bytes(),
                    p.joinpath("real-records.jsonl").read_bytes(),
                )
                for p in directories
            ],
            allowlist=allowlist,
            inventory=inventory,
        )
        facts.probe["component_license_basis"] = cpl.build_basis(allowlist)
        metadata = ce.generic_probe_record(target, pin.source_id, pin.view_id)
        if metadata is None:
            raise DriverError(
                "Common Pile live metadata probe missing; operator must run "
                "python -m xlm.cli.main data probe --live at the catalog pin"
            )
        facts = ce.translate_store_probe(pin, facts, *metadata)
        receipt, evidence_record = ce.build_bridge(
            pin, facts, evidence_type=EvidenceType.REAL_OBSERVED
        )
        return pin, receipt, evidence_record
    files = calibration_files(args, spec)
    probe_dir = Path(args.probe_dir) if getattr(args, "probe_dir", None) else DEFAULT_PROBE_DIR
    inputs = [files["plan"], files["journal"], files["records"], files["summary"]]
    if spec.certified_probe:
        inputs += [probe_dir / "probe_receipt.json", probe_dir / "real-records.jsonl"]
    ce.inputs_outside(inputs, REPO)
    calibration = ce.CalibrationEvidence(
        plan=files["plan"].read_bytes(),
        journal=files["journal"].read_bytes(),
        records=files["records"].read_bytes(),
        summary=files["summary"].read_bytes(),
    )
    if spec.certified_probe:
        facts = ce.translate_ultrax_probe(
            pin,
            (probe_dir / "probe_receipt.json").read_bytes(),
            (probe_dir / "real-records.jsonl").read_bytes(),
        )
        facts = ce.translate_calibration(pin, calibration, facts)
    else:
        facts = ce.translate_calibration(pin, calibration)
    metadata = ce.generic_probe_record(target, pin.source_id, pin.view_id)
    if metadata is None:
        raise DriverError("no earlier real metadata probe of this view is in the store")
    facts = ce.translate_store_probe(pin, facts, *metadata)
    receipt, record = ce.build_bridge(pin, facts, evidence_type=EvidenceType.REAL_OBSERVED)
    return pin, receipt, record


def cmd_evidence(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
    target = store()
    if args.action == "verify":
        pin = pin_of(spec)
        receipt = ce.verify_current(
            target, pin, rebuild=lambda: build_bridge(args, spec, target)[1:]
        )
        print(f"bridge verified: {receipt['digest']} fingerprint {receipt['probe_fingerprint']}")
        return 0
    pin, receipt, record = build_bridge(args, spec, target)
    sets = receipt["adapter"]["row_sets"]
    print(f"source      {pin.source_id}:{pin.view_id} @ {pin.revision}")
    accepted = ", ".join(f"{k} {v['accepted']}/{v['rows']}" for k, v in sets.items())
    print(f"adapter     {pin.adapter_id} accepted {accepted}")
    print(f"schema      {receipt['schema_basis']} fields {sorted(receipt['schema']['fields'])}")
    print(f"files       {[(f['path'], f['length']) for f in receipt['observed_files']]}")
    print(f"license     {receipt['declared_license']} (repository declaration only)")
    print(f"fingerprint {receipt['probe_fingerprint']}")
    print(f"BRIDGE RECEIPT DIGEST {receipt['digest']}")
    if args.action == "publish":
        directory, published = ce.publish_bridge(target, receipt, record)
        print(("published " if published else "already published ") + directory)
    else:
        print("show only: nothing written (use 'evidence publish')")
    return 0


# --------------------------------------------------------------------- review


def registry_notes(spec: SourceSpec) -> list[str]:
    for view in load_mix01_views(VIEWS).views:
        if view.source_id == spec.source_id and spec.view_id in (
            view.observed_configs or [view.component_id]
        ):
            return list(view.notes)
    return []


def stored_receipt(spec: SourceSpec, target: ArtifactStore) -> tuple[ce.SourcePin, dict[str, Any]]:
    pin = pin_of(spec)
    return pin, ce.verify_current(target, pin)


def cmd_review(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
    pin, receipt = stored_receipt(spec, store())
    if args.action == "show":
        emit(review.review_facts(pin, receipt, registry_notes(spec)))
        return 0
    decisions = review.OperatorDecisions(
        operator=args.operator,
        license_decision=args.license_decision,
        provenance_decision=args.provenance_decision,
        benchmark_risk=args.benchmark_risk,
        rationale=args.rationale,
    )
    reviews = review.build_reviews(pin, receipt, decisions, registry_notes(spec))
    hashes = review.write_reviews(Path(args.review_dir), reviews)
    emit({"review_dir": args.review_dir, "sha256": hashes})
    return 0


def current_admission(spec: SourceSpec, target: ArtifactStore) -> dict[str, str]:
    """The admission a plan binds; refuses unless the stored decision admits this bridge."""
    pin, receipt = stored_receipt(spec, target)
    evidence = load_probe_evidence(pin.source_id, pin.view_id, target)
    decision = load_admission_decision(pin.source_id, pin.view_id, target)
    if evidence is None or decision is None:
        raise DriverError("no admission decision is recorded for this view")
    gate = AdmissionGate.evaluate(evidence, decision)
    if not gate.admitted or not review.decision_binds_bridge(decision, receipt):
        raise DriverError("view is not admitted on the current bridge: " + "; ".join(gate.reasons))
    return {
        "probe_fingerprint": str(receipt["probe_fingerprint"]),
        "bridge_receipt_digest": str(receipt["digest"]),
        "decision_contract": decision.contract_version,
        "benchmark_risk": decision.benchmark_risk.value,
    }


def admission_check(
    spec: SourceSpec, target: ArtifactStore, expected: dict[str, str]
) -> Callable[[AcquisitionPlan], None]:
    def check(plan: AcquisitionPlan) -> None:
        resolve_verified_production_admission(plan, target)
        if current_admission(spec, target) != expected:
            raise DriverError("admission changed since this plan was made; plan again")

    return check


def cmd_admit(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
    target = store()
    pin, receipt = stored_receipt(spec, target)
    reviews, hashes = review.read_reviews(Path(args.review_dir))
    evidence = load_probe_evidence(pin.source_id, pin.view_id, target)
    if evidence is None:
        raise DriverError("no probe evidence; publish the bridge first")
    decision = review.build_decision(pin, evidence, receipt, reviews, hashes)
    gate = AdmissionGate.evaluate(evidence, decision)
    if not gate.admitted:
        raise DriverError("gate refuses the decision: " + "; ".join(gate.reasons))
    existing = load_admission_decision(pin.source_id, pin.view_id, target)
    if existing is not None and existing.model_dump(exclude={"decision_timestamp"}) == (
        decision.model_dump(exclude={"decision_timestamp"})
    ):
        print("identical admission already recorded; nothing written")
        return 0
    attempt = next_attempt(target, "admission_decision", f"admission_{pin.source_id}_{pin.view_id}")
    staging = data_root(args) / "temp" / f"admission-{args.source_key}"
    directory = save_admission_decision(decision, target, staging, attempt=attempt)
    print(
        f"admitted {pin.source_id}:{pin.view_id} ({decision.contract_version}, "
        f"{decision.benchmark_risk.value}) -> {directory}"
    )
    return 0


# -------------------------------------------------------------- requirement


def cmd_requirement(args: argparse.Namespace) -> int:
    """OFFLINE: record or show a multi-view component's frozen requirement split."""
    component = str(args.component)
    estimate_path = data_root(args) / "calib" / "headroom_estimate.json"
    quotas = yaml.safe_load(QUOTAS.read_text(encoding="utf-8"))
    estimate = load_json(estimate_path)
    if args.action == "split-show":
        path = split_path(args, component)
        if not path.is_file():
            raise DriverError(f"no requirement split is recorded for '{component}'")
        split = planner.check_view_split(load_json(path), quotas, estimate)
        emit(split)
        return 0
    allocations: dict[str, int] = {}
    for item in str(args.view_tokens or "").split(","):
        if not item.strip():
            continue
        if "=" not in item:
            raise DriverError("allocations read as VIEW=TOKENS,... e.g. general=165M,planning=165M")
        view, raw = item.split("=", 1)
        view, raw = view.strip(), raw.strip()
        if not view or not raw.isdigit():
            raise DriverError("allocations read as VIEW=TOKENS,... with positive integer tokens")
        allocations[view] = int(raw)
    try:
        record = planner.build_view_split(
            component_id=component,
            quotas=quotas,
            estimate=estimate,
            quotas_sha256=sha256(QUOTAS),
            estimate_sha256=sha256(estimate_path),
            view_tokens=allocations,
            operator=str(args.operator or ""),
            rationale=str(args.rationale or ""),
        )
    except planner.PlanError as exc:
        raise DriverError(str(exc)) from exc
    path = split_path(args, component)
    if path.is_file():
        existing = load_json(path)
        if existing == record:
            print(f"identical split already recorded; nothing written -> {path}")
            return 0
        raise DriverError(
            f"a different split is already recorded at {path}; refusing to overwrite it"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"requirement split of {component}: {record['digest']} -> {path}")
    for view, entry in sorted(record["views"].items()):
        print(
            f"  {view}: first-pass {entry['first_pass_tokens']:,} "
            f"(final {entry['final_tokens']:,}, canonical {entry['required_canonical_bytes']:,})"
        )
    return 0


# ------------------------------------------------------------------ allowlist


def allowlist_path(args: argparse.Namespace, source_key: str) -> Path:
    """Write-once home of a source's operator component allowlist."""
    if getattr(args, "allowlist", None):
        return Path(str(args.allowlist))
    return data_root(args) / "calib" / "component_allowlists" / f"{source_key}.json"


def discovery_listing(args: argparse.Namespace, source_key: str) -> dict[str, Any]:
    """The frozen full-repository listing an allowlist is decided against."""
    explicit = getattr(args, "listing", None)
    path = (
        Path(str(explicit))
        if explicit
        else data_root(args) / "inventories" / f"{source_key}.discovery.listing.json"
    )
    try:
        return hf_inventory.read_listing(path)
    except hf_inventory.HfInventoryError as exc:
        raise DriverError(str(exc)) from exc


def names_of(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def cmd_allowlist(args: argparse.Namespace) -> int:
    """OFFLINE: preview, record or show a source's write-once component allowlist."""
    key = str(args.source_key)
    if key not in ALLOWLIST_REQUIRED:
        raise DriverError(
            f"'{key}' takes no component allowlist; known: {sorted(ALLOWLIST_REQUIRED)}"
        )
    views = [v for v in load_mix01_views(VIEWS).views if v.source_id == key]
    if len(views) != 1:
        raise DriverError(f"'{key}' needs exactly one registry view to bind an allowlist")
    view = views[0]
    listing = discovery_listing(args, key)
    if (listing["source_id"], listing["repository"], listing["resolved_revision"]) != (
        key,
        view.repository,
        view.observed_revision,
    ):
        raise DriverError("the discovery listing is not the registry's pinned source revision")
    path = allowlist_path(args, key)
    try:
        if args.action == "show":
            if not path.is_file():
                raise DriverError(f"no component allowlist is recorded for '{key}' -> {path}")
            emit(allow.check_allowlist(allow.read_json(path), listing))
            return 0
        matrix_path = (
            Path(args.evidence_matrix) if args.evidence_matrix else ALLOWLIST_REQUIRED[key]
        )
        record = allow.build_allowlist(
            source_key=key,
            component_id=view.component_id,
            listing=listing,
            matrix=allow.read_json(matrix_path),
            matrix_sha256=allow.file_sha256(matrix_path),
            included=names_of(args.include),
            accepted_flags=names_of(args.accept_flagged),
            operator=str(args.operator or ""),
            rationale=str(args.rationale or ""),
        )
        if args.action == "preview":
            emit(record)
            print("preview only: nothing written (use 'allowlist record')")
            return 0
        written = allow.write_once(path, record)
    except allow.AllowlistError as exc:
        raise DriverError(str(exc)) from exc
    state = "recorded" if written else "identical allowlist already recorded; nothing written"
    print(f"component allowlist of {key}: {state} -> {path}")
    for name in record["included"]:
        entry = record["evidence"][name]
        print(
            f"  include {name}: {record['universe'][name]['bytes']:,} B in "
            f"{record['universe'][name]['files']} files ({entry['license_class']}, "
            f"{entry['content_fit']}, {entry['provenance_confidence']})"
        )
    print(f"  exclude {len(record['excluded'])} components: {', '.join(record['excluded'])}")
    print(f"ALLOWLIST DIGEST {record['digest']}")
    return 0


def allowlist_gate(
    args: argparse.Namespace, pin: ce.SourcePin, inventory: dict[str, Any]
) -> str | None:
    """Refuse a production inventory that is not its source's allowlisted inventory."""
    if pin.source_id not in ALLOWLIST_REQUIRED:
        if "component_allowlist" in inventory:
            raise DriverError(
                f"inventory binds a component allowlist, but '{pin.source_id}' takes none"
            )
        return None
    path = allowlist_path(args, args.source_key)
    if not path.is_file():
        raise DriverError(
            f"'{pin.source_id}' production needs the operator component allowlist: "
            f"record it with 'allowlist record' -> {path}"
        )
    listing = discovery_listing(args, args.source_key)
    try:
        record = allow.check_allowlist(allow.read_json(path), listing)
        if (record["repository"], record["revision"], record["component_id"]) != (
            pin.repository,
            pin.revision,
            pin.component_id,
        ):
            raise DriverError("the component allowlist is not bound to this source pin")
        allow.check_inventory_binding(record, listing, inventory)
    except allow.AllowlistError as exc:
        raise DriverError(str(exc)) from exc
    return str(record["digest"])


def production_inventory(
    args: argparse.Namespace, pin: ce.SourcePin
) -> tuple[dict[str, Any], Path, str | None]:
    """The source's production inventory after the component-allowlist gate."""
    path = data_root(args) / "inventories" / f"{args.source_key}.inventory.json"
    inventory = load_json(path)
    return inventory, path, allowlist_gate(args, pin, inventory)


# --------------------------------------------------------------------- policy


def layout_of(
    args: argparse.Namespace, spec: SourceSpec
) -> tuple[policy.SourceLayout, dict[str, Any]]:
    if spec.calibration == "component":
        record, path = component_calibration(args, spec)
        names = {"component_calibration": sha256(path)}
        return cc.layout_of(record, record_sha256=sha256(path)), {
            "perf": record["observed_transfer"],
            "adapt_rate": FALLBACK_ADAPT_ROWS_PER_SECOND,
            "evidence": names,
        }
    files = calibration_files(args, spec)
    measurement = load_json(data_root(args) / "calib" / "calibration.json")["sources"][
        spec.calibration if spec.calibration != "ultrax" else "ultrax_ultrafineweb"
    ]
    perf = load_json(files["perf"])
    names = {name: sha256(path) for name, path in sorted(files.items())}
    layout = policy.layout_from_calibration(
        spec.source_id,
        load_json(files["rows"]),
        perf,
        load_json(files["journal"]),
        measurement,
        source_files=spec.source_files,
        evidence_names=names,
    )
    rate = (
        policy.adapt_rate_from_log(files["adapt_log"].read_text(encoding="utf-8-sig"))
        if "adapt_log" in files
        else None
    )
    return layout, {"perf": perf, "adapt_rate": rate, "evidence": names}


def component_calibration(
    args: argparse.Namespace, spec: SourceSpec
) -> tuple[dict[str, Any], Path]:
    pin = pin_of(spec)
    inventory, _, _ = production_inventory(args, pin)
    allowlist = allow.read_json(allowlist_path(args, args.source_key))
    cpl.build_basis(allowlist)
    path = data_root(args) / "calib" / pin.component_id / "component-calibration.json"
    if not path.is_file():
        raise DriverError(
            f"component calibration missing: {path}; authorize the twelve "
            "bounded prefix samples, then run scripts/component_calibration.py build"
        )
    return cc.check_calibration(load_json(path), allowlist=allowlist, inventory=inventory), path


def component_ready(args: argparse.Namespace, spec: SourceSpec) -> dict[str, Any]:
    record, path = component_calibration(args, spec)
    bounds_path = path.with_name("reviewed-bounds.json")
    if not bounds_path.is_file():
        raise DriverError(
            f"reviewed source bounds missing: {bounds_path}; "
            "review calibration then use component-bounds record"
        )
    bounds = cp.check_bounds(load_json(bounds_path), record)
    split_path = path.with_name("component-split.json")
    if not split_path.is_file():
        raise DriverError(
            "Common Pile component allocation decision missing: compare A/B/C "
            "and use component-split record; no allocation is chosen by software"
        )
    split = cp.check_split(load_json(split_path), record)
    # Rebuilding must reproduce the published bridge; merely accepting a newer
    # valid sample would leave an older operator review silently in force.
    target = store()
    ce.verify_current(target, pin_of(spec), rebuild=lambda: build_bridge(args, spec, target)[1:])
    current_admission(spec, target)
    return {"calibration": record, "reviewed_bounds": bounds, "component_split": split}


def cmd_component_policy(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
    if spec.calibration != "component":
        raise DriverError("component bounds/split applies only to component-calibrated sources")
    calibration, path = component_calibration(args, spec)
    is_bounds = args.command == "component-bounds"
    target = path.with_name("reviewed-bounds.json" if is_bounds else "component-split.json")
    checker = cp.check_bounds if is_bounds else cp.check_split
    if args.action == "show":
        record = checker(load_json(target), calibration)
    else:
        if not args.input:
            raise DriverError("preview/record needs --input with reviewed ceilings or token shares")
        builder = cp.build_bounds if is_bounds else cp.build_split
        record = builder(
            calibration,
            load_json(Path(args.input)),
            operator=args.operator,
            rationale=args.rationale,
        )
        if args.action == "record":
            runner.write_once(target, record)
    emit(record)
    return 0


def split_path(args: argparse.Namespace, component_id: str) -> Path:
    """Write-once home of a component's frozen per-view requirement split."""
    if getattr(args, "requirement_split", None):
        return Path(str(args.requirement_split))
    return data_root(args) / "calib" / "requirement_splits" / f"{component_id}.json"


def requirement_of(args: argparse.Namespace, spec: SourceSpec) -> planner.Requirement:
    component = pin_of(spec).component_id
    estimate_path = data_root(args) / "calib" / "headroom_estimate.json"
    quotas = yaml.safe_load(QUOTAS.read_text(encoding="utf-8"))
    estimate = load_json(estimate_path)
    if len([s for s in SOURCES.values() if s.source_id == spec.source_id]) > 1:
        path = split_path(args, component)
        if not path.is_file():
            raise DriverError(
                "a multi-view component needs an explicit per-view requirement decision: "
                f"record it with 'requirement split-record' -> {path}"
            )
        split = load_json(path)
        try:
            return planner.requirement_from_split(
                split, spec.view_id, quotas=quotas, estimate=estimate
            )
        except planner.PlanError as exc:
            raise DriverError(str(exc)) from exc
    return planner.requirement_from(
        component,
        quotas,
        estimate,
        quotas_sha256=sha256(QUOTAS),
        estimate_sha256=sha256(estimate_path),
    )


#: Slowest single-process adapt rate measured on any Mix-01 calibration (FinePDFs),
#: used only where a source's own adapt timing was not retained.
FALLBACK_ADAPT_ROWS_PER_SECOND = 5814.0


@dataclass(frozen=True)
class Measured:
    """Everything a measured policy binds: receipts or structural audits, never guesses."""

    models: dict[policy.TransportMode, policy.ThroughputModel]
    inputs: dict[str, str]
    sizing: dict[str, Any]
    dispositions: list[dict[str, str]]


def measured_basis(args: argparse.Namespace, spec: SourceSpec) -> Measured:
    """The whole-file receipt plus exactly one range receipt or range reach audit."""
    if not args.whole_receipt:
        raise DriverError("a measured policy needs --whole-receipt")
    if bool(args.range_receipt) == bool(args.range_reach):
        raise DriverError("a measured policy needs exactly one of --range-receipt/--range-reach")
    pin = pin_of(spec).as_dict()
    whole_path = Path(args.whole_receipt)
    whole = load_json(whole_path)
    runner.check_digest(whole, "whole-file benchmark receipt")
    if whole.get("outcome", {}).get("status") != "completed":
        raise DriverError("whole-file benchmark did not complete")
    inputs = {"whole": sha256(whole_path)}
    sizing = planner.sizing_from_receipt(whole, pin, receipt_sha256=inputs["whole"])
    local = policy.measured_model(whole)
    models = {
        policy.TransportMode.WHOLE_FILE_LOCAL: local,
        policy.TransportMode.SMALL_SOURCE_DIRECT: local,
    }
    dispositions: list[dict[str, str]] = []
    if args.range_receipt:
        ranged = load_json(Path(args.range_receipt))
        runner.check_digest(ranged, "range benchmark receipt")
        models[policy.TransportMode.RANGE_SELECTED] = policy.measured_model(ranged)
        inputs["range"] = sha256(Path(args.range_receipt))
    else:
        path = Path(args.range_reach)
        audit = reach.check_reach(load_json(path), pin, sizing["files"])
        inputs["range_reach"] = sha256(path)
        dispositions.append(reach.disposition_of(audit, inputs["range_reach"]))
    return Measured(models, inputs, sizing, dispositions)


def evaluate_policy(
    args: argparse.Namespace, spec: SourceSpec, measured: Measured | None = None
) -> dict[str, Any]:
    layout, extra = layout_of(args, spec)
    req = requirement_of(args, spec)
    requirement = policy.Requirement(
        spec.source_id, req.required_canonical_bytes, req.safety_margin
    )
    if measured is not None:
        # The whole-file measurement, not the small calibration, sizes the workloads.
        layout = planner.sized_layout(layout, measured.sizing)
        models = measured.models
    elif spec.calibration == "component":
        perf = extra["perf"]
        if perf["seconds"] <= 0:
            raise DriverError("component calibration has no positive observed transfer duration")
        rate = perf["transferred_bytes"] / perf["seconds"]
        model = policy.ThroughputModel(
            streams=1,
            per_stream_bytes_per_second=rate,
            aggregate_bytes_per_second=rate,
            seconds_per_request=0,
            rows_per_process_second=FALLBACK_ADAPT_ROWS_PER_SECOND,
            processes=1,
            basis="observed_transfer prefix rate; fallback adaptation rate; estimate",
        )
        models = {mode: model for mode in planner.JSONL_GZ_MODES}
    else:
        rate = extra["adapt_rate"] or FALLBACK_ADAPT_ROWS_PER_SECOND
        models = policy.modeled_models(
            policy.request_latency(extra["perf"]),
            rate,
            aggregate_bytes_per_second=args.aggregate_mbps * policy.MB
            if args.aggregate_mbps
            else None,
        )
    ceilings = policy.Ceilings(
        scratch_bytes=planner.SCRATCH_CAP_BYTES, durable_bytes=args.durable_budget_bytes
    )
    if spec.calibration == "component":
        models = {mode: model for mode, model in models.items() if mode in planner.JSONL_GZ_MODES}
    return policy.evaluate(layout, requirement, ceilings, models)


def cmd_policy(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
    component = None
    if spec.calibration == "component" and args.action == "freeze":
        component = component_ready(args, spec)
    measured = measured_basis(args, spec) if args.basis == "measured" else None
    report = evaluate_policy(args, spec, measured)
    emit(policy.summarize([report]))
    if measured is not None:
        derived = measured.sizing["derived"]
        print(
            f"SIZING {measured.sizing['contract']} digest {measured.sizing['digest']}: "
            f"{derived['rows_per_file']:,} rows/file, "
            f"{derived['canonical_bytes_per_row']:.1f} canonical B/row, "
            f"accepted {derived['accepted_fraction']:.6f}"
        )
        for item in measured.dispositions:
            print(f"DISPOSITION {item['mode']} {item['status']} ({item['contract']})")
    if args.action == "freeze":
        if measured is not None:
            pin = pin_of(spec).as_dict()
            record = policy.freeze(
                report,
                basis="measured",
                inputs=measured.inputs,
                subject={key: pin[key] for key in policy.SUBJECT_KEYS},
                sizing=measured.sizing,
                dispositions=measured.dispositions,
            )
        else:
            inputs = {
                "model": "named prior measurements (transport_policy.ESSENTIAL_WEB_MEASUREMENTS)"
            }
            if component is not None:
                inputs = {
                    key: component[key]["digest"]
                    for key in ("calibration", "reviewed_bounds", "component_split")
                }
                inputs["model"] = "observed prefix transfer; fallback adaptation rate; estimate"
            record = policy.freeze(report, basis=args.basis, inputs=inputs)
        roots = roots_of(args)
        runner.write_once(roots.plans / "transport-policy.json", record)
        print(
            f"TRANSPORT POLICY {record['selected_mode']} ({args.basis}) digest {record['digest']}"
        )
    return 0


# --------------------------------------------------------------------- plans


def predecessor(roots: runner.Roots) -> planner.Predecessor | None:
    sequences = roots.sequences()
    if not sequences:
        return None
    record = runner.load_plan(roots, sequences[-1])
    totals = runner.account(roots, record)
    status = runner.sufficiency(roots)
    if status["status"] != "TOP_UP":
        raise DriverError(f"latest plan state is {status['status']}: {status['action']}")
    return planner.Predecessor(
        plan=record,
        sealed_canonical_bytes=int(totals["canonical_bytes"]),
        sealed_files=int(totals["units_sealed"]),
        accounting_digest=str(totals["digest"]),
    )


def superseded_of(roots: runner.Roots, sequence: int) -> planner.Superseded:
    """The latest plan, provided nothing of it exists beyond its plan record."""
    sequences = roots.sequences()
    if not sequences or sequence != sequences[-1]:
        raise DriverError("only the latest plan can be superseded")
    record = runner.load_plan(roots, sequence)
    others = sorted(p.name for p in roots.plan_dir(sequence).iterdir() if p.name != "plan.json")
    if others:
        raise DriverError(
            f"plan {sequence} holds {others}: an authorized or run plan is resumed or repaired, "
            "never superseded"
        )
    label = f"p{sequence:02d}"
    for place in (roots.canonical / label, roots.staging(label), roots.scratch(label)):
        if place.exists() and any(place.iterdir()):
            raise DriverError(f"plan {sequence} left work in {place}; it cannot be superseded")
    return planner.Superseded(plan=record)


def cmd_plan(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
    component = component_ready(args, spec) if spec.calibration == "component" else None
    roots = roots_of(args)
    target = store()
    pin = pin_of(spec)
    frozen = runner.read_json(roots.plans / "transport-policy.json")
    inventory, inventory_path, allowlist_digest = production_inventory(args, pin)
    layout, extra = layout_of(args, spec)
    superseded = superseded_of(roots, args.plan) if args.command == "plan-supersede" else None
    component_acquired: dict[str, int] = {}
    if component is not None:
        for seq in roots.sequences():
            for receipt in runner.resume_state(roots, runner.load_plan(roots, seq))["receipts"]:
                name = allow.component_of(receipt["file"])
                component_acquired[name] = (
                    component_acquired.get(name, 0) + receipt["canonical_bytes"]
                )
    record = planner.build_plan(
        source_key=args.source_key,
        pin=pin.as_dict(),
        requirement=requirement_of(args, spec),
        inventory=inventory,
        inventory_sha256=sha256(inventory_path),
        layout=layout,
        calibration=extra["evidence"],
        policy=frozen,
        admission=current_admission(spec, target),
        predecessor=None if superseded is not None else predecessor(roots),
        superseded=superseded,
        component_policy=component,
        component_acquired=component_acquired if component else None,
    )
    path = runner.store_plan(roots, record)
    supersedes = record.get("supersedes")
    if supersedes is not None:
        print(
            f"supersedes plan     {supersedes['plan_sequence']} {supersedes['plan_digest']} "
            "(never authorized or run; kept unchanged; can no longer be authorized)"
        )
        print(f"changed sections    {supersedes['changed_sections']}")
        for key, change in supersedes["changed_limits"].items():
            print(f"changed limit       {key}: {change['from']} -> {change['to']}")
        for key, change in supersedes["changed_acquisition_limits"].items():
            print(f"changed plan limit  {key}: {change['from']} -> {change['to']}")
    limits = record["limits"]
    acq = record["acquisition_plan"]["limits"]
    selection = record["selection"]
    files = len(selection["files"])
    print(f"plan {record['sequence']} of {args.source_key}: {path}")
    print(f"transport mode      {record['transport_mode']} ({frozen['basis']} policy)")
    print(f"policy digest       {frozen['digest']}")
    if allowlist_digest is not None:
        print(f"component allowlist {allowlist_digest} (bound by the inventory digest)")
    sizing = record["inputs"].get("sizing")
    print(
        "sizing basis        "
        + (
            f"whole-file measurement {sizing['digest']} (receipt {sizing['receipt_digest']})"
            if sizing
            else "calibration estimate"
        )
    )
    print(
        f"inventory ranks     [{selection['start_rank']}, {selection['stop_rank']}) of "
        f"{record['inventory']['file_count']} ({len(selection['files'])} whole files); "
        f"next top-up cursor {selection['next_cursor']}"
    )
    print(
        f"expected            {record['expected']['transfer_bytes']:,} B transfer, "
        f"{record['expected']['requests']} requests, {record['expected']['rows']:,} rows, "
        f"{record['expected']['estimated_tokens']:,} est. tokens"
    )
    print(f"transfer ceiling    {acq['max_transferred_bytes']:,} B")
    print(f"request ceiling     {acq['max_requests']:,}")
    print(
        f"per-file ceiling    {limits['max_file_bytes']:,} B, {limits['max_rows_per_file']:,} rows"
    )
    print_anchor(limits)
    keep_free = limits["scratch_min_free_bytes"]
    print(f"scratch ceiling     {limits['scratch_cap_bytes']:,} B (+{keep_free:,} B kept free)")
    print(f"durable ceiling     {acq['max_output_disk_bytes']:,} B (raw + canonical)")
    print(f"canonical ceiling   {limits['max_canonical_bytes_per_file'] * files:,} B")
    streams, processes = limits["download_workers"], limits["process_workers"]
    print(f"concurrency         {streams} streams, {processes} processes")
    parallel = limits.get("row_group_parallel")
    if parallel is not None:
        print(
            f"intra-file          {parallel['workers']} row-group workers + 1 coordinator per "
            f"file (lookahead {parallel['lookahead']}); {processes} x "
            f"{parallel['workers'] + 1} <= {parallel['processing_slots']} slots; "
            f"{parallel['memory_bytes']:,} B sampled memory ceiling per file"
        )
    print(f"PLAN DIGEST: {record['digest']}")
    print("STOP - USER MUST REVIEW PLAN DIGEST BEFORE AUTHORIZATION")
    return 0


def print_anchor(limits: dict[str, Any]) -> None:
    anchor = limits.get("file_size_anchor")
    if anchor is not None:
        print(
            f"size anchor         largest selected file {anchor['largest_selected_file_bytes']:,} B"
            f" > estimate ceiling {anchor['estimate_max_file_bytes']:,} B "
            f"(calibration file {anchor['calibration_file_bytes']:,} B)"
        )
    basis = limits.get("file_bounds_basis")
    if basis is not None:
        print(f"file bounds basis   {basis.split(':', 1)[0]}")


def repaired_of(roots: runner.Roots, sequence: int) -> planner.Repaired:
    """The authorized latest plan, its sealed outcome, failed runs and retained sources."""
    sequences = roots.sequences()
    if not sequences or sequence != sequences[-1]:
        raise DriverError("only the latest plan can be repaired")
    record = runner.load_plan(roots, sequence)
    directory = roots.plan_dir(sequence)
    if not (directory / "authorization.json").is_file():
        raise DriverError("an unauthorized plan is planned again, not repaired")
    resume = runner.resume_state(roots, record)
    totals = runner.account(roots, record)
    failures: list[dict[str, Any]] = []
    for path in sorted(directory.glob("performance-*.json")):
        receipt = runner.read_json(path)
        runner.check_digest(receipt, path.name)
        if receipt["plan"]["digest"] != record["digest"]:
            raise DriverError(f"{path.name} belongs to another plan")
        if receipt["outcome"]["status"] == "failed":
            failures.append(
                {
                    "receipt": path.name,
                    "digest": receipt["digest"],
                    "root_failure": receipt["outcome"]["root_failure"],
                }
            )
    retained: dict[str, str] = {}
    pin = record["source"]
    for entry in resume["remaining"]:
        # Re-hashes the whole retained file against its identity sidecar.
        source = load_durable(roots.raw_path(str(entry["file"])), str(entry["file"]))
        if source is None:
            continue
        if (source["source_file"], source["repository"], source["revision"]) != (
            entry["file"],
            pin["repository"],
            pin["revision"],
        ):
            raise DriverError(f"retained source identity differs for {entry['file']}")
        retained[str(entry["file"])] = str(source["sha256"])
    return planner.Repaired(
        plan=record,
        authorized_digest=str(runner.read_json(directory / "authorization.json")["plan_digest"]),
        sealed_ranks=tuple(int(r["rank"]) for r in resume["receipts"]),
        sealed_canonical_bytes=int(totals["canonical_bytes"]),
        accounting_digest=str(totals["digest"]),
        failures=tuple(failures),
        retained=retained,
    )


def cmd_plan_repair(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
    component = component_ready(args, spec) if spec.calibration == "component" else None
    roots = roots_of(args)
    target = store()
    pin = pin_of(spec)
    frozen = runner.read_json(roots.plans / "transport-policy.json")
    inventory, inventory_path, _ = production_inventory(args, pin)
    layout, extra = layout_of(args, spec)
    repaired = repaired_of(roots, args.plan)
    record = planner.build_repair_plan(
        source_key=args.source_key,
        pin=pin.as_dict(),
        requirement=requirement_of(args, spec),
        inventory=inventory,
        inventory_sha256=sha256(inventory_path),
        layout=layout,
        calibration=extra["evidence"],
        policy=frozen,
        admission=current_admission(spec, target),
        repaired=repaired,
        component_policy=component,
    )
    path = runner.store_plan(roots, record)
    repair, limits = record["repair"], record["limits"]
    print(f"repair plan {record['sequence']} of {args.source_key}: {path}")
    print(f"repairs plan        {repair['plan_sequence']} {repair['plan_digest']}")
    print(f"kept sealed ranks   {repair['sealed_ranks']} (their receipts are not touched)")
    files = [entry["file"] for entry in record["selection"]["files"]]
    print(f"re-planned ranks    {repair['ranks']}: {files}")
    for failure in repair["failures"]:
        root = failure["root_failure"]
        print(f"failed run          {failure['receipt']} {failure['digest']} {root}")
    for key, change in repair["changed_limits"].items():
        print(f"changed limit       {key}: {change['from']} -> {change['to']}")
    # An admission repair: the repaired plan's admission (e.g. its bridge after a
    # versioned adapter contract) no longer verifies, so that plan cannot run.
    for key, change in repair.get("changed_admission", {}).items():
        print(f"changed admission   {key}: {change['from']} -> {change['to']}")
    # Plan-wide ceilings follow from the per-unit ones and the remaining file count;
    # both plans bind theirs, so these lines only display the difference.
    prior = runner.load_plan(roots, int(repair["plan_sequence"]))
    for key, change in planner.limit_diff(prior["limits"], limits).items():
        if key not in repair["changed_limits"]:
            print(f"plan-wide limit     {key}: {change['from']} -> {change['to']}")
    acquisition = planner.limit_diff(
        prior["acquisition_plan"]["limits"], record["acquisition_plan"]["limits"]
    )
    for key, change in acquisition.items():
        print(f"acquisition limit   {key}: {change['from']} -> {change['to']}")
    print_anchor(limits)
    for name, digest in repair["retained_sha256"].items():
        print(f"retained source     {name} sha256 {digest} (no download)")
    print(f"next top-up cursor  {record['selection']['next_cursor']} (unchanged)")
    print(
        f"expected            {record['expected']['transfer_bytes']:,} B transfer, "
        f"{record['expected']['rows']:,} rows, "
        f"{record['expected']['estimated_tokens']:,} est. tokens"
    )
    basis = limits.get("max_record_bytes_basis")
    print(f"record bound        {limits['max_record_bytes']:,} B ({basis})")
    print(f"parser bound        {limits['max_parser_bytes']:,} B")
    parallel = limits.get("row_group_parallel")
    if parallel is not None:
        print(
            f"intra-file          {parallel['workers']} row-group workers, lookahead "
            f"{parallel['lookahead']}, {parallel['memory_bytes']:,} B sampled memory ceiling"
        )
    print(f"PLAN DIGEST: {record['digest']}")
    print("STOP - USER MUST REVIEW PLAN DIGEST BEFORE AUTHORIZATION")
    return 0


def cmd_authorize(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
    roots = roots_of(args)
    record = runner.load_plan(roots, args.plan)
    if spec.calibration == "component":
        current = component_ready(args, spec)
        for key in ("component_split", "reviewed_bounds"):
            if record["inputs"].get(key) != current[key]:
                raise DriverError(f"Common Pile {key} changed after planning")
    # A plan whose own file bound excludes a known selected size can only fail closed.
    inventory, _, _ = production_inventory(args, pin_of(spec))
    planner.check_selected_file_bounds(record, inventory)
    check = admission_check(spec, store(), dict(record["inputs"]["admission"]))
    plan = runner.authorize(roots, args.plan, args.digest, args.operator, check)
    print(f"authorized plan {args.plan}: acquisition plan {plan.plan_hash}")
    return 0


def cmd_resume_check(args: argparse.Namespace) -> int:
    roots = roots_of(args)
    record = runner.load_plan(roots, args.plan)
    resume = runner.resume_state(roots, record)
    restart = runner.classify(roots, record, resume, f"p{args.plan:02d}")
    emit(
        {
            "plan_digest": record["digest"],
            **{k: v for k, v in resume.items() if k != "receipts"},
            "restart": restart,
        }
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
    roots = roots_of(args)
    record = runner.load_plan(roots, args.plan)
    if spec.calibration == "component":
        current = component_ready(args, spec)
        for key in ("component_split", "reviewed_bounds"):
            if record["inputs"].get(key) != current[key]:
                raise DriverError(f"Common Pile {key} changed after planning")
    prior = sum(
        int(runner.account(roots, runner.load_plan(roots, s))["canonical_bytes"])
        for s in roots.sequences()
        if s != args.plan
    )
    report = runner.run_plan(
        roots,
        args.plan,
        admitted=admission_check(spec, store(), dict(record["inputs"]["admission"])),
        download_workers=args.download_workers,
        process_workers=args.process_workers,
        target=int(record["requirement"]["required_canonical_bytes"]),
        prior_canonical=prior,
        offline=args.offline,
    )
    transfer = report["transfer"]
    print(
        f"run {report['outcome']['status']}: {transfer['transferred_bytes']:,} B, "
        f"{transfer['requests']} requests, {transfer['retries']} retries, "
        f"{report['processing']['rows']:,} rows; receipt {report['digest']}"
    )
    emit(runner.sufficiency(roots))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    roots = roots_of(args)
    plans = []
    for sequence in roots.sequences():
        record = runner.load_plan(roots, sequence)
        resume = runner.resume_state(roots, record)
        plans.append(
            {
                "plan": sequence,
                "digest": record["digest"],
                "authorized": (roots.plan_dir(sequence) / "authorization.json").is_file(),
                **(
                    {"superseded_by": sequence + 1}
                    if runner.superseding(roots, sequence) is not None
                    else {}
                ),
                "accounting": runner.account(roots, record),
                "restart": runner.classify(roots, record, resume, f"p{sequence:02d}")["counts"],
            }
        )
    emit(
        {
            "source_key": args.source_key,
            "plans": plans,
            "sufficiency": runner.sufficiency(roots) if plans else None,
        }
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    roots = roots_of(args)
    emit(
        runner.verify_plan(roots, runner.load_plan(roots, args.plan), content=not args.skip_content)
    )
    return 0


def cmd_sufficiency(args: argparse.Namespace) -> int:
    roots = roots_of(args)
    status = runner.sufficiency(roots)
    runner.write_json(roots.plans / "sufficiency.json", status)
    emit(status)
    return 0


def cmd_seal(args: argparse.Namespace) -> int:
    roots = roots_of(args)
    seal = runner.first_pass_seal(roots, content=not args.skip_content)
    acquired = seal["sufficiency"]["acquired_canonical_bytes"]
    print(f"first-pass seal of {args.source_key}: {seal['digest']}")
    print(f"{len(seal['units'])} units, {acquired:,} canonical bytes")
    return 0


# ------------------------------------------------------------------ benchmark


def cmd_benchmark(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
    component = component_ready(args, spec) if spec.calibration == "component" else None
    if component and args.action in ("range-reach", "record-range"):
        raise DriverError("Common Pile jsonl.gz has no Parquet range benchmark mode")
    roots = roots_of(args)
    target = store()
    if args.action == "plan":
        pin = pin_of(spec)
        layout, _ = layout_of(args, spec)
        if args.file:
            if pin.source_id in ALLOWLIST_REQUIRED:
                inventory, _, _ = production_inventory(args, pin)
                if args.file not in {str(e["file"]) for e in inventory["files"]}:
                    raise DriverError("a named benchmark file must be in the allowlisted inventory")
            entries = [{"rank": None, "file": args.file, "reason": "named calibration file"}]
            seed = 0
        else:
            inventory, _, _ = production_inventory(args, pin)
            ordered = planner.check_inventory(
                inventory, pin.source_id, pin.repository, pin.revision
            )
            entries = bench.reserved_entries(
                ordered, planner.BENCHMARK_RESERVED_POSITIONS, args.files
            )
            seed = int(inventory["seed"])
        record = bench.build_benchmark(
            source_key=args.source_key,
            label=args.label,
            pin=pin.as_dict(),
            entries=entries,
            layout=layout,
            seed=seed,
            admission=current_admission(spec, target),
            download_workers=args.download_workers,
            process_workers=args.process_workers,
            retained_scratch_bytes=ObservedScratch(roots.scratch(), 1, 0).occupied(),
            reviewed_bounds=component["reviewed_bounds"] if component else None,
        )
        path = bench.store_benchmark(roots, record)
        print(f"benchmark {args.label}: {[e['file'] for e in entries]} -> {path}")
        expected_run = record["expected"]
        print(f"expected {expected_run['transfer_bytes']:,} B, {expected_run['requests']} requests")
        print(f"BENCHMARK DIGEST: {record['digest']}")
        print("STOP - USER MUST REVIEW BENCHMARK DIGEST BEFORE AUTHORIZATION")
        return 0
    record = runner.read_json(bench.benchmark_dir(roots, args.label) / "benchmark.json")
    if (
        component
        and record["limits"].get("reviewed_bounds_digest") != component["reviewed_bounds"]["digest"]
    ):
        raise DriverError("Common Pile benchmark reviewed bounds changed after planning")
    expected = dict(record["inputs"]["admission"])
    if args.action == "authorize":
        bench.authorize_benchmark(
            roots, args.label, args.digest, args.operator, admission_check(spec, target, expected)
        )
        print(f"authorized benchmark {args.label}")
        return 0
    if args.action == "adopt":
        adoption = bench.adopt_benchmark_download(roots, args.label, args.donor)
        for item in adoption["files"]:
            print(f"adopted {item['file']} ({item['length']:,} B, sha256 {item['sha256']})")
        print(f"adoption {adoption['digest']}: no bytes transferred")
        return 0
    if args.action == "run":
        receipt = bench.run_benchmark(
            roots,
            args.label,
            admitted=admission_check(spec, target, expected),
            offline=args.offline,
        )
        print(f"benchmark receipt {receipt['digest']}: {json.dumps(receipt['transfer'])}")
        return 0
    if args.action == "range-reach":
        return range_reach(args, spec, roots, record)
    receipt = bench.range_benchmark_receipt(
        source_key=args.source_key,
        pin=pin_of(spec).as_dict(),
        plan=load_json(Path(args.range_plan)),
        perf=load_json(Path(args.range_perf)),
        journal=load_json(Path(args.range_journal)),
        documents=Path(args.range_documents),
        # PowerShell Out-File -Encoding utf8 writes a BOM.
        adapt_log=Path(args.adapt_log).read_text(encoding="utf-8-sig"),
    )
    runner.write_once(bench.benchmark_dir(roots, args.label) / "range-receipt.json", receipt)
    print(f"range benchmark receipt {receipt['digest']}")
    return 0


def file_sha256(path: Path) -> tuple[str, int]:
    """Streaming SHA-256 and length of a large local file."""
    digest, length = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
            length += len(chunk)
    return digest.hexdigest(), length


def range_reach(
    args: argparse.Namespace, spec: SourceSpec, roots: runner.Roots, record: dict[str, Any]
) -> int:
    """OFFLINE: footer-only range-v1 reach audit of one verified benchmark file."""
    runner.check_digest(record, "benchmark plan")
    pin = pin_of(spec).as_dict()
    if any(record["source"].get(key) != pin[key] for key in policy.SUBJECT_KEYS):
        raise DriverError("benchmark belongs to another source view, repository or revision")
    names = [str(entry["file"]) for entry in record["files"]]
    name = args.file or (names[0] if len(names) == 1 else "")
    if name not in names:
        raise DriverError(f"--file must name one benchmark file: {names}")
    if not args.local_file or not args.expected_sha256:
        raise DriverError("range-reach needs --local-file and --expected-sha256")
    local = Path(args.local_file)
    if not local.is_file():
        raise DriverError(f"local file '{local}' does not exist")
    found, length = file_sha256(local)
    if found != args.expected_sha256.lower():
        raise DriverError(f"local file sha256 {found} differs from the expected identity")
    try:
        layout = discover_layout_local(
            local,
            name=name,
            max_parser_bytes=planner.MAX_PARSER_BYTES,
            max_decompression_ratio=planner.MAX_DECOMPRESSION_RATIO,
        )
    except SamplingRefusal as exc:
        raise DriverError(str(exc)) from exc
    audit = reach.reach_audit(
        layout,
        subject=pin,
        file_sha256=found,
        file_bytes=length,
        max_parser_bytes=planner.MAX_PARSER_BYTES,
        max_decompression_ratio=planner.MAX_DECOMPRESSION_RATIO,
        projected_fields=columns_for(spec.adapter_id),
    )
    path = bench.benchmark_dir(roots, args.label) / "range-reach.json"
    runner.write_once(path, audit)
    print(
        f"range reach {audit['status']}: {audit['reachable_groups']}/{audit['row_groups']} "
        f"groups reachable, {audit['refused_rows']:,}/{audit['rows']:,} rows refused; "
        f"longest reachable run {audit['longest_reachable_run_rows']:,} rows"
    )
    print(f"RANGE REACH DIGEST {audit['digest']} -> {path}")
    return 0


# --------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--source-key", required=True)
        p.add_argument("--data-root")
        p.add_argument("--scratch-root")
        p.add_argument("--requirement-split", default="")
        return p

    p = common(
        sub.add_parser("evidence", help="OFFLINE: bridge certified evidence to C04 evidence")
    )
    p.add_argument("action", choices=("show", "publish", "verify"))
    p.add_argument("--probe-dir")
    p.add_argument("--sample-dir", action="append", default=[])
    p.set_defaults(func=cmd_evidence)

    p = common(sub.add_parser("review", help="OFFLINE: operator review facts and decisions"))
    p.add_argument("action", choices=("show", "record"))
    p.add_argument("--review-dir")
    p.add_argument("--operator", default="")
    p.add_argument("--license-decision", default="")
    p.add_argument("--provenance-decision", default="")
    p.add_argument("--benchmark-risk", default="")
    p.add_argument("--rationale", default="")
    p.set_defaults(func=cmd_review)

    p = common(sub.add_parser("admit", help="OFFLINE: record the reviewed mitigated admission"))
    p.add_argument("--review-dir", required=True)
    p.set_defaults(func=cmd_admit)

    p = sub.add_parser("requirement", help="OFFLINE: record/show a multi-view requirement split")
    p.add_argument("--data-root")
    p.add_argument("--scratch-root")
    p.add_argument("--requirement-split", default="")
    p.add_argument("action", choices=("split-record", "split-show"))
    p.add_argument("--component", required=True)
    p.add_argument(
        "--view-tokens",
        default="",
        help="split-record allocations, e.g. general=165000000,planning=165000000",
    )
    p.add_argument("--operator", default="")
    p.add_argument("--rationale", default="")
    p.set_defaults(func=cmd_requirement)

    p = sub.add_parser(
        "allowlist", help="OFFLINE: preview/record/show a write-once component allowlist"
    )
    p.add_argument("action", choices=("preview", "record", "show"))
    p.add_argument("--source-key", required=True)
    p.add_argument("--data-root")
    p.add_argument(
        "--allowlist", default="", help="override <data-root>/calib/component_allowlists"
    )
    p.add_argument("--listing", default="", help="override the frozen discovery listing path")
    p.add_argument("--evidence-matrix", default="", help="override the committed evidence matrix")
    p.add_argument("--include", default="", help="exact top-level components, comma separated")
    p.add_argument(
        "--accept-flagged",
        default="",
        help="included components the evidence flags, named again to accept them",
    )
    p.add_argument("--operator", default="")
    p.add_argument("--rationale", default="")
    p.set_defaults(func=cmd_allowlist)

    for command in ("component-bounds", "component-split"):
        p = common(sub.add_parser(command, help="OFFLINE: write-once reviewed component policy"))
        p.add_argument("action", choices=("preview", "record", "show"))
        p.add_argument("--input", default="")
        p.add_argument("--operator", default="")
        p.add_argument("--rationale", default="")
        p.set_defaults(func=cmd_component_policy)

    p = common(sub.add_parser("policy", help="OFFLINE: model or freeze the transport policy"))
    p.add_argument("action", choices=("model", "freeze"))
    p.add_argument("--basis", choices=("modeled", "measured"), default="modeled")
    p.add_argument("--aggregate-mbps", type=float)
    p.add_argument("--durable-budget-bytes", type=int)
    p.add_argument("--whole-receipt")
    p.add_argument("--range-receipt")
    p.add_argument(
        "--range-reach", help="measured: range-v1 reach audit in place of a range receipt"
    )
    p.set_defaults(func=cmd_policy)

    p = common(sub.add_parser("plan", help="OFFLINE: next deterministic plan (first or top-up)"))
    p.set_defaults(func=cmd_plan)

    p = common(
        sub.add_parser(
            "plan-repair",
            help="OFFLINE: re-plan the unsealed ranks of a failed plan under changed limits",
        )
    )
    p.add_argument("--plan", type=int, required=True)
    p.set_defaults(func=cmd_plan_repair)

    p = common(
        sub.add_parser(
            "plan-supersede",
            help="OFFLINE: re-plan an unauthorized, never-run latest plan under today's inputs",
        )
    )
    p.add_argument("--plan", type=int, required=True)
    p.set_defaults(func=cmd_plan)

    p = common(sub.add_parser("authorize", help="OFFLINE: authorize one reviewed plan digest"))
    p.add_argument("--plan", type=int, required=True)
    p.add_argument("--digest", required=True)
    p.add_argument("--operator", required=True)
    p.set_defaults(func=cmd_authorize)

    for name, func, helptext in (
        ("resume-check", cmd_resume_check, "OFFLINE: restart classification before a run"),
        ("verify", cmd_verify, "OFFLINE: re-verify sealed units"),
    ):
        p = common(sub.add_parser(name, help=helptext))
        p.add_argument("--plan", type=int, required=True)
        if name == "verify":
            p.add_argument("--skip-content", action="store_true")
        p.set_defaults(func=func)

    p = common(sub.add_parser("run", help="NETWORK: execute one authorized plan (resumes)"))
    p.add_argument("--plan", type=int, required=True)
    p.add_argument("--download-workers", type=int)
    p.add_argument("--process-workers", type=int)
    p.add_argument("--offline", action="store_true")
    p.set_defaults(func=cmd_run)

    for name, func, helptext in (
        ("status", cmd_status, "OFFLINE: plans, accounting, restart classes"),
        ("sufficiency", cmd_sufficiency, "OFFLINE: cumulative availability vs requirement"),
    ):
        p = common(sub.add_parser(name, help=helptext))
        p.set_defaults(func=func)

    p = common(sub.add_parser("seal", help="OFFLINE: write-once first-pass seal of the source"))
    p.add_argument("--skip-content", action="store_true")
    p.set_defaults(func=cmd_seal)

    p = common(sub.add_parser("benchmark", help="bounded transport benchmark (run = NETWORK)"))
    p.add_argument(
        "action", choices=("plan", "authorize", "adopt", "run", "record-range", "range-reach")
    )
    p.add_argument("--label", required=True)
    p.add_argument("--donor", default="", help="adopt: earlier benchmark label to reuse")
    p.add_argument(
        "--offline", action="store_true", help="run: require verified complete local inputs"
    )
    p.add_argument("--files", type=int, default=2)
    p.add_argument("--file")
    p.add_argument("--download-workers", type=int, default=8)
    p.add_argument("--process-workers", type=int, default=8)
    p.add_argument("--digest", default="")
    p.add_argument("--operator", default="")
    p.add_argument("--range-plan")
    p.add_argument("--range-perf")
    p.add_argument("--range-journal")
    p.add_argument("--range-documents")
    p.add_argument("--adapt-log")
    p.add_argument("--local-file", help="range-reach: verified local copy of the file")
    p.add_argument("--expected-sha256", help="range-reach: required sha256 of --local-file")
    p.set_defaults(func=cmd_benchmark)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
        return result
    except (
        DriverError,
        ce.BridgeRefusal,
        review.ReviewRefusal,
        planner.PlanError,
        policy.PolicyError,
        runner.RunError,
        cc.CalibrationError,
        cp.ComponentPolicyError,
        cpl.LicenseBasisError,
        allow.AllowlistError,
        OSError,
    ) as exc:
        print(f"mix01_source: refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
