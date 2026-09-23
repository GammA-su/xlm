"""Exact P29C fixture checks with explicit implementation-provenance differences."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from benchmark_cleaning_long import normalized_result, reference
from benchmark_cleaning_v2 import digest_files
from benchmark_pipeline import Measurements

from xlm.data.canonical_io import CanonicalDatasetReader
from xlm.data.cleaning.features import TextFeatures
from xlm.data.cleaning.pipeline import create_pipeline_preset


def summary_identity(value: dict[str, Any]) -> tuple[dict[str, Any], str]:
    value = json.loads(json.dumps(value))
    fingerprint = value.pop("pipeline_hash")
    value.pop("elapsed_seconds", None)
    for row in value["stage_metrics"]:
        row.pop("duration_ms", None)
    return value, fingerprint


def compare_cleaning(before: Path, after: Path) -> dict[str, Any]:
    a, b = (json.loads((p / "report.json").read_text()) for p in (before, after))
    for key in ("accepted_sha256", "quarantine_logical_sha256"):
        assert a[key] == b[key], key
    left, old = summary_identity(a["scientific_summary"])
    right, new = summary_identity(b["scientific_summary"])
    assert left == right, "scientific summary differs"
    return {
        "before": str(before),
        "after": str(after),
        "accepted_sha256": a["accepted_sha256"],
        "quarantine_logical_sha256": a["quarantine_logical_sha256"],
        "before_code_fingerprint": old,
        "after_code_fingerprint": new,
    }


def compare_pipeline(before: Path, after: Path) -> dict[str, Any]:
    hashes = {}
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
        a, b = (digest_files([p / relative]) for p in (before, after))
        assert a == b, relative
        hashes[relative] = a
    quarantine = [
        digest_files([p / "quarantine/quarantine.jsonl"], quarantine=True) for p in (before, after)
    ]
    assert quarantine[0] == quarantine[1]
    summaries = [
        summary_identity(json.loads((p / "cleaned/cleaning_summary.json").read_text()))
        for p in (before, after)
    ]
    assert summaries[0][0] == summaries[1][0]
    reports = [json.loads((p / "report.json").read_text()) for p in (before, after)]
    for a, b in zip(reports[0]["stages"], reports[1]["stages"], strict=True):
        for key in ("stage", "membership_digest", "manifest", "valid_targets", "batch_digest"):
            assert a.get(key) == b.get(key), key
    return {
        "hashes": hashes,
        "quarantine_logical_sha256": quarantine[0],
        "code_fingerprints": [s[1] for s in summaries],
        "before_seconds": reports[0]["pipeline_wall_seconds"],
        "after_seconds": reports[1]["pipeline_wall_seconds"],
    }


def transform_oracle(source: Path) -> dict[str, Any]:
    if source.stat().st_size > 512 * 1024**2:
        raise ValueError("512 MiB oracle input cap exceeded")
    old = create_pipeline_preset("prose")
    current = create_pipeline_preset("prose")
    replacements = {
        "language_filter": reference("language").LanguageFilter(),
        "repetition_filter": reference("repetition").RepetitionFilter(),
        "pii_secret_filter": reference("pii").PiiSecretFilter(),
    }
    old.transforms = [replacements.get(t.transform_id, t) for t in old.transforms]
    deadline = time.monotonic() + 180
    digests = [hashlib.sha256(), hashlib.sha256()]
    count = 0
    for doc in itertools.islice(CanonicalDatasetReader.read_jsonl(source), 10000):
        if time.monotonic() > deadline:
            raise TimeoutError("180 second exactness cap")
        docs = [doc, doc]
        features = [TextFeatures(), TextFeatures()]
        for pair in zip(old.transforms, current.transforms, strict=True):
            results = [t.apply(d, f) for t, d, f in zip(pair, docs, features, strict=True)]
            values = [normalized_result(r) for r in results]
            if values[0] != values[1]:
                raise AssertionError(f"stage mismatch at authored document {count}")
            for digest, value in zip(digests, values, strict=True):
                digest.update(json.dumps(value, sort_keys=True).encode())
            if results[0].document is None:
                break
            docs = []
            for result in results:
                if result.document is None:
                    raise AssertionError("reference and candidate continuation differ")
                docs.append(result.document)
        count += 1
    assert digests[0].digest() == digests[1].digest()
    return {
        "documents": count,
        "all_stage_results_sha256": digests[0].hexdigest(),
        "fields": (
            "actions, cleaned text, metadata, quality metrics, ordered reasons, "
            "byte counts, identities; only durations excluded"
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--pipeline", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(dir=args.output.parent, prefix=".oracle-") as temporary:
        measure = Measurements(Path(temporary), 180, 1, max_artifact_bytes=1024**2)
        with measure.stage("exactness"):
            result = (
                transform_oracle(args.source)
                if args.source
                else (
                    compare_pipeline(args.before, args.after)
                    if args.pipeline
                    else compare_cleaning(args.before, args.after)
                )
            )
    payload = json.dumps(result, indent=2)
    if len(payload.encode()) > 1024**2:
        raise ValueError("1 MiB oracle output cap exceeded")
    args.output.write_text(payload, encoding="utf-8")
    print(json.dumps(result, indent=2))
