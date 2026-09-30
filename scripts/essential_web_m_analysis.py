# Requires: operator-run only, offline (evidence reporting, no network).
"""Run and seal the frozen Essential-Web Arm-M selector-evidence analysis.

Reads the hash-bound Phase-D M output read-only, builds the checked
``derived_analysis_input`` view, runs the UNCHANGED frozen evaluator
(``essential_web_selector_sweep.run_sweep``) for A-D normal/strict,
compares against the hash-verified frozen development artifacts and
writes a sealed, text-free result package. Evidence reporting only: no
policy ranking, no selector decision, no Arm-T access.

Fail-closed: any binding, hash, count, order or conservation deviation
exits nonzero before anything is written; an existing seal is never
overwritten.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from xlm.data.evidence_v2 import canonical, m_analysis, m_input_adapter

REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATOR_PATH = REPO_ROOT / "scripts" / "essential_web_selector_sweep.py"
POLICY_PATH = REPO_ROOT / "recipes" / "selectors" / "essential_web_selector_sweep_v1.yaml"
DERIVED_NAME = "m_derived_analysis_input.jsonl"
RECEIPT_NAME = "phase_d_receipt.json"
CODE_FILES = (
    "scripts/essential_web_m_analysis.py",
    "src/xlm/data/evidence_v2/m_analysis.py",
    "src/xlm/data/evidence_v2/m_input_adapter.py",
    "src/xlm/data/evidence_v2/canonical.py",
)


def load_evaluator(path: Path = EVALUATOR_PATH) -> Any:
    """Import the frozen evaluator file as a module, unmodified."""
    spec = importlib.util.spec_from_file_location("essential_web_selector_sweep", path)
    if spec is None or spec.loader is None:
        raise m_analysis.AnalysisError(f"cannot load evaluator '{path}'")
    module = importlib.util.module_from_spec(spec)
    sys.modules["essential_web_selector_sweep"] = module
    spec.loader.exec_module(module)
    return module


def _stat(paths: Sequence[Path]) -> list[tuple[str, int, int]]:
    return [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths]


def _code_bindings() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in CODE_FILES:
        raw = (REPO_ROOT / name).read_bytes()
        out[name] = {"bytes": len(raw), "sha256": m_analysis.sha256_bytes(raw)}
    return out


def _environment() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "network": "none",
    }


def _write_derived(path: Path, payload: bytes) -> None:
    """Write the derived input once; an existing different file refuses."""
    if path.exists():
        if m_analysis.sha256_bytes(path.read_bytes()) != m_analysis.sha256_bytes(payload):
            raise m_analysis.AnalysisError(f"existing derived input '{path}' differs; refusing")
        return
    canonical.write_atomic(path, payload)


def build_package(args: argparse.Namespace) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Every check and computation; returns (files to write, run facts)."""
    started = time.monotonic()
    preparation, preparation_binding = m_analysis.load_preparation(args.preparation)
    sources = [
        args.phase_d_root / m_input_adapter.M_OUTPUT_NAME,
        args.phase_d_root / m_input_adapter.M_MANIFEST_NAME,
        args.phase_d_root / RECEIPT_NAME,
    ]
    for path in sources:
        m_analysis.refuse_t_material(path.relative_to(args.phase_d_root))
    before = _stat(sources)

    evaluator = load_evaluator()
    spec, evaluator_binding = m_analysis.verify_evaluator(
        EVALUATOR_PATH, POLICY_PATH, preparation, evaluator
    )
    if dict(spec["caps"]) != dict(preparation["analysis_caps"]):
        raise m_analysis.AnalysisError("frozen evaluator caps differ from the preparation caps")
    development, development_binding = m_analysis.verify_development(
        args.development_dir, preparation
    )

    adapted = m_input_adapter.adapt_m_input(sources[0], sources[1], sources[2], preparation)
    if m_input_adapter.metadata_digest_of(adapted.payload) != adapted.manifest["metadata_digest"]:
        raise m_analysis.AnalysisError("derived input metadata differs from the source metadata")
    derived_path = args.work_dir / DERIVED_NAME
    _write_derived(derived_path, adapted.payload)
    binding = m_input_adapter.evaluator_binding(adapted.manifest)

    result = evaluator.run_sweep(derived_path, binding, spec, spec["caps"])
    payloads = result["payloads"]
    kernel = m_analysis.kernel_tables(evaluator, adapted.payload, spec, binding)
    results = m_analysis.build_results(payloads, kernel, binding["crawls"])
    m_artifacts = {name: payloads[name] for name in m_analysis.SWEEP_PAYLOADS}

    files: dict[str, bytes] = {}
    for name in m_analysis.SWEEP_PAYLOADS:
        files[f"m_sweep/{name}"] = evaluator._dumps(payloads[name]).encode("utf-8")
    files["m_sweep/summary.md"] = evaluator.render_summary(
        evaluator_binding["policy"]["digest"],
        adapted.manifest["output"]["sha256"],
        result["summary"],
        result["per_crawl"],
        result["diagnostics"],
    ).encode("utf-8")
    files["m_sweep/policy_spec.json"] = evaluator._dumps(spec).encode("utf-8")
    frozen_policy = development_binding["artifacts"]["policy_spec.json"]["sha256"]
    if m_analysis.sha256_bytes(files["m_sweep/policy_spec.json"]) != frozen_policy:
        raise m_analysis.AnalysisError("policy spec differs from the development sweep's spec")

    derived_binding = {
        "path": str(derived_path),
        "bytes": len(adapted.payload),
        "sha256": m_analysis.sha256_bytes(adapted.payload),
    }
    comparison = m_analysis.build_comparison(
        development,
        m_artifacts,
        kernel,
        {
            "m_source_sha256": adapted.manifest["source"]["records"]["sha256"],
            "m_derived_input_sha256": derived_binding["sha256"],
            "development_manifest_digest": development_binding["manifest"]["digest"],
            "development_artifacts": {
                name: entry["sha256"] for name, entry in development_binding["artifacts"].items()
            },
            "evaluator_sha256": evaluator_binding["sha256"],
            "policy_digest": evaluator_binding["policy"]["digest"],
        },
    )
    code = _code_bindings()
    if (
        code["src/xlm/data/evidence_v2/m_input_adapter.py"]["sha256"]
        != (adapted.manifest["adapter"]["code_sha256"])
    ):
        raise m_analysis.AnalysisError("adapter code hash differs from the repository file")
    files["adapter_manifest.json"] = m_analysis.dumps(
        {
            "kind": "essential_web_m_adapter_manifest",
            "adapter": adapted.manifest["adapter"],
            "output_kind": adapted.manifest["kind"],
            "mapping": adapted.manifest["mapping"],
            "marker": adapted.manifest["marker"],
            "not_invented": adapted.manifest["not_invented"],
            "evaluator_entry_point": "run_sweep (frozen legacy load_binding is not used)",
            "evaluator_binding_view": {k: v for k, v in binding.items() if k != "files"},
            "refuses": [
                "count != 4096",
                "duplicate row identity",
                "missing row identity",
                "order drift",
                "source hash mismatch",
                "unsupported input shape",
            ],
        }
    )
    files["adapted_input_manifest.json"] = m_analysis.dumps(
        {**adapted.manifest, "derived_file": derived_binding}
    )
    files["development_binding.json"] = m_analysis.dumps(development_binding)
    files["m_sweep_results.json"] = m_analysis.dumps(results)
    files["m_comparison.json"] = m_analysis.dumps(comparison)
    files["m_comparison.md"] = m_analysis.render_comparison(comparison).encode("utf-8")

    manifest = m_analysis.artifact_manifest(files)
    commands = [
        "uv run --offline --locked --no-sync --extra cpu --extra eval python "
        "scripts/essential_web_m_analysis.py run "
        f"--preparation {args.preparation.as_posix()} "
        f"--phase-d-root {args.phase_d_root.as_posix()} "
        f"--development-dir {args.development_dir.as_posix()} "
        f"--work-dir {args.work_dir.as_posix()} "
        f"--output-dir {args.output_dir.as_posix()}"
    ]
    seal = m_analysis.build_seal(
        preparation=preparation,
        preparation_binding=preparation_binding,
        adapted_manifest=adapted.manifest,
        derived_input=derived_binding,
        evaluator=evaluator_binding,
        development=development_binding,
        analysis_code=code,
        manifest=manifest,
        commands=commands,
        environment=_environment(),
    )
    files[m_analysis.ARTIFACT_MANIFEST_NAME] = m_analysis.dumps(manifest)
    files[m_analysis.SEAL_NAME] = m_analysis.dumps(seal)

    total = sum(len(raw) for raw in files.values())
    if total + len(adapted.payload) > int(spec["caps"]["max_output_bytes"]):
        raise m_analysis.AnalysisError("analysis outputs exceed the frozen output cap")
    if _stat(sources) != before:
        raise m_analysis.AnalysisError("a Phase-D source file changed during the analysis")
    facts = {
        "seal_digest": seal["seal_digest"],
        "artifact_manifest_digest": manifest["digest"],
        "records": results["input_records"],
        "source_sha256": adapted.manifest["source"]["records"]["sha256"],
        "derived_input_sha256": derived_binding["sha256"],
        "row_identity_digest": adapted.manifest["row_identity_digest"],
        "output_bytes": total,
        "derived_input_bytes": len(adapted.payload),
        "evaluator_wall_seconds": result["stats"]["wall_seconds"],
        "endpoint_rss_bytes": evaluator._endpoint_rss_bytes(),
        "wall_seconds": round(time.monotonic() - started, 3),
        "source_files_unchanged": True,
    }
    return files, facts


def cmd_run(args: argparse.Namespace) -> int:
    if (args.output_dir / m_analysis.SEAL_NAME).exists():
        raise m_analysis.AnalysisError("output dir already holds a sealed M package; refusing")
    files, facts = build_package(args)
    # The seal is written last: an interrupted write leaves no seal.
    ordered = [n for n in sorted(files) if n != m_analysis.SEAL_NAME] + [m_analysis.SEAL_NAME]
    for name in ordered:
        canonical.write_atomic(args.output_dir / name, files[name])
    print(json.dumps(facts, indent=2, sort_keys=True))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Recheck the seal, its result files and every external bound input."""
    seal = m_analysis.verify_seal(args.output_dir)
    files, facts = build_package(args)
    if facts["seal_digest"] != seal["seal_digest"]:
        raise m_analysis.AnalysisError("recomputed seal digest differs from the sealed digest")
    for name, raw in files.items():
        if (args.output_dir / name).read_bytes() != raw:
            raise m_analysis.AnalysisError(f"recomputed {name} differs from the sealed file")
    print(json.dumps({"verified": True, "seal_digest": seal["seal_digest"]}, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Frozen Essential-Web M analysis (offline).")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, text in (
        ("run", cmd_run, "Adapt, evaluate, compare and seal."),
        ("verify", cmd_verify, "Recompute everything and compare with the seal."),
    ):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("--preparation", type=Path, required=True)
        cmd.add_argument("--phase-d-root", type=Path, required=True)
        cmd.add_argument("--development-dir", type=Path, required=True)
        cmd.add_argument("--work-dir", type=Path, required=True)
        cmd.add_argument("--output-dir", type=Path, required=True)
        cmd.set_defaults(func=func)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (
        m_analysis.AnalysisError,
        m_input_adapter.AdapterError,
        canonical.CanonicalError,
        OSError,
    ) as exc:
        print(f"essential_web_m_analysis: error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if type(exc).__name__ != "SweepError":
            raise
        print(f"essential_web_m_analysis: evaluator refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
