"""Operator CLI: ``python -m xlm.data.quality {audit,report,status,benchmark,materialize-review}``,
the Phase-B dry run ``{clean-freeze-policy,clean-dry-run,clean-report,
clean-materialize-review}`` and Phase-C production cleaning ``{clean-production,
clean-production-verify,clean-production-manifest}``.

stdout carries one final JSON object; progress goes to stderr. Refusals print a
content-free message (every message in this package is authored, never corpus text).
The whole-command deadline starts at dispatch, before any argument is acted on.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

GIB = 1024**3
MIB = 1024**2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m xlm.data.quality")
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser("audit", help="read-only streaming quality audit")
    audit.add_argument("--manifest", type=Path, required=True, help="C05 input manifest JSON")
    audit.add_argument("--output", type=Path, required=True, help="audit directory (resumable)")
    audit.add_argument("--data-root", type=Path, default=None, help="default: manifest data_root")
    audit.add_argument("--c05-proof", type=Path, default=None, help="optional kept overlay")
    audit.add_argument(
        "--allow-authored-proof",
        action="store_true",
        help="rehearsal only: accept an authored (non-protected) C05 proof",
    )
    audit.add_argument("--workers", type=int, choices=(1, 2, 4, 8, 12, 16), required=True)
    audit.add_argument("--max-rss-gib", type=float, default=12.0)
    audit.add_argument("--free-reserve-gib", type=float, default=8.0)
    audit.add_argument("--max-output-gib", type=float, default=4.0)
    audit.add_argument("--max-document-mib", type=int, default=64)
    audit.add_argument("--deadline-hours", type=float, default=12.0)
    audit.add_argument(
        "--progress-interval-seconds",
        "--progress-interval",
        dest="progress_interval",
        type=float,
        default=5.0,
        help="stderr progress line period (default 5; operational only)",
    )
    audit.add_argument("--no-progress", action="store_true", help="no stderr progress lines")
    audit.add_argument(
        "--progress-log",
        type=Path,
        default=None,
        help="also append progress lines here (outside --output, the data root and inputs)",
    )

    report = commands.add_parser(
        "report", help="validate the receipt, re-hash sources, re-derive every artifact"
    )
    report.add_argument("--manifest", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--data-root", type=Path, default=None)
    report.add_argument("--c05-proof", type=Path, default=None)
    report.add_argument("--allow-authored-proof", action="store_true")
    report.add_argument("--workers", type=int, choices=(1, 2, 4, 8, 12, 16), default=4)
    report.add_argument("--max-rss-gib", type=float, default=8.0)
    report.add_argument("--deadline-hours", type=float, default=6.0)

    status = commands.add_parser(
        "status", help="READ-ONLY progress of an audit output (safe while it runs)"
    )
    status.add_argument("--manifest", type=Path, required=True)
    status.add_argument("--output", type=Path, required=True)
    status.add_argument("--data-root", type=Path, default=None)
    status.add_argument("--progress-log", type=Path, default=None, help="read its last line")
    status.add_argument("--watch", action="store_true", help="repeat until COMPLETE")
    status.add_argument("--interval-seconds", type=float, default=10.0)
    status.add_argument("--max-watch-hours", type=float, default=24.0)
    status.add_argument("--no-process", action="store_true", help="skip process discovery")

    bench = commands.add_parser(
        "benchmark",
        help="bounded AUTHORED-data throughput benchmark of the production audit path",
    )
    bench.add_argument("--scratch", type=Path, required=True, help="NEW empty directory")
    bench.add_argument("--corpus-mib", type=int, default=768)
    bench.add_argument("--workers", type=int, nargs="+", choices=(1, 2, 4, 8, 12, 16), default=None)
    bench.add_argument("--budget-seconds", type=float, default=300.0)
    bench.add_argument("--keep", action="store_true", help="keep the generated corpus")

    review = commands.add_parser(
        "materialize-review",
        help="OPERATOR ONLY: copy selected review documents' text into a new local directory",
    )
    review.add_argument("--output", type=Path, required=True, help="completed audit directory")
    review.add_argument("--destination", type=Path, required=True, help="new directory")
    review.add_argument("--manifest", type=Path, default=None, help="default: from the receipt")
    review.add_argument("--data-root", type=Path, default=None)
    review.add_argument("--c05-proof", type=Path, default=None)
    review.add_argument("--allow-authored-proof", action="store_true")
    review.add_argument("--operator-confirm", action="store_true", required=False)
    review.add_argument("--roles", nargs="*", default=None)
    review.add_argument("--detectors", nargs="*", default=None)
    review.add_argument("--components", nargs="*", default=None)
    review.add_argument("--max-documents", type=int, default=500)
    review.add_argument("--max-chars", type=int, default=20_000)
    review.add_argument("--max-output-mib", type=int, default=512)
    review.add_argument("--max-rss-gib", type=float, default=8.0)
    review.add_argument("--free-reserve-gib", type=float, default=1.0)
    review.add_argument("--deadline-hours", type=float, default=3.0)
    _clean_parsers(commands)
    return parser


def _clean_parsers(commands: Any) -> None:
    freeze = commands.add_parser(
        "clean-freeze-policy",
        help="copy the verified Phase-A conservative cuts into a NEW frozen cleaning policy",
    )
    freeze.add_argument("--template", type=Path, required=True, help="cleaning_policy_v1.yaml")
    freeze.add_argument("--audit-output", type=Path, required=True, help="Phase-A audit output")
    freeze.add_argument("--destination", type=Path, required=True, help="new frozen policy file")
    freeze.add_argument(
        "--predecessor",
        type=Path,
        default=None,
        help="v2 only: the FROZEN v1 policy (same Phase-A audit, identical cuts)",
    )

    dry = commands.add_parser(
        "clean-dry-run",
        help="READ-ONLY policy dry run: KEEP/DROP/REVIEW reports, never a cleaned corpus",
    )
    dry.add_argument("--manifest", type=Path, required=True, help="C05 input manifest JSON")
    dry.add_argument("--policy", type=Path, required=True, help="FROZEN cleaning policy YAML")
    dry.add_argument("--output", type=Path, required=True, help="dry-run directory (resumable)")
    dry.add_argument("--data-root", type=Path, default=None, help="default: manifest data_root")
    dry.add_argument("--c05-proof", type=Path, default=None, help="diagnostic kept overlay")
    dry.add_argument("--allow-authored-proof", action="store_true")
    dry.add_argument("--workers", type=int, choices=(1, 2, 4, 8, 12, 16), required=True)
    dry.add_argument("--max-rss-gib", type=float, default=12.0)
    dry.add_argument("--free-reserve-gib", type=float, default=8.0)
    dry.add_argument("--max-output-gib", type=float, default=4.0)
    dry.add_argument("--max-document-mib", type=int, default=64)
    dry.add_argument("--deadline-hours", type=float, default=12.0)
    dry.add_argument(
        "--progress-interval-seconds",
        "--progress-interval",
        dest="progress_interval",
        type=float,
        default=5.0,
        help="stderr progress line period (default 5; operational only)",
    )
    dry.add_argument("--no-progress", action="store_true", help="no stderr progress lines")
    dry.add_argument(
        "--progress-log",
        type=Path,
        default=None,
        help="also append progress lines here (outside --output, the data root and inputs)",
    )

    report = commands.add_parser(
        "clean-report", help="validate the dry-run receipt, re-hash sources, re-derive artifacts"
    )
    report.add_argument("--manifest", type=Path, required=True)
    report.add_argument("--policy", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--data-root", type=Path, default=None)
    report.add_argument("--c05-proof", type=Path, default=None)
    report.add_argument("--allow-authored-proof", action="store_true")
    report.add_argument("--workers", type=int, choices=(1, 2, 4, 8, 12, 16), default=4)
    report.add_argument("--max-rss-gib", type=float, default=8.0)
    report.add_argument("--deadline-hours", type=float, default=6.0)

    review = commands.add_parser(
        "clean-materialize-review",
        help="OPERATOR ONLY: copy the selected dry-run review rows' text into a new directory",
    )
    review.add_argument("--output", type=Path, required=True, help="completed dry-run directory")
    review.add_argument("--destination", type=Path, required=True, help="new directory")
    review.add_argument("--manifest", type=Path, default=None, help="default: from the receipt")
    review.add_argument("--policy", type=Path, default=None, help="default: from the receipt")
    review.add_argument("--data-root", type=Path, default=None)
    review.add_argument("--c05-proof", type=Path, default=None)
    review.add_argument("--allow-authored-proof", action="store_true")
    review.add_argument("--operator-confirm", action="store_true", required=False)
    review.add_argument("--strata", nargs="*", default=None)
    review.add_argument("--components", nargs="*", default=None)
    review.add_argument("--outcomes", nargs="*", choices=("KEEP", "DROP", "REVIEW"), default=None)
    review.add_argument("--max-documents", type=int, default=150)
    review.add_argument("--max-chars", type=int, default=20_000)
    review.add_argument("--max-output-mib", type=int, default=256)
    review.add_argument("--max-rss-gib", type=float, default=8.0)
    review.add_argument("--free-reserve-gib", type=float, default=1.0)
    review.add_argument("--deadline-hours", type=float, default=3.0)
    _production_parsers(commands)


def _production_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, required=True, help="ORIGINAL input manifest")
    parser.add_argument("--policy", type=Path, required=True, help="cleaning_policy_v2.frozen.yaml")
    parser.add_argument(
        "--approved-dry-run", type=Path, required=True, help="COMPLETE clean-dry-run v2 output"
    )
    parser.add_argument(
        "--approved-result-digest",
        required=True,
        help="the operator-approved dry-run result_digest (the dry run must equal it)",
    )
    parser.add_argument("--output-root", type=Path, required=True, help="cleaned corpus root")
    parser.add_argument(
        "--state-output", type=Path, required=True, help="cleaning receipts/state (not corpus)"
    )
    parser.add_argument("--data-root", type=Path, default=None, help="default: manifest data_root")


def _progress_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--progress-interval-seconds",
        "--progress-interval",
        dest="progress_interval",
        type=float,
        default=5.0,
        help="stderr progress line period (default 5; operational only)",
    )
    parser.add_argument("--no-progress", action="store_true", help="no stderr progress lines")
    parser.add_argument(
        "--progress-log",
        type=Path,
        default=None,
        help="also append progress lines here (outside the corpus, state, data root, inputs)",
    )


def _production_parsers(commands: Any) -> None:
    production = commands.add_parser(
        "clean-production",
        help="PRODUCTION DROP-only cleaning bound to the approved dry run (writes a NEW corpus)",
    )
    _production_inputs(production)
    production.add_argument("--workers", type=int, choices=(1, 2, 4, 8, 12, 16), required=True)
    production.add_argument("--max-rss-gib", type=float, default=12.0)
    production.add_argument("--free-reserve-gib", type=float, default=16.0)
    production.add_argument(
        "--max-output-gib", type=float, default=160.0, help="cleaned corpus byte ceiling"
    )
    production.add_argument("--deadline-hours", type=float, default=12.0)
    _progress_options(production)

    verify = commands.add_parser(
        "clean-production-verify",
        help="re-read and re-hash the finished cleaned corpus (never modifies it)",
    )
    _production_inputs(verify)
    verify.add_argument("--workers", type=int, choices=(1, 2, 4, 8, 12, 16), default=8)
    verify.add_argument(
        "--compare-sources",
        action="store_true",
        help="also re-hash every source and prove KEEP rows byte-identical and in order",
    )
    verify.add_argument(
        "--reevaluate", action="store_true", help="also re-decide every output row (must be KEEP)"
    )
    verify.add_argument("--max-rss-gib", type=float, default=8.0)
    verify.add_argument("--deadline-hours", type=float, default=12.0)
    _progress_options(verify)

    manifest = commands.add_parser(
        "clean-production-manifest",
        help="cleaned-corpus input manifest candidate from a VERIFIED cleaning (no C05)",
    )
    manifest.add_argument("--state-output", type=Path, required=True)
    manifest.add_argument("--output-root", type=Path, required=True)


def _clean_production(args: argparse.Namespace, started: float) -> dict[str, Any]:
    from xlm.data.quality.production import run_production
    from xlm.data.quality.progress import check_interval
    from xlm.data.quality.runner import Limits

    limits = Limits(
        workers=args.workers,
        max_rss_bytes=int(args.max_rss_gib * GIB),
        free_reserve_bytes=int(args.free_reserve_gib * GIB),
        max_output_bytes=int(args.max_output_gib * GIB),
        line_ceiling=64 * MIB,  # replaced by the approved dry run's document ceiling
        deadline_seconds=args.deadline_hours * 3600,
    )
    return run_production(
        args.manifest,
        args.policy,
        args.approved_dry_run,
        args.output_root,
        args.state_output,
        approved_result_digest=args.approved_result_digest,
        limits=limits,
        data_root=args.data_root,
        progress_interval=None if args.no_progress else check_interval(args.progress_interval),
        progress_log=args.progress_log,
        started=started,
    )


def _clean_production_verify(args: argparse.Namespace, started: float) -> dict[str, Any]:
    from xlm.data.quality.production_verify import verify_production
    from xlm.data.quality.progress import check_interval

    return verify_production(
        args.manifest,
        args.policy,
        args.approved_dry_run,
        args.output_root,
        args.state_output,
        approved_result_digest=args.approved_result_digest,
        data_root=args.data_root,
        workers=args.workers,
        compare_sources=args.compare_sources,
        reevaluate=args.reevaluate,
        max_rss_bytes=int(args.max_rss_gib * GIB),
        deadline_seconds=args.deadline_hours * 3600,
        progress_interval=None if args.no_progress else check_interval(args.progress_interval),
        progress_log=args.progress_log,
        started=started,
    )


def _clean_production_manifest(args: argparse.Namespace, started: float) -> dict[str, Any]:
    from xlm.data.quality.production_verify import build_cleaned_manifest

    return build_cleaned_manifest(args.state_output, args.output_root)


def _clean_freeze(args: argparse.Namespace, started: float) -> dict[str, Any]:
    from xlm.data.quality.cleaning_policy import freeze_policy

    return freeze_policy(
        args.template, args.audit_output, args.destination, predecessor=args.predecessor
    )


def _clean_dry_run(args: argparse.Namespace, started: float) -> dict[str, Any]:
    from xlm.data.quality.cleaning_runner import run_dry_run
    from xlm.data.quality.progress import check_interval
    from xlm.data.quality.runner import Limits

    limits = Limits(
        workers=args.workers,
        max_rss_bytes=int(args.max_rss_gib * GIB),
        free_reserve_bytes=int(args.free_reserve_gib * GIB),
        max_output_bytes=int(args.max_output_gib * GIB),
        line_ceiling=args.max_document_mib * MIB,
        deadline_seconds=args.deadline_hours * 3600,
    )
    return run_dry_run(
        args.manifest,
        args.output,
        args.policy,
        limits=limits,
        data_root=args.data_root,
        proof=args.c05_proof,
        allow_authored_proof=args.allow_authored_proof,
        progress_interval=None if args.no_progress else check_interval(args.progress_interval),
        progress_log=args.progress_log,
        started=started,
    )


def _clean_report(args: argparse.Namespace, started: float) -> dict[str, Any]:
    from xlm.data.quality.cleaning_runner import verify_dry_run

    return verify_dry_run(
        args.manifest,
        args.output,
        args.policy,
        data_root=args.data_root,
        proof=args.c05_proof,
        allow_authored_proof=args.allow_authored_proof,
        workers=args.workers,
        max_rss_bytes=int(args.max_rss_gib * GIB),
        deadline_seconds=args.deadline_hours * 3600,
        started=started,
    )


def _clean_materialize(args: argparse.Namespace, started: float) -> dict[str, Any]:
    from xlm.data.quality.cleaning_runner import materialize_review
    from xlm.data.quality.review import ReviewError
    from xlm.data.quality.runner import ReviewLimits

    if not args.operator_confirm:
        raise ReviewError(
            "clean-materialize-review copies corpus text; rerun with --operator-confirm "
            "(operator only, never an automated agent)"
        )
    return materialize_review(
        args.output,
        args.destination,
        limits=ReviewLimits(
            max_documents=args.max_documents,
            max_chars=args.max_chars,
            max_output_bytes=args.max_output_mib * MIB,
            max_rss_bytes=int(args.max_rss_gib * GIB),
            free_reserve_bytes=int(args.free_reserve_gib * GIB),
            deadline_seconds=args.deadline_hours * 3600,
        ),
        manifest_path=args.manifest,
        policy_path=args.policy,
        data_root=args.data_root,
        proof=args.c05_proof,
        allow_authored_proof=args.allow_authored_proof,
        strata=args.strata,
        components=args.components,
        outcomes=args.outcomes,
        started=started,
    )


def _audit(args: argparse.Namespace, started: float) -> dict[str, Any]:
    from xlm.data.quality.progress import check_interval
    from xlm.data.quality.runner import Limits, run_audit

    limits = Limits(
        workers=args.workers,
        max_rss_bytes=int(args.max_rss_gib * GIB),
        free_reserve_bytes=int(args.free_reserve_gib * GIB),
        max_output_bytes=int(args.max_output_gib * GIB),
        line_ceiling=args.max_document_mib * MIB,
        deadline_seconds=args.deadline_hours * 3600,
    )
    return run_audit(
        args.manifest,
        args.output,
        limits=limits,
        data_root=args.data_root,
        proof=args.c05_proof,
        allow_authored_proof=args.allow_authored_proof,
        progress_interval=None if args.no_progress else check_interval(args.progress_interval),
        progress_log=args.progress_log,
        started=started,
    )


def _report(args: argparse.Namespace, started: float) -> dict[str, Any]:
    from xlm.data.quality.runner import verify_report

    return verify_report(
        args.manifest,
        args.output,
        data_root=args.data_root,
        proof=args.c05_proof,
        allow_authored_proof=args.allow_authored_proof,
        workers=args.workers,
        max_rss_bytes=int(args.max_rss_gib * GIB),
        deadline_seconds=args.deadline_hours * 3600,
        started=started,
    )


def _status(args: argparse.Namespace, started: float) -> dict[str, Any]:
    from xlm.data.quality.status import audit_status, status_line, watch

    def produce() -> dict[str, Any]:
        return audit_status(
            args.manifest,
            args.output,
            data_root=args.data_root,
            progress_log=args.progress_log,
            processes=not args.no_process,
        )

    if args.watch:
        return watch(produce, args.interval_seconds, args.max_watch_hours * 3600)
    state = produce()
    print(status_line(state), file=sys.stderr, flush=True)
    return state


def _benchmark(args: argparse.Namespace, started: float) -> dict[str, Any]:
    from xlm.data.quality.bench import run_benchmark

    return run_benchmark(
        args.scratch,
        corpus_mib=args.corpus_mib,
        workers=args.workers,
        budget_seconds=args.budget_seconds,
        keep=args.keep,
        log=lambda line: print(f"[quality-bench] {line}", file=sys.stderr, flush=True),
    )


def _materialize(args: argparse.Namespace, started: float) -> dict[str, Any]:
    from xlm.data.quality.review import ReviewError
    from xlm.data.quality.runner import ReviewLimits, materialize_from_audit

    if not args.operator_confirm:
        raise ReviewError(
            "materialize-review copies corpus text; rerun with --operator-confirm "
            "(operator only, never an automated agent)"
        )
    return materialize_from_audit(
        args.output,
        args.destination,
        limits=ReviewLimits(
            max_documents=args.max_documents,
            max_chars=args.max_chars,
            max_output_bytes=args.max_output_mib * MIB,
            max_rss_bytes=int(args.max_rss_gib * GIB),
            free_reserve_bytes=int(args.free_reserve_gib * GIB),
            deadline_seconds=args.deadline_hours * 3600,
        ),
        manifest_path=args.manifest,
        data_root=args.data_root,
        proof=args.c05_proof,
        allow_authored_proof=args.allow_authored_proof,
        roles=args.roles,
        detectors=args.detectors,
        components=args.components,
        started=started,
    )


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()  # the whole-command deadline starts at dispatch
    from xlm.data.quality.aggregate import AggregateError
    from xlm.data.quality.overlay import OverlayError
    from xlm.data.quality.progress import ProgressError
    from xlm.data.quality.review import ReviewError
    from xlm.data.quality.scan import QualityError

    args = _parser().parse_args(argv)
    handlers = {
        "audit": _audit,
        "report": _report,
        "status": _status,
        "benchmark": _benchmark,
        "materialize-review": _materialize,
        "clean-freeze-policy": _clean_freeze,
        "clean-dry-run": _clean_dry_run,
        "clean-report": _clean_report,
        "clean-materialize-review": _clean_materialize,
        "clean-production": _clean_production,
        "clean-production-verify": _clean_production_verify,
        "clean-production-manifest": _clean_production_manifest,
    }
    try:
        result = handlers[args.command](args, started)
    except (QualityError, OverlayError, ReviewError, AggregateError, ProgressError) as exc:
        print(json.dumps({"refused": True, "error": str(exc)}))
        return 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Unexpected errors: type only (a value could echo malformed corpus bytes).
        print(json.dumps({"refused": True, "error_type": type(exc).__name__}))
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"refused": True, "error_type": "KeyboardInterrupt"}))
        return 130
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
