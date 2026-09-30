# Requires: operator-run only, offline (verification and dry planning, no network).
"""Verify and plan the frozen Essential-Web B-normal production selector.

Subcommands (all offline, read-only on every evidence and operator root):

- ``verify-m``: run the production adapters' admission over the sealed
  4,096-row Arm-M derived input and compare with the sealed M sweep.
- ``verify-development``: the same over the frozen development bundle.
- ``dry-plan``: write the dry production-selection plan.
- ``readiness``: write the production-acquisition readiness vector.

Arm-T material is never opened: any ``t_*`` or ``sealed/`` path is refused.
Metadata rows only; a row that carries ``text`` is refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from xlm.data.acquisition import plan as acquisition_plan
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.adapters.mix01_adapters import EssentialWebSelectedAdapter
from xlm.data.adapters.rejections import ADAPT_INPUT_MAX_BYTES
from xlm.data.evidence_v2 import canonical, fasttrack_freeze, m_analysis
from xlm.data.sources import essential_web_production as production
from xlm.data.sources.mix01 import load_mix01_views

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "docs" / "implementation" / "evidence"
M_DIR = EVIDENCE / "ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS"
FREEZE_PATH = EVIDENCE / "ESSENTIAL-WEB-SELECTOR-FASTTRACK-FREEZE" / fasttrack_freeze.FREEZE_NAME
VIEWS_PATH = REPO_ROOT / "recipes" / "mixtures" / "mix01_views.yaml"
PRESET_PATH = REPO_ROOT / "recipes" / "mixtures" / "mix01.yaml"
QUOTAS_PATH = REPO_ROOT / "recipes" / "mixtures" / "mix01_quotas_6b.yaml"


def _adapters() -> list[EssentialWebSelectedAdapter]:
    return [EssentialWebSelectedAdapter(name) for name in selector.ADMITTED_COMPONENTS]


def _write(path: Path, payload: dict[str, Any]) -> None:
    canonical.write_atomic(path, m_analysis.dumps(payload))


def _bound(path: Path, expected: dict[str, Any], what: str) -> bytes:
    m_analysis.refuse_t_material(path)
    raw = path.read_bytes()
    if len(raw) != expected["bytes"] or m_analysis.sha256_bytes(raw) != expected["sha256"]:
        raise production.ProductionCheckError(f"{what} differs from its bound bytes/hash")
    return raw


def _finish(result: dict[str, Any], output: Path) -> int:
    _write(output, result)
    print(
        json.dumps(
            {key: result[key] for key in ("replicate", "rows", "final", "sum", "match")},
            indent=2,
            sort_keys=True,
        )
    )
    if not result["match"]:
        for line in result["mismatches"]:
            print(f"essential_web_production_selector: mismatch: {line}", file=sys.stderr)
        return 1
    return 0


def cmd_verify_m(args: argparse.Namespace) -> int:
    seal, summary, _ = fasttrack_freeze.load_m_evidence(M_DIR)
    adapted = seal["adapted_input"]
    payload = _bound(args.derived_input, adapted, "M derived input")
    manifest = json.loads((M_DIR / "adapted_input_manifest.json").read_bytes().decode("utf-8"))
    per_crawl = json.loads((M_DIR / "m_sweep" / "per_crawl.json").read_bytes().decode("utf-8"))
    total, crawls = production.expected_b_normal(summary, per_crawl)
    crawl_of = {entry["file"]: entry["crawl"] for entry in manifest["files"]}
    result = production.reproduce_b_normal(payload, crawl_of, _adapters(), total, crawls)
    result.update(
        {
            "replicate": "sealed Arm-M confirmation (4096 rows)",
            "input": {"sha256": adapted["sha256"], "bytes": adapted["bytes"]},
            "m_seal_digest": seal["seal_digest"],
            "selector": selector.selector_identity(),
        }
    )
    return _finish(result, args.output)


def cmd_verify_development(args: argparse.Namespace) -> int:
    seal, _, _ = fasttrack_freeze.load_m_evidence(M_DIR)
    summary, binding = fasttrack_freeze.load_development(args.development_dir, seal)
    per_crawl_raw = _bound(
        args.development_dir / "per_crawl.json",
        binding["artifacts"]["per_crawl.json"],
        "development per-crawl artifact",
    )
    bundle = json.loads((args.recon_dir / "bundle.json").read_bytes().decode("utf-8"))
    frozen = binding["binding"]
    if canonical.self_digest(bundle) != bundle.get("digest"):
        raise production.ProductionCheckError("development bundle self-digest mismatch")
    if bundle["digest"] != frozen["bundle_digest"] or bundle["revision"] != frozen["revision"]:
        raise production.ProductionCheckError("bundle is not the frozen development bundle")
    if bundle["combined_sha256"] != frozen["combined_sha256"]:
        raise production.ProductionCheckError("bundle payload hash is not the frozen value")
    payload = _bound(
        args.recon_dir / "raw" / "selected_records.jsonl",
        {"bytes": bundle["combined_bytes"], "sha256": bundle["combined_sha256"]},
        "development records",
    )
    total, crawls = production.expected_b_normal(summary, json.loads(per_crawl_raw.decode("utf-8")))
    crawl_of = {part["file"]: part["crawl"] for part in bundle["parts"]}
    result = production.reproduce_b_normal(payload, crawl_of, _adapters(), total, crawls)
    result.update(
        {
            "replicate": "frozen development (4096 rows)",
            "input": {"sha256": bundle["combined_sha256"], "bytes": bundle["combined_bytes"]},
            "bundle_digest": bundle["digest"],
            "development_manifest_digest": binding["manifest"]["digest"],
            "selector": selector.selector_identity(),
        }
    )
    return _finish(result, args.output)


def _limits() -> dict[str, Any]:
    return {
        "pilot_max_transferred_bytes": acquisition_plan.PILOT_MAX_TRANSFERRED_BYTES,
        "pilot_max_records": acquisition_plan.PILOT_MAX_RECORDS,
        "pilot_max_output_disk_bytes": acquisition_plan.PILOT_MAX_OUTPUT_DISK_BYTES,
        "pilot_max_decompressed_bytes": acquisition_plan.PILOT_MAX_DECOMPRESSED_BYTES,
        "pilot_max_scanned_records": acquisition_plan.PILOT_MAX_SCANNED_RECORDS,
        "pilot_max_requests": acquisition_plan.PILOT_MAX_REQUESTS,
        "adapt_default_max_input_bytes": ADAPT_INPUT_MAX_BYTES,
        "production_limits": "set per plan with --max-bytes/--max-records/--max-output-disk; "
        "anything above the pilot caps requires recorded production admission",
    }


def cmd_dry_plan(args: argparse.Namespace) -> int:
    plan = production.build_dry_plan(
        freeze=fasttrack_freeze.load_freeze(FREEZE_PATH),
        registry=load_mix01_views(VIEWS_PATH),
        weights=yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8"))["weights"],
        quotas=yaml.safe_load(QUOTAS_PATH.read_text(encoding="utf-8")),
        limits=_limits(),
    )
    _write(args.output, plan)
    print(json.dumps({"units": len(plan["units"]), "status": plan["status"]}, sort_keys=True))
    return 0


def _optional(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    loaded = json.loads(path.read_bytes().decode("utf-8"))
    return loaded if isinstance(loaded, dict) else None


def cmd_readiness(args: argparse.Namespace) -> int:
    readiness = production.evaluate_readiness(
        freeze=fasttrack_freeze.load_freeze(FREEZE_PATH),
        registry=load_mix01_views(VIEWS_PATH),
        reproductions={
            "development": _optional(args.development_reproduction),
            "m": _optional(args.m_reproduction),
        },
        xlm_home=args.xlm_home,
        inventory_path=args.inventory,
        calibration_path=args.calibration,
    )
    _write(args.output, readiness)
    print(json.dumps(readiness["vector"], indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Essential-Web production selector (offline).")
    sub = parser.add_subparsers(dest="command", required=True)
    verify_m = sub.add_parser("verify-m", help="Reproduce B-normal on the sealed M replicate.")
    verify_m.add_argument("--derived-input", type=Path, required=True)
    verify_m.add_argument("--output", type=Path, required=True)
    verify_m.set_defaults(func=cmd_verify_m)
    verify_dev = sub.add_parser("verify-development", help="Reproduce B-normal on development.")
    verify_dev.add_argument("--recon-dir", type=Path, required=True)
    verify_dev.add_argument("--development-dir", type=Path, required=True)
    verify_dev.add_argument("--output", type=Path, required=True)
    verify_dev.set_defaults(func=cmd_verify_development)
    dry = sub.add_parser("dry-plan", help="Write the dry production-selection plan.")
    dry.add_argument("--output", type=Path, required=True)
    dry.set_defaults(func=cmd_dry_plan)
    ready = sub.add_parser("readiness", help="Write the production readiness vector.")
    ready.add_argument("--m-reproduction", type=Path)
    ready.add_argument("--development-reproduction", type=Path)
    ready.add_argument("--xlm-home", type=Path)
    ready.add_argument("--inventory", type=Path)
    ready.add_argument("--calibration", type=Path)
    ready.add_argument("--output", type=Path, required=True)
    ready.set_defaults(func=cmd_readiness)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (
        production.ProductionCheckError,
        selector.SelectorIdentityError,
        fasttrack_freeze.FreezeError,
        m_analysis.AnalysisError,
        canonical.CanonicalError,
        OSError,
    ) as exc:
        print(f"essential_web_production_selector: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
