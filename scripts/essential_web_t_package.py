# Requires: operator-run only, offline (custodian packaging, no network).
"""Materialize and verify the frozen Essential-Web Arm-T blinded review package.

Confirms the sealed Arm-M result first, then reads the hash-bound Phase-D
Arm-T acquisition read-only and writes the custodian ledger and the two
reviewer packages to an access-controlled directory outside Git and outside
the evidence roots. Only text-free, secret-free bindings are written to the
repository output directory. No labeling, unblinding, reselection, selector
decision or network access.

Fail-closed: any seal, hash, membership, leak or reuse deviation exits
nonzero before anything is written; existing blinding material is reused
exactly and a sealed package is never overwritten.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import blinding, canonical, m_analysis, t_package

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = REPO_ROOT / "docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md"
RUBRIC_PATH = REPO_ROOT / "src/xlm/data/evidence_v2/rubric.py"
GIT_TIMEOUT_SECONDS = 60
CODE_FILES = (
    "scripts/essential_web_t_package.py",
    "src/xlm/data/evidence_v2/t_package.py",
    "src/xlm/data/evidence_v2/blinding.py",
    "src/xlm/data/evidence_v2/rubric.py",
    "src/xlm/data/evidence_v2/frozen.py",
    "src/xlm/data/evidence_v2/canonical.py",
    "src/xlm/data/evidence_v2/m_analysis.py",
)


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )


def check_m_commit(seal_dir: Path, commit: str) -> None:
    """The working M seal file is byte-identical to the sealed M result commit."""
    try:
        relative = seal_dir.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError as exc:
        raise t_package.PackageError("M seal directory is not inside this repository") from exc
    if _git("merge-base", "--is-ancestor", commit, "HEAD").returncode != 0:
        raise t_package.PackageError("sealed M result commit is not an ancestor of HEAD")
    for name in (m_analysis.SEAL_NAME, m_analysis.ARTIFACT_MANIFEST_NAME):
        blob = _git("show", f"{commit}:{relative}/{name}")
        if blob.returncode != 0 or blob.stdout != (seal_dir / name).read_bytes():
            raise t_package.PackageError(f"working {name} differs from the sealed M result commit")


def refuse_location(root: Path, forbidden: Sequence[Path]) -> None:
    """The package lives outside Git and outside the evidence roots."""
    resolved = root.resolve()
    for parent in forbidden:
        base = parent.resolve()
        if resolved == base or base in resolved.parents:
            raise t_package.PackageError(f"package root must be outside '{base}'")


def _code_bindings() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in CODE_FILES:
        raw = (REPO_ROOT / name).read_bytes()
        out[name] = {"bytes": len(raw), "sha256": t_package.sha256_bytes(raw)}
    return out


def _environment() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "network": "none",
    }


def _sources(args: argparse.Namespace) -> t_package.Sources:
    return t_package.Sources(
        preparation=args.preparation,
        entry_bindings=args.entry_bindings,
        phase_d_root=args.phase_d_root,
        selection=args.selection,
        m_seal_dir=args.m_seal_dir,
        protocol=PROTOCOL_PATH,
        rubric_code=RUBRIC_PATH,
    )


def _stat(sources: t_package.Sources) -> list[tuple[str, int, int]]:
    paths = [
        sources.phase_d_root / name
        for name in (
            t_package.DOCUMENTS_NAME,
            t_package.PROVENANCE_NAME,
            t_package.T_MANIFEST_NAME,
            t_package.RECEIPT_NAME,
        )
    ] + [sources.selection]
    return [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths]


def _resolve_prior(args: argparse.Namespace, root: Path) -> tuple[t_package.Prior | None, str]:
    """Existing blinding material wins; nothing is generated when any exists."""
    own = t_package.load_prior(root / t_package.CUSTODIAN)
    record = root / t_package.MATERIALIZATION_PATH
    if own is not None:
        mode = t_package.FIRST_MATERIALIZATION
        if record.exists():
            mode = str(json.loads(record.read_bytes().decode("utf-8"))["mode"])
        return own, mode
    if args.command == "verify":
        raise t_package.PackageError("package root holds no custodian key to verify against")
    if args.prior_custodian_dir is not None:
        prior = t_package.load_prior(args.prior_custodian_dir)
        if prior is None:
            raise t_package.PackageError("prior custodian directory holds no custodian key")
        return prior, t_package.REUSED_PRIOR
    return None, t_package.FIRST_MATERIALIZATION


def build_package(args: argparse.Namespace) -> tuple[t_package.Built, t_package.Blinding, Path]:
    """Every check and computation; only a first-time custodian key is written here."""
    root = args.package_root.resolve()
    refuse_location(root, [REPO_ROOT, args.phase_d_root.resolve().parent])
    pins = t_package.REAL_PINS
    check_m_commit(args.m_seal_dir, t_package.M_RESULT_COMMIT)
    m_parent = t_package.verify_m_seal(args.m_seal_dir, pins, t_package.M_RESULT_COMMIT)
    sources = _sources(args)
    before = _stat(sources)
    source = t_package.load_sources(sources, pins, m_parent)
    prior, mode = _resolve_prior(args, root)
    if prior is None:
        # First materialization: K is generated exactly once and persisted before
        # anything is derived from it, so a later refusal can never cause a reroll.
        secret = blinding.generate_secret()
        t_package.write_key(root, secret)
        prior = t_package.Prior(secret=secret, ids=None, orders=None)
    blind = t_package.assign_blinding(source.entries, prior)
    built = t_package.build(
        source,
        blind,
        m_parent,
        pins,
        mode=mode,
        code=_code_bindings(),
        environment=_environment(),
    )
    if _stat(sources) != before:
        raise t_package.PackageError("a read-only source file changed during materialization")
    return built, blind, root


def _facts(built: t_package.Built, root: Path) -> dict[str, Any]:
    manifest = built.manifest
    return {
        "package_digest": manifest["package_digest"],
        "package_root": str(root),
        "m_seal_digest": manifest["m_seal_parent"]["seal_digest"],
        "materialization": manifest["blinding"]["materialization"],
        "key_commitment": manifest["blinding"]["key_commitment"],
        "counts": manifest["counts"],
        "order_digests": {r: c["order_digest"] for r, c in manifest["reviewers"].items()},
        "leakage_structural_hits": manifest["leakage_audit"]["structural_hits"],
        "leakage_content_level_hits": manifest["leakage_audit"]["content_level_hits"],
        "payload_bytes": manifest["limits"]["payload_bytes"],
        "code_digest": manifest["code_digest"],
    }


def cmd_materialize(args: argparse.Namespace) -> int:
    built, blind, root = build_package(args)
    for name, raw in built.public.items():
        path = args.output_dir / name
        if path.exists() and path.read_bytes() != raw:
            raise t_package.PackageError(f"existing Git binding differs; refusing: {name}")
    t_package.write_package(root, built, blind.secret)
    for name in sorted(built.public):
        canonical.write_atomic(args.output_dir / name, built.public[name])
    print(json.dumps(_facts(built, root), indent=2, sort_keys=True))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Recompute the whole package from the bound inputs and the sealed key."""
    built, blind, root = build_package(args)
    t_package.verify_package(root, args.output_dir, built, blind.secret)
    print(json.dumps({"verified": True, **_facts(built, root)}, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Frozen Essential-Web T blinded package (offline)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, text in (
        ("materialize", cmd_materialize, "Verify parents, build and seal the package."),
        ("verify", cmd_verify, "Recompute everything and compare with the sealed package."),
    ):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("--preparation", type=Path, required=True)
        cmd.add_argument("--entry-bindings", type=Path, required=True)
        cmd.add_argument("--phase-d-root", type=Path, required=True)
        cmd.add_argument("--selection", type=Path, required=True)
        cmd.add_argument("--m-seal-dir", type=Path, required=True)
        cmd.add_argument("--package-root", type=Path, required=True)
        cmd.add_argument("--output-dir", type=Path, required=True)
        cmd.add_argument("--prior-custodian-dir", type=Path, default=None)
        cmd.set_defaults(func=func)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (
        t_package.PackageError,
        canonical.CanonicalError,
        subprocess.SubprocessError,
        OSError,
    ) as exc:
        print(f"essential_web_t_package: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
