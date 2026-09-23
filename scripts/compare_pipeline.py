"""Compare offline benchmark outputs exactly and report relative stage performance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from itertools import zip_longest
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def compare(before: Path, after: Path) -> dict[str, Any]:
    """Fail on changed scientific outputs; never assert an absolute wall time."""
    left = json.loads((before / "report.json").read_text())
    right = json.loads((after / "report.json").read_text())
    if left["documents"] != right["documents"]:
        raise ValueError("document counts differ")
    identities = {}
    for relative in (
        "selected.jsonl",
        "adapted/documents.jsonl",
        "cleaned/documents.jsonl",
        "split/documents.jsonl",
        "tokens/tokens.bin",
        "tokens/offsets.jsonl",
        "tokens/shard_manifest.json",
        "tokens/shard_counters.json",
        "tokenizer/tokenizer.json",
        "tokenizer/tokenizer_manifest.json",
        "sharded/dataset-manifest.json",
    ):
        a, b = digest(before / relative), digest(after / relative)
        if a != b:
            raise AssertionError(f"artifact differs: {relative}")
        identities[relative] = a
    with (
        (before / "quarantine/quarantine.jsonl").open(encoding="utf-8") as left_stream,
        (after / "quarantine/quarantine.jsonl").open(encoding="utf-8") as right_stream,
    ):
        for line_a, line_b in zip_longest(left_stream, right_stream):
            if line_a is None or line_b is None:
                raise AssertionError("quarantine lengths differ")
            qa, qb = json.loads(line_a), json.loads(line_b)
            qa.pop("recorded_at")
            qb.pop("recorded_at")
            if qa != qb:
                raise AssertionError("quarantine decisions/metrics differ")
    summaries = []
    for root in (before, after):
        summary = json.loads((root / "cleaned/cleaning_summary.json").read_text())
        summary.pop("elapsed_seconds")
        for stage in summary["stage_metrics"]:
            stage.pop("duration_ms")
        summaries.append(summary)
    if summaries[0] != summaries[1]:
        raise AssertionError("cleaning summary differs")
    rows = []
    for a, b in zip(left["stages"], right["stages"], strict=True):
        if a["stage"] != b["stage"]:
            raise ValueError("stage order differs")
        for identity in ("membership_digest", "manifest", "valid_targets", "batch_digest"):
            if a.get(identity) != b.get(identity):
                raise AssertionError(f"{identity} differs")
        rows.append(
            {
                "stage": a["stage"],
                "before_seconds": a["wall_seconds"],
                "after_seconds": b["wall_seconds"],
                "speedup": a["wall_seconds"] / b["wall_seconds"],
                "before_rss_bytes": a["peak_tree_rss_bytes"],
                "after_rss_bytes": b["peak_tree_rss_bytes"],
            }
        )
    return {
        "fixture_only": True,
        "documents": left["documents"],
        "stages": rows,
        "exact_artifact_sha256": identities,
        "decisions_metrics_and_targets_equal": True,
        "excluded_nondeterministic_fields": ["recorded_at", "elapsed_seconds", "duration_ms"],
        "before_seconds": left["pipeline_wall_seconds"],
        "after_seconds": right["pipeline_wall_seconds"],
        "speedup": left["pipeline_wall_seconds"] / right["pipeline_wall_seconds"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.before, args.after)
    for row in result["stages"]:
        print(
            f"{row['stage']:24s} {row['before_seconds']:8.3f} -> "
            f"{row['after_seconds']:8.3f}s  {row['speedup']:.2f}x"
        )
    temporary = args.output.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, args.output)


if __name__ == "__main__":
    main()
