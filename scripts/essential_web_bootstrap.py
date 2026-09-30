"""Essential-Web admission bootstrap: offline freeze, bounded schema probe, operator admission.

``freeze`` and ``status`` are offline. ``probe`` is the only network command and
runs only with ``--authorize-network`` and ``HF_HUB_OFFLINE=0``. ``admit`` records
the operator's decision and needs ``--operator-approve``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mix01_inventory

from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.data.acquisition.plan import (
    PILOT_MAX_REQUESTS,
    AuthorizationRequiredError,
    PlanAuthorization,
    load_acquisition_plan,
    plan_requires_production_admission,
    validate_plan_authorization,
)
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.evidence_v2 import canonical
from xlm.data.evidence_v2.footer import RangeEvidence
from xlm.data.sources import essential_web_bootstrap as boot
from xlm.data.sources import essential_web_readiness as ready
from xlm.data.sources.admission import (
    AdmissionDecision,
    AdmissionGate,
    load_admission_decision,
    load_probe_evidence,
    next_attempt,
    resolve_verified_production_admission,
    save_admission_decision,
    save_probe_evidence,
)
from xlm.data.sources.prober import EvidenceType, ProbeOutcome

REPO = Path(__file__).resolve().parents[1]
READINESS = "docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS"
BOOTSTRAP = "docs/implementation/evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP"
CALIBRATION_DIGEST = "a6cab8cd58a127b77dd130249147f3541ac4575f3ea8369bf248879fd52e89b0"
VERDICT_READY = "READY FOR LIVE ESSENTIAL-WEB PROBE AND CALIBRATION"


def read_json(path: Path) -> Any:
    return mix01_inventory._read_bounded_json(path, 8 * ready.MIB, "bootstrap input")


def write_json(path: Path, value: Any) -> None:
    mix01_inventory._atomic_write_json(path, value)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_reviews(directory: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """Checked review package and the SHA-256 of each review file."""
    reviews = {name: read_json(directory / file) for name, file in boot.REVIEW_FILES.items()}
    boot.check_reviews(reviews)
    hashes = {name: sha256_file(directory / file) for name, file in boot.REVIEW_FILES.items()}
    return reviews, hashes


class ReplayTransport:
    """Offline replay of retained real footer bytes with an authored card; never live evidence."""

    def __init__(self, file: dict[str, Any], footer_payload: bytes) -> None:
        self.file, self.payload, self.calls = file, footer_payload, 0

    def fetch_card(self, limit: int) -> boot.CardEvidence:
        self.calls += 1
        return boot.CardEvidence(f"---\nlicense: {boot.EXPECTED_LICENSE}\n---\n".encode())

    def fetch_range(self, source_file: str, start: int, end: int) -> RangeEvidence:
        self.calls += 1
        length = self.file["remote_length"]
        footer_start = length - len(self.payload)
        if (start, end) == (0, 3):
            body = b"PAR1"
        elif start >= footer_start:
            body = self.payload[start - footer_start : end - footer_start + 1]
        else:
            raise ValueError("replay holds footer bytes only")
        return RangeEvidence(body=body, total_length=length, etag=self.file["strong_etag"])


def retained_footer(footer_root: Path, window: dict[str, Any]) -> bytes:
    """The retained Phase-P M footer payload of one calibration file; M bytes only."""
    layout = read_json(footer_root / "m_phase_p_layout.json")
    for index, file in enumerate(layout["files"]):
        if file["file"] == window["file"]:
            descriptor = file["payloads"][f"M-{index:02d}-footer"]
            retained: Path = footer_root / descriptor["retained_file"]
            return retained.read_bytes()
    raise ValueError("calibration file has no retained footer")


def production_probe_dry(freeze: Path) -> dict[str, Any]:
    """Classify the frozen shared probe plan and exercise its authorization rules."""
    plan = load_acquisition_plan(freeze / "probe-00.plan.json")
    script = (freeze / "future-probe.ps1").read_text(encoding="utf-8")
    authorized = plan.model_copy(
        update={
            "authorization": PlanAuthorization(
                authorization_hash=plan.plan_hash,
                authorized_by="dry_check",
                authorized_at="dry",
                scope="production",
            )
        }
    )
    refusals: dict[str, str | None] = {}
    for name, candidate, admitted in (
        ("authorized_but_not_admitted", authorized, False),
        ("admitted_but_not_authorized", plan, True),
    ):
        try:
            validate_plan_authorization(candidate, admitted)
            refusals[name] = None
        except AuthorizationRequiredError as exc:
            refusals[name] = str(exc)
    validate_plan_authorization(authorized, True)
    return {
        "plan": "probe-00.plan.json",
        "plan_hash": plan.plan_hash,
        "plan_hash_verified": plan.plan_hash == plan.compute_behavioral_hash(),
        "classification": "production" if plan_requires_production_admission(plan) else "pilot",
        "is_pilot": plan.is_pilot,
        "reason": f"max_requests {plan.limits.max_requests} exceeds the pilot ceiling "
        f"{PILOT_MAX_REQUESTS}",
        "rows": sum(stop - start for start, stop in (plan.row_ranges or {}).values()),
        "limits": plan.limits.model_dump(),
        "future_script_carries_matching_authorization_hash": (
            f"--authorization-hash {plan.plan_hash} " in script
        ),
        "refused_when": refusals,
        "accepted_when": "stored admission verified by the fetch gate and authorization hash "
        "equal to the plan hash",
        "executed": False,
    }


def calibration_unchanged(freeze: Path) -> dict[str, Any]:
    """Compare the calibration freeze with the manifest sealed at commit 57cb42f."""
    plan = read_json(freeze / "calibration-plan.json")
    body = dict(plan)
    recorded = body.pop("digest")
    sealed = {
        Path(entry["path"]).name: entry["sha256"]
        for entry in read_json(freeze / "artifact-manifest.json")["artifacts"]
    }
    names = ["calibration-plan.json", "future-calibration.ps1"] + [
        f"calibration-{i:02d}.{kind}.json" for i in range(8) for kind in ("plan", "rows", "limits")
    ]
    changed = [name for name in names if sha256_file(freeze / name) != sealed[name]]
    rows = [window["rows"] for window in plan["windows"]]
    return {
        "digest": recorded,
        "digest_recomputed_equal": canonical.digest(body) == recorded == CALIBRATION_DIGEST,
        "rows": sum(rows),
        "windows": len(rows),
        "rows_per_window": sorted(set(rows)),
        "files_compared_with_sealed_manifest": len(names),
        "files_changed": changed,
        "unchanged": not changed and sum(rows) == 16384 and set(rows) == {2048},
    }


def bootstrap_script(output: Path) -> dict[str, str]:
    head = [
        "$ErrorActionPreference = 'Stop'",
        "if (-not $env:XLM_DATA_ROOT) { throw 'Dot-source scripts/operator_storage.ps1 first' }",
        f"$B = Join-Path (Get-Location) '{BOOTSTRAP}'",
        "$U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')",
        "$R = Join-Path $env:XLM_DATA_ROOT 'calib/essential-web-production/schema-probe'",
    ]
    probe = [
        "# Live schema probe: NOT executed by the offline freeze. About 8 requests, cap 24.",
        *head,
        "$oldHub = $env:HF_HUB_OFFLINE",
        "try {",
        "  $env:HF_HUB_OFFLINE = '0'",
        "  uv @U python scripts/essential_web_bootstrap.py probe "
        '--plan "$B/schema-probe-plan.json" --output-dir "$R" --authorize-network',
        "  if ($LASTEXITCODE -ne 0) { throw 'schema probe refused' }",
        "} finally { $env:HF_HUB_OFFLINE = $oldHub }",
    ]
    admit = [
        "# Operator admission: offline. Read the four review files in $B first.",
        "param([Parameter(Mandatory = $true)][string]$Operator)",
        *head,
        "uv @U python scripts/essential_web_bootstrap.py admit --reviews $B "
        '--operator $Operator --operator-approve --prepared "$B/admission-decisions.json" '
        '--output "$R/admission-record.json"',
        "if ($LASTEXITCODE -ne 0) { throw 'admission refused' }",
        "uv @U python scripts/essential_web_bootstrap.py status",
        "if ($LASTEXITCODE -ne 0) { throw 'views are not admitted' }",
    ]
    scripts = {"future-schema-probe.ps1": probe, "future-admit.ps1": admit}
    for name, lines in scripts.items():
        temporary = output / (name + ".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        temporary.replace(output / name)
    return {name: sha256_file(output / name) for name in scripts}


def freeze(args: argparse.Namespace) -> int:
    out: Path = args.output
    calibration = read_json(args.freeze / "calibration-plan.json")
    window = calibration["windows"][0]
    probe_file = read_json(args.freeze / "probe-plan.json")["file"]
    if window["file"] != probe_file:
        raise ValueError("schema probe file differs from the shared production probe file")
    payload = retained_footer(args.footer_root, window)
    plan = boot.build_probe_plan(window, payload, calibration["digest"])
    write_json(out / "schema-probe-plan.json", plan)
    _, hashes = load_reviews(out)
    write_json(
        out / "review-manifest.json",
        {
            "kind": "essential_web_admission_review_manifest",
            "binding": ready.source_binding(),
            "sha256": {boot.REVIEW_FILES[name]: digest for name, digest in hashes.items()},
            "check": "xlm.data.sources.essential_web_bootstrap.check_reviews passed",
        },
    )
    replay = ReplayTransport(plan["file"], payload)
    result = boot.run_schema_probe(plan, replay)
    binding = ready.source_binding()
    binding["adapter_code_sha256"] = sha256_file(REPO / "src/xlm/data/adapters/mix01_adapters.py")
    decisions = []
    for view in selector.ADMITTED_COMPONENTS:
        record = result["records"][view]
        decision = boot.build_decision(record, hashes, "<operator>")
        synthetic = AdmissionGate.evaluate(record, decision)
        relabeled = AdmissionGate.evaluate(
            record.model_copy(update={"evidence_type": EvidenceType.REAL_OBSERVED}), decision
        )
        decisions.append(
            {
                "component": view,
                "component_semantics": f"frozen final == {view}",
                "binding": binding,
                "canonicalization": binding["canonicalization"],
                "resource_contract": "C04/C13; exact limits and matching authorization required",
                "reviews_sha256": hashes,
                "decision": "PREPARED; NOT RECORDED",
                "production_admission_ok": False,
                "pending": [
                    "live schema probe evidence (future-schema-probe.ps1)",
                    "operator approval (future-admit.ps1)",
                ],
                "prepared_decision": {
                    **decision.model_dump(exclude={"decision_timestamp"}),
                    "probe_fingerprint": None,
                    "operator_notes": decision.operator_notes,
                },
                "expected_probe_fingerprint_if_live_equals_plan": record.probe_fingerprint,
                "offline_gate_check": {
                    "evidence": "replay of the retained real footer with an authored card; "
                    "labeled synthetic_fixture and never stored",
                    "gate_on_replay": {
                        "admitted": synthetic.admitted,
                        "reasons": synthetic.reasons,
                    },
                    "every_other_criterion_passes": relabeled.admitted,
                    "note": "the second evaluation relabels the replay in memory only, to show "
                    "that nothing but live evidence is missing",
                },
            }
        )
    write_json(out / "admission-decisions.json", decisions)
    probe_dry = production_probe_dry(args.freeze)
    write_json(out / "production-probe-authorization-dry.json", probe_dry)
    unchanged = calibration_unchanged(args.freeze)
    write_json(out / "calibration-unchanged.json", unchanged)
    scripts = bootstrap_script(out)
    reviews_ready = True
    probe_ready = (
        replay.calls == 4
        and plan["limits"]["max_physical_requests"] <= PILOT_MAX_REQUESTS
        and probe_dry["classification"] == "production"
        and probe_dry["future_script_carries_matching_authorization_hash"]
        and all(d["offline_gate_check"]["every_other_criterion_passes"] for d in decisions)
    )
    write_json(
        out / "readiness.json",
        {
            "supersedes": f"{READINESS}/readiness.json (kept unchanged)",
            "vector": {
                "selector_frozen": True,
                "selector_integration_ok": True,
                "source_revision_ok": True,
                "production_admission_ok": False,
                "inventory_ready": True,
                "malformed_policy_ready": True,
                "transfer_strategy_ready": True,
                "probe_plan_ready": probe_ready,
                "calibration_plan_ready": unchanged["unchanged"],
                "acquisition_plan_ready": False,
            },
            "reasons": {
                "production_admission_ok": [
                    "reviews complete; admission needs the live schema probe record and the "
                    "operator's approval, neither of which can be produced offline"
                ],
                "acquisition_plan_ready": [
                    "admission not yet recorded",
                    "science retained-byte yield and source capacity unmeasured",
                ],
            },
            "admission_reviews_complete": reviews_ready,
            "live_schema_probe_run": False,
            "live_probe_run": False,
            "live_calibration_run": False,
            "schema_probe": {
                "plan_digest": plan["digest"],
                "logical_operations": replay.calls,
                "nominal_physical_requests": plan["nominal_physical_requests"],
                "max_physical_requests": plan["limits"]["max_physical_requests"],
                "pilot_request_ceiling": PILOT_MAX_REQUESTS,
            },
            "operator_scripts_sha256": scripts,
            "next_live_sequence": [
                "future-schema-probe.ps1",
                "future-admit.ps1 -Operator <name>",
                f"{READINESS}/future-probe.ps1",
                f"{READINESS}/future-calibration.ps1",
            ],
            "verdict": VERDICT_READY if probe_ready and unchanged["unchanged"] else "BLOCKED",
            "verdict_scope": "the live sequence starts with the schema probe and the operator "
            "admission; the fetch gate refuses the production probe until both exist",
        },
    )
    print(json.dumps({"plan_digest": plan["digest"], "probe_plan_ready": probe_ready}, indent=2))
    return 0


def probe(args: argparse.Namespace) -> int:
    if not args.authorize_network or os.environ.get("HF_HUB_OFFLINE") != "0":
        raise ValueError("live probe needs --authorize-network and HF_HUB_OFFLINE=0")
    plan = read_json(args.plan)
    boot.check_probe_plan(plan)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    budget = boot.probe_budget()
    transport = boot.LiveSchemaProbeTransport(budget)
    try:
        result = boot.run_schema_probe(
            plan, _CardKeeper(transport), evidence_type=EvidenceType.REAL_OBSERVED
        )
    except boot.SchemaProbeRefusal as exc:
        write_json(
            args.output_dir / f"schema-probe-{stamp}.refusal.json",
            {
                "kind": boot.RECEIPT_KIND,
                "status": "REFUSED",
                "outcome": exc.outcome.value,
                "reason": str(exc),
                "plan_digest": plan["digest"],
                "resource_metrics": budget.to_metrics(),
                "evidence_published": False,
            },
        )
        raise
    metrics = budget.to_metrics()
    card = _CardKeeper.last
    if card is not None:
        digest = result["receipt"]["observed"]["card"]["sha256"]
        with (args.output_dir / f"dataset-card-{digest[:16]}.md").open("wb") as stream:
            stream.write(card)
    store = ArtifactStore(ArtifactPaths.from_env())
    published = {}
    with tempfile.TemporaryDirectory() as staging:
        for view, record in result["records"].items():
            record.resource_metrics = {**metrics, **record.resource_metrics}
            attempt = next_attempt(store, "probe_evidence", f"probe_{boot.SOURCE_ID}_{view}")
            published[view] = {
                "attempt": attempt,
                "artifact": save_probe_evidence(
                    record, store, staging_dir=Path(staging) / view, attempt=attempt
                ),
                "probe_fingerprint": record.probe_fingerprint,
            }
    write_json(
        args.output_dir / f"schema-probe-{stamp}.receipt.json",
        {**result["receipt"], "resource_metrics": metrics, "published": published},
    )
    print(json.dumps({"status": "ACCESSIBLE", "requests": metrics["requests_made"]}, indent=2))
    return 0


class _CardKeeper:
    """Keep the fetched card bytes so the operator root holds what was hashed."""

    last: bytes | None = None

    def __init__(self, inner: boot.SchemaProbeTransport) -> None:
        self.inner = inner

    def fetch_card(self, limit: int) -> boot.CardEvidence:
        card = self.inner.fetch_card(limit)
        _CardKeeper.last = card.body
        return card

    def fetch_range(self, source_file: str, start: int, end: int) -> RangeEvidence:
        return self.inner.fetch_range(source_file, start, end)


def gate_status(store: ArtifactStore) -> dict[str, Any]:
    views = {}
    for view in selector.ADMITTED_COMPONENTS:
        evidence = load_probe_evidence(boot.SOURCE_ID, view, store)
        if evidence is None:
            views[view] = {"admitted": False, "reasons": ["no verified probe evidence"]}
            continue
        gate = AdmissionGate.evaluate(
            evidence, load_admission_decision(boot.SOURCE_ID, view, store)
        )
        views[view] = {"admitted": gate.admitted, "reasons": gate.reasons}
    return {
        "production_admission_ok": all(view["admitted"] for view in views.values()),
        "views": views,
    }


def prepare_admission(args: argparse.Namespace) -> int:
    """Reseal decisions using existing live records; verify only in a temporary store."""
    _, hashes = load_reviews(args.reviews)
    binding = ready.source_binding()
    binding["adapter_code_sha256"] = sha256_file(REPO / "src/xlm/data/adapters/mix01_adapters.py")
    store = ArtifactStore(ArtifactPaths(root=args.store.resolve()))
    receipt = read_json(args.receipt)
    body = {
        key: value
        for key, value in receipt.items()
        if key not in {"digest", "published", "resource_metrics"}
    }
    if (
        canonical.digest(body) != receipt["digest"]
        or receipt["status"] != "ACCESSIBLE"
        or receipt["evidence_type"] != "real_observed"
        or receipt["observed"]["repository"] != ready.REPOSITORY
        or receipt["observed"]["revision"] != selector.SOURCE_REVISION
    ):
        raise ValueError("existing live schema receipt failed identity/integrity verification")
    prepared = []
    with tempfile.TemporaryDirectory(prefix="ew-c04-") as scratch:
        copy_store = ArtifactStore(ArtifactPaths(root=Path(scratch) / "store"))
        for view in selector.ADMITTED_COMPONENTS:
            evidence = load_probe_evidence(boot.SOURCE_ID, view, store)
            if (
                evidence is None
                or evidence.evidence_type != EvidenceType.REAL_OBSERVED
                or evidence.probe_fingerprint != receipt["published"][view]["probe_fingerprint"]
                or evidence.resource_metrics.get("receipt_digest") != receipt["digest"]
            ):
                raise ValueError(f"{view}: stored evidence differs from the live receipt")
            decision = boot.build_decision(evidence, hashes, "offline-verifier-only")
            gate = AdmissionGate.evaluate(evidence, decision)
            if not gate.admitted:
                raise ValueError(f"{view}: " + "; ".join(gate.reasons))
            save_probe_evidence(evidence, copy_store, Path(scratch) / "probe" / view)
            save_admission_decision(decision, copy_store, Path(scratch) / "decision" / view)
            plan = load_acquisition_plan(REPO / READINESS / "probe-00.plan.json")
            # Same bounded plan, exercising the verifier for each exact source view.
            resolve_verified_production_admission(
                plan.model_copy(update={"view_id": view}), copy_store
            )
            decision.operator_approved = False
            decision.operator_notes = boot.decision_notes(hashes, "pending operator approval")
            prepared.append(
                {
                    "component": view,
                    "decision": "PREPARED; NOT RECORDED",
                    "prepared_decision": decision.model_dump(mode="json"),
                    "source_binding": binding,
                    "live_receipt_sha256": sha256_file(args.receipt),
                    "live_receipt_digest": receipt["digest"],
                    "production_plan_hash": plan.plan_hash,
                    "production_limits": plan.limits.model_dump(mode="json"),
                    "offline_verifier": "PASS in temporary store with simulated operator approval",
                    "production_admission_ok": False,
                    "official_benchmark_claims_allowed": False,
                    "pending": ["explicit operator approval", "later frozen-pool C05 exclusion"],
                }
            )
    write_json(args.output, prepared)
    write_json(
        args.output.with_suffix(".seal.json"),
        {
            "contract_version": "c04-benchmark-risk-v2",
            "decisions_sha256": sha256_file(args.output),
            "reviews_sha256": hashes,
            "live_receipt_sha256": sha256_file(args.receipt),
            "real_store_admission_published": False,
            "verdict": "READY FOR OPERATOR ADMISSION",
        },
    )
    print(json.dumps({"views_verified": len(prepared), "real_admission_published": False}))
    return 0


def admit(args: argparse.Namespace) -> int:
    if not args.operator_approve:
        raise ValueError("admission is an operator decision; pass --operator-approve")
    _, hashes = load_reviews(args.reviews)
    prepared: dict[str, AdmissionDecision] | None = None
    if args.prepared is not None:
        seal = read_json(args.prepared.with_suffix(".seal.json"))
        if (
            seal["decisions_sha256"] != sha256_file(args.prepared)
            or seal["reviews_sha256"] != hashes
            or seal["contract_version"] != "c04-benchmark-risk-v2"
        ):
            raise ValueError("prepared admission seal or review hashes differ")
        rows = read_json(args.prepared)
        binding = ready.source_binding()
        binding["adapter_code_sha256"] = sha256_file(
            REPO / "src/xlm/data/adapters/mix01_adapters.py"
        )
        if any(row["source_binding"] != binding for row in rows):
            raise ValueError("prepared source/adapter binding differs from current code")
        prepared = {
            row["component"]: AdmissionDecision.model_validate(row["prepared_decision"])
            for row in rows
        }
        if len(rows) != 3 or set(prepared) != set(selector.ADMITTED_COMPONENTS):
            raise ValueError("prepared admission must bind exactly the three Essential views")
    store = ArtifactStore(ArtifactPaths.from_env())
    decisions = {}
    for view in selector.ADMITTED_COMPONENTS:
        evidence = load_probe_evidence(boot.SOURCE_ID, view, store)
        if evidence is None or evidence.outcome != ProbeOutcome.ACCESSIBLE:
            raise ValueError(f"{view}: no accessible probe evidence; run the schema probe first")
        decision = boot.build_decision(evidence, hashes, args.operator)
        if prepared is not None:
            exclude = {"decision_timestamp", "operator_notes", "operator_approved"}
            if prepared[view].model_dump(exclude=exclude) != decision.model_dump(exclude=exclude):
                raise ValueError(f"{view}: prepared decision differs from current evidence/reviews")
        gate = AdmissionGate.evaluate(evidence, decision)
        if not gate.admitted:
            raise ValueError(f"{view}: gate refuses: " + "; ".join(gate.reasons))
        decisions[view] = decision
    recorded = {}
    with tempfile.TemporaryDirectory() as staging:
        for view, decision in decisions.items():
            attempt = next_attempt(
                store, "admission_decision", f"admission_{boot.SOURCE_ID}_{view}"
            )
            recorded[view] = {
                "attempt": attempt,
                "artifact": save_admission_decision(
                    decision, store, staging_dir=Path(staging) / view, attempt=attempt
                ),
                "decision": decision.model_dump(),
            }
    resolve_verified_production_admission(
        load_acquisition_plan(REPO / READINESS / "probe-00.plan.json"), store
    )
    status = gate_status(store)
    write_json(args.output, {"reviews_sha256": hashes, "recorded": recorded, "status": status})
    print(json.dumps(status, indent=2))
    return 0 if status["production_admission_ok"] else 1


def status(args: argparse.Namespace) -> int:
    result = gate_status(ArtifactStore(ArtifactPaths.from_env()))
    print(json.dumps(result, indent=2))
    return 0 if result["production_admission_ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("freeze")
    command.add_argument("--footer-root", type=Path, required=True)
    command.add_argument("--freeze", type=Path, default=REPO / READINESS)
    command.add_argument("--output", type=Path, default=REPO / BOOTSTRAP)
    command.set_defaults(run=freeze)
    command = commands.add_parser("probe")
    command.add_argument("--plan", type=Path, required=True)
    command.add_argument("--output-dir", type=Path, required=True)
    command.add_argument("--authorize-network", action="store_true")
    command.set_defaults(run=probe)
    command = commands.add_parser("admit")
    command.add_argument("--reviews", type=Path, required=True)
    command.add_argument("--operator", required=True)
    command.add_argument("--operator-approve", action="store_true")
    command.add_argument("--prepared", type=Path)
    command.add_argument("--output", type=Path, required=True)
    command.set_defaults(run=admit)
    command = commands.add_parser("prepare-admission")
    command.add_argument("--reviews", type=Path, required=True)
    command.add_argument("--store", type=Path, required=True)
    command.add_argument("--receipt", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.set_defaults(run=prepare_admission)
    command = commands.add_parser("status")
    command.set_defaults(run=status)
    args = parser.parse_args(argv)
    try:
        return int(args.run(args))
    except (ValueError, OSError) as exc:
        print(f"essential_web_bootstrap: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
