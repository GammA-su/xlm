"""Build an evaluation-input manifest from prepared local benchmark artifacts.

This is a thin operator wrapper. All construction and verification logic lives
in :mod:`xlm.evaluation.inputs`; this script only reads a data-only entry
description, calls that package, and prints the result.

It never downloads anything, never contacts a provider, and never selects data.
The operator performs the selection and extraction separately, under whatever
transfer and licensing authority applies; this turns the resulting files into a
frozen, verifiable declaration of what may be evaluated.

Usage
-----

::

    uv run --offline --locked --extra cpu --extra eval python scripts/build_eval_inputs.py \\
        --entries manifests/eval_inputs/dev_v1.entries.yaml \\
        --out     manifests/eval_inputs/dev_v1.yaml

The entries file is a mapping::

    scope_label: "frozen development subset v1"
    scope_kind: frozen_development_subset      # or authored_fixture
    exposure_class: development_exposed        # or authored_fixture
    tier: search                               # search | confirmation
    required_blimp_subdatasets: [blimp_adjunct_island, blimp_anaphor_gender_agreement]
    acquisition_receipts: []                   # D02 receipt ids, when available
    entries:
      - task: arc_easy
        leaf_task: arc_easy
        source_repository: allenai/ai2_arc
        source_revision: 210d026faf9955653af8916fad021475a3f00453
        source_config: ARC-Easy
        source_split: train                    # must match the tier policy split
        source_population_size: 2251
        record_schema_version: ai2_arc.v1
        adapter_version: xlm_eval_json.v1
        item_id_field: id
        label_field: answerKey
        data_file: arc_easy_search.json

``item_ids`` and the content digests are read from the artifacts themselves, so
they cannot be mistyped or quietly widened.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:  # pragma: no cover - operator convenience
    sys.path.insert(0, str(REPO_ROOT / "src"))

from xlm.evaluation.harness import harness_version  # noqa: E402
from xlm.evaluation.inputs import (  # noqa: E402
    EvaluationInputError,
    build_evaluation_inputs,
    save_evaluation_inputs,
    verify_evaluation_inputs,
)
from xlm.evaluation.suites import SuiteTier  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--entries", type=Path, required=True, help="Entry description (YAML or JSON)."
    )
    parser.add_argument("--out", type=Path, required=True, help="Manifest path to write.")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=None,
        help="Directory that relative data_file paths resolve against "
        "(default: the entries file's directory).",
    )
    parser.add_argument(
        "--harness-version",
        default=None,
        help="Override the recorded harness version (default: the installed one).",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Write the manifest without verifying it afterwards.",
    )
    parser.add_argument("--json", action="store_true", help="Print the summary as JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.entries.is_file():
        print(f"error: entries file not found: {args.entries}", file=sys.stderr)
        return 2
    raw: Any = yaml.safe_load(args.entries.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        print(f"error: {args.entries} must be a mapping with an 'entries' list", file=sys.stderr)
        return 2

    base_dir = (args.base_dir or args.entries.parent).resolve()
    installed = args.harness_version or harness_version()
    if installed is None:
        print(
            "error: the evaluation extra is not installed, so no harness version can be "
            "recorded. Run with --extra eval, or pass --harness-version deliberately.",
            file=sys.stderr,
        )
        return 2

    try:
        manifest = build_evaluation_inputs(
            scope_label=str(raw["scope_label"]),
            scope_kind=str(raw["scope_kind"]),
            exposure_class=str(raw["exposure_class"]),
            tier=SuiteTier(str(raw["tier"])),
            harness_version=installed,
            entries=raw["entries"],
            base_dir=base_dir,
            required_blimp_subdatasets=raw.get("required_blimp_subdatasets") or (),
            acquisition_receipts=raw.get("acquisition_receipts") or (),
            notes=raw.get("notes") or (),
        )
    except (EvaluationInputError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    save_evaluation_inputs(manifest, args.out)

    summary: dict[str, Any] = {
        "manifest": str(args.out),
        "manifest_id": manifest.manifest_id(),
        "scope_label": manifest.scope_label,
        "scope_kind": manifest.scope_kind,
        "tier": str(manifest.tier),
        "selections": len(manifest.selections),
        "verified": False,
    }
    if not args.no_verify:
        try:
            verified = verify_evaluation_inputs(
                manifest, base_dir=base_dir, harness_version=installed
            )
        except EvaluationInputError as exc:
            print(f"error: manifest written but failed verification: {exc}", file=sys.stderr)
            return 1
        summary["verified"] = True
        summary["detail"] = verified.summary()

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"Manifest written : {args.out}")
        print(f"Manifest id      : {summary['manifest_id']}")
        print(f"Scope            : {manifest.scope_kind} '{manifest.scope_label}'")
        print(f"Tier             : {manifest.tier} | selections: {len(manifest.selections)}")
        print(f"Verified         : {summary['verified']}")
        if not manifest.acquisition_receipts:
            print(
                "NOTE: no acquisition receipt referenced; this manifest attests to local "
                "content identity only, not to how the data was obtained."
            )
    return 0


if __name__ == "__main__":  # pragma: no cover - script entry point
    raise SystemExit(main())
