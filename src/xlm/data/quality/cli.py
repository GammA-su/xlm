"""Operator CLI: ``python -m xlm.data.quality {audit,report,status,benchmark,materialize-review}``.

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
    return parser


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
