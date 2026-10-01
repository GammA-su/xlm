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
from xlm.data.acquisition import range_reach as reach
from xlm.data.acquisition import source_benchmark as bench
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import source_run as runner
from xlm.data.acquisition import transport_policy as policy
from xlm.data.acquisition.plan import AcquisitionPlan
from xlm.data.acquisition.sampling import SamplingRefusal, discover_layout_local
from xlm.data.acquisition.source_dashboard import ObservedScratch
from xlm.data.adapters.columns import columns_for
from xlm.data.sources import certified_evidence as ce
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
BLOCKED = {"common_pile": "license/provenance and component allowlist unresolved (C04)"}


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


# --------------------------------------------------------------------- policy


def layout_of(
    args: argparse.Namespace, spec: SourceSpec
) -> tuple[policy.SourceLayout, dict[str, Any]]:
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


def requirement_of(args: argparse.Namespace, spec: SourceSpec) -> planner.Requirement:
    component = pin_of(spec).component_id
    estimate_path = data_root(args) / "calib" / "headroom_estimate.json"
    return planner.requirement_from(
        component,
        yaml.safe_load(QUOTAS.read_text(encoding="utf-8")),
        load_json(estimate_path),
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
    if len([s for s in SOURCES.values() if s.source_id == spec.source_id]) > 1:
        raise DriverError("a multi-view component needs an explicit per-view requirement decision")
    layout, extra = layout_of(args, spec)
    req = requirement_of(args, spec)
    requirement = policy.Requirement(
        spec.source_id, req.required_canonical_bytes, req.safety_margin
    )
    if measured is not None:
        # The whole-file measurement, not the small calibration, sizes the workloads.
        layout = planner.sized_layout(layout, measured.sizing)
        models = measured.models
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
    return policy.evaluate(layout, requirement, ceilings, models)


def cmd_policy(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
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


def cmd_plan(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
    roots = roots_of(args)
    target = store()
    pin = pin_of(spec)
    frozen = runner.read_json(roots.plans / "transport-policy.json")
    inventory_path = data_root(args) / "inventories" / f"{args.source_key}.inventory.json"
    layout, extra = layout_of(args, spec)
    record = planner.build_plan(
        source_key=args.source_key,
        pin=pin.as_dict(),
        requirement=requirement_of(args, spec),
        inventory=load_json(inventory_path),
        inventory_sha256=sha256(inventory_path),
        layout=layout,
        calibration=extra["evidence"],
        policy=frozen,
        admission=current_admission(spec, target),
        predecessor=predecessor(roots),
    )
    path = runner.store_plan(roots, record)
    limits = record["limits"]
    acq = record["acquisition_plan"]["limits"]
    selection = record["selection"]
    files = len(selection["files"])
    print(f"plan {record['sequence']} of {args.source_key}: {path}")
    print(f"transport mode      {record['transport_mode']} ({frozen['basis']} policy)")
    print(f"policy digest       {frozen['digest']}")
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
    keep_free = limits["scratch_min_free_bytes"]
    print(f"scratch ceiling     {limits['scratch_cap_bytes']:,} B (+{keep_free:,} B kept free)")
    print(f"durable ceiling     {acq['max_output_disk_bytes']:,} B (raw + canonical)")
    print(f"canonical ceiling   {limits['max_canonical_bytes_per_file'] * files:,} B")
    streams, processes = limits["download_workers"], limits["process_workers"]
    print(f"concurrency         {streams} streams, {processes} processes")
    print(f"PLAN DIGEST: {record['digest']}")
    print("STOP - USER MUST REVIEW PLAN DIGEST BEFORE AUTHORIZATION")
    return 0


def cmd_authorize(args: argparse.Namespace) -> int:
    spec = spec_of(args.source_key)
    roots = roots_of(args)
    record = runner.load_plan(roots, args.plan)
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
    roots = roots_of(args)
    target = store()
    if args.action == "plan":
        pin = pin_of(spec)
        layout, _ = layout_of(args, spec)
        inventory_path = data_root(args) / "inventories" / f"{args.source_key}.inventory.json"
        if args.file:
            entries = [{"rank": None, "file": args.file, "reason": "named calibration file"}]
            seed = 0
        else:
            inventory = load_json(inventory_path)
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
        )
        path = bench.store_benchmark(roots, record)
        print(f"benchmark {args.label}: {[e['file'] for e in entries]} -> {path}")
        expected_run = record["expected"]
        print(f"expected {expected_run['transfer_bytes']:,} B, {expected_run['requests']} requests")
        print(f"BENCHMARK DIGEST: {record['digest']}")
        print("STOP - USER MUST REVIEW BENCHMARK DIGEST BEFORE AUTHORIZATION")
        return 0
    record = runner.read_json(bench.benchmark_dir(roots, args.label) / "benchmark.json")
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
        return p

    p = common(
        sub.add_parser("evidence", help="OFFLINE: bridge certified evidence to C04 evidence")
    )
    p.add_argument("action", choices=("show", "publish", "verify"))
    p.add_argument("--probe-dir")
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
    ) as exc:
        print(f"mix01_source: refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
