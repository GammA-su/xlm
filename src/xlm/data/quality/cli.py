"""Operator CLI: ``python -m xlm.data.quality {audit,report,materialize-review}``.

stdout carries one final JSON object; progress goes to stderr. Refusals print a
content-free message (every message in this package is authored, never corpus text).
"""

from __future__ import annotations

import argparse
import json
import sys
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
    audit.add_argument("--workers", type=int, choices=(1, 2, 4, 8, 16), required=True)
    audit.add_argument("--max-rss-gib", type=float, default=12.0)
    audit.add_argument("--free-reserve-gib", type=float, default=8.0)
    audit.add_argument("--max-output-gib", type=float, default=4.0)
    audit.add_argument("--max-document-mib", type=int, default=64)
    audit.add_argument("--deadline-hours", type=float, default=12.0)
    audit.add_argument("--progress-interval", type=float, default=5.0)
    audit.add_argument("--no-progress", action="store_true")

    report = commands.add_parser("report", help="re-derive and verify every artifact")
    report.add_argument("--manifest", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--data-root", type=Path, default=None)
    report.add_argument("--c05-proof", type=Path, default=None)
    report.add_argument("--allow-authored-proof", action="store_true")

    review = commands.add_parser(
        "materialize-review",
        help="OPERATOR ONLY: copy selected review documents' text into a new local directory",
    )
    review.add_argument("--output", type=Path, required=True, help="completed audit directory")
    review.add_argument("--destination", type=Path, required=True, help="new directory")
    review.add_argument("--data-root", type=Path, default=None)
    review.add_argument("--operator-confirm", action="store_true", required=False)
    review.add_argument("--roles", nargs="*", default=None)
    review.add_argument("--detectors", nargs="*", default=None)
    review.add_argument("--components", nargs="*", default=None)
    review.add_argument("--max-documents", type=int, default=500)
    review.add_argument("--max-chars", type=int, default=20_000)
    return parser


def _audit(args: argparse.Namespace) -> dict[str, Any]:
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
        progress_interval=None if args.no_progress else args.progress_interval,
    )


def _report(args: argparse.Namespace) -> dict[str, Any]:
    from xlm.data.quality.runner import verify_report

    return verify_report(
        args.manifest,
        args.output,
        data_root=args.data_root,
        proof=args.c05_proof,
        allow_authored_proof=args.allow_authored_proof,
    )


def _materialize(args: argparse.Namespace) -> dict[str, Any]:
    from xlm.data.quality.review import ReviewError, materialize_review, read_review_rows
    from xlm.data.quality.scan import RECEIPT_FILE

    if not args.operator_confirm:
        raise ReviewError(
            "materialize-review copies corpus text; rerun with --operator-confirm "
            "(operator only, never an automated agent)"
        )
    receipt = json.loads((args.output / RECEIPT_FILE).read_bytes())
    if receipt.get("status") != "COMPLETE":
        raise ReviewError("audit is not complete")
    binding = receipt["binding"]
    root = args.data_root if args.data_root is not None else Path(binding["data_root"])
    files = {f["path"]: f for f in receipt["source_files"]}
    rows = read_review_rows(args.output / "review-manifest.jsonl")
    selected = [
        r
        for r in rows
        if (args.roles is None or set(r["roles"]) & set(args.roles))
        and (args.detectors is None or r["detector"] in args.detectors)
        and (args.components is None or r["component"] in args.components)
    ]
    return materialize_review(
        selected,
        data_root=root,
        files=files,
        destination=args.destination,
        max_documents=args.max_documents,
        max_chars=args.max_chars,
        line_ceiling=int(binding["line_ceiling"]),
    )


def main(argv: list[str] | None = None) -> int:
    from xlm.data.quality.aggregate import AggregateError
    from xlm.data.quality.overlay import OverlayError
    from xlm.data.quality.review import ReviewError
    from xlm.data.quality.scan import QualityError

    args = _parser().parse_args(argv)
    handlers = {"audit": _audit, "report": _report, "materialize-review": _materialize}
    try:
        result = handlers[args.command](args)
    except (QualityError, OverlayError, ReviewError, AggregateError) as exc:
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
