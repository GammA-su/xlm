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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except OSError as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
