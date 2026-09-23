"""P28 lexical dedup throughput: offline fixtures only.

Structural assertions are deterministic; wall times and RSS are reported,
never threshold-gated (except one generous peak-RSS bound). Per-run timings
(elapsed/duration fields) are excluded from cross-run comparisons.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.core.contracts import CanonicalDocument
from xlm.data.datasets.shards import ShardedJsonlWriter, load_manifest, verify_manifest
from xlm.data.dedup.engine import DedupConfig, DeduplicationEngine
from xlm.data.dedup.index import PartitionedKeyIndex, partition_for
from xlm.data.dedup.matchview import match_normalize
from xlm.data.dedup.minhash import (
    MinHasher,
    _signature_python,
    shingles,
    shingles_from_tokens,
    stable_hash64,
)
from xlm.data.dedup.sharded import run_sharded_dedup
from xlm.data.normalization import compute_sha256

WORDS = (
    "the study of neural computation involves learning representations from data "
    "through optimization of differentiable objectives with careful regularization "
    "and validation across diverse benchmarks for robust generalization in practice".split()
)


def _prose(rng: random.Random, n_words: int) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(n_words))


def _doc_dict(doc_id: str, text: str, row: int, source: str = "fixture") -> dict:
    blob = text.encode("utf-8")
    return {
        "doc_id": doc_id,
        "source_id": source,
        "source_revision": "rev0",
        "source_file": "f.jsonl",
        "source_row": row,
        "raw_hash": compute_sha256(blob),
        "clean_hash": compute_sha256(blob),
        "text": text,
        "utf8_byte_count": len(blob),
        "language": "en",
        "language_confidence": 1.0,
        "document_kind": "prose",
        "source_metadata": {},
        "parent_ids": [],
        "license_reference": "cc-by-4.0",
        "transform_log": [],
        "quality_reasons": [],
        "cluster_ids": {},
        "split": "train",
    }


def gen_dedup(n: int, seed: int) -> list[str]:
    """Seven deterministic near/duplicate categories plus unrelated prose."""
    rng = random.Random(seed)
    base_texts = [_prose(rng, 300) for _ in range(max(1, n // 10))]
    template = "Section standard header. {} Section standard footer."
    lines: list[str] = []
    for i in range(n):
        cat = i % 14
        base = rng.choice(base_texts)
        if cat == 0:
            text = base
        elif cat == 1:
            text = base.upper().replace(",", ";")
        elif cat == 2:
            words = base.split()
            words[::13] = ["EDITED"] * len(words[::13])
            text = " ".join(words)
        elif cat == 3:
            text = _prose(rng, 300)
        elif cat == 4:
            text = template.format(_prose(rng, 120))
        elif cat == 5:
            text = _prose(rng, 2500)
        elif cat == 6:
            text = ("boilerplate shared line. " * 30) + _prose(rng, 40)
        else:
            text = _prose(rng, 300) if i % 2 else base
        lines.append(json.dumps(_doc_dict(f"doc-{i:06d}", text, i), ensure_ascii=False))
    return lines


def _reference_run(
    tmp_path: Path, lines: list[str], name: str, **config_kwargs: object
) -> tuple[list[dict], object]:
    from xlm.data.dedup.engine import DedupResult

    docs = [CanonicalDocument(**json.loads(line)) for line in lines]
    config = DedupConfig(**config_kwargs)  # type: ignore[arg-type]
    result = DeduplicationEngine(config).run(iter(docs), tmp_path / f"{name}_work")
    assert isinstance(result, DedupResult)
    from xlm.data.dedup.engine import iter_surviving_documents

    survivors = [doc.to_dict() for doc in iter_surviving_documents(iter(docs), result)]
    return survivors, result


def _engine_run(
    tmp_path: Path,
    selected: Path,
    name: str,
    *,
    workers: int = 1,
    output_shard_bytes: int | None = None,
    input_shard_bytes: int = 1024 * 1024,
    **config_kwargs: object,
) -> tuple[Path, object, dict]:
    out = tmp_path / f"{name}_out"
    config = DedupConfig(**config_kwargs)  # type: ignore[arg-type]
    result, _, assembled, throughput, _ = run_sharded_dedup(
        input_path=selected,
        output_dir=out,
        config=config,
        workers=workers,
        input_shard_bytes=input_shard_bytes,
        output_shard_bytes=output_shard_bytes,
        work_dir=tmp_path / f"{name}_work",
        max_input_bytes=4 * 1024**3,
    )
    return out, result, throughput


def _read_survivors(out: Path) -> list[dict]:
    manifest_path = out / "dataset-manifest.json"
    files = []
    if manifest_path.is_file():
        manifest = load_manifest(manifest_path)
        assert verify_manifest(out, manifest) is manifest
        files = [out / entry.path for entry in sorted(manifest.shards, key=lambda e: e.ordinal)]
    else:
        files = [out / "documents.jsonl"]
    survivors = []
    for path in files:
        survivors.extend(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return survivors


# Exactness of the building blocks.


def test_signature_view_sharing_matches() -> None:
    hasher = MinHasher()
    samples = ["", "a", "one two three", "x" * 5000, "mixed CASE, punct! text here. " * 40]
    for text in samples:
        view = match_normalize(text)
        assert hasher.signature_from_view(view) == hasher.signature(text)
        assert shingles_from_tokens(
            view.split(" ") if view else [], hasher.config.shingle_size
        ) == shingles(text, hasher.config.shingle_size)


@pytest.mark.optional_dependency
def test_kernel_matches_reference_when_numpy_present() -> None:
    numpy = pytest.importorskip("numpy")
    assert numpy is not None
    from xlm.data.dedup.minhash import _signature_vectorized

    rng = random.Random(20260919)
    hasher = MinHasher()
    params = hasher._params
    for _ in range(60):
        n = rng.choice([0, 1, 2, 9, 60, 400])
        text = " ".join(f"w{rng.randint(0, 2000):05d}" for _ in range(n))
        grams = shingles(text, 5)
        hashed = {stable_hash64(g) for g in grams}
        if not hashed:
            assert hasher.signature(text) == [(1 << 64) - 1] * 128
            continue
        assert _signature_vectorized(hashed, params) == _signature_python(hashed, params)
    for h in (0, 1, (1 << 61) - 1, 1 << 61, (1 << 64) - 1):
        assert _signature_vectorized({h}, params) == _signature_python({h}, params)


def test_binary_index_matches_json_groupings_and_fails_closed(tmp_path: Path) -> None:
    index = PartitionedKeyIndex(tmp_path / "idx", partition_count=8)
    with index:
        for i in range(300):
            index.add(f"key_{i % 25}", f"doc_{i:04d}")
    assert index.entries_written == 300
    groups = dict(index.groups(min_size=2))
    assert len(groups) == 25
    for doc_ids in groups.values():
        assert doc_ids == sorted(doc_ids)
    assert dict(index.groups(min_size=300)) == {}
    # Foreign magic fails closed.
    foreign = tmp_path / "idx" / "part_0000.bin"
    foreign.write_bytes(b"NOTMAGIC" + foreign.read_bytes()[8:])
    with pytest.raises(ValueError, match="magic"):
        dict(index.groups(min_size=2))
    # Truncation fails closed (separate index: the magic case above aborts first).
    index2 = PartitionedKeyIndex(tmp_path / "idx2", partition_count=8)
    with index2:
        for i in range(50):
            index2.add(f"tk_{i % 5}", f"td_{i:04d}")
    assert len(dict(index2.groups(min_size=2))) == 5
    victim = next(p for p in sorted((tmp_path / "idx2").glob("part_*.bin")) if p.stat().st_size > 8)
    victim.write_bytes(victim.read_bytes()[:7])
    with pytest.raises(ValueError, match="truncat|magic"):
        dict(index2.groups(min_size=2))
    assert partition_for("key_3", 8) == partition_for("key_3", 8)


def test_clean_hash_is_not_exact_identity(tmp_path: Path) -> None:
    """P28-C: punctuation-only variants share exact identity but not clean_hash."""
    first = _doc_dict("c1", "Hello, World! This is a Test.", 0)
    second = _doc_dict("c2", "hello world this is a test", 1)
    assert first["clean_hash"] != second["clean_hash"]
    lines = [json.dumps(first), json.dumps(second)]
    _, result = _reference_run(tmp_path, lines, "cleanhash")
    assert result.stats.exact_duplicate_pairs == 1
    assert len(result.survivor_doc_ids) == 1


# Engine vs reference and layout/worker invariance (P28-AC).


@pytest.mark.scale
def test_engine_matches_reference(tmp_path: Path, documents: int = 1500) -> None:
    lines = gen_dedup(documents, 51)
    ref_survivors, ref_result = _reference_run(tmp_path, lines, "ref1500")
    selected = tmp_path / "ref1500.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out, result, _ = _engine_run(tmp_path, selected, "eng1500")
    assert result.to_dict() == ref_result.to_dict()
    assert _read_survivors(out) == ref_survivors
    assert (out / "dedup_report.json").is_file()
    assert (out / "dedup_throughput.json").is_file()


@pytest.mark.scale
@pytest.mark.serial
def test_workers_and_layouts_agree(tmp_path: Path, documents: int = 1200) -> None:
    lines = gen_dedup(documents, 52)
    selected = tmp_path / "w.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_dir = tmp_path / "w_shards"
    writer = ShardedJsonlWriter(manifest_dir, dataset_id="in", target_shard_bytes=64 * 1024)
    for index, line in enumerate(lines):
        writer.write_line(line, f"doc-{index:06d}")
    in_manifest = writer.finish()
    assert verify_manifest(manifest_dir, in_manifest) is in_manifest
    runs = []
    for tag, path, workers in (
        ("w1", selected, 1),
        ("w2", selected, 2),
        ("w4", selected, 4),
        ("manifest", manifest_dir, 4),
    ):
        out, result, _ = _engine_run(tmp_path, path, tag, workers=workers)
        runs.append((result.to_dict(), _read_survivors(out)))
    assert runs[0] == runs[1] == runs[2] == runs[3]


@pytest.mark.scale
def test_sharded_output_equivalence(tmp_path: Path, documents: int = 800) -> None:
    lines = gen_dedup(documents, 53)
    selected = tmp_path / "s.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    legacy_out, legacy_result, _ = _engine_run(tmp_path, selected, "leg")
    shard_out, shard_result, _ = _engine_run(tmp_path, selected, "sh", output_shard_bytes=32 * 1024)
    assert shard_result.to_dict() == legacy_result.to_dict()
    assert _read_survivors(shard_out) == _read_survivors(legacy_out)
    assert (shard_out / "dataset-manifest.json").is_file()


@pytest.mark.scale
def test_output_shard_sizes_preserve_decisions(tmp_path: Path, documents: int = 600) -> None:
    lines = gen_dedup(documents, 54)
    selected = tmp_path / "z.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    streams = []
    for target_kb, tag in ((8, "a"), (128, "b"), (1024, "c")):
        out, _, _ = _engine_run(tmp_path, selected, f"z{tag}", output_shard_bytes=target_kb * 1024)
        streams.append(_read_survivors(out))
    assert streams[0] == streams[1] == streams[2]


@pytest.mark.scale
def test_exact_only_and_threshold_modes(tmp_path: Path, documents: int = 400) -> None:
    from xlm.data.dedup.minhash import MinHashConfig

    lines = gen_dedup(documents, 55)
    ref_exact, _ = _reference_run(tmp_path, lines, "exactref", enable_near_duplicates=False)
    selected = tmp_path / "exact.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out, _, _ = _engine_run(tmp_path, selected, "exacteng", enable_near_duplicates=False)
    assert _read_survivors(out) == ref_exact
    strict = MinHashConfig(jaccard_threshold=0.95)
    ref_strict_docs, ref_strict = _reference_run(tmp_path, lines, "strictref", minhash=strict)
    out_s, result_s, _ = _engine_run(tmp_path, selected, "stricteng", minhash=strict)
    assert result_s.to_dict() == ref_strict.to_dict()
    assert _read_survivors(out_s) == ref_strict_docs


def test_oversized_buckets_and_budgets_preserved(tmp_path: Path) -> None:
    # Thirty near-identical long documents share every LSH band, so each
    # band bucket (30 members) exceeds the cap of 4 and is reported.
    rng = random.Random(77)
    base_words = _prose(rng, 300).split()
    lines = []
    for i in range(30):
        words = list(base_words)
        words[i % len(words)] = f"variant{i:03d}"
        lines.append(json.dumps(_doc_dict(f"boiler_{i}", " ".join(words), i)))
    _, result = _reference_run(
        tmp_path, lines, "over", max_bucket_size=4, max_candidates_per_document=2
    )
    selected = tmp_path / "over.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _, eng_result, _ = _engine_run(
        tmp_path, selected, "overeng", max_bucket_size=4, max_candidates_per_document=2
    )
    assert eng_result.to_dict() == result.to_dict()
    assert result.stats.oversized_buckets > 0


def test_empty_input(tmp_path: Path) -> None:
    selected = tmp_path / "empty.jsonl"
    selected.write_text("", encoding="utf-8")
    out, result, _ = _engine_run(tmp_path, selected, "empty")
    assert result.stats.documents_seen == 0
    assert _read_survivors(out) == []


@pytest.mark.serial
def test_worker_failure_aborts_without_publication(tmp_path: Path) -> None:
    lines = gen_dedup(200, 56)
    lines[100] = "{poisoned"
    selected = tmp_path / "poison.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = tmp_path / "poison_out"
    with pytest.raises(ValueError, match="Malformed JSON|Invalid canonical"):
        run_sharded_dedup(
            input_path=selected,
            output_dir=out,
            config=DedupConfig(),
            workers=2,
            input_shard_bytes=8 * 1024,
            output_shard_bytes=8 * 1024,
            work_dir=tmp_path / "poison_work",
            max_input_bytes=4 * 1024**3,
        )
    assert not (out / "dataset-manifest.json").exists()
    assert not (out / "dedup_report.json").exists()


# CLI surface.


@pytest.mark.serial
def test_cli_dedup_legacy_and_sharded(tmp_path: Path) -> None:
    lines = gen_dedup(300, 57)
    selected = tmp_path / "cli.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    runner = CliRunner()
    legacy = runner.invoke(
        data_app, ["dedup", "--input", str(selected), "--output-dir", str(tmp_path / "cli_leg")]
    )
    assert legacy.exit_code == 0, legacy.output
    assert (tmp_path / "cli_leg" / "documents.jsonl").is_file()
    assert (tmp_path / "cli_leg" / "documents.parquet").is_file()
    assert (tmp_path / "cli_leg" / "dedup_report.json").is_file()
    sharded = runner.invoke(
        data_app,
        [
            "dedup",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "cli_sh"),
            "--workers",
            "2",
            "--output-shard-bytes",
            "16384",
        ],
    )
    assert sharded.exit_code == 0, sharded.output
    assert (tmp_path / "cli_sh" / "dataset-manifest.json").is_file()
    assert _read_survivors(tmp_path / "cli_sh") == _read_survivors(tmp_path / "cli_leg")


@pytest.mark.serial
def test_cli_dedup_manifest_input_and_errors(tmp_path: Path) -> None:
    lines = gen_dedup(200, 58)
    selected = tmp_path / "e.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_dir = tmp_path / "e_shards"
    writer = ShardedJsonlWriter(manifest_dir, dataset_id="in", target_shard_bytes=8192)
    for index, line in enumerate(lines):
        writer.write_line(line, f"doc-{index:06d}")
    writer.finish()
    runner = CliRunner()
    result = runner.invoke(
        data_app,
        [
            "dedup",
            "--input",
            str(manifest_dir),
            "--output-dir",
            str(tmp_path / "e_out"),
            "--workers",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    single = runner.invoke(
        data_app, ["dedup", "--input", str(selected), "--output-dir", str(tmp_path / "e_single")]
    )
    assert single.exit_code == 0, single.output
    assert _read_survivors(tmp_path / "e_out") == _read_survivors(tmp_path / "e_single")
    tiny_budget = runner.invoke(
        data_app,
        [
            "dedup",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "y"),
            "--max-input-bytes",
            "10",
        ],
    )
    assert tiny_budget.exit_code == 1
    assert "exceeds limit" in tiny_budget.output


# Benchmarks (slow): throughput tables are printed, never asserted on time.


def _benchmark_table(tag: str, rows: list[tuple]) -> None:
    print(
        tag + ": " + ", ".join(f"w{w}/{d}docs/{wall:.1f}s/{d / wall:.0f}dps" for w, d, wall in rows)
    )


@pytest.mark.slow
@pytest.mark.performance
@pytest.mark.serial
def test_benchmark_10k_workers(tmp_path: Path) -> None:
    lines = gen_dedup(10_000, 59)
    selected = tmp_path / "bench10k.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = []
    for workers in (1, 2, 4, 8):
        started = time.monotonic()
        out, result, throughput = _engine_run(
            tmp_path, selected, f"b10k_{workers}", workers=workers
        )
        wall = time.monotonic() - started
        assert result.stats.documents_seen == 10_000
        assert _read_survivors(out)
        rows.append((workers, 10_000, wall))
    _benchmark_table("10k-lexical", rows)


@pytest.mark.slow
@pytest.mark.performance
@pytest.mark.serial
def test_benchmark_100k_endpoints(tmp_path: Path) -> None:
    lines = gen_dedup(100_000, 60)
    selected = tmp_path / "bench100k.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    input_mib = selected.stat().st_size / (1024**2)
    rows = []
    for workers in (1, 8):
        started = time.monotonic()
        out, result, throughput = _engine_run(
            tmp_path, selected, f"b100k_{workers}", workers=workers
        )
        wall = time.monotonic() - started
        assert result.stats.documents_seen == 100_000
        print(
            f"100k-lexical w{workers}: in={input_mib:.0f}MiB wall={wall:.1f}s "
            f"docs/s={100_000 / wall:.0f}"
        )
        rows.append((workers, 100_000, wall))
    _benchmark_table("100k-lexical", rows)


@pytest.mark.slow
@pytest.mark.performance
@pytest.mark.serial
def test_benchmark_long_docs(tmp_path: Path) -> None:
    rng = random.Random(61)
    lines = [json.dumps(_doc_dict(f"long-{i:04d}", _prose(rng, 2500), i)) for i in range(2000)]
    selected = tmp_path / "long.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    input_mib = selected.stat().st_size / (1024**2)
    rows = []
    for workers in (1, 4):
        started = time.monotonic()
        out, result, _ = _engine_run(tmp_path, selected, f"long_{workers}", workers=workers)
        wall = time.monotonic() - started
        assert result.stats.documents_seen == 2000
        print(f"long-docs w{workers}: in={input_mib:.0f}MiB wall={wall:.1f}s")
        rows.append((workers, 2000, wall))
    _benchmark_table("long-docs", rows)


@pytest.mark.slow
@pytest.mark.scale
@pytest.mark.serial
def test_memory_bound(tmp_path: Path) -> None:
    import psutil

    lines = gen_dedup(20_000, 62)
    selected = tmp_path / "mem.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    process = psutil.Process()
    baseline = process.memory_info().rss
    out, result, _ = _engine_run(tmp_path, selected, "mem", workers=4)
    assert result.stats.documents_seen == 20_000
    growth_mib = (process.memory_info().rss - baseline) / (1024**2)
    print(f"memory: rss_growth={growth_mib:.1f}MiB for 20k docs, 4 workers")
    assert growth_mib < 2048
