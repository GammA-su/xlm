"""P27A deterministic sharded datasets: offline fixtures only.

Structural assertions are deterministic; wall times and RSS are reported,
never threshold-gated (except one generous peak-RSS bound).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.data.acquisition.plan import AcquisitionLimits, AcquisitionPlan, load_acquisition_plan
from xlm.data.datasets.shards import (
    MANIFEST_FILENAME,
    SHARD_MANIFEST_VERSION,
    ShardedJsonlWriter,
    ShardEntry,
    ShardManifest,
    aggregate_identity,
    input_byte_size,
    iter_input_blocks,
    iter_shard_records,
    iter_single_records,
    load_manifest,
    shard_filename,
    verify_manifest,
)


def _lines(n: int, size: int, prefix: str = "doc") -> list[str]:
    return [json.dumps({"doc_id": f"{prefix}-{i:06d}", "text": "x" * size}) for i in range(n)]


def _write_dataset(
    root: Path,
    lines: list[str],
    *,
    target: int,
    dataset_id: str = "test_ds",
) -> ShardManifest:
    writer = ShardedJsonlWriter(
        root, dataset_id=dataset_id, target_shard_bytes=target, source_artifact={"t": 1}
    )
    for index, line in enumerate(lines):
        writer.write_line(line, f"doc-{index:06d}")
    return writer.finish()


def test_shard_filenames_and_manifest_roundtrip(tmp_path: Path) -> None:
    assert shard_filename(0) == "shard-00000.jsonl"
    assert shard_filename(41) == "shard-00041.jsonl"
    with pytest.raises(ValueError, match="ordinal"):
        shard_filename(-1)
    manifest = ShardManifest(
        dataset_id="d",
        shards=(),
        total_documents=0,
        total_bytes=0,
        aggregate_sha256=aggregate_identity([]),
    )
    assert ShardManifest.from_dict(manifest.to_dict()) == manifest
    assert SHARD_MANIFEST_VERSION == 1
    with pytest.raises(ValueError, match="version"):
        ShardManifest.from_dict({"schema_version": 999, "shards": []})


def test_target_roll_and_oversize_flag(tmp_path: Path) -> None:
    lines = _lines(10, 100)
    line_bytes = len(lines[0].encode()) + 1
    manifest = _write_dataset(tmp_path / "ds", lines, target=4 * line_bytes + 1)
    assert [entry.doc_count for entry in manifest.shards] == [5, 5]
    assert manifest.total_documents == 10
    assert manifest.total_bytes == sum(len(line.encode()) + 1 for line in lines)
    assert all(not entry.oversize for entry in manifest.shards)
    assert manifest.shards[0].first_doc_id == "doc-000000"
    assert manifest.shards[1].last_doc_id == "doc-000009"
    big = _write_dataset(tmp_path / "big", [_lines(1, 5000)[0]], target=100)
    assert len(big.shards) == 1 and big.shards[0].oversize is True
    assert verify_manifest(tmp_path / "big", big) is big


def test_determinism_across_runs_and_orders(tmp_path: Path) -> None:
    lines = _lines(50, 200)
    first = _write_dataset(tmp_path / "a", lines, target=4096)
    second = _write_dataset(tmp_path / "b", lines, target=4096)
    assert first == second
    assert (tmp_path / "a" / "dataset-manifest.json").read_bytes() == (
        tmp_path / "b" / "dataset-manifest.json"
    ).read_bytes()


def test_no_manifest_before_finish_and_crash_states(tmp_path: Path) -> None:
    root = tmp_path / "ds"
    lines = _lines(10, 100)
    writer = ShardedJsonlWriter(root, dataset_id="d", target_shard_bytes=250)
    for line in lines[:3]:
        writer.write_line(line, "x")
    assert not (root / MANIFEST_FILENAME).exists()
    writer.abandon()
    assert not (root / MANIFEST_FILENAME).exists()
    assert list(root.glob("*.tmp")) == []
    # Rolled finals without a manifest are recoverable only by rerun, which
    # regenerates byte-identical outputs.
    writer2 = ShardedJsonlWriter(root, dataset_id="d", target_shard_bytes=100000)
    full = ShardedJsonlWriter(tmp_path / "ref", dataset_id="d", target_shard_bytes=100000)
    for line in lines:
        writer2.write_line(line, "x")
        full.write_line(line, "x")
    assert writer2.finish() == full.finish()


@pytest.mark.serial
def test_crash_mid_shard_leaves_no_manifest(tmp_path: Path) -> None:
    root = tmp_path / "ds"
    lines = _lines(30, 300)
    writer = ShardedJsonlWriter(root, dataset_id="d", target_shard_bytes=4096)
    for line in lines[:7]:
        writer.write_line(line, "x")
    # Simulated crash: abandon without finish (staging temps only or nothing final).
    writer.abandon()
    assert not (root / MANIFEST_FILENAME).exists()
    rerun = ShardedJsonlWriter(tmp_path / "rerun", dataset_id="d", target_shard_bytes=4096)
    for line in lines:
        rerun.write_line(line, "x")
    manifest = rerun.finish()
    assert manifest.total_documents == 30
    assert verify_manifest(tmp_path / "rerun", manifest) is manifest


@pytest.mark.serial
def test_crash_after_rolls_before_manifest_rerun_identical(tmp_path: Path) -> None:
    root = tmp_path / "ds"
    lines = _lines(30, 300)
    writer = ShardedJsonlWriter(root, dataset_id="d", target_shard_bytes=1024)
    for line in lines:
        writer.write_line(line, "x")
    # Crash after several atomic shard rolls but before manifest publish.
    writer.abandon()
    assert not (root / MANIFEST_FILENAME).exists()
    finals_before = sorted(p.name for p in root.glob("shard-*.jsonl"))
    assert len(finals_before) > 1
    rerun = ShardedJsonlWriter(tmp_path / "rerun", dataset_id="d", target_shard_bytes=1024)
    for line in lines:
        rerun.write_line(line, "x")
    manifest = rerun.finish()
    # The crashed run's rolled finals are a byte-identical prefix of the
    # rerun: same leading documents through the same target always produce
    # the same shard bytes (the rerun additionally publishes the tail).
    for name in finals_before:
        assert (tmp_path / "rerun" / name).read_bytes() == (root / name).read_bytes()
    assert len(manifest.shards) >= len(finals_before)


@pytest.mark.serial
def test_corrupt_shard_blocks_manifest(tmp_path: Path) -> None:
    root = tmp_path / "ds"
    lines = _lines(12, 200)
    writer = ShardedJsonlWriter(root, dataset_id="d", target_shard_bytes=1024)
    assert len(writer.entries) == 0
    for line in lines[:6]:
        writer.write_line(line, "x")
    # Corrupt the still-staging bytes before finish: finish must refuse.
    # Flush first so the corruption lands after durable bytes instead of
    # being overwritten by the writer's own buffered flush on close.
    assert writer._writer is not None and writer._current_temp is not None
    writer._writer.flush()
    with writer._current_temp.open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(ValueError):
        writer.finish()
    assert not (root / MANIFEST_FILENAME).exists()


def test_verifier_strictness(tmp_path: Path) -> None:
    root = tmp_path / "ds"
    manifest = _write_dataset(root, _lines(8, 120), target=1024)
    assert verify_manifest(root, manifest) is manifest

    def with_shards(
        shards: tuple[ShardEntry, ...],
        *,
        total_documents: int | None = None,
        total_bytes: int | None = None,
        aggregate_sha256: str | None = None,
    ) -> ShardManifest:
        entries = list(shards)
        return ShardManifest(
            dataset_id="d",
            shards=shards,
            total_documents=(
                sum(entry.doc_count for entry in entries)
                if total_documents is None
                else total_documents
            ),
            total_bytes=(
                sum(entry.byte_count for entry in entries) if total_bytes is None else total_bytes
            ),
            aggregate_sha256=(
                aggregate_identity(entries) if aggregate_sha256 is None else aggregate_sha256
            ),
        )

    # Totals disagree with the shard entries.
    with pytest.raises(ValueError, match="total"):
        verify_manifest(
            root, with_shards(manifest.shards, total_documents=manifest.total_documents + 1)
        )
    # Duplicate ordinal.
    with pytest.raises(ValueError, match="ordinals"):
        verify_manifest(root, with_shards(manifest.shards + (manifest.shards[0],)))
    # Skipped ordinal.
    with pytest.raises(ValueError, match="ordinals"):
        verify_manifest(root, with_shards(manifest.shards[1:]))
    # Path traversal outside the dataset directory.
    evil = ShardEntry(ordinal=0, path="../evil.jsonl", doc_count=1, byte_count=1, sha256="0" * 64)
    with pytest.raises(ValueError, match="not a dataset shard"):
        verify_manifest(root, with_shards((evil,)))
    # Non-canonical shard filename.
    odd = ShardEntry(ordinal=0, path="other.jsonl", doc_count=1, byte_count=1, sha256="0" * 64)
    with pytest.raises(ValueError, match="not a dataset shard"):
        verify_manifest(root, with_shards((odd,)))
    # Wrong aggregate identity.
    with pytest.raises(ValueError, match="aggregate"):
        verify_manifest(root, with_shards(manifest.shards, aggregate_sha256="0" * 64))
    # Tampered shard bytes.
    victim = root / manifest.shards[0].path
    victim.write_bytes(victim.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="byte count"):
        verify_manifest(root, manifest)
    # Manifest loading: missing file, non-JSON shard file, wrong version.
    with pytest.raises((ValueError, FileNotFoundError), match="not found"):
        load_manifest(root / "nope.json")
    with pytest.raises(ValueError, match="malformed"):
        load_manifest(root / manifest.shards[1].path)
    bad_version = root / "bad-version.json"
    payload = manifest.to_dict()
    payload["schema_version"] = 999
    bad_version.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported|malformed"):
        load_manifest(bad_version)


def _prefix_manifest_from_disk(root: Path, dataset_id: str = "d") -> ShardManifest:
    """Rebuild a manifest for the atomically published shards on disk (test only)."""
    entries = []
    for ordinal, path in enumerate(sorted(root.glob("shard-*.jsonl"))):
        assert path.name == shard_filename(ordinal)
        payload = path.read_bytes()
        entries.append(
            ShardEntry(
                ordinal=ordinal,
                path=path.name,
                doc_count=payload.count(b"\n"),
                byte_count=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
    return ShardManifest(
        dataset_id=dataset_id,
        shards=tuple(entries),
        total_documents=sum(entry.doc_count for entry in entries),
        total_bytes=sum(entry.byte_count for entry in entries),
        aggregate_sha256=aggregate_identity(entries),
    )


def test_adopt_verified_prefix(tmp_path: Path) -> None:
    lines = _lines(12, 150)
    root = tmp_path / "ds"
    writer = ShardedJsonlWriter(root, dataset_id="d", target_shard_bytes=1024)
    for index, line in enumerate(lines):
        writer.write_line(line, f"doc-{index:06d}")
    assert len(writer.entries) >= 1
    # Simulated crash after several atomic rolls but before manifest publish.
    writer.abandon()
    assert not (root / MANIFEST_FILENAME).exists()
    prefix = _prefix_manifest_from_disk(root)
    assert 0 < prefix.total_documents < len(lines)
    resumed = ShardedJsonlWriter(root, dataset_id="d", target_shard_bytes=1024)
    assert resumed.adopt(prefix) == prefix.total_documents
    with pytest.raises(ValueError, match="fresh writer"):
        resumed.adopt(prefix)
    for index in range(prefix.total_documents, len(lines)):
        resumed.write_line(lines[index], f"doc-{index:06d}")
    manifest = resumed.finish()
    assert manifest.total_documents == len(lines)
    assert verify_manifest(root, manifest) is manifest
    stream = [
        json.loads(line)
        for entry in sorted(manifest.shards, key=lambda item: item.ordinal)
        for line in (root / entry.path).read_text(encoding="utf-8").splitlines()
    ]
    assert stream == [json.loads(line) for line in lines]
    # Adopting a manifest whose shards are absent fails without adopting anything.
    fresh = ShardedJsonlWriter(tmp_path / "fresh", dataset_id="d", target_shard_bytes=1024)
    with pytest.raises(ValueError, match="missing"):
        fresh.adopt(prefix)
    assert fresh.entries == []
    # Tampered digests and out-of-sequence ordinals are refused.
    tampered_entries = list(prefix.shards)
    tampered_entries[0] = ShardEntry(
        ordinal=0,
        path=tampered_entries[0].path,
        doc_count=tampered_entries[0].doc_count,
        byte_count=tampered_entries[0].byte_count,
        sha256="0" * 64,
    )
    tampered = ShardManifest(
        dataset_id="d",
        shards=tuple(tampered_entries),
        total_documents=prefix.total_documents,
        total_bytes=prefix.total_bytes,
        aggregate_sha256=aggregate_identity(tampered_entries),
    )
    tampered_dir = tmp_path / "tampered"
    tampered_dir.mkdir()
    for path in root.glob("shard-*.jsonl"):
        (tampered_dir / path.name).write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="failed verification"):
        ShardedJsonlWriter(tampered_dir, dataset_id="d", target_shard_bytes=1024).adopt(tampered)
    gapped_entry = ShardEntry(
        ordinal=3,
        path="shard-00003.jsonl",
        doc_count=1,
        byte_count=1,
        sha256="0" * 64,
    )
    gapped = ShardManifest(
        dataset_id="d",
        shards=(gapped_entry,),
        total_documents=1,
        total_bytes=1,
        aggregate_sha256=aggregate_identity([gapped_entry]),
    )
    with pytest.raises(ValueError, match="out of sequence"):
        ShardedJsonlWriter(tmp_path / "fresh3", dataset_id="d", target_shard_bytes=1024).adopt(
            gapped
        )


def test_input_abstraction_single_vs_manifest(tmp_path: Path) -> None:
    lines = _lines(20, 100)
    single = tmp_path / "single.jsonl"
    single.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_dir = tmp_path / "sharded"
    _write_dataset(manifest_dir, lines, target=1024)
    manifest = load_manifest(manifest_dir / "dataset-manifest.json")
    from_single = [(record, seq) for record, _, _, seq in iter_single_records(single)]
    from_manifest = [
        (record, seq) for record, _, _, seq in iter_shard_records(manifest_dir, manifest)
    ]
    assert from_single == from_manifest
    assert [seq for _, seq in from_single] == list(range(20))
    assert input_byte_size(single) == single.stat().st_size
    assert input_byte_size(manifest_dir) == sum(len(line.encode()) + 1 for line in lines)
    with pytest.raises(ValueError, match="unsupported"):
        list(iter_input_blocks(tmp_path / "missing.jsonl"))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no dataset-manifest"):
        list(iter_input_blocks(empty))


def _adapt_plan(tmp_path: Path, plan_id: str, n: int, files: list[str]) -> Path:
    plan = AcquisitionPlan(
        plan_id=plan_id,
        source_id="common_pile",
        provider="https",
        repository="http://127.0.0.1:9/unused",
        revision="base-rev",
        mode="selected_records",
        selected_files=files,
        row_ranges={name: (0, n) for name in files},
        output_artifact_id="base",
        is_pilot=True,
        limits=AcquisitionLimits(
            max_transferred_bytes=256 * 1024**2,
            max_records=100000,
            max_output_disk_bytes=2 * 1024**3,
        ),
    )
    path = tmp_path / f"{plan_id}.json"
    path.write_text(plan.with_computed_hash().model_dump_json(), encoding="utf-8")
    return path


def _adapt_input(
    tmp_path: Path, name: str, texts: list[str], sel_hash: str, source_file: str
) -> Path:
    lines = []
    for index, text in enumerate(texts):
        lines.append(
            json.dumps(
                {
                    "text": text,
                    "source_row": index,
                    "_xlm_acquisition": {
                        "source_id": "common_pile",
                        "repository": "http://127.0.0.1:9/unused",
                        "revision": "base-rev",
                        "source_file": source_file,
                        "row_index": index,
                        "selection_hash": sel_hash,
                    },
                }
            )
        )
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_adapt_sharded_output_and_equivalence(tmp_path: Path) -> None:

    texts = [f"common pile prose document {i:04d} " + ("w" * 300) for i in range(40)]
    plan_path = _adapt_plan(tmp_path, "p_shard", 40, ["news/f.jsonl"])
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = _adapt_input(tmp_path, "selected.jsonl", texts, sel_hash, "news/f.jsonl")
    runner = CliRunner()
    legacy = runner.invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "common_pile",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "legacy"),
        ],
    )
    assert legacy.exit_code == 0, legacy.output
    sharded = runner.invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "common_pile",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "sharded"),
            "--output-shard-bytes",
            "4096",
        ],
    )
    assert sharded.exit_code == 0, sharded.output
    legacy_docs = [
        json.loads(line)
        for line in (tmp_path / "legacy/documents.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    manifest = load_manifest(tmp_path / "sharded/dataset-manifest.json")
    assert manifest.total_documents == 40
    assert len(manifest.shards) > 1
    assert manifest.shard_target_bytes == 4096
    assert all(entry.path.startswith("shard-") for entry in manifest.shards)
    assert verify_manifest(tmp_path / "sharded", manifest) is manifest
    stream_docs: list[dict[str, object]] = []
    for entry in sorted(manifest.shards, key=lambda item: item.ordinal):
        stream_docs.extend(
            json.loads(line)
            for line in (tmp_path / "sharded" / entry.path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    assert stream_docs == legacy_docs
    summary = json.loads((tmp_path / "sharded/adaptation_summary.json").read_text(encoding="utf-8"))
    assert summary["accepted_records"] == 40
    assert summary["output_shard_bytes"] == 4096
    assert summary["manifest"]["file"] == "dataset-manifest.json"
    assert (
        summary["manifest"]["sha256"]
        == hashlib.sha256((tmp_path / "sharded/dataset-manifest.json").read_bytes()).hexdigest()
    )
    assert summary["documents"]["aggregate_sha256"] == manifest.aggregate_sha256
    assert summary["documents"]["count"] == 40
    assert summary["documents"]["bytes"] == manifest.total_bytes
    assert len(summary["shards"]) == len(manifest.shards)


def test_adapt_manifest_input_matches_single(tmp_path: Path) -> None:

    texts = [f"manifest input prose {i:04d} " + ("v" * 200) for i in range(12)]
    plan_path = _adapt_plan(tmp_path, "p_mani", 12, ["news/f.jsonl"])
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = _adapt_input(tmp_path, "selected.jsonl", texts, sel_hash, "news/f.jsonl")
    # Build an honest sharded input directory from the same selected records.
    manifest_dir = tmp_path / "input_shards"
    input_lines = [
        line for line in selected.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    _write_dataset(manifest_dir, input_lines, target=2048, dataset_id="input_ds")
    assert load_manifest(manifest_dir / "dataset-manifest.json").total_documents == 12
    runner = CliRunner()
    from_single = runner.invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "common_pile",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "o_single"),
            "--output-shard-bytes",
            "2048",
        ],
    )
    from_manifest = runner.invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "common_pile",
            "--input",
            str(manifest_dir),
            "--output-dir",
            str(tmp_path / "o_manifest"),
            "--output-shard-bytes",
            "2048",
        ],
    )
    assert from_single.exit_code == 0, from_single.output
    assert from_manifest.exit_code == 0, from_manifest.output
    first = load_manifest(tmp_path / "o_single/dataset-manifest.json")
    second = load_manifest(tmp_path / "o_manifest/dataset-manifest.json")
    assert [e.to_dict() for e in first.shards] == [e.to_dict() for e in second.shards]
    assert first.aggregate_sha256 == second.aggregate_sha256


def test_adapt_shard_boundaries_stable(tmp_path: Path) -> None:

    texts = [f"stable boundary prose {i:04d} " + ("u" * 150) for i in range(30)]
    plan_path = _adapt_plan(tmp_path, "p_stable", 30, ["news/f.jsonl"])
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = _adapt_input(tmp_path, "selected.jsonl", texts, sel_hash, "news/f.jsonl")
    runner = CliRunner()
    manifests = []
    for batch in (128, 2048):
        out_dir = tmp_path / f"stable{batch}"
        result = runner.invoke(
            data_app,
            [
                "adapt",
                "--plan",
                str(plan_path),
                "--adapter",
                "common_pile",
                "--input",
                str(selected),
                "--output-dir",
                str(out_dir),
                "--output-shard-bytes",
                "2048",
                "--batch-records",
                str(batch),
            ],
        )
        assert result.exit_code == 0, result.output
        manifests.append(load_manifest(out_dir / "dataset-manifest.json"))
    assert manifests[0].to_dict() == manifests[1].to_dict()


def test_adapt_shard_overwrite_and_mixing_refused(tmp_path: Path) -> None:

    texts = [f"refusal prose {i}" for i in range(3)]
    plan_path = _adapt_plan(tmp_path, "p_ref", 3, ["news/f.jsonl"])
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = _adapt_input(tmp_path, "selected.jsonl", texts, sel_hash, "news/f.jsonl")
    runner = CliRunner()
    out_dir = tmp_path / "out"
    first = runner.invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "common_pile",
            "--input",
            str(selected),
            "--output-dir",
            str(out_dir),
            "--output-shard-bytes",
            "2048",
        ],
    )
    assert first.exit_code == 0, first.output
    rerun = runner.invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "common_pile",
            "--input",
            str(selected),
            "--output-dir",
            str(out_dir),
            "--output-shard-bytes",
            "2048",
        ],
    )
    assert rerun.exit_code == 1
    legacy = runner.invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "common_pile",
            "--input",
            str(selected),
            "--output-dir",
            str(out_dir),
        ],
    )
    assert legacy.exit_code == 1


def test_adapt_budget_gate_reports_counts(tmp_path: Path) -> None:

    texts = [f"budget prose {i} " + ("q" * 100) for i in range(10)]
    plan_path = _adapt_plan(tmp_path, "p_bud", 10, ["news/f.jsonl"])
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = _adapt_input(tmp_path, "selected.jsonl", texts, sel_hash, "news/f.jsonl")
    size = selected.stat().st_size
    runner = CliRunner()
    result = runner.invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "common_pile",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "out"),
            "--max-input-bytes",
            str(size - 1),
        ],
    )
    assert result.exit_code == 1
    assert f"{size:,}" in result.output
    assert not (tmp_path / "out" / "documents.jsonl").exists()


def _large_texts(n: int, size: int, tag: str) -> list[str]:
    return [f"{tag} document {i:06d} " + ("z" * size) for i in range(n)]


@pytest.mark.slow
@pytest.mark.scale
@pytest.mark.serial
def test_large_benchmark_128mib(tmp_path: Path) -> None:
    import psutil

    texts = _large_texts(4000, 32000, "bench128")
    plan_path = _adapt_plan(tmp_path, "p_big", len(texts), ["news/f.jsonl"])
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = _adapt_input(tmp_path, "selected.jsonl", texts, sel_hash, "news/f.jsonl")
    input_mib = selected.stat().st_size / (1024**2)
    assert 120 < input_mib < 160, f"input size {input_mib:.1f} MiB outside 128 MiB band"
    process = psutil.Process()
    baseline_rss = process.memory_info().rss
    runner = CliRunner()
    started = time.monotonic()
    result = runner.invoke(
        data_app,
        [
            "adapt",
            "--plan",
            str(plan_path),
            "--adapter",
            "common_pile",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "out"),
            "--output-shard-bytes",
            str(64 * 1024**2),
            "--max-input-bytes",
            str(512 * 1024**2),
        ],
    )
    wall = time.monotonic() - started
    assert result.exit_code == 0, result.output
    peak = process.memory_info().rss
    manifest = load_manifest(tmp_path / "out/dataset-manifest.json")
    assert verify_manifest(tmp_path / "out", manifest) is manifest
    assert manifest.total_documents == len(texts)
    summary = json.loads((tmp_path / "out/adaptation_summary.json").read_text(encoding="utf-8"))
    assert summary["accepted_records"] == len(texts)
    growth_mib = (peak - baseline_rss) / (1024**2)
    print(
        f"128MiB: shards={len(manifest.shards)} wall={wall:.1f}s "
        f"records/s={len(texts) / wall:.0f} rss_growth={growth_mib:.1f}MiB"
    )
    assert growth_mib < 1024


@pytest.mark.performance
@pytest.mark.serial
def test_shard_size_comparison_table(tmp_path: Path) -> None:

    texts = _large_texts(1500, 4000, "table")
    plan_path = _adapt_plan(tmp_path, "p_table", len(texts), ["news/f.jsonl"])
    sel_hash = load_acquisition_plan(plan_path).compute_behavioral_hash()
    selected = _adapt_input(tmp_path, "selected.jsonl", texts, sel_hash, "news/f.jsonl")
    input_mib = selected.stat().st_size / (1024**2)
    assert 4 < input_mib < 16, f"input size {input_mib:.1f} MiB outside table band"
    runner = CliRunner()
    table = []
    manifests = {}
    for target_mib in (1, 2, 4):
        out_dir = tmp_path / f"t{target_mib}"
        started = time.monotonic()
        result = runner.invoke(
            data_app,
            [
                "adapt",
                "--plan",
                str(plan_path),
                "--adapter",
                "common_pile",
                "--input",
                str(selected),
                "--output-dir",
                str(out_dir),
                "--output-shard-bytes",
                str(target_mib * 1024**2),
                "--max-input-bytes",
                str(512 * 1024**2),
            ],
        )
        wall = time.monotonic() - started
        assert result.exit_code == 0, result.output
        manifest = load_manifest(out_dir / "dataset-manifest.json")
        assert verify_manifest(out_dir, manifest) is manifest
        assert len(manifest.shards) > 1
        manifests[target_mib] = manifest
        table.append((target_mib, len(manifest.shards), wall))
    print("shard-target-MiB/shards/wall-s: " + ", ".join(f"{m}/{k}/{w:.1f}" for m, k, w in table))
    counts = [k for _, k, _ in table]
    assert counts[0] > counts[1] > counts[2]
    totals = {(manifest.total_documents, manifest.total_bytes) for manifest in manifests.values()}
    assert len(totals) == 1
    streams = []
    for target_mib in (1, 2, 4):
        out_dir = tmp_path / f"t{target_mib}"
        manifest = manifests[target_mib]
        streams.append(
            [
                line
                for entry in sorted(manifest.shards, key=lambda item: item.ordinal)
                for line in (out_dir / entry.path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        )
    assert streams[0] == streams[1] == streams[2]
    assert manifests[1].aggregate_sha256 != manifests[4].aggregate_sha256
