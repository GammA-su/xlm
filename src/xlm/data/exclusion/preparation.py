"""Offline C05 preparation CLI; deliberately has no execution/authorization verb.

The current exclusion engine is development-only. An audit artifact is never
an executable plan. Official benchmark material and detailed matches belong to
the isolated operator environment defined by EVALUATION_POLICY.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from xlm.data.acquisition.source_run import write_once
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.inputs import (
    InputError,
    build_input_manifest,
    read_metadata,
    require_equal,
    verify_input_manifest,
)
from xlm.evaluation.suites import OFFICIAL_DATASET_REPOS, load_dataset_pins

LOG = logging.getLogger(__name__)


def benchmark_requirements(pins_path: Path) -> dict[str, Any]:
    """Read public metadata pins, never benchmark examples or a raw signature index."""
    pins = load_dataset_pins(pins_path)
    require_equal(set(pins), set(OFFICIAL_DATASET_REPOS), "benchmark task coverage")
    tasks: dict[str, Any] = {}
    fields = {
        "arc_easy": ["question", "choices.text"],
        "hellaswag": ["ctx", "ctx_a", "ctx_b", "endings"],
        "piqa": ["goal", "sol1", "sol2"],
        "blimp": ["sentence_good", "sentence_bad"],
    }
    for name, pin in sorted(pins.items()):
        revision = pin.require_resolved()
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise InputError("benchmark revision is not an immutable commit")
        require_equal(pin.repository, OFFICIAL_DATASET_REPOS[name], "benchmark repository")
        tasks[name] = {
            "repository": pin.repository,
            "revision": revision,
            "configuration_requirement": "ARC-Easy"
            if name == "arc_easy"
            else "all subdatasets"
            if name == "blimp"
            else "publisher configuration",
            "split_requirement": "all publisher splits, including train and final",
            "expected_text_fields_to_validate": fields[name],
            "verified_configs": None,
            "verified_splits": None,
            "verified_item_count": None,
            "material_sha256": None,
            "index_identity": None,
            "status": "PINNED_METADATA_ONLY",
        }
    return {
        "pins_file_sha256": hashlib.sha256(pins_path.read_bytes()).hexdigest(),
        "tasks": tasks,
        "operator_preparation_required": True,
        "network_acquisition_required": "unknown until isolated operator local inventory",
    }


def preparation_audit(manifest: dict[str, Any], pins_path: Path) -> dict[str, Any]:
    require_equal(manifest.get("digest"), canonical.self_digest(manifest), "input digest")
    benchmark = benchmark_requirements(pins_path)
    body: dict[str, Any] = {
        "kind": "c05_preparation_audit",
        "version": 1,
        "input_manifest_digest": manifest["digest"],
        "benchmark_requirements": benchmark,
        "status": "BLOCKED",
        "executable": False,
        "plan_digest": None,
        "blockers": [
            "No verified protected benchmark material/index receipt, "
            "exact config/split coverage or counts",
            "Development matcher is in-memory and suppresses repeated benchmark spans "
            "using corpus frequency",
            "No bounded global C05 scan journal, crash recovery or protected "
            "atomic publication integration",
            "No end-to-end C05 kept-membership gate in tokenizer/final-mixture production tooling",
            "Unknown Gutenberg book lineage and SYNTH seed-URL grouping "
            "require explicit policy treatment",
        ],
        "resource_observations": manifest["totals"],
        "execution_authorized": False,
        "limitations": [
            "Metadata inventory is not corpus-content revalidation or global deduplication",
            "No exclusions, kept membership, split freeze or protected receipt have been produced",
            "No task material, raw signature hashes or benchmark labels were opened",
        ],
    }
    body["digest"] = canonical.digest(body)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "verify", "preflight"))
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--pins", type=Path, default=Path("manifests/eval_dataset_pins.yaml"))
    parser.add_argument("--audit-output", type=Path)
    args = parser.parse_args(argv)
    try:
        for output in (args.manifest if args.command == "inventory" else None, args.audit_output):
            if output is not None and output.resolve().is_relative_to(args.data_root.resolve()):
                raise InputError("preparation output must be outside the immutable data root")
        if args.command == "inventory":
            manifest = build_input_manifest(args.data_root, args.scratch_root)
            write_once(args.manifest, manifest)
            print(json.dumps({"manifest_digest": manifest["digest"], "totals": manifest["totals"]}))
            return 0
        manifest = read_metadata(args.manifest)
        verify_input_manifest(manifest, args.data_root, args.scratch_root)
        if args.command == "verify":
            print(
                json.dumps(
                    {"verified": "metadata_and_sizes", "manifest_digest": manifest["digest"]}
                )
            )
            return 0
        audit = preparation_audit(manifest, args.pins)
        if args.audit_output is not None:
            write_once(args.audit_output, audit)
        print(json.dumps(audit, indent=2, sort_keys=True))
        return 2  # Explicit refusal, never a success exit for missing production prerequisites.
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        LOG.error("c05_preparation_failed", extra={"error_type": type(exc).__name__})
        print(f"C05 preparation refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
