# Requires: operator-run only, offline (planning decisions, no network).
"""Adoption checks for calibration reruns (offline, no network).

Decides, per calibration stage, whether prior outputs may be REUSED or the
stage must RUN — using only existing repository adoption/resume semantics:

- probe evidence: ArtifactStore same-payload republication is a no-op, but
  a fresh probe mints new timestamps/metrics, so republication CONFLICTS.
  Reuse requires loading the persisted evidence through
  ``load_probe_evidence`` and verifying source/view/revision/repository,
  real-observed type, discovery outcome, and completion.
- sample-blocks rows/report and adaptation outputs: plain files; reuse
  requires byte-level parameter agreement, never silent overwrite.
- acquisition plans: ``save_acquisition_plan`` is same-hash idempotent, so
  rerunning the CLI is safe; reuse additionally verifies the stored plan
  against current parameters and its own hash.
- fetch journals: natively resumable; nothing to decide here.
- verify publications: republication embeds fresh timestamps, so it
  CONFLICTS; reuse requires the published artifact to verify and bind to
  the current plan and outputs (then the driver re-confirms with
  ``verify --no-publish`` instead of republishing).
- record aggregation: handled by ``mix01_inventory.py record --adopt``.

Exit codes: 0 = no usable output, run the stage; 2 = compatible output
exists, reuse it and skip the mutating call; 1 = incompatible, corrupt or
incomplete output: FAIL CLOSED (never delete, overwrite, or mint a fresh
identity to bypass it). Nothing here touches the network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

RUN = 0
REFUSE = 1
REUSE = 2


class AdoptionRefused(ValueError):
    """Compatible prior output is absent or must not be reused."""


def _fail(message: str) -> int:
    print(f"calibration_adopt: error: {message}", file=sys.stderr)
    return REFUSE


def _read_json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise AdoptionRefused(f"{what} at '{path}' is unreadable: {exc}") from exc


def _store(root: Path) -> Any:
    from xlm.artifacts.store import ArtifactStore
    from xlm.core.paths import ArtifactPaths

    return ArtifactStore(ArtifactPaths(root=root))


def check_probe_evidence(
    record: Any, *, source: str, view: str, revision: str, repository: str
) -> None:
    """Verify persisted probe evidence binds this exact source/view/revision."""
    if record.source_id != source or record.view_id != view:
        raise AdoptionRefused(
            f"probe evidence is for '{record.source_id}:{record.view_id}', not '{source}:{view}'"
        )
    if record.immutable_revision != revision:
        raise AdoptionRefused(
            f"probe evidence pins revision '{record.immutable_revision}', not '{revision}'"
        )
    if record.repository != repository:
        raise AdoptionRefused(
            f"probe evidence repository '{record.repository}' differs from '{repository}'"
        )
    if record.evidence_type.value != "real_observed":
        raise AdoptionRefused(
            f"probe evidence type '{record.evidence_type.value}' is not real_observed"
        )
    if record.outcome.value not in ("accessible", "partial"):
        raise AdoptionRefused(
            f"probe outcome '{record.outcome.value}' carries no usable discovery facts"
        )
    if (record.observed_files_count or 0) < 1:
        raise AdoptionRefused("probe evidence observes zero files")


def cmd_probe(args: argparse.Namespace) -> int:
    from xlm.data.sources.admission import load_probe_evidence

    store = _store(args.store)
    artifact_dir = args.store / "probe_evidence" / f"probe_{args.source}_{args.view}"
    if not artifact_dir.is_dir():
        print("no completed probe evidence; run the live probe")
        return RUN
    if not (artifact_dir / "_COMPLETED").is_file():
        return _fail(
            f"probe evidence at '{artifact_dir}' lacks its _COMPLETED marker "
            "(incomplete); refusing to overwrite it or mint a fresh probe identity"
        )
    try:
        record = load_probe_evidence(args.source, args.view, store)
    except Exception as exc:
        return _fail(f"probe evidence store read failed: {exc}")
    if record is None:
        return _fail(
            f"probe evidence at '{artifact_dir}' is corrupt or incomplete "
            "(unreadable or failed verification); refusing to overwrite it or "
            "mint a fresh probe identity"
        )
    try:
        check_probe_evidence(
            record,
            source=args.source,
            view=args.view,
            revision=args.revision,
            repository=args.repository,
        )
    except AdoptionRefused as exc:
        return _fail(str(exc))
    print("existing compatible probe evidence reused")
    return REUSE


def check_sample_blocks(
    rows: Any,
    report: Any,
    *,
    source: str,
    view: str,
    revision: str,
    seed: int,
    files: list[str],
) -> None:
    """Verify sampled row ranges match current parameters exactly."""
    if not isinstance(rows, dict) or not rows:
        raise AdoptionRefused("row ranges are empty or not a mapping")
    if not isinstance(report, dict):
        raise AdoptionRefused("sampling report is not a mapping")
    for key, expected in (
        ("source_id", source),
        ("view_id", view),
        ("revision", revision),
        ("seed", seed),
    ):
        if report.get(key) != expected:
            raise AdoptionRefused(
                f"sampling report {key}={report.get(key)!r} differs from {expected!r}"
            )
    for name, span in rows.items():
        if (
            not isinstance(span, list)
            or len(span) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in span)
            or not 0 <= span[0] < span[1]
        ):
            raise AdoptionRefused(f"row range for '{name}' is malformed: {span!r}")
    if sorted(rows) != sorted(files):
        raise AdoptionRefused(f"row ranges cover {sorted(rows)} instead of {sorted(files)}")


def cmd_sample_blocks(args: argparse.Namespace) -> int:
    files = [name.strip() for name in args.files_csv.split(",") if name.strip()]
    if not Path(args.rows).is_file() or not Path(args.report).is_file():
        print("no sampled row ranges; run sample-blocks")
        return RUN
    try:
        rows = _read_json(Path(args.rows), "row ranges")
        report = _read_json(Path(args.report), "sampling report")
        check_sample_blocks(
            rows,
            report,
            source=args.source,
            view=args.view,
            revision=args.revision,
            seed=args.seed,
            files=files,
        )
    except AdoptionRefused as exc:
        return _fail(f"{exc}; remove the outputs explicitly to redo them")
    print("existing compatible row ranges reused")
    return REUSE


def check_plan(
    plan: Any,
    rows: Any,
    *,
    source: str,
    view: str,
    revision: str,
    seed: int,
    files: list[str],
    mode: str,
) -> None:
    """Verify a stored plan binds current parameters and its own hash."""
    if plan.source_id != source or plan.view_id != view:
        raise AdoptionRefused(
            f"plan is for '{plan.source_id}:{plan.view_id}', not '{source}:{view}'"
        )
    if plan.revision != revision:
        raise AdoptionRefused(f"plan revision '{plan.revision}' differs from '{revision}'")
    if plan.mode.value != mode:
        raise AdoptionRefused(f"plan mode '{plan.mode.value}' differs from '{mode}'")
    if sorted(plan.selected_files) != sorted(files):
        raise AdoptionRefused("plan files differ from the requested file list")
    normalized = {name: [int(v) for v in span] for name, span in plan.row_ranges.items()}
    if normalized != rows:
        raise AdoptionRefused("plan row ranges differ from the sampled row ranges")
    if (plan.sampling_frame.selection_seed or 0) != seed:
        raise AdoptionRefused("plan seed differs from the requested seed")
    if plan.compute_behavioral_hash() != plan.plan_hash:
        raise AdoptionRefused("plan hash does not recompute; plan file is corrupt")


def cmd_plan(args: argparse.Namespace) -> int:
    from xlm.data.acquisition.plan import load_acquisition_plan

    files = [name.strip() for name in args.files_csv.split(",") if name.strip()]
    if not Path(args.plan).is_file():
        print("no acquisition plan; run data plan")
        return RUN
    try:
        rows = _read_json(Path(args.rows), "row ranges")
        try:
            plan = load_acquisition_plan(Path(args.plan))
        except Exception as exc:
            raise AdoptionRefused(f"plan file failed validation: {exc}") from exc
        check_plan(
            plan,
            rows,
            source=args.source,
            view=args.view,
            revision=args.revision,
            seed=args.seed,
            files=files,
            mode=args.mode,
        )
    except AdoptionRefused as exc:
        return _fail(f"{exc}; use a new reviewed plan path to redo it")
    print(f"existing compatible plan reused: {plan.plan_id} {plan.plan_hash}")
    return REUSE


def check_adapt(summary: Any, *, plan_id: str, plan_hash: str) -> None:
    """Verify adapted outputs belong to this exact plan."""
    if not isinstance(summary, dict):
        raise AdoptionRefused("adaptation summary is not a mapping")
    if summary.get("plan_id") != plan_id or summary.get("plan_hash") != plan_hash:
        raise AdoptionRefused("adaptation summary binds a different plan")
    total = summary.get("total_input_records", 0)
    if not isinstance(total, int) or total < 1:
        raise AdoptionRefused("adaptation summary reports no adapted records")


def cmd_adapt(args: argparse.Namespace) -> int:
    from xlm.data.acquisition.plan import load_acquisition_plan

    out = Path(args.output_dir)
    docs = out / "documents.jsonl"
    summary_path = out / "adaptation_summary.json"
    if not docs.is_file() or not summary_path.is_file():
        print("no adapted outputs; run data adapt")
        return RUN
    try:
        try:
            plan = load_acquisition_plan(Path(args.plan))
        except Exception as exc:
            raise AdoptionRefused(f"plan file failed validation: {exc}") from exc
        summary = _read_json(summary_path, "adaptation summary")
        check_adapt(summary, plan_id=plan.plan_id, plan_hash=plan.compute_behavioral_hash())
    except AdoptionRefused as exc:
        return _fail(f"{exc}; use a fresh output dir to redo it")
    print("existing compatible adapted outputs reused")
    return REUSE


def check_verify(
    manifest: Any,
    current: dict[str, tuple[int, str]],
    *,
    plan_hash: str,
    source: str,
    view: str,
    revision: str,
) -> None:
    """Verify a published raw artifact binds this plan and current outputs."""
    meta = manifest.metadata if isinstance(manifest.metadata, dict) else {}
    for key, expected in (
        ("plan_hash", plan_hash),
        ("source_id", source),
        ("view_id", view),
        ("revision", revision),
    ):
        if meta.get(key) != expected:
            raise AdoptionRefused(
                f"published artifact {key}={meta.get(key)!r} differs from {expected!r}"
            )
    # plan_hash already binds the full plan (plan_id derives from it); the
    # manifest metadata carries no separate plan_id for raw datasets.
    stored = {
        entry.path: entry for entry in manifest.files if entry.path != "acquisition_receipt.json"
    }
    missing = sorted(set(stored) - set(current))
    if missing:
        raise AdoptionRefused(f"published files missing from outputs: {missing}")
    for path, entry in sorted(stored.items()):
        size, digest = current[path]
        if size != entry.size_bytes or digest != entry.sha256:
            raise AdoptionRefused(f"published file '{path}' differs from current outputs")


def cmd_verify(args: argparse.Namespace) -> int:
    from xlm.artifacts.store import compute_file_sha256
    from xlm.data.acquisition.plan import load_acquisition_plan

    try:
        plan = load_acquisition_plan(Path(args.plan))
    except Exception as exc:
        print(f"plan file failed validation: {exc}")
        return REFUSE
    store = _store(args.store)
    artifact_dir = args.store / "raw_dataset" / plan.output_artifact_id
    if not artifact_dir.is_dir() or not (artifact_dir / "_COMPLETED").is_file():
        print("no verified publication; run data verify")
        return RUN
    try:
        manifest = store.verify_artifact(artifact_dir)
    except Exception as exc:
        return _fail(f"published artifact failed verification: {exc}")
    try:
        current: dict[str, tuple[int, str]] = {}
        for item in sorted(Path(args.output_dir).iterdir()):
            if item.is_file() and item.name != "acquisition_receipt.json":
                current[item.name] = (
                    item.stat().st_size,
                    compute_file_sha256(item, max_bytes=item.stat().st_size),
                )
        check_verify(
            manifest,
            current,
            plan_hash=plan.compute_behavioral_hash(),
            source=plan.source_id,
            view=plan.view_id,
            revision=plan.revision,
        )
    except AdoptionRefused as exc:
        return _fail(str(exc))
    print("existing verified publication reused; re-confirm with verify --no-publish")
    return REUSE


def cmd_plan_identity(args: argparse.Namespace) -> int:
    """Print plan_id, plan_hash and revision for a stored plan (Path API)."""
    from xlm.data.acquisition.plan import load_acquisition_plan

    try:
        plan = load_acquisition_plan(Path(args.plan))
    except Exception as exc:
        print(f"calibration_adopt: error: cannot load plan: {exc}", file=sys.stderr)
        return REFUSE
    print(plan.plan_id)
    print(plan.compute_behavioral_hash())
    print(plan.revision)
    return RUN


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calibration rerun adoption checks (offline).")
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="Adopt compatible probe evidence or run.")
    probe.add_argument("--store", type=Path, required=True)
    probe.add_argument("--source", required=True)
    probe.add_argument("--view", required=True)
    probe.add_argument("--revision", required=True)
    probe.add_argument("--repository", required=True)
    probe.set_defaults(func=cmd_probe)

    blocks = sub.add_parser("sample-blocks", help="Adopt compatible row ranges or run.")
    blocks.add_argument("--rows", type=Path, required=True)
    blocks.add_argument("--report", type=Path, required=True)
    blocks.add_argument("--source", required=True)
    blocks.add_argument("--view", required=True)
    blocks.add_argument("--revision", required=True)
    blocks.add_argument("--seed", type=int, required=True)
    blocks.add_argument("--files-csv", required=True)
    blocks.set_defaults(func=cmd_sample_blocks)

    plan = sub.add_parser("plan", help="Adopt a compatible stored plan or run.")
    plan.add_argument("--plan", type=Path, required=True)
    plan.add_argument("--rows", type=Path, required=True)
    plan.add_argument("--source", required=True)
    plan.add_argument("--view", required=True)
    plan.add_argument("--revision", required=True)
    plan.add_argument("--seed", type=int, required=True)
    plan.add_argument("--files-csv", required=True)
    plan.add_argument("--mode", default="selected_records")
    plan.set_defaults(func=cmd_plan)

    adapt = sub.add_parser("adapt", help="Adopt compatible adapted outputs or run.")
    adapt.add_argument("--output-dir", type=Path, required=True)
    adapt.add_argument("--plan", type=Path, required=True)
    adapt.set_defaults(func=cmd_adapt)

    verify = sub.add_parser("verify", help="Adopt a verified publication or run.")
    verify.add_argument("--store", type=Path, required=True)
    verify.add_argument("--plan", type=Path, required=True)
    verify.add_argument("--output-dir", type=Path, required=True)
    verify.set_defaults(func=cmd_verify)

    identity = sub.add_parser("plan-identity", help="Print plan_id, plan_hash and revision.")
    identity.add_argument("--plan", type=Path, required=True)
    identity.set_defaults(func=cmd_plan_identity)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except OSError as exc:
        return _fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
