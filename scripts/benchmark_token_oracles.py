"""Exact P29B oracles against the trusted local starting commit, without network."""

from __future__ import annotations

import argparse
import gc
import hashlib
import itertools
import json
import subprocess
import sys
import time
import tracemalloc
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from benchmark_token_path import fixture_document

from xlm.core.contracts import CanonicalDocument
from xlm.data.canonical_io import CanonicalDatasetReader
from xlm.data.pools.tokenizer_fit import TokenizerFitConfig, build_tokenizer_fit_manifest
from xlm.tokenizers.bpe import ByteLevelBPETokenizer, _fit_text_stream

BASELINE = "44c19b85ba2c1f0b8bd4502af875d98aed460347"


def reference(path: str, name: str) -> Any:
    source = subprocess.check_output(["git", "show", f"{BASELINE}:{path}"])
    module = types.ModuleType(name)
    module.__file__ = path
    sys.modules[name] = module
    exec(compile(source, path, "exec"), module.__dict__)
    return module


def run(tokenizer: Path, source: Path) -> dict[str, Any]:
    old = reference("src/xlm/tokenizers/bpe.py", "p29b_reference_bpe")
    docs = list(itertools.islice(CanonicalDatasetReader.read_jsonl(source), 10000))
    before = old.ByteLevelBPETokenizer.load(tokenizer)
    after = ByteLevelBPETokenizer.load(tokenizer)
    timings = []
    for repeat in range(2):
        results = []
        for name, tok in (
            (("before", before), ("after", after))
            if repeat == 0
            else (("after", after), ("before", before))
        ):
            digest = hashlib.sha256()
            elapsed = 0.0
            for d in docs:
                start = time.perf_counter()
                ids, spans = tok.encode_with_offsets(d.text, True)
                elapsed += time.perf_counter() - start
                digest.update(json.dumps((ids, spans)).encode())
            results.append({"mode": name, "seconds": elapsed, "digest": digest.hexdigest()})
        assert results[0]["digest"] == results[1]["digest"]
        timings.append(results)
    fit_docs = [d for d in docs if d.split == "train"][:256]
    old_fit = old.ByteLevelBPETokenizer.train_from_documents(fit_docs, target_vocab_size=512)
    new_fit = ByteLevelBPETokenizer.train_from_documents(iter(fit_docs), target_vocab_size=512)
    assert old_fit._tok.to_str() == new_fit._tok.to_str()
    assert old_fit.fingerprint == new_fit.fingerprint
    old_pool = reference("src/xlm/data/pools/tokenizer_fit.py", "p29b_reference_fit")

    def generated() -> Iterator[CanonicalDocument]:
        for i in range(10000):
            yield fixture_document(f"Authored memory fixture {i}. " * 80, i)

    membership = {"view": [str(i) for i in range(10000)]}
    memory = []
    manifests = []
    for name, select in (
        ("before", old_pool.build_tokenizer_fit_manifest),
        ("after", build_tokenizer_fit_manifest),
    ):
        gc.collect()
        tracemalloc.start()
        start = time.perf_counter()
        manifest = select(
            generated(),
            membership,
            {"view": 1.0},
            "fixture",
            TokenizerFitConfig(target_sample_bytes=1000000),
        )
        wall = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        memory.append({"mode": name, "wall_seconds": wall, "python_peak_bytes": peak})
        manifests.append(manifest.to_dict())
    assert manifests[0] == manifests[1]
    prep = []
    for mode in ("list", "spool"):
        gc.collect()
        tracemalloc.start()
        start = time.perf_counter()
        digest = hashlib.sha256()
        if mode == "list":
            texts = [d.text for d in generated()]
            for text in texts:
                digest.update(text.encode())
            del texts
        else:
            with _fit_text_stream(generated(), 10000, 100 * 1024**2) as (texts_iter, _):
                for text in texts_iter:
                    digest.update(text.encode())
        wall = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        prep.append(
            {
                "mode": mode,
                "wall_seconds": wall,
                "python_peak_bytes": peak,
                "text_digest": digest.hexdigest(),
            }
        )
    assert prep[0]["text_digest"] == prep[1]["text_digest"]
    return {
        "baseline": BASELINE,
        "span_timings": timings,
        "fit_json_and_fingerprint_exact": True,
        "fit_manifest_exact": True,
        "fit_selection_memory": memory,
        "fit_preparation_memory": prep,
        "fixture_documents": 10000,
        "memory_kind": "tracemalloc Python heap; native trainer excluded",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.tokenizer, args.source)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
