"""Seal the completed Essential-Web first pass as one immutable pool binding (OFFLINE).

``build`` loads the fast campaign through its own verified loader (source,
selector, adapter code, inventory, quotas, code-compatibility chain), recomputes
the campaign accounting from the unit receipts, re-hashes every retained raw
Parquet file and every canonical output the receipts bind, binds the stored
production admissions, and writes ``first-pass-seal.json`` once under the
campaign plan root. An identical rebuild is a no-op; a different one is refused.

``verify`` rebuilds the seal from the store and requires it to equal a saved one.

Nothing here downloads, deletes, moves or rewrites an existing file; the only
write is the new write-once seal (and an optional write-once copy).

Exit codes: 0 success, 1 refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import essential_web_fast as tool

from xlm.data.acquisition.source_parquet import identity_path
from xlm.data.sources import essential_web_calibration as calibration
from xlm.data.sources import essential_web_local as local
from xlm.data.sources import essential_web_pool_seal as seal_lib
from xlm.data.sources import essential_web_recovery as recovery

EXIT_OK, EXIT_REFUSED = 0, 1
READ_BYTES = 8 * 1024 * 1024
DEFAULT_WORKERS = 8
MAX_WORKERS = 16
UNIT_FILES = frozenset(
    {local.RECEIPT_FILENAME}
    | {
        f"{view}/{name}"
        for view in calibration.VIEWS
        for name in ("documents.jsonl", local.LEDGER_FILENAME, "adaptation_summary.json")
    }
)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(READ_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def planned_batches(campaign: tool.Campaign) -> list[dict[str, Any]]:
    """Every planned batch with its record, membership, plan and authorization identity."""
    batches: list[dict[str, Any]] = []
    for directory in sorted(campaign.plans.glob("b[0-9][0-9][0-9][0-9]")):
        if not (directory / "batch.json").is_file():
            continue
        index = int(directory.name[1:])
        record = tool.batch_record(campaign, index)
        if not (directory / "authorization.json").is_file():
            raise seal_lib.SealError(f"batch {index} is planned but was never authorized or run")
        authorization = tool.read_json(directory / "authorization.json")
        if authorization.get("authorization_digest") != record["authorization_digest"]:
            raise seal_lib.SealError(f"batch {index} authorization differs from its record")
        entry: dict[str, Any] = {
            "index": index,
            "files": len(record["files"]),
            "batch_record_digest": record["digest"],
            "membership_digest": record["membership_digest"],
            "plan_id": record["plan_id"],
            "plan_hash": record["plan_hash"],
            "authorization_digest": record["authorization_digest"],
        }
        recovery_authorization = directory / "recovery-authorization.json"
        if recovery_authorization.is_file():
            entry["recovery_authorization_digest"] = tool.read_json(recovery_authorization)[
                "digest"
            ]
        batches.append(entry)
    return batches


def content_targets(
    campaign: tool.Campaign, receipt: Mapping[str, Any]
) -> list[tuple[Path, int | None, str]]:
    """(path, size or None, SHA-256) of every byte a receipt binds."""
    targets: list[tuple[Path, int | None, str]] = [
        (
            campaign.root / str(receipt["raw"]["path"]),
            int(receipt["raw"]["bytes"]),
            str(receipt["raw"]["sha256"]),
        )
    ]
    unit = campaign.unit_dir(int(receipt["batch"]), int(receipt["inventory_rank"]))
    for view in calibration.VIEWS:
        entry = receipt["views"][view]
        targets += [
            (
                unit / view / "documents.jsonl",
                int(entry["documents_file_bytes"]),
                str(entry["documents_sha256"]),
            ),
            (
                unit / view / local.LEDGER_FILENAME,
                int(entry["rejections_file_bytes"]),
                str(entry["rejections_file_sha256"]),
            ),
            (
                unit / view / "adaptation_summary.json",
                None,
                str(entry["adaptation_summary_sha256"]),
            ),
        ]
    return targets


def check_layout(campaign: tool.Campaign, receipts: Sequence[Mapping[str, Any]]) -> None:
    """Exactly the published units, each with exactly its files; no staging residue."""
    canonical_root = campaign.root / str(campaign.config["roots"]["canonical"])
    staging = canonical_root / ".staging"
    if staging.exists() and any(path.is_file() for path in staging.rglob("*")):
        raise seal_lib.SealError("an unpublished staging unit remains")
    expected = {
        campaign.unit_dir(int(r["batch"]), int(r["inventory_rank"])).resolve() for r in receipts
    }
    found = {path.resolve() for path in canonical_root.glob("b[0-9]*/f[0-9]*") if path.is_dir()}
    if found != expected:
        raise seal_lib.SealError("a canonical unit directory has no sealed receipt")
    for unit in sorted(expected):
        files = {path.relative_to(unit).as_posix() for path in unit.rglob("*") if path.is_file()}
        if files != UNIT_FILES:
            raise seal_lib.SealError(f"{unit.name} holds unexpected or missing files")


def check_raw_identity(campaign: tool.Campaign, receipt: Mapping[str, Any]) -> None:
    raw = campaign.root / str(receipt["raw"]["path"])
    if raw != campaign.raw_path(str(receipt["file"])):
        raise seal_lib.SealError(f"{receipt['file']}: receipt raw path is not its store path")
    if tool.read_json(identity_path(raw)) != receipt["source"]:
        raise seal_lib.SealError(f"{receipt['file']}: identity sidecar differs from the receipt")


def verify_content(targets: Sequence[tuple[Path, int | None, str]], workers: int) -> int:
    """Hash every target (bounded streaming reads); refuse on the first mismatch list."""

    def check(target: tuple[Path, int | None, str]) -> str | None:
        path, size, expected = target
        if not path.is_file():
            return f"{path}: missing"
        if size is not None and path.stat().st_size != size:
            return f"{path}: size differs from the receipt"
        return None if sha256_of(path) == expected else f"{path}: SHA-256 differs"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        problems = [problem for problem in pool.map(check, targets) if problem]
    if problems:
        raise seal_lib.SealError(f"{len(problems)} content mismatch(es): {problems[:5]}")
    return sum(size or 0 for _, size, _ in targets)


def amendment_bindings(campaign: tool.Campaign) -> dict[str, Any]:
    """Recovery amendment, the ordered code-compatibility chain and the running code."""
    result: dict[str, Any] = {
        "recovery": None,
        "compatibility": [],
        "running_code_sha256": recovery.code_identity(tool.CODE_REPO),
    }
    amendment = campaign.recovery
    if amendment is None:
        return result
    result["recovery"] = {
        "path": recovery.MANIFEST,
        "digest": amendment["digest"],
        "batch": amendment["batch"],
        "file": amendment["file"],
        "max_record_bytes": amendment["max_record_bytes"],
    }
    for relative, kind, allowed in recovery.COMPATIBILITY:
        path = tool.CODE_REPO / relative
        if not path.is_file():
            break
        record = tool.read_json(path)
        if record.get("campaign") != campaign.config["digest"] or record.get("kind") != kind:
            raise seal_lib.SealError(f"{relative} does not belong to this campaign")
        result["compatibility"].append(
            {"path": relative, "kind": kind, "digest": record["digest"], "changes": sorted(allowed)}
        )
    return result


def admission_bindings(campaign: tool.Campaign) -> dict[str, Any]:
    """The stored production admission of every view; each must pass the C04 gate now."""
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths
    from xlm.data.sources import admission

    store = ArtifactStore(ArtifactPaths.from_env())
    binding = campaign.config["binding"]
    result: dict[str, Any] = {}
    for view in calibration.VIEWS:
        evidence = admission.load_probe_evidence(binding["source_id"], view, store)
        decision = admission.load_admission_decision(binding["source_id"], view, store)
        if evidence is None or decision is None:
            raise seal_lib.SealError(f"{view}: no stored probe evidence or admission decision")
        gate = admission.AdmissionGate.evaluate(evidence, decision)
        if not gate.admitted or decision.immutable_revision != binding["revision"]:
            raise seal_lib.SealError(f"{view}: stored admission does not pass the C04 gate")
        entry: dict[str, Any] = {}
        for kind, base, filename in (
            ("probe_evidence", f"probe_{binding['source_id']}_{view}", "probe_evidence.json"),
            (
                "admission_decision",
                f"admission_{binding['source_id']}_{view}",
                "admission_decision.json",
            ),
        ):
            attempt = admission.latest_attempt(store, kind, base)
            artifact = admission.attempt_artifact_id(base, attempt)
            entry[kind] = {
                "artifact": artifact,
                "sha256": sha256_of(store.paths.root / kind / artifact / filename),
            }
        entry.update(
            {
                "probe_fingerprint": evidence.probe_fingerprint,
                "contract_version": decision.contract_version,
                "adapter_id": decision.adapter_id,
                "selector_binding": decision.selector_binding,
                "benchmark_risk": str(decision.benchmark_risk),
                "gate": str(gate.status),
            }
        )
        result[view] = entry
    return result


def build(campaign: tool.Campaign, workers: int, *, content: bool = True) -> dict[str, Any]:
    """Verify the store and return the seal (nothing is written here)."""
    state, _ = tool.campaign_state(campaign)
    canonical_root = campaign.root / str(campaign.config["roots"]["canonical"])
    receipts = [
        tool.load_receipt(path, campaign)
        for path in sorted(canonical_root.glob(f"b*/f*/{local.RECEIPT_FILENAME}"))
    ]
    check_layout(campaign, receipts)
    for receipt in receipts:
        check_raw_identity(campaign, receipt)
    if content:
        targets = [target for receipt in receipts for target in content_targets(campaign, receipt)]
        verified = verify_content(targets, workers)
        print(f"content verified: {len(targets):,} files, {verified:,} sized bytes")
    return seal_lib.build_seal(
        config=campaign.config,
        receipts=receipts,
        batches=planned_batches(campaign),
        state=state,
        amendments=amendment_bindings(campaign),
        admissions=admission_bindings(campaign),
    )


def seal_path(campaign: tool.Campaign) -> Path:
    return campaign.plans / seal_lib.SEAL_FILENAME


def report(seal: Mapping[str, Any]) -> None:
    totals = seal["totals"]
    print(f"seal digest: {seal['digest']}")
    print(
        f"campaign {seal['campaign']['digest']}: {len(seal['batches'])} batches, "
        f"{totals['files']} files, {totals['rows']:,} rows"
    )
    for view in calibration.VIEWS:
        entry = seal["sufficiency"][view]
        print(
            f"  {view}: {entry['acquired_canonical_bytes']:,} canonical bytes, "
            f"{entry['estimated_tokens']:,.0f} estimated tokens "
            f"(first-pass target {entry['first_pass_estimated_token_target']:,}) "
            f"-> {entry['status']}; membership {seal['membership'][view]['digest']}"
        )
    print("stage: first-pass canonical availability (not final training exposure)")
    print("C05 NOT RUN; tokenizer NOT TRAINED; exact count PENDING; training NOT PERMITTED")


def cmd_build(args: argparse.Namespace) -> int:
    campaign = tool.load_campaign(args.campaign, args.data_root)
    seal = build(campaign, args.workers)
    seal_lib.check_seal(seal)
    wrote = tool.write_once(seal_path(campaign), seal)
    print(f"{'wrote' if wrote else 'unchanged'}: {seal_path(campaign)}")
    if args.copy is not None:
        copied = tool.write_once(args.copy, seal)
        print(f"{'wrote' if copied else 'unchanged'}: {args.copy}")
    report(seal)
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    campaign = tool.load_campaign(args.campaign, args.data_root)
    path = args.seal if args.seal is not None else seal_path(campaign)
    saved = tool.read_json(path)
    seal_lib.check_seal(saved)
    rebuilt = build(campaign, args.workers, content=not args.skip_content)
    if rebuilt != saved:
        raise seal_lib.SealError("the store no longer reproduces the saved seal")
    scope = "PARTIAL (content not re-hashed)" if args.skip_content else "FULL"
    print(f"seal verified ({scope}): {path}")
    report(saved)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--campaign", type=Path, default=tool.REPO / tool.FAST_DIR / "campaign.json"
    )
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    sub = parser.add_subparsers(dest="command", required=True)
    build_cmd = sub.add_parser("build", help="OFFLINE: verify the store and write the seal once.")
    build_cmd.add_argument("--copy", type=Path, default=None)
    build_cmd.set_defaults(func=cmd_build)
    verify_cmd = sub.add_parser("verify", help="OFFLINE: rebuild and compare with a saved seal.")
    verify_cmd.add_argument("--seal", type=Path, default=None)
    verify_cmd.add_argument("--skip-content", action="store_true")
    verify_cmd.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.workers <= MAX_WORKERS:
        print(f"essential_web_pool_seal: refused: --workers must be 1..{MAX_WORKERS}")
        return EXIT_REFUSED
    if args.data_root is None:
        configured = os.environ.get("XLM_DATA_ROOT", "")
        if not configured:
            print("essential_web_pool_seal: refused: set XLM_DATA_ROOT or --data-root")
            return EXIT_REFUSED
        args.data_root = Path(configured)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    try:
        return int(args.func(args))
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        print(f"essential_web_pool_seal: refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
