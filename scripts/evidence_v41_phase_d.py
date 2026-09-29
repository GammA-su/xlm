"""Essential-Web evidence v4.1 Phase-D CLI: THE frozen Phase-D acquisition plan only.

Commands:
  verify          recompute every committed Phase-D binding (after the v4.0 and
                  v4.1 parents verify) and the reviewed dry-plan ranges (offline)
  show-plan       print the frozen Phase-D plan: 8 M and 47 T operations (offline)
  phase-d-status  read-only status of the fresh Phase-D execution root
  phase-d         execute THE frozen Phase-D plan against the live source at THE
                  Phase-D root, reading THE COMPLETE v4.1 Phase-P parent
                  read-only; requires --confirm-plan-digest <exact plan digest>

There is deliberately no option for a URL, host, file, range, ETag, locator,
operation, plan, output path, root, force or skip.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from xlm.data.evidence_v4 import frozen, phase_d, verify
from xlm.data.evidence_v4 import phase_d_plan as pd


def _fail(message: str, code: int = 1) -> int:
    print(f"evidence_v41_phase_d: error: {message}", file=sys.stderr)
    return code


def cmd_verify(_: argparse.Namespace) -> int:
    try:
        summary = phase_d.verify_repository()
    except (verify.VerifyError, frozen.PlanError, OSError, KeyError) as exc:
        return _fail(f"verification failed: {exc}")
    print(json.dumps(summary, indent=2))
    return 0


def cmd_show_plan(_: argparse.Namespace) -> int:
    try:
        plan = pd.load_committed_plan()
    except (frozen.PlanError, OSError) as exc:
        return _fail(str(exc))
    print(f"plan digest {plan.digest}  (protocol {pd.PROTOCOL_VERSION})")
    print(f"dry plan    {plan.parents.dry_plan_digest}")
    print(f"source      {plan.fetch.source.repository}@{plan.fetch.source.revision}")
    print(f"root        {plan.fetch.execution_root}")
    parents = plan.parents
    print(f"parent      {parents.phase_p_root} (read-only; plan {parents.phase_p_plan_digest})")
    print(f"hosts       {' '.join(plan.fetch.profile.hosts.hosts)}")
    for op in plan.fetch.operations:
        f = op.source_file
        start, end = op.range or (0, -1)
        print(
            f"{op.seq:>2} {op.op_id:<9} {op.kind:<18} {start}-{end} "
            f"({end - start + 1} B) N={f.remote_length} {f.file}"
        )
    m_rows = sum(f.window[1] - f.window[0] for f in plan.m_files)
    locators = sum(len(g.locators) for g in plan.t_files)
    print(f"M rows {m_rows}; T locators {locators}")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    try:
        summary = phase_d.inspect(phase_d.live_root())
    except phase_d.RefusedError as exc:
        return _fail(str(exc))
    print(json.dumps(summary, indent=2))
    return 0


def cmd_phase_d(args: argparse.Namespace) -> int:
    try:
        result = phase_d.run_live(confirm_plan_digest=args.confirm_plan_digest)
    except phase_d.RefusedError as exc:
        return _fail(f"refused: {exc}")
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.status == "COMPLETE" else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evidence_v41_phase_d.py",
        description="Essential-Web evidence v4.1 Phase-D acquirer (frozen plan only).",
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify", help="Verify the committed Phase-D freeze.", allow_abbrev=False)
    sub.add_parser("show-plan", help="Print the frozen Phase-D plan.", allow_abbrev=False)
    sub.add_parser("phase-d-status", help="Read-only Phase-D root status.", allow_abbrev=False)
    live = sub.add_parser("phase-d", help="Run THE frozen Phase-D plan (live).", allow_abbrev=False)
    live.add_argument("--confirm-plan-digest", required=True, metavar="DIGEST")
    handlers = {
        "verify": cmd_verify,
        "show-plan": cmd_show_plan,
        "phase-d-status": cmd_status,
        "phase-d": cmd_phase_d,
    }
    parser.set_defaults(handlers=handlers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = args.handlers[args.command]
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
