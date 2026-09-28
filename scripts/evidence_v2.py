"""Evidence-v2.0 offline operator CLI (no acquisition execution by default).

Offline subcommands (no network, no X: writes):
  verify-freeze      recompute freeze/inventory/policy bindings from repo files
  expand-inventory   print the eight verified frozen winners
  dry-m-plan         eight dry single-file window plans, costs unresolved
  select-text        read-only X: development bundle -> G: locator manifest
  dry-t-plan         sparse text cost plan with unresolved physicals

Planning subcommands (separately authorized footer/cost evidence ONLY):
  plan-footers       footer inspection for the eight frozen files
  plan-text-costs    file/group cost evidence for the frozen T selection

Planning subcommands default to --dry-run (offline). Live mode requires
the explicit --authorize-network flag AND only contacts the two frozen
allowlisted hosts; without it they refuse with a STOP message and write
nothing. Live transport was NOT exercised in the implementation task.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import (
    budgets,
    canonical,
    frozen,
    inventory,
    receipts,
    text_select,
    windows,
)

ROOT = Path(__file__).resolve().parents[1]

EVIDENCE_DIR = ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2"
EVIDENCE_V21 = ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1"
DEFAULT_X = Path(r"X:\XLM\recon\essential_web")
DEFAULT_G_ROOT = Path(r"G:\Project\xlm-evidence-v2\essential-web")


def _fail(message: str) -> int:
    print(f"evidence_v2: error: {message}", file=sys.stderr)
    return 1


def _load_json(path: Path) -> Any:
    return canonical.loads_bytes_strict(path.read_bytes())


def cmd_verify_freeze(args: argparse.Namespace) -> int:
    try:
        freeze = _load_json(EVIDENCE_DIR / "freeze.json")
        root_files = {
            a["relative_path"]: (ROOT / a["relative_path"]).read_bytes()
            for a in freeze["artifacts"]
        }
        inventory.verify_freeze_binding(freeze, root_files)
        inv = _load_json(EVIDENCE_DIR / "inventory-freeze.json")
        result = inventory.verify_inventory_freeze(inv)
        spec = (ROOT / "recipes/selectors/essential_web_selector_sweep_v1.yaml").read_bytes()
        import yaml

        if canonical.digest(yaml.safe_load(spec.decode("utf-8"))) != frozen.POLICY_DIGEST:
            raise inventory.InventoryError("policy digest drift")
    except (inventory.InventoryError, canonical.CanonicalError, OSError) as exc:
        return _fail(str(exc))
    print(
        json.dumps(
            {
                "freeze_digest": frozen.FREEZE_DIGEST,
                "inventory_digest": frozen.INVENTORY_DIGEST,
                "policy_digest": frozen.POLICY_DIGEST,
                "eligible_total": result["eligible_total"],
                "winners": result["winners"],
            },
            indent=2,
        )
    )
    return 0


def cmd_expand_inventory(args: argparse.Namespace) -> int:
    try:
        inv = _load_json(EVIDENCE_DIR / "inventory-freeze.json")
        result = inventory.verify_inventory_freeze(inv)
    except (inventory.InventoryError, canonical.CanonicalError, OSError) as exc:
        return _fail(str(exc))
    print(json.dumps(result["winners"], indent=2))
    return 0


def _code_paths() -> list[Path]:
    package = ROOT / "src/xlm/data/evidence_v2"
    return sorted(package.glob("*.py")) + [ROOT / "scripts/evidence_v2.py"]


def cmd_dry_m_plan(args: argparse.Namespace) -> int:
    try:
        inv = _load_json(EVIDENCE_DIR / "inventory-freeze.json")
        result = inventory.verify_inventory_freeze(inv)
        plan = windows.dry_arm_plan(result["winners"])
        windows.check_plan_shape(plan)
        ledger = budgets.new_arm_m()
        body = {
            "kind": "essential_web_evidence_v2_dry_arm_m_plan",
            "plan": plan,
            "budget": ledger.snapshot(),
        }
        provenance = receipts.provenance(
            root=ROOT,
            code_paths=_code_paths(),
            command="dry-m-plan",
            exit_status=0,
            caps=frozen.ARM_M_LIMITS,
            parents={"freeze": frozen.FREEZE_DIGEST},
            stage="dry-planning",
        )
        manifest = receipts.seal({**body, "provenance": provenance})
    except (inventory.InventoryError, windows.WindowError, budgets.BudgetRefusal) as exc:
        return _fail(str(exc))
    if args.out is not None:
        try:
            receipts.publish_manifest(args.out, manifest)
        except receipts.ReceiptError as exc:
            return _fail(str(exc))
    else:
        print(canonical.canonical_bytes(manifest).decode("utf-8"))
    return 0


def cmd_select_text(args: argparse.Namespace) -> int:
    try:
        evaluator, evaluator_hash = text_select.load_evaluator(ROOT / "scripts")
        spec, _ = text_select.load_policy_spec(evaluator, ROOT / "recipes")
        bundle = _load_json(args.bundle)
        execution = _load_json(args.execution)
        rows = list(text_select.iter_bundle_records(args.records, bundle, execution))
        if len(rows) != frozen.DEV_RECORDS:
            raise text_select.SelectionError(f"read {len(rows)} rows, not 4096")
        evaluated = text_select.evaluate_rows(evaluator, spec, rows)
        crawls = sorted({r["crawl"] for r in evaluated})
        selection = text_select.select_text(evaluated, crawls)
        manifest = text_select.build_manifest(
            selection,
            evaluator_hash=evaluator_hash,
            command="select-text",
            exit_status=0,
        )
        sealed = text_select.seal(manifest)
    except (text_select.SelectionError, canonical.CanonicalError, OSError) as exc:
        return _fail(str(exc))
    try:
        binding = receipts.publish_manifest(args.out, sealed)
    except receipts.ReceiptError as exc:
        return _fail(str(exc))
    print(
        json.dumps(
            {
                "total_selected": sealed["total_selected"],
                "digest": sealed["digest"],
                "bytes": binding["bytes"],
                "sha256": binding["sha256"],
            },
            indent=2,
        )
    )
    return 0


def cmd_dry_t_plan(args: argparse.Namespace) -> int:
    try:
        manifest = _load_json(args.selection_manifest)
        if manifest.get("digest") != canonical.self_digest(
            {k: v for k, v in manifest.items() if k != "digest"}
        ):
            raise canonical.CanonicalError("selection manifest digest mismatch")
        by_file: dict[str, list[int]] = {}
        for cell in manifest["cells"]:
            groups = [cell] if "identities" in cell else cell["crawls"]
            for group in groups:
                for locator in group["identities"]:
                    by_file.setdefault(str(locator[2]), []).append(int(locator[3]))
        files = {
            name: {
                "wanted_rows": sorted(rows),
                "row_groups": "UNRESOLVED_requires_authorized_text_footer",
                "transfer_request_plan": "UNRESOLVED_until_group_layout_known",
            }
            for name, rows in sorted(by_file.items())
        }
        ledger = budgets.new_arm_t()
        body = {
            "kind": "essential_web_evidence_v2_dry_arm_t_plan",
            "selection_digest": manifest["digest"],
            "total_selected": manifest["total_selected"],
            "files": files,
            "physical_unknowns": [
                "text chunk offsets per file/group",
                "compressed and uncompressed lengths",
                "codec and dictionary/page requirements",
                "range/request counts and worst decoder workspace",
            ],
            "executable": False,
            "budget": ledger.snapshot(),
        }
        provenance = receipts.provenance(
            root=ROOT,
            code_paths=_code_paths(),
            command="dry-t-plan",
            exit_status=0,
            caps=frozen.ARM_T_LIMITS,
            parents={
                "freeze": frozen.FREEZE_DIGEST,
                "selection": manifest["digest"],
            },
            stage="dry-planning",
        )
        sealed = receipts.seal({**body, "provenance": provenance})
    except (canonical.CanonicalError, budgets.BudgetRefusal, OSError, KeyError) as exc:
        return _fail(str(exc))
    print(canonical.canonical_bytes(sealed).decode("utf-8"))
    return 0


def _refuse_live(args: argparse.Namespace, what: str) -> int:
    if not args.authorize_network:
        print(
            f"evidence_v2: STOP: {what} requires separately authorized network. "
            "Re-run with --authorize-network after the user explicitly enables it; "
            "only huggingface.co and cas-bridge.xethub.hf.co:443 are ever contacted, "
            "and only footer/cost evidence is fetched (never corpus rows/text).",
            file=sys.stderr,
        )
        return 2
    return 0


def _live_transport() -> Any:
    from xlm.data.evidence_v2.footer import LiveFooterTransport
    from xlm.data.sources.transport import TransportBudget

    budget = TransportBudget(
        max_bytes=frozen.ARM_M_LIMITS["response_body_bytes_total"],
        max_requests=frozen.ARM_M_LIMITS["requests_total"],
        deadline_seconds=float(frozen.ARM_M_LIMITS["time_seconds_arm"]),
        per_request_timeout=float(frozen.PER_REQUEST_TIMEOUT_SECONDS),
    )
    return LiveFooterTransport(budget)


def cmd_plan_footers(args: argparse.Namespace) -> int:
    if args.dry_run:
        return cmd_dry_m_plan(args)
    code = _refuse_live(args, "footer planning")
    if code:
        return code
    from xlm.data.evidence_v2 import footer as footer_mod

    if args.out is None:
        return _fail("live footer planning requires --out for the evidence file")
    try:
        inv = _load_json(EVIDENCE_DIR / "inventory-freeze.json")
        result = inventory.verify_inventory_freeze(inv)
        ledger = budgets.new_arm_m()
        aggregate = footer_mod.plan_arm_m(_live_transport(), ledger, result["winners"])
        provenance = receipts.provenance(
            root=ROOT,
            code_paths=_code_paths(),
            command="plan-footers --no-dry-run --authorize-network",
            exit_status=0,
            caps=frozen.ARM_M_LIMITS,
            parents={"freeze": frozen.FREEZE_DIGEST},
            stage="footer-planning",
        )
        sealed = receipts.seal({**aggregate, "provenance": provenance})
    except footer_mod.ArmIncomplete as exc:
        receipt = receipts.seal(footer_mod.incomplete_receipt(exc, command="plan-footers live"))
        incomplete = args.out.with_name(args.out.stem + ".incomplete.json")
        try:
            receipts.publish_manifest(incomplete, receipt)
        except (receipts.ReceiptError, OSError) as publish_exc:
            return _fail(f"{exc}; also failed to write incomplete receipt: {publish_exc}")
        print(f"evidence_v2: Arm M INCOMPLETE; failure receipt: {incomplete}", file=sys.stderr)
        return _fail(str(exc))
    except (footer_mod.FooterError, budgets.BudgetRefusal, receipts.ReceiptError) as exc:
        return _fail(str(exc))
    try:
        receipts.publish_manifest(args.out, sealed)
    except (receipts.ReceiptError, OSError) as exc:
        return _fail(str(exc))
    print(
        json.dumps(
            {
                "status": sealed["status"],
                "digest": sealed["digest"],
                "units": len(sealed["units"]),
                "budget": sealed["budget"],
            },
            indent=2,
        )
    )
    return 0


def cmd_plan_text_costs(args: argparse.Namespace) -> int:
    if args.dry_run:
        return cmd_dry_t_plan(args)
    code = _refuse_live(args, "text cost planning")
    if code:
        return code
    from xlm.data.evidence_v2 import text_costs as text_costs_mod

    if args.out is None:
        return _fail("live text cost planning requires --out for the evidence file")
    try:
        manifest = _load_json(args.selection_manifest)
        ledger = budgets.new_arm_t()
        aggregate = text_costs_mod.plan_text_costs(
            manifest, transport=_live_transport(), ledger=ledger
        )
        provenance = receipts.provenance(
            root=ROOT,
            code_paths=_code_paths(),
            command="plan-text-costs --no-dry-run --authorize-network",
            exit_status=0,
            caps=frozen.ARM_T_LIMITS,
            parents={
                "freeze": frozen.FREEZE_DIGEST,
                "selection": manifest["digest"],
            },
            stage="text-cost-planning",
        )
        sealed = receipts.seal({**aggregate, "provenance": provenance})
    except text_costs_mod.TextCostsIncomplete as exc:
        aggregate = text_costs_mod.build_incomplete_aggregate(
            exc, manifest, command="plan-text-costs live", exit_status=1
        )
        aggregate["provenance"] = receipts.provenance(
            root=ROOT,
            code_paths=_code_paths(),
            command="plan-text-costs --no-dry-run --authorize-network",
            exit_status=1,
            caps=frozen.ARM_T_LIMITS,
            parents={
                "freeze": frozen.FREEZE_DIGEST,
                "selection": manifest["digest"],
            },
            stage="text-cost-planning",
        )
        receipt = receipts.seal(aggregate)
        incomplete = args.out.with_name(args.out.stem + ".incomplete.json")
        try:
            receipts.publish_manifest(incomplete, receipt)
        except (receipts.ReceiptError, OSError) as publish_exc:
            return _fail(f"{exc}; also failed to write incomplete receipt: {publish_exc}")
        print(
            f"evidence_v2: Arm-T costs INCOMPLETE; failure receipt: {incomplete}", file=sys.stderr
        )
        return _fail(str(exc))
    except (
        text_costs_mod.TextCostError,
        budgets.BudgetRefusal,
        receipts.ReceiptError,
        canonical.CanonicalError,
    ) as exc:
        return _fail(str(exc))
    try:
        receipts.publish_manifest(args.out, sealed)
    except (receipts.ReceiptError, OSError) as exc:
        return _fail(str(exc))
    print(
        json.dumps(
            {
                "status": sealed["status"],
                "digest": sealed["digest"],
                "files": len(sealed["files"]),
                "budget": sealed["budget"],
            },
            indent=2,
        )
    )
    return 0


def _read_receipt(path: Path) -> Any:
    return canonical.loads_bytes_strict(Path(path).read_bytes())


def cmd_verify_v21_freeze(args: argparse.Namespace) -> int:
    from xlm.data.evidence_v2 import lineage as lineage_mod

    try:
        freeze = canonical.loads_bytes_strict(Path(args.freeze).read_bytes())
        lineage_mod.verify_freeze(freeze)
        lineage_mod.verify_protocol_bytes(
            (
                ROOT / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.1-PROTOCOL.md"
            ).read_bytes()
        )
        for artifact in freeze.get("artifacts", []):
            lineage_mod.verify_freeze_artifact(
                freeze, artifact["relative_path"], (ROOT / artifact["relative_path"]).read_bytes()
            )
        repo_parents = lineage_mod.parent_records(ROOT, freeze)
        for key, path in sorted(repo_parents.items()):
            lineage_mod.verify_parent(freeze, key, raw=path.read_bytes(), digest=None)
        g_root = Path(args.g_root)
        for key in (
            "footer_evidence.json",
            "footer_evidence.incomplete.json",
            "text_cost_evidence.incomplete.json",
            "text_cost_evidence_attempt2.incomplete.json",
            "text_selection_manifest.json",
        ):
            bound = freeze["parents"][key]
            raw = (g_root / Path(bound["path"]).name).read_bytes()
            lineage_mod.verify_parent(freeze, key, raw=raw, digest=None)
        lineage_mod.verify_selection_identity(
            (g_root / "text_selection_manifest.json").read_bytes()
        )
        lineage_mod.check_scientific_namespace()
    except (lineage_mod.LineageError, canonical.CanonicalError, OSError) as exc:
        return _fail(str(exc))
    print(
        json.dumps(
            {
                "freeze_digest": frozen.V21_FREEZE_DIGEST,
                "artifacts": len(freeze.get("artifacts", [])),
                "parents": sorted(freeze.get("parents", {})),
                "selection_digest": frozen.V21_SELECTION_DIGEST,
            },
            indent=2,
        )
    )
    return 0


def _v21_carry(g_root: Path) -> tuple[Any, dict[str, Any]]:
    """Build the v2.1 T durable ledger and its sealed reconciliation receipt."""
    from xlm.data.evidence_v2 import carry as carry_mod

    attempt2 = _read_receipt(g_root / "text_cost_evidence_attempt2.incomplete.json")
    prior = _read_receipt(g_root / "text_cost_evidence.incomplete.json")
    if attempt2.get("digest") != frozen.V21_COSTMAP_DIGEST:
        raise carry_mod.CarryError("attempt-2 cost map digest mismatch")
    if prior.get("budget", {}).get("requests", -1) != 6:
        raise carry_mod.CarryError("prior T receipt request total mismatch")
    attempt2_files = {
        name: {
            "requests": 6,
            "bytes": n,
            "measurement": "derived_uniform_split_of_measured_arm_total",
        }
        for name, n in sorted(attempt2["budget"]["transfer_per_file"].items())
    }
    prior_files = {
        name: {"requests": 6, "bytes": n, "measurement": "measured"}
        for name, n in sorted(prior["budget"]["transfer_per_file"].items())
    }
    entries = carry_mod.v21_t_carry_entries(
        frozen.V21_COSTMAP_DIGEST,
        str(g_root / "text_cost_evidence_attempt2.incomplete.json"),
        attempt2_files,
        prior["digest"],
        str(g_root / "text_cost_evidence.incomplete.json"),
        prior_files,
    )
    durable = carry_mod.DurableLedger(budgets.new_arm_t_v21())
    for entry in entries:
        durable.adopt(entry)
    durable.register_gap(
        None,
        48,
        48 * 4096,
        "attempt-2 redirect/error response bodies metered only by transport, "
        "unattributed to the arm ledger (requests counted, bodies not)",
    )
    durable.register_gap(
        "data/crawl=CC-MAIN-2014-15/train-01787-of-02772.parquet",
        6,
        6 * 4096,
        "pre-fix prior run: redirect follows uncounted and their bodies "
        "unattributed (attempts counted)",
    )
    receipt = durable.reconciliation_receipt(command="reconcile-carry-in", exit_status=0)
    return durable, receipts.seal(receipt)


def cmd_reconcile_carry_in(args: argparse.Namespace) -> int:
    from xlm.data.evidence_v2 import carry as carry_mod

    try:
        _, sealed = _v21_carry(Path(args.g_root))
    except (carry_mod.CarryError, canonical.CanonicalError, OSError) as exc:
        return _fail(str(exc))
    if args.out is not None:
        try:
            receipts.publish_manifest(args.out, sealed)
        except (receipts.ReceiptError, OSError) as exc:
            return _fail(str(exc))
    else:
        print(canonical.canonical_bytes(sealed).decode("utf-8"))
    return 0


def cmd_audit_m_history(args: argparse.Namespace) -> int:
    from xlm.data.evidence_v2 import m_audit as m_audit_mod

    try:
        g_root = Path(args.g_root)
        old = _read_receipt(g_root / "footer_evidence.incomplete.json")
        complete = _read_receipt(g_root / "footer_evidence.json")
        target = "data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet"
        verdict = m_audit_mod.audit_m_file_compliance(old, complete, target)
    except (m_audit_mod.AuditError, canonical.CanonicalError, OSError) as exc:
        return _fail(str(exc))
    print(json.dumps(verdict, indent=2))
    return 0


def cmd_build_child_plans(args: argparse.Namespace) -> int:
    from xlm.data.evidence_v2 import child_plans as child_plans_mod
    from xlm.data.evidence_v2 import lineage as lineage_mod
    from xlm.data.evidence_v2 import m_audit as m_audit_mod
    from xlm.data.evidence_v2 import schedule as schedule_mod

    try:
        g_root = Path(args.g_root)
        old = _read_receipt(g_root / "footer_evidence.incomplete.json")
        complete = _read_receipt(g_root / "footer_evidence.json")
        attempt2 = _read_receipt(g_root / "text_cost_evidence_attempt2.incomplete.json")
        selection = _read_receipt(g_root / "text_selection_manifest.json")
        if attempt2.get("digest") != frozen.V21_COSTMAP_DIGEST:
            return _fail("attempt-2 cost map digest mismatch")
        freeze_path = ROOT / "docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2/freeze.json"
        freeze22 = json.loads(freeze_path.read_bytes())
        lineage_mod.verify_v22_freeze(freeze22)
        contents = {
            key: Path(bound["path"]).read_bytes()
            for key, bound in freeze22.get("parents", {}).items()
        }
        parents = lineage_mod.complete_parent_descriptors(freeze22, contents)
        target = "data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet"
        try:
            audit = m_audit_mod.audit_m_file_compliance(old, complete, target)
        except m_audit_mod.AuditError as exc:
            audit = {"conclusion": "C", "blocked": True, "reason": str(exc), "evidence": {}}
        m_windows = [
            {"file": u["file"], "absolute_window": u["absolute_window"]}
            for u in complete.get("units", [])
        ]
        m_data = schedule_mod.m_data_schedule(complete.get("units", []))
        revalidation_files = {
            entry["file"]: {"etag": entry["etag"], "remote_length": entry["remote_length"]}
            for entry in freeze22.get("M_accounting", {}).get("file_history", [])
        }
        revalidation_history = {
            entry["file"]: int(entry["historical_requests"])
            for entry in freeze22.get("M_accounting", {}).get("file_history", [])
        }
        m_revalidation = schedule_mod.m_revalidation_plan(
            revalidation_files, historical_requests=revalidation_history
        )
        files: dict[str, list[dict[str, Any]]] = {}
        lengths: dict[str, int] = {}
        wanted: dict[str, list[int]] = {}
        decomp_by_file: dict[str, int] = {}
        for unit in attempt2.get("units", []):
            chunks = []
            for group in unit["text_costs"]["groups"]:
                for chunk in group["text_chunks"]:
                    chunks.append(
                        {
                            "offset": chunk["offset"],
                            "dictionary_page_offset": chunk.get("dictionary_page_offset"),
                            "compressed_bytes": chunk["compressed_bytes"],
                        }
                    )
            files[unit["file"]] = chunks
            lengths[unit["file"]] = int(unit["remote_length"])
            wanted[unit["file"]] = list(unit["wanted_rows"])
            decomp_by_file[unit["file"]] = int(unit["decompressed_upper_bytes"])
        scheduled = schedule_mod.schedule_arm(files, lengths)
        caps = schedule_mod.v21_data_caps()
        durable, carry_sealed = _v21_carry(g_root)
        carried = durable.effective()
        carried_file_requests = carried.get("requests_per_file", {})
        carried_file_bytes = {
            name: cell.get("footer", 0)
            for name, cell in carried.get("transfer_per_file", {}).items()
        }
        remaining = {
            name: schedule_mod.remaining_budgets(
                nominal_data_ranges=scheduled["files"][name]["nominal_data_range_count"],
                nominal_data_bytes=scheduled["files"][name]["nominal_data_bytes"],
                nominal_controls=scheduled["files"][name]["nominal_control_requests"],
                carried_requests=int(sum(carried_file_requests.get(name, {}).values())),
                carried_data_bytes=0,
                carried_footer_bytes=int(carried_file_bytes.get(name, 0)),
                request_cap=caps["per_file_requests"],
                data_cap=caps["per_file_data"],
                total_cap=schedule_mod.v21_data_caps()["per_file_total"],
            )
            for name in scheduled["files"]
        }
        from xlm.data.evidence_v2 import reserves as reserves_mod

        reservations = {
            name: reserves_mod.memory_reservation(
                compressed_staged_bytes=sum(c["compressed_bytes"] for c in files[name]),
                decompressed_upper_bytes=decomp_by_file[name],
                output_retained_bytes=reserves_mod.output_retained_upper(len(wanted[name])),
            )
            for name in scheduled["files"]
        }
        physical = schedule_mod.t_physical_plan(
            files={
                name: {
                    "nominal_ranges": scheduled["files"][name]["nominal_data_range_count"],
                    "nominal_bytes": scheduled["files"][name]["nominal_data_bytes"],
                }
                for name in scheduled["files"]
            },
            carried_requests=int(carried.get("requests", 0)),
            carried_bytes=int(carried.get("response_body_bytes", 0)),
            request_cap=caps["arm_requests"],
            data_cap=caps["arm_data"],
            total_cap=schedule_mod.v21_data_caps()["arm_total"],
        )
        adopted = 23807 + 24617 + 7000
        stages = []
        for name in sorted(scheduled["files"]):
            out = reserves_mod.output_retained_upper(len(wanted[name]))
            scratch = (
                sum(c["compressed_bytes"] for c in files[name])
                + decomp_by_file[name]
                + 33554432
                + out
                + out
            )
            stages.append({"file": name, "scratch": scratch, "final": out})
        disk = reserves_mod.disk_schedule(
            adopted_artifact_bytes=adopted,
            per_file_stages=stages,
            review_package_bytes=9465152,
            log_bytes=1048576,
        )
        disk["conditional"] = (
            "final-disk fit uses an estimated review package; "
            "conditional on measured labeling volume"
        )
        deadlines = reserves_mod.DeadlineTracker(history_unknown=True)
        m_items = {
            "scientific_identity_ok": True,
            "lineage_ok": True,
            "historical_accounting_ok": False,
            "range_schedule_ok": True,
            "requests_ok": False,
            "response_bytes_ok": False,
            "decompression_ok": True,
            "scan_ok": True,
            "memory_supervision_ok": False,
            "disk_schedule_ok": False,
            "deadlines_ok": False,
        }
        m_reasons = [
            "cumulative 12 > 10 first-file planning usage (conclusion A)",
            "historical redirect/error bodies unmeasured (no sound bound)",
            "historical durations unmeasured",
            "no supervised live enforcement has run",
        ]
        t_items = {
            "scientific_identity_ok": True,
            "lineage_ok": True,
            "historical_accounting_ok": False,
            "range_schedule_ok": True,
            "requests_ok": True,
            "response_bytes_ok": False,
            "decompression_ok": True,
            "scan_ok": True,
            "memory_supervision_ok": False,
            "disk_schedule_ok": False,
            "deadlines_ok": False,
        }
        t_reasons = [
            "historical redirect/error bodies unmeasured (bounded gaps only)",
            "historical durations unmeasured",
            "final-disk fit conditional on measured labeling volume",
            "no supervised live enforcement has run",
        ]
        bundle = child_plans_mod.build_v22_bundle(
            freeze22=freeze22,
            parents=parents,
            m_audit_verdict=audit,
            m_windows=m_windows,
            m_data_schedule=m_data,
            m_revalidation=m_revalidation,
            t_schedule={**scheduled, "physical": physical},
            t_remaining=remaining,
            t_physical=physical,
            t_reservations=reservations,
            carry_digest=carry_sealed["digest"],
            costmap_digest=frozen.V21_COSTMAP_DIGEST,
            selection_digest=selection["digest"],
            total_selected=selection["total_selected"],
            wanted_by_file=wanted,
            command="build-child-plans",
            m_readiness_items=m_items,
            m_readiness_reasons=m_reasons,
            t_readiness_items=t_items,
            t_readiness_reasons=t_reasons,
            t_disk=disk,
            t_memory=reservations,
            t_deadlines=deadlines.state(),
        )
        artifacts: dict[str, str] = {}
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "arm_m_child_plan.json",
            "arm_t_child_plan.json",
            "readiness_review.json",
            "ledger_reconciliation.json",
            "disk_schedule.json",
            "memory_schedule.json",
            "deadline_schedule.json",
            "range_schedule_m.json",
            "range_schedule_t.json",
        ):
            path = out_dir / name
            if path.exists():
                return _fail(f"refusing to overwrite {path}")
        filing = dict(bundle)
        filing["ledger_reconciliation.json"] = carry_sealed
        for name, body in filing.items():
            receipts.publish_manifest(out_dir / name, body)
            artifacts[name] = body["digest"]
    except (
        m_audit_mod.AuditError,
        child_plans_mod.ChildPlanError,
        schedule_mod.ScheduleError,
        canonical.CanonicalError,
        OSError,
    ) as exc:
        return _fail(str(exc))
    print(json.dumps(artifacts, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evidence-v2.0 offline mechanisms.")
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify-freeze", help="Recompute frozen bindings.")
    verify.set_defaults(func=cmd_verify_freeze)

    expand = sub.add_parser("expand-inventory", help="Print verified winners.")
    expand.set_defaults(func=cmd_expand_inventory)

    dry_m = sub.add_parser("dry-m-plan", help="Dry Arm-M window plans.")
    dry_m.add_argument("--out", type=Path, default=None)
    dry_m.set_defaults(func=cmd_dry_m_plan)

    select = sub.add_parser("select-text", help="X: metadata -> G: locator manifest.")
    select.add_argument("--records", type=Path, default=DEFAULT_X / "raw/selected_records.jsonl")
    select.add_argument("--bundle", type=Path, default=DEFAULT_X / "bundle.json")
    select.add_argument("--execution", type=Path, default=DEFAULT_X / "execution.json")
    select.add_argument("--out", type=Path, default=DEFAULT_G_ROOT / "text_selection_manifest.json")
    select.set_defaults(func=cmd_select_text)

    dry_t = sub.add_parser("dry-t-plan", help="Dry Arm-T sparse cost plan.")
    dry_t.add_argument("--selection-manifest", type=Path, required=True)
    dry_t.set_defaults(func=cmd_dry_t_plan)

    footers = sub.add_parser("plan-footers", help="Footer planning (gated).")
    footers.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    footers.add_argument("--authorize-network", action="store_true", default=False)
    footers.add_argument("--out", type=Path, default=None)
    footers.set_defaults(func=cmd_plan_footers)

    costs = sub.add_parser("plan-text-costs", help="Text cost planning (gated).")
    costs.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    costs.add_argument("--authorize-network", action="store_true", default=False)
    costs.add_argument("--selection-manifest", type=Path, required=True)
    costs.add_argument("--out", type=Path, default=None)
    costs.set_defaults(func=cmd_plan_text_costs)

    verify21 = sub.add_parser("verify-v21-freeze", help="Verify v2.1 freeze + parents.")
    verify21.add_argument("--freeze", type=Path, default=EVIDENCE_V21 / "freeze.json")
    verify21.add_argument("--g-root", type=Path, default=DEFAULT_G_ROOT)
    verify21.set_defaults(func=cmd_verify_v21_freeze)

    reconcile = sub.add_parser("reconcile-carry-in", help="Build T carry-in ledger.")
    reconcile.add_argument("--g-root", type=Path, default=DEFAULT_G_ROOT)
    reconcile.add_argument("--out", type=Path, default=None)
    reconcile.set_defaults(func=cmd_reconcile_carry_in)

    audit_m = sub.add_parser("audit-m-history", help="Audit M historical accounting.")
    audit_m.add_argument("--g-root", type=Path, default=DEFAULT_G_ROOT)
    audit_m.set_defaults(func=cmd_audit_m_history)

    child = sub.add_parser("build-child-plans", help="Dry v2.1 child artifacts.")
    child.add_argument("--g-root", type=Path, default=DEFAULT_G_ROOT)
    child.add_argument("--out-dir", type=Path, required=True)
    child.set_defaults(func=cmd_build_child_plans)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except OSError as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
