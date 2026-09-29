"""Essential-Web evidence v4.0 CLI: the dedicated Phase-P structural fetcher.

Commands:
  verify          recompute every committed v4 freeze binding and the exact
                  scientific adoption from repository bytes (offline)
  show-plan       print the frozen Phase-P plan (offline)
  phase-p-status  read-only status of the frozen execution root
  phase-p         execute THE frozen plan against the live source at THE frozen
                  root; requires --confirm-plan-digest <exact v4 plan digest>

There is deliberately no option for a URL, file, range, ETag, operation kind,
plan, output path, root, force or skip. Phase D is not implemented.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from xlm.data.evidence_v4 import frozen, phase_p, verify


def _fail(message: str, code: int = 1) -> int:
    print(f"evidence_v4: error: {message}", file=sys.stderr)
    return code


def cmd_verify(_: argparse.Namespace) -> int:
    try:
        summary = verify.verify_repository()
    except (verify.VerifyError, frozen.PlanError, OSError, KeyError) as exc:
        return _fail(f"verification failed: {exc}")
    print(json.dumps(summary, indent=2))
    return 0


def cmd_show_plan(_: argparse.Namespace) -> int:
    try:
        plan = frozen.load_committed_plan()
    except (frozen.PlanError, OSError) as exc:
        return _fail(str(exc))
    print(f"plan digest {plan.digest}  (protocol {frozen.PROTOCOL_VERSION})")
    print(f"source      {plan.source.repository}@{plan.source.revision}")
    print(f"root        {plan.execution_root}")
    for op in plan.operations:
        f = op.source_file
        span = f"{op.range[0]}-{op.range[1]}" if op.range else frozen.T_FOOTER_RULE
        print(f"{op.seq:>2} {op.op_id:<14} {op.kind:<22} {span:<22} N={f.remote_length} {f.file}")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    try:
        summary = phase_p.inspect(phase_p.live_root())
    except phase_p.RefusedError as exc:
        return _fail(str(exc))
    print(json.dumps(summary, indent=2))
    return 0


def cmd_phase_p(args: argparse.Namespace) -> int:
    try:
        result = phase_p.run_live(confirm_plan_digest=args.confirm_plan_digest)
    except phase_p.RefusedError as exc:
        return _fail(f"refused: {exc}")
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.status == "COMPLETE" else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evidence_v4.py",
        description="Essential-Web evidence v4.0 Phase-P fetcher (frozen plan only).",
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify", help="Verify the committed v4 freeze.", allow_abbrev=False)
    sub.add_parser("show-plan", help="Print the frozen Phase-P plan.", allow_abbrev=False)
    sub.add_parser("phase-p-status", help="Read-only root status.", allow_abbrev=False)
    live = sub.add_parser("phase-p", help="Run THE frozen Phase-P plan (live).", allow_abbrev=False)
    live.add_argument("--confirm-plan-digest", required=True, metavar="DIGEST")
    handlers = {
        "verify": cmd_verify,
        "show-plan": cmd_show_plan,
        "phase-p-status": cmd_status,
        "phase-p": cmd_phase_p,
    }
    parser.set_defaults(handlers=handlers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = args.handlers[args.command]
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
