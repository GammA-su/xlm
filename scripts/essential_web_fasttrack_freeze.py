# Requires: operator-run only, offline (freeze record, no network).
"""Build and verify the Essential-Web B-normal fast-track selector freeze.

Reads the sealed Arm-M package, the hash-verified development sweep
summary, the unchanged evaluator and policy files and the Git-committed
Arm-T package manifest (hashes and counts only). Writes one canonical
``freeze.json``. No Arm-T text, label, review ID or mapping is opened; no
selector logic is executed or changed here.

Fail-closed: any binding, count or status deviation exits nonzero before
anything is written; an existing freeze is never overwritten.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from xlm.data.evidence_v2 import canonical, fasttrack_freeze, m_analysis

REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATOR_PATH = REPO_ROOT / "scripts" / "essential_web_selector_sweep.py"
POLICY_PATH = REPO_ROOT / "recipes" / "selectors" / "essential_web_selector_sweep_v1.yaml"
PRESET_PATH = REPO_ROOT / "recipes" / "mixtures" / "mix01.yaml"
QUOTAS_PATH = REPO_ROOT / "recipes" / "mixtures" / "mix01_quotas_6b.yaml"
CODE_FILES = (
    "scripts/essential_web_fasttrack_freeze.py",
    "src/xlm/data/evidence_v2/fasttrack_freeze.py",
    "src/xlm/data/evidence_v2/m_analysis.py",
    "src/xlm/data/evidence_v2/canonical.py",
)
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _file_binding(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "path": path.relative_to(REPO_ROOT).as_posix(),
        "bytes": len(raw),
        "sha256": m_analysis.sha256_bytes(raw),
    }


def _mixture() -> dict[str, Any]:
    """Bind the unchanged Mix-01 preset and quota files (no weight is edited)."""
    weights = yaml.safe_load(PRESET_PATH.read_bytes().decode("utf-8"))["weights"]
    return {
        "preset": _file_binding(PRESET_PATH),
        "quotas": _file_binding(QUOTAS_PATH),
        "essential_weights": {name: weights[name] for name in fasttrack_freeze.ADMITTED_COMPONENTS},
        "ultrax_ultrafineweb_weight": weights["ultrax_ultrafineweb"],
        "changed_by_this_freeze": False,
    }


def _environment() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "network": "none",
    }


def build(args: argparse.Namespace, parent_commit: str, decision_date: str) -> dict[str, Any]:
    """Every check and computation; returns the freeze body."""
    if not _SHA_RE.fullmatch(parent_commit):
        raise fasttrack_freeze.FreezeError("parent commit must be an exact 40-hex SHA")
    if not _DATE_RE.fullmatch(decision_date):
        raise fasttrack_freeze.FreezeError("decision date must be YYYY-MM-DD")
    seal, m_summary, m_binding = fasttrack_freeze.load_m_evidence(args.m_evidence_dir)
    development_summary, development_binding = fasttrack_freeze.load_development(
        args.development_dir, seal
    )
    spec, evaluator_binding = fasttrack_freeze.bind_evaluator(EVALUATOR_PATH, POLICY_PATH, seal)
    t_arm = fasttrack_freeze.t_arm_record(args.t_package_manifest)
    commands = [
        "uv run --offline --locked --no-sync --extra cpu --extra eval python "
        "scripts/essential_web_fasttrack_freeze.py build "
        f"--m-evidence-dir {args.m_evidence_dir.as_posix()} "
        f"--development-dir {args.development_dir.as_posix()} "
        f"--t-package-manifest {args.t_package_manifest.as_posix()} "
        f"--output-dir {args.output_dir.as_posix()} "
        f"--parent-commit {parent_commit} --decision-date {decision_date}"
    ]
    return fasttrack_freeze.build_freeze(
        seal=seal,
        m_binding=m_binding,
        m_summary=m_summary,
        development_summary=development_summary,
        development_binding=development_binding,
        spec=spec,
        evaluator_binding=evaluator_binding,
        t_arm=t_arm,
        mixture=_mixture(),
        code={name: _file_binding(REPO_ROOT / name) for name in CODE_FILES},
        parent_commit=parent_commit,
        decision_date=decision_date,
        commands=commands,
        environment=_environment(),
    )


def cmd_build(args: argparse.Namespace) -> int:
    target = args.output_dir / fasttrack_freeze.FREEZE_NAME
    if target.exists():
        raise fasttrack_freeze.FreezeError("output dir already holds a freeze; refusing")
    freeze = build(args, args.parent_commit, args.decision_date)
    canonical.write_atomic(target, m_analysis.dumps(freeze))
    counts = freeze["counts"]
    print(
        json.dumps(
            {
                "freeze_digest": freeze["freeze_digest"],
                "selector": freeze["production_selector"]["condition"],
                "semantics_digest": freeze["production_selector"]["semantics_digest"],
                "t_arm_status": freeze["t_arm"]["status"],
                "development_b_normal": counts["development"]["B-normal"],
                "m_b_normal": counts["m"]["B-normal"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Recompute the freeze from its bound inputs and compare byte for byte."""
    target = args.output_dir / fasttrack_freeze.FREEZE_NAME
    stored = fasttrack_freeze.load_freeze(target)
    rebuilt = build(args, stored["parent_commit"], stored["decision_date"])
    if m_analysis.dumps(rebuilt) != target.read_bytes():
        raise fasttrack_freeze.FreezeError("recomputed freeze differs from the stored freeze")
    print(json.dumps({"verified": True, "freeze_digest": stored["freeze_digest"]}, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Essential-Web fast-track freeze (offline).")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, text in (
        ("build", cmd_build, "Verify every parent and write the freeze."),
        ("verify", cmd_verify, "Recompute the freeze and compare with the stored file."),
    ):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("--m-evidence-dir", type=Path, required=True)
        cmd.add_argument("--development-dir", type=Path, required=True)
        cmd.add_argument("--t-package-manifest", type=Path, required=True)
        cmd.add_argument("--output-dir", type=Path, required=True)
        if name == "build":
            cmd.add_argument("--parent-commit", required=True)
            cmd.add_argument("--decision-date", required=True)
        cmd.set_defaults(func=func)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (
        fasttrack_freeze.FreezeError,
        m_analysis.AnalysisError,
        canonical.CanonicalError,
        KeyError,
        OSError,
    ) as exc:
        print(f"essential_web_fasttrack_freeze: error: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
