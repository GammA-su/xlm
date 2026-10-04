"""Authored fixtures for Phase-C production cleaning (synthetic text only, no real corpus).

``production_layout`` holds a few targeted documents per component, diluted by filler
prose so the v2 dry run stays within the pinned guardrails (an approved dry run must
be ``POLICY_WITHIN_GUARDRAILS``). It includes a mixed KEEP/DROP file whose last row
has no trailing newline, an all-DROP file, an all-KEEP file and an empty file.
:func:`make_flow` runs the real chain: authored corpus -> Phase-A audit -> frozen v1 ->
frozen v2 (predecessor v1) -> authored test cuts -> v2 dry run.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cleaning_fixtures import DOCS, override_thresholds, unique_prose
from quality_fixtures import build_corpus, document
from xlm.data.evidence_v2 import canonical
from xlm.data.quality.cleaning import evaluate
from xlm.data.quality.cleaning_policy import (
    DROP,
    KEEP,
    dump_yaml,
    freeze_policy,
    load_frozen,
    parse_yaml,
)
from xlm.data.quality.cleaning_runner import load_receipt, run_dry_run
from xlm.data.quality.detectors import analyze
from xlm.data.quality.runner import Limits, run_audit

REPO = Path(__file__).resolve().parents[1]
TEMPLATE_V1 = REPO / "recipes" / "quality" / "cleaning_policy_v1.yaml"
TEMPLATE_V2 = REPO / "recipes" / "quality" / "cleaning_policy_v2.yaml"
NO_NEWLINE = "canonical/finewiki_en/default/a/documents.jsonl"
ALL_DROP = "canonical/ultrax_ultrafineweb/alldrop/a/documents.jsonl"
ALL_KEEP = "canonical/simple_stories/default/a/documents.jsonl"
EMPTY = "canonical/simple_stories/empty/a/documents.jsonl"
# Substrings of authored texts that must never appear in a content-free artifact.
TEXT_CANARIES = ("Rivers of region", "cafÃ©", "lost � glyph", "Once upon a time", "abc\x00def")


def limits(workers: int = 1) -> Limits:
    return Limits(workers, 8 * 1024**3, 0, 256 * 1024**2, 1024**2, 600.0)


def _filler(component: str, count: int, start: int, first_row: int) -> list[dict[str, Any]]:
    return [
        document(f"{component}-filler-{start + k}", unique_prose(start + k), first_row + k)
        for k in range(count)
    ]


def production_layout() -> dict[str, list[dict[str, Any]]]:
    def rows(component: str, names: list[str], filler: int, start: int) -> list[dict[str, Any]]:
        out = [
            document(f"{component}-{name}", DOCS[component][name], n + 1)
            for n, name in enumerate(names)
        ]
        # Targeted rows interleaved with filler: KEEP rows on both sides of every DROP.
        mixed: list[dict[str, Any]] = []
        fill = _filler(component, filler, start, len(out) + 1)
        step = max(1, filler // (len(out) + 1))
        for k, row in enumerate(out):
            mixed += fill[k * step : (k + 1) * step] + [row]
        return mixed + fill[len(out) * step :]

    stories = [
        document(f"story-{n:03d}", f"Once upon a time {n} a fox met a crow near a well.", n + 1)
        for n in range(60)
    ]
    return {
        "finewiki_en/default/a": rows("finewiki_en", ["rep_paragraphs", "one_run"], 140, 2000),
        "finewiki_en/default/b": _filler("finewiki_en", 40, 2500, 1),
        "ultrax_ultrafineweb/alldrop/a": [
            document("ultrax-nul", DOCS["ultrax_ultrafineweb"]["nul"], 1),
            document("ultrax-nonchar", DOCS["ultrax_ultrafineweb"]["noncharacter"], 2),
            document("ultrax-mojibake", DOCS["ultrax_ultrafineweb"]["mojibake_16"], 3),
        ],
        "ultrax_ultrafineweb/default/a": rows(
            "ultrax_ultrafineweb", ["html_page", "code_example"], 200, 3000
        ),
        "essential_science/default/a": rows(
            "essential_science", ["replacement_8", "mojibake_10", "code_two"], 150, 4000
        ),
        "common_pile_prose/default/a": rows(
            "common_pile_prose", ["japanese", "emoji", "combining_marks"], 120, 5000
        ),
        "simple_stories/default/a": stories,
        "simple_stories/empty/a": [],
    }


def strip_final_newline(manifest: Path, relative: str) -> None:
    """Rewrite one corpus file without its final newline (and re-digest the manifest)."""
    body = json.loads(manifest.read_bytes())
    path = Path(body["data_root"]) / relative
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    raw = raw[:-1]
    path.write_bytes(raw)
    for entry in body["files"]:
        if entry["path"] == relative:
            entry["file_bytes"] = len(raw)
            entry["documents_sha256"] = hashlib.sha256(raw).hexdigest()
    body.pop("digest")
    body["digest"] = canonical.self_digest(body)
    canonical.write_canonical_json(manifest, body)


def with_predecessor_cuts(frozen_v2: Path, cuts_v1: Path, destination: Path) -> Path:
    body = parse_yaml(frozen_v2.read_bytes())
    v1 = parse_yaml(cuts_v1.read_bytes())
    body["thresholds"] = v1["thresholds"]
    body["provenance"]["predecessor"]["thresholds_digest"] = canonical.digest(v1["thresholds"])
    body["digest"] = canonical.self_digest(body)
    destination.write_bytes(dump_yaml(body))
    return destination


def make_flow(root: Path) -> dict[str, Any]:
    """Authored corpus -> Phase-A audit -> v1/v2 freeze -> test cuts -> v2 dry run."""
    manifest = build_corpus(root / "corpus", production_layout())
    strip_final_newline(manifest, NO_NEWLINE)
    audit = root / "audit"
    run_audit(manifest, audit, limits=limits(), progress_interval=None)
    policies = root / "policies"
    policies.mkdir()
    frozen_v1 = policies / "v1.frozen.yaml"
    freeze_policy(TEMPLATE_V1, audit, frozen_v1)
    frozen_v2 = policies / "v2.frozen.yaml"
    freeze_policy(TEMPLATE_V2, audit, frozen_v2, predecessor=frozen_v1)
    cuts_v1 = override_thresholds(frozen_v1, policies / "v1.cuts.yaml")
    cuts_v2 = with_predecessor_cuts(frozen_v2, cuts_v1, policies / "v2.cuts.yaml")
    dry = root / "dry-run"
    run_dry_run(manifest, dry, cuts_v2, limits=limits(), progress_interval=None)
    receipt = load_receipt(dry)
    return {
        "root": root,
        "manifest": manifest,
        "data": Path(json.loads(manifest.read_bytes())["data_root"]),
        "audit": audit,
        "frozen_v1": frozen_v1,
        "frozen_v2": frozen_v2,
        "cuts_v1": cuts_v1,
        "policy": cuts_v2,
        "dry": dry,
        "digest": receipt["result_digest"],
        "policy_status": receipt["policy_status"],
    }


def oracle(flow: dict[str, Any]) -> dict[str, Any]:
    """Independent expectation: per file the KEEP raw lines (exact bytes) and decisions."""
    compiled = load_frozen(flow["policy"])
    body = json.loads(flow["manifest"].read_bytes())
    files: dict[str, Any] = {}
    for entry in body["files"]:
        raw = (flow["data"] / entry["path"]).read_bytes()
        kept: list[bytes] = []
        outcomes: list[int] = []
        for line in raw.splitlines(keepends=True):
            row = json.loads(line)
            text = row["text"]
            result = analyze(text, len(text.encode("utf-8")))
            decision = evaluate(
                result.values,
                frozenset(result.flags),
                result.doc_class,
                compiled.rules_for(entry["component"]),
                compiled.params,
            )
            outcomes.append(decision.outcome)
            if decision.outcome == KEEP:
                kept.append(line)
        files[entry["path"]] = {
            "component": entry["component"],
            "output": b"".join(kept),
            "outcomes": outcomes,
            "drops": outcomes.count(DROP),
        }
    return files
