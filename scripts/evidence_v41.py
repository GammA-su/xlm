"""Essential-Web evidence v4.1 CLI: the v4 Phase-P fetcher with the v4.1 host set.

Commands:
  verify          recompute every committed v4.1 binding, after the v4.0 parent
                  freeze and exact scientific adoption verify (offline)
  show-plan       print the frozen v4.1 Phase-P plan and exact host set (offline)
  phase-p-status  read-only status of the fresh v4.1 execution root
  phase-p         execute THE frozen v4.1 plan against the live source at THE
                  v4.1 root; requires --confirm-plan-digest <exact v4.1 plan digest>

There is deliberately no option for a URL, host, file, range, ETag, operation
kind, plan, output path, root, force or skip. Phase D is not implemented.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from xlm.data.evidence_v4 import frozen, phase_p, v41, verify


def _fail(message: str, code: int = 1) -> int:
    print(f"evidence_v41: error: {message}", file=sys.stderr)
    return code


def cmd_verify(_: argparse.Namespace) -> int:
    try:
        summary = v41.verify_repository()
    except (verify.VerifyError, frozen.PlanError, OSError, KeyError) as exc:
        return _fail(f"verification failed: {exc}")
    print(json.dumps(summary, indent=2))
    return 0


def cmd_show_plan(_: argparse.Namespace) -> int:
    try:
        plan = v41.load_committed_plan()
    except (frozen.PlanError, OSError) as exc:
        return _fail(str(exc))
    print(f"plan digest {plan.digest}  (protocol {v41.PROTOCOL_VERSION})")
    print(f"source      {plan.source.repository}@{plan.source.revision}")
    print(f"root        {plan.execution_root}")
    print(f"hosts       {' '.join(plan.profile.hosts.hosts)}")
    for op in plan.operations:
        f = op.source_file
        span = f"{op.range[0]}-{op.range[1]}" if op.range else frozen.T_FOOTER_RULE
        print(f"{op.seq:>2} {op.op_id:<14} {op.kind:<22} {span:<22} N={f.remote_length} {f.file}")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    try:
        summary = phase_p.inspect(phase_p.live_root_v41())
    except phase_p.RefusedError as exc:
        return _fail(str(exc))
    print(json.dumps(summary, indent=2))
    return 0


def cmd_phase_p(args: argparse.Namespace) -> int:
    try:
        result = phase_p.run_live_v41(confirm_plan_digest=args.confirm_plan_digest)
    except phase_p.RefusedError as exc:
        return _fail(f"refused: {exc}")
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.status == "COMPLETE" else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evidence_v41.py",
        description="Essential-Web evidence v4.1 Phase-P fetcher (frozen plan only).",
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify", help="Verify the committed v4.1 freeze.", allow_abbrev=False)
    sub.add_parser("show-plan", help="Print the frozen v4.1 plan.", allow_abbrev=False)
    sub.add_parser("phase-p-status", help="Read-only v4.1 root status.", allow_abbrev=False)
    live = sub.add_parser(
        "phase-p", help="Run THE frozen v4.1 Phase-P plan (live).", allow_abbrev=False
    )
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
