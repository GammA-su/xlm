"""Bounded, key-free local audit of the pinned C07-v2 tokenizer and selection.

No production output, network, signing key, or corpus text is written.
"""

from __future__ import annotations

import hashlib
import json
import random
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from xlm.core.contracts import CanonicalDocument
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.tokenfast import assemble_line, index_line_parts
from xlm.data.normalization import canonical_normalize
from xlm.data.tokens import derived_byte_spans, token_byte_lengths
from xlm.tokenizers.bpe import ByteLevelBPETokenizer

FINGERPRINT = "50bf9d45f6f6fa88062483d0e83f3be419e610dee6b75d37809252504bac5b54"
SELECTION = "17b1cfce2ce643ecb72d53f367f95ade00f1a842d23a242c6f3a234c74c34662"
SELECTED_SHA = "a4539473bdfcbcf0d52efc621dcfb8efa3fff1cbba8e0e7cc943cf31cad54be9"


def strings(seed: int, count: int) -> list[str]:
    rng = random.Random(seed)
    atoms = [
        "",
        "<bos>",
        "<eos>",
        "<pad>",
        "<unk>",
        "\r\n",
        "\t",
        "\x00",
        "e\u0301",
        "\u0301",
        "\u200d",
        "\U0001f469\u200d\U0001f4bb",
        " ",
        "!!!",
    ]
    atoms += [chr(i) for i in range(128)]
    atoms += [chr(i) for i in (0x80, 0xFF, 0x800, 0x2028, 0x4E2D, 0xFFFF, 0x10000, 0x10FFFF)]
    return atoms + ["".join(rng.choices(atoms, k=rng.randrange(160))) for _ in range(count)]


def exactness(tokenizer: ByteLevelBPETokenizer, count: int = 10_000) -> dict[str, Any]:
    start = time.monotonic()
    lengths = np.frombuffer(token_byte_lengths(tokenizer), dtype="<u2").astype(np.int64)
    samples = strings(7107, count)
    comparisons = 0
    for text in samples:
        if time.monotonic() - start > 120:
            raise RuntimeError("property probe exceeded 120 seconds")
        for special in (False, True):
            ids, spans = tokenizer.encode_with_offsets(text, special)
            expected = [list(s) for s in spans]
            if derived_byte_spans(ids, lengths) != expected:
                raise AssertionError("v1/v2 span mismatch (content suppressed)")
            assert sum(int(lengths[i]) for i in ids) == len(canonical_normalize(text).encode())
            for stop in {0, 1, len(ids) // 2, max(0, len(ids) - 1), len(ids)}:
                assert derived_byte_spans(ids[:stop], lengths) == expected[:stop]
                comparisons += 1
    return {
        "strings": len(samples),
        "prefix_comparisons": comparisons,
        "mismatches": 0,
        "seconds": time.monotonic() - start,
        "table_bytes": lengths.size * 2,
        "max_token_bytes": int(lengths.max()),
    }


def selection_sizes() -> dict[str, Any]:
    start = time.monotonic()
    proof = json.loads(Path("G:/XLM/c05-policy-v2/policy-v2-p0001.proof.json").read_bytes())
    plan = json.loads(Path(proof["plan"]).read_bytes())
    envelope = json.loads(Path("G:/XLM/selection/mix01-policy-v2/selection.json").read_bytes())
    assert envelope["digest"] == canonical.digest(envelope["payload"]) == SELECTION
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in plan["files"]:
        groups[json.dumps([entry[k] for k in ("component", "view", "upstream_component")])].append(
            entry
        )
    candidates = {}
    scanned = 0
    for entries in groups.values():
        for n in sorted({0, len(entries) // 2, len(entries) - 1}):
            local = 0
            with (Path(plan["data_root"]) / entries[n]["path"]).open("rb") as source:
                while local < 512 * 1024:
                    raw = source.readline(plan["resources"]["document_bytes"] + 1)
                    if not raw:
                        break
                    local += len(raw)
                    scanned += len(raw)
                    if len(raw) > plan["resources"]["document_bytes"] or scanned > 64 * 1024**2:
                        raise RuntimeError("bounded source sample exceeded")
                    row = canonical.loads_bytes_strict(raw)
                    candidates[row["doc_id"]] = row
    stats: dict[str, Any] = {}
    digest = hashlib.sha256()
    selected_bytes = 0
    with Path("G:/XLM/selection/mix01-policy-v2/selected.jsonl").open("rb") as source:
        while raw := source.readline(65537):
            selected_bytes += len(raw)
            if len(raw) > 65536 or selected_bytes > 1_428_788_117 or time.monotonic() - start > 165:
                raise RuntimeError("bounded metadata audit exceeded")
            digest.update(raw)
            row = json.loads(raw)
            component = row["allocation"][0]
            s = stats.setdefault(
                component,
                {
                    "documents": 0,
                    "targets": 0,
                    "max_tokens": 0,
                    "sample_lines": 0,
                    "sample_index_bytes": 0,
                    "max_sample_line": 0,
                    "max_id_bytes": 0,
                },
            )
            s["documents"] += 1
            s["targets"] += row["selected_valid_targets"]
            count = row["selected_valid_targets"] + 1
            s["max_tokens"] = max(s["max_tokens"], count)
            s["max_id_bytes"] = max(s["max_id_bytes"], len(row["doc_id"].encode()))
            doc = candidates.get(row["doc_id"])
            if doc is not None:
                assert canonical.digest(doc) == row["content"]
                parts = index_line_parts(
                    CanonicalDocument(**doc),
                    component,
                    count,
                    doc["utf8_byte_count"],
                    None,
                    [0],
                    [count - 1],
                    proof["completion_digest"],
                    SELECTION,
                    row["content"],
                    row["counted_valid_targets"],
                    row["selected_valid_targets"],
                )
                size = len(assemble_line(*parts, 6_005_824_661, 104_000_000_000, 104_000_000_000))
                s["sample_lines"] += 1
                s["sample_index_bytes"] += size
                s["max_sample_line"] = max(s["max_sample_line"], size)
    assert digest.hexdigest() == SELECTED_SHA
    for s in stats.values():
        s["token_bytes_exact"] = 2 * (s["targets"] + s["documents"])
        s["index_projection"] = s["documents"] * s["sample_index_bytes"] / s["sample_lines"]
        s["index_bound_at_2k_record_cap"] = s["documents"] * 2048
        s["shard_projection"] = s["token_bytes_exact"] + s["index_projection"] + 65536 + 65536
    return {
        "components": stats,
        "source_sample_bytes": scanned,
        "selected_sha256": digest.hexdigest(),
        "signature_verified": False,
        "seconds": time.monotonic() - start,
        "method": "three file prefixes/allocation; nonrandom estimate; independent selected hash",
    }


def resources() -> dict[str, Any]:
    """Private authored 50k-document sequential consumer probe, capped at 512 MiB."""
    import importlib.util
    import subprocess
    import sys

    import psutil

    from xlm.core.contracts import TokenShardManifest
    from xlm.data.exclusion.freezefast import _index_blocks, parse_index_block
    from xlm.data.input_validation import validate_training_index
    from xlm.data.tokens import TokenShardReader

    began = time.monotonic()
    scratch = Path("C:/XLM-scratch")
    scratch.mkdir(exist_ok=True)
    tokenizer = ByteLevelBPETokenizer.load(
        Path("G:/XLM/tokfit/mix01-fit-shares-v1-policy-v2/tokenizer")
    )
    assert tokenizer.fingerprint == FINGERPRINT
    text = "Authored diagnostic only. The river flows past trees and stones. " * 100
    ids, _ = tokenizer.encode_with_offsets(text, True)
    packed = np.asarray(ids, dtype="<u2").tobytes()
    nbytes = len(text.encode())
    count = len(ids)
    documents = 50_000
    with tempfile.TemporaryDirectory(prefix="c07-consumer-probe-", dir=scratch) as temp:
        root = Path(temp)
        tokens_hash, index_hash = hashlib.sha256(), hashlib.sha256()
        written = 0
        with (
            (root / "tokens.bin").open("wb") as tokens,
            (root / "offsets.jsonl").open("wb") as index,
        ):
            for i in range(documents):
                if time.monotonic() - began > 120 or written > 512 * 1024**2:
                    raise RuntimeError("consumer probe bound")
                record = {
                    "doc_id": str(i),
                    "source_id": "authored",
                    "split": "train",
                    "token_count": count,
                    "token_start": i * count,
                    "byte_count": nbytes,
                    "covered_bytes": nbytes,
                    "bos_positions": [0],
                    "eos_positions": [count - 1],
                    "c05_content": "0" * 64,
                    "c05_counted_valid_targets": count - 1,
                    "c05_selected_valid_targets": count - 1,
                    "valid_targets": count - 1,
                    "c05_receipt": "authored",
                    "c05_selection": "authored",
                }
                raw = (json.dumps(record) + "\n").encode()
                tokens.write(packed)
                tokens_hash.update(packed)
                index.write(raw)
                index_hash.update(raw)
                written += len(packed) + len(raw)
        table = token_byte_lengths(tokenizer)
        (root / "token_bytes.u16").write_bytes(table)
        manifest = TokenShardManifest(
            "authored",
            "authored",
            count * documents,
            documents,
            "uint16",
            "little",
            tokenizer.fingerprint,
            "authored",
            tokens_hash.hexdigest(),
            index_hash.hexdigest(),
            1.0,
        )
        (root / "shard_manifest.json").write_text(json.dumps(manifest.to_dict()))
        (root / "shard_counters.json").write_text(
            json.dumps(
                {
                    "index_schema": "c07-offsets-v2",
                    "token_bytes_sha256": hashlib.sha256(table).hexdigest(),
                }
            )
        )
        start = time.perf_counter()
        reader = TokenShardReader(root)
        init = time.perf_counter() - start
        start = time.perf_counter()
        reader.verify_integrity()
        hashes = time.perf_counter() - start
        start = time.perf_counter()
        validate_training_index(reader)
        validate = time.perf_counter() - start
        # Same authored metadata through source-SHA and changed parser once each.
        source = subprocess.run(
            [
                "git",
                "show",
                "602cd3ff83a6495589b8d9d7608c9ebc77118488:src/xlm/data/exclusion/freezefast.py",
            ],
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
        module_path = root / "reference.py"
        module_path.write_bytes(source)
        spec = importlib.util.spec_from_file_location("c07_reference_freeze", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        timings = {}
        for name, parser in (("source", module.parse_index_block), ("reviewed", parse_index_block)):
            start = time.perf_counter()
            rows = sum(
                parser(block).rows
                for block in _index_blocks(
                    root / "offsets.jsonl", "authored", "authored", "authored", True
                )
            )
            assert rows == documents
            timings[name] = time.perf_counter() - start
        memory = psutil.Process().memory_info()
        result = {
            "kind": "authored-consumer-probe",
            "documents": documents,
            "tokens": count * documents,
            "payload_bytes": written,
            "reader_init_seconds": init,
            "hash_seconds": hashes,
            "validate_seconds": validate,
            "validate_docs_per_second": documents / validate,
            "freeze_parse_seconds": timings,
            "rss_bytes": memory.rss,
            "parent_peak_rss_bytes": getattr(memory, "peak_wset", memory.rss),
            "seconds": time.monotonic() - began,
            "scratch_payload_removed": True,
            "notes": "authored repeated text; C: warm-cache probe; no real signatures",
        }
    return result


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["properties"]:
        directory = Path("G:/XLM/tokfit/mix01-fit-shares-v1-policy-v2/tokenizer")
        tok = ByteLevelBPETokenizer.load(directory)
        assert tok.fingerprint == FINGERPRINT
        envelope = json.loads(Path("G:/XLM/selection/mix01-policy-v2/selection.json").read_bytes())
        assert canonical.digest(envelope["payload"]) == envelope["digest"] == SELECTION
        files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
        assert canonical.digest(files) == envelope["payload"]["tokenizer"]["files_digest"]
        from xlm.tokenizers.bpe import _U2B, SPECIAL_TOKENS

        model = json.loads(tok._tok.to_str())
        assert model["added_tokens"] == [] and model["normalizer"] is None
        assert model["model"]["dropout"] is None and model["post_processor"] is None
        assert model["pre_tokenizer"]["type"] == "ByteLevel"
        assert model["pre_tokenizer"]["add_prefix_space"] is False
        assert model["truncation"] is None and model["padding"] is None
        vocab = model["model"]["vocab"]
        assert all(s in SPECIAL_TOKENS or (s and all(c in _U2B for c in s)) for s in vocab)
        result = exactness(tok)
        result["tokenizer_files_digest"] = canonical.digest(files)
        result["table_sha256"] = hashlib.sha256(token_byte_lengths(tok)).hexdigest()
        result["frozen_configuration_audited"] = True
    elif sys.argv[1:] == ["sizes"]:
        result = selection_sizes()
    elif sys.argv[1:] == ["resources"]:
        result = resources()
    else:
        raise SystemExit("choose properties or sizes")
    print(json.dumps(result, sort_keys=True))
