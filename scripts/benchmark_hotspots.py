"""Offline paired benchmarks against reviewed repository source at a fixed revision."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import subprocess
import sys
import time
import types
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from benchmark_pipeline import Measurements, fixture_rows

from xlm.core.contracts import CanonicalDocument
from xlm.data.adapters.jsonl import JsonlAdapter
from xlm.data.cleaning.features import compute_char_stats
from xlm.data.normalization import canonical_normalize
from xlm.data.pools.splits import SplitConfig, assign_splits
from xlm.data.tokens import TokenShardWriter
from xlm.tokenizers.bpe import ByteLevelBPETokenizer

REFERENCE = "2a82dfd8c2bc3e0d183d9c339ae0cfe8d073784f"


def reference_module(name: str, source: str) -> types.ModuleType:
    """Load only the explicitly pinned, trusted repository implementation oracle."""
    code = subprocess.check_output(["git", "show", f"{REFERENCE}:{source}"], timeout=10)
    module = types.ModuleType(name)
    sys.modules[name] = module  # dataclass resolution requires module registration.
    exec(compile(code, f"{REFERENCE}:{source}", "exec"), module.__dict__)
    return module


def documents(count: int) -> list[CanonicalDocument]:
    adapter = JsonlAdapter("authored_mix")
    return [
        adapter.process_line(json.dumps(row).encode(), "authored.jsonl", i)
        for i, row in enumerate(fixture_rows(count))
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    args = parser.parse_args()
    root: Path = args.output
    root.mkdir(parents=True, exist_ok=False)
    measurements = Measurements(root, 600, 1)
    tokenizer = ByteLevelBPETokenizer.load(args.tokenizer)
    old_bpe = reference_module("_p29_bpe", "src/xlm/tokenizers/bpe.py").ByteLevelBPETokenizer.load(
        args.tokenizer
    )
    old_features = reference_module("_p29_features", "src/xlm/data/cleaning/features.py")
    old_tokens = reference_module("_p29_tokens", "src/xlm/data/tokens.py")
    old_splits = reference_module("_p29_splits", "src/xlm/data/pools/splits.py")
    docs = documents(2000)
    comparisons: dict[str, Any] = {}

    def paired(name: str, before: Callable[[], Any], after: Callable[[], Any]) -> None:
        times: list[float] = []
        values: list[Any] = []
        for suffix, call in (("before", before), ("after", after)):
            with measurements.stage(f"{name}_{suffix}") as row:
                start = time.perf_counter()
                values.append(call())
                times.append(time.perf_counter() - start)
                row["call_seconds"] = times[-1]
        if values[0] != values[1]:
            raise AssertionError(f"{name} reference mismatch")
        comparisons[name] = {
            "before_seconds": times[0],
            "after_seconds": times[1],
            "speedup": times[0] / times[1],
            "exact_equal": True,
        }

    def characters(function: Callable[..., Any], texts: list[str]) -> list[tuple[int, ...]]:
        return [
            tuple(getattr(stats, key) for key in stats.__slots__)
            for text in texts
            for stats in [function(text)]
        ]

    for label, texts in (
        ("ascii_chars", [d.text.encode("ascii", "ignore").decode() for d in docs]),
        ("unicode_chars", [d.text + "\u00e9" for d in docs]),
    ):
        paired(
            label,
            partial(characters, old_features.compute_char_stats, texts),
            partial(characters, compute_char_stats, texts),
        )
    paired(
        "bpe_offsets",
        lambda: [old_bpe.encode_with_offsets(d.text, True) for d in docs],
        lambda: [tokenizer.encode_with_offsets(d.text, True) for d in docs],
    )
    paired(
        "bpe_ids",
        lambda: [old_bpe.encode(d.text, True) for d in docs],
        lambda: [tokenizer.encode(d.text, True) for d in docs],
    )

    def write(writer_type: Any, directory: str) -> dict[str, str]:
        target = root / directory
        writer_type(target, "fixture", "authored_mix", tokenizer).write_documents(docs, True)
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir()}

    paired(
        "token_writer",
        lambda: write(old_tokens.TokenShardWriter, "reference"),
        lambda: write(TokenShardWriter, "candidate"),
    )
    split_config = SplitConfig(
        diagnostic_val_target_bytes=1000, quick_val_target_bytes=500, audit_target_bytes=500
    )
    paired(
        "split",
        lambda: old_splits.assign_splits(iter(docs), config=split_config).to_dict(),
        lambda: assign_splits(iter(docs), config=split_config).to_dict(),
    )

    batch_times = []
    texts = [d.text for d in docs]
    expected = [tokenizer.encode(text) for text in texts]
    for size in (1, 16, 64, 128, 512):
        with measurements.stage(f"bpe_batch_{size}") as row:
            start = time.perf_counter()
            # Reproduce the discarded Rust-batch experiment without changing the
            # public tokenizer. Authored rows are <=8 KiB; cap batches at 128.
            effective_size = min(size, 128)
            actual = [
                encoding.ids
                for i in range(0, len(texts), effective_size)
                for encoding in tokenizer._tok.encode_batch(
                    [canonical_normalize(t) for t in texts[i : i + effective_size]],
                    add_special_tokens=False,
                )
            ]
            elapsed = time.perf_counter() - start
            if actual != expected:
                raise AssertionError("batch IDs differ")
            row["call_seconds"] = elapsed
            batch_times.append(
                {"batch_size": size, "effective_batch_size": effective_size, "seconds": elapsed}
            )
    del actual, expected
    gc.collect()

    # Investigate serialization without changing the public detached-dict contract.
    serialization = []
    for count in (10_000, 100_000, 1_000_000):
        with measurements.stage(f"serialization_{count}") as row:
            start = time.perf_counter()
            total = sum(
                len(json.dumps(docs[i % 6].to_dict(), ensure_ascii=False)) for i in range(count)
            )
            row["characters_encoded"] = total
            row["call_seconds"] = time.perf_counter() - start
            serialization.append({"documents": count, "seconds": row["call_seconds"]})
    # Small direct-dict experiment: same JSON for these fixtures, but it lacks
    # to_dict's general deep-copy/dataclass semantics, so it is not a product path.
    paired(
        "serialize_fixture",
        lambda: [json.dumps(d.to_dict(), ensure_ascii=False) for d in docs],
        lambda: [json.dumps(vars(d), ensure_ascii=False) for d in docs],
    )

    report = {
        "reference": REFERENCE,
        "fixture_only": True,
        "comparisons": comparisons,
        "batch_scaling": batch_times,
        "serialization": serialization,
        "stages": measurements.rows,
        "python": sys.version,
        "note": "serialization fixture experiment is not an implemented optimization",
    }
    temporary = root / "report.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, root / "report.json")


if __name__ == "__main__":
    main()
