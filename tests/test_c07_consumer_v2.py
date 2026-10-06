"""Independent authored C07-v2 consumer, policy and bounded sparse-file checks."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import psutil
import pytest
from scripts.c07_consumer_probe import exactness

from test_performance_tokenization import document
from test_tokenize_freeze_fast import flow as _flow
from test_tokenize_freeze_fast import key as _key
from xlm.core.paths import ArtifactPaths
from xlm.data.input_policy import (
    POLICY_ID,
    TrainingInputPolicy,
    admit_sizes,
    admit_sources,
    check_frozen_policy,
    file_limits,
    policy_from_binding,
)
from xlm.data.input_validation import validate_training_index
from xlm.data.sampling import MixtureBatcher
from xlm.data.tokens import TokenShardReader, TokenShardWriter, token_byte_lengths
from xlm.tokenizers.bpe import ByteLevelBPETokenizer
from xlm.training.inputs import resolve_training_input

flow = _flow
key = _key


@pytest.fixture(scope="module")
def tokenizer() -> ByteLevelBPETokenizer:
    return ByteLevelBPETokenizer.train_from_documents(
        [document("Authored only: river trees <eos> café emoji \U0001f600\n", 0)],
        target_vocab_size=260,
    )


def test_independent_authored_10000_strings(tokenizer: ByteLevelBPETokenizer) -> None:
    result = exactness(tokenizer)
    assert result["strings"] > 10_000 and result["mismatches"] == 0


def convert_v2(first: Path, second: Path, tokenizer: ByteLevelBPETokenizer) -> TokenShardReader:
    """Authored representation conversion independent of the production v2 writer."""
    shutil.copytree(first, second)
    records = [json.loads(line) for line in (first / "offsets.jsonl").read_bytes().splitlines()]
    for record in records:
        record.pop("token_byte_spans")
    raw = b"".join((json.dumps(r, ensure_ascii=False) + "\n").encode() for r in records)
    (second / "offsets.jsonl").write_bytes(raw)
    manifest = json.loads((second / "shard_manifest.json").read_bytes())
    manifest["offsets_checksum_sha256"] = hashlib.sha256(raw).hexdigest()
    (second / "shard_manifest.json").write_text(json.dumps(manifest))
    table = token_byte_lengths(tokenizer)
    (second / "token_bytes.u16").write_bytes(table)
    counters = json.loads((second / "shard_counters.json").read_bytes())
    counters.update(
        index_schema="c07-offsets-v2", token_bytes_sha256=hashlib.sha256(table).hexdigest()
    )
    (second / "shard_counters.json").write_text(json.dumps(counters))
    return TokenShardReader(second)


@pytest.fixture
def pair(
    tmp_path: Path, tokenizer: ByteLevelBPETokenizer
) -> tuple[TokenShardReader, TokenShardReader]:
    v1 = tmp_path / "v1"
    TokenShardWriter(v1, "fixture", "fixture", tokenizer).write_documents(
        [document(text, i) for i, text in enumerate(["", "<bos><eos>", "é \U0001f600\n" * 30])],
        True,
    )
    return TokenShardReader(v1), convert_v2(v1, tmp_path / "v2", tokenizer)


def test_v2_reconstruction_and_order_member_identity(
    pair: tuple[TokenShardReader, TokenShardReader],
) -> None:
    from xlm.data.ordering.membership import build_membership

    first, second = pair
    second.verify_integrity()
    validate_training_index(first)
    validate_training_index(second)
    for original, compact in zip(
        first.iter_document_offsets(), second.iter_document_offsets(), strict=True
    ):
        assert second.with_byte_spans(compact) == original
    a = build_membership({"fixture": first}).sources["fixture"]
    b = build_membership({"fixture": second}).sources["fixture"]
    assert a.doc_ids == b.doc_ids and a.member_digests == b.member_digests


def test_v2_malformed_coverage_override_and_window_refuse(
    pair: tuple[TokenShardReader, TokenShardReader],
) -> None:
    _, reader = pair
    record = list(reader.iter_document_offsets())[-1]
    wrong = copy.deepcopy(record)
    wrong["covered_bytes"] += 1
    with pytest.raises(ValueError, match="coverage"):
        reader.with_byte_spans(wrong)
    wrong = copy.deepcopy(record)
    wrong["token_byte_spans"] = []
    with pytest.raises(ValueError, match="override"):
        reader.with_byte_spans(wrong)
    with pytest.raises(ValueError, match="window"):
        reader.check_read_window(1_048_577)
    wrong = copy.deepcopy(record)
    wrong["token_count"] = 1_048_577
    with pytest.raises(ValueError, match="outside"):
        reader.with_byte_spans(wrong)


def small_sizes(policy: TrainingInputPolicy) -> dict[str, int]:
    return {name: 1 for name in file_limits(policy)}


@pytest.mark.parametrize("filename", list(file_limits(TrainingInputPolicy())))
def test_each_file_boundary(filename: str) -> None:
    policy = TrainingInputPolicy()
    sizes = small_sizes(policy)
    sizes[filename] = file_limits(policy)[filename] - 1
    admit_sizes({"a": sizes}, policy)
    sizes[filename] += 1
    admit_sizes({"a": sizes}, policy)
    sizes[filename] += 1
    with pytest.raises(ValueError, match="per-file"):
        admit_sizes({"a": sizes}, policy)


def test_shard_aggregate_component_boundaries() -> None:
    policy = TrainingInputPolicy()
    sizes = small_sizes(policy)
    sizes["tokens.bin"] = 3 * 1024**3
    sizes["offsets.jsonl"] = policy.shard_bytes - sum(
        v for k, v in sizes.items() if k != "offsets.jsonl"
    )
    admit_sizes({"a": sizes}, policy)
    sizes["offsets.jsonl"] -= 1
    admit_sizes({"a": sizes}, policy)
    sizes["offsets.jsonl"] += 1
    admit_sizes({str(i): sizes for i in range(4)}, policy)
    below = {str(i): dict(sizes) for i in range(4)}
    below["0"]["offsets.jsonl"] -= 1
    admit_sizes(below, policy)
    sizes["offsets.jsonl"] += 1
    with pytest.raises(ValueError, match="per-shard"):
        admit_sizes({"a": sizes}, policy)
    sizes["offsets.jsonl"] -= 1
    with pytest.raises(ValueError, match="aggregate"):
        admit_sizes({**{str(i): sizes for i in range(4)}, "extra": small_sizes(policy)}, policy)
    admit_sizes({str(i): small_sizes(policy) for i in range(11)}, policy)
    with pytest.raises(ValueError, match="component"):
        admit_sizes({str(i): small_sizes(policy) for i in range(12)}, policy)


def test_policy_cannot_drift_or_authorize_another_production_chain() -> None:
    binding = TrainingInputPolicy().binding()
    assert policy_from_binding(binding) == TrainingInputPolicy()
    binding["aggregate_bytes"] += 1
    with pytest.raises(ValueError, match="changed"):
        policy_from_binding(binding)
    with pytest.raises(ValueError, match="pinned"):
        check_frozen_policy(
            {"mode": "protected", "training_input_policy": TrainingInputPolicy().binding()}
        )


def test_document_admission_boundary(pair: tuple[TokenShardReader, TokenShardReader]) -> None:
    from xlm.data.input_policy import validate_headers

    _, reader = pair
    path = reader.directory / "shard_manifest.json"
    manifest = reader.manifest.to_dict()
    policy = TrainingInputPolicy()
    # Metadata-only boundary test, not a claim that this synthetic header is a valid shard.
    for count in (policy.documents - 1, policy.documents):
        manifest.update(num_documents=count, num_tokens=count + reader.counters["valid_targets"])
        path.write_text(json.dumps(manifest))
        validate_headers({"fixture": reader.directory}, policy)
    manifest["num_documents"] += 1
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="document ceiling"):
        validate_headers({"fixture": reader.directory}, policy)
    reader.check_read_window(policy.read_window_tokens - 1)
    reader.check_read_window(policy.read_window_tokens)
    with pytest.raises(ValueError, match="window"):
        reader.check_read_window(policy.read_window_tokens + 1)


def test_signed_policy_reference_fast_freeze_agree(flow: dict[str, Any], tmp_path: Path) -> None:
    from scripts.c05_synthetic_flow import ISSUER, KEY_ENV

    from xlm.data.exclusion.operator import main as operator

    root = flow["root"]
    common = [
        *flow["common"],
        "--selection",
        str(root / "selection"),
        "--shards",
        str(root / "shards"),
        "--training-input-policy",
        POLICY_ID,
        "--issuer",
        ISSUER,
        "--key-env",
        KEY_ENV,
    ]
    for name in ("freeze-reference", "freeze"):
        extra = ["--workers", "2", "--no-progress"] if name == "freeze" else []
        assert operator([name, *common, "--output", str(tmp_path / name), *extra]) == 0
    assert (tmp_path / "freeze/freeze.json").read_bytes() == (
        tmp_path / "freeze-reference/freeze.json"
    ).read_bytes()


def sparse_file(path: Path, size: int) -> None:
    with path.open("wb") as stream:
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            control = ctypes.windll.kernel32.DeviceIoControl
            control.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                wintypes.LPVOID,
            ]
            control.restype = wintypes.BOOL
            returned = wintypes.DWORD()
            if not control(
                msvcrt.get_osfhandle(stream.fileno()),
                0x900C4,
                None,
                0,
                None,
                0,
                ctypes.byref(returned),
                None,
            ):
                raise OSError("cannot create bounded sparse fixture")
        stream.truncate(size)


@pytest.mark.serial
def test_sparse_over_2gib_admission_and_mmap_use_bounded_ram(
    pair: tuple[TokenShardReader, TokenShardReader],
) -> None:
    """Admission/window proof only: intentionally no fake checksum certification."""
    _, reader = pair
    path = reader.directory
    size = 2 * 1024**3 + 128
    sparse_file(path / "tokens.bin", size)
    manifest = reader.manifest.to_dict()
    manifest["num_tokens"] = size // 2
    (path / "shard_manifest.json").write_text(json.dumps(manifest))
    (path / "c05-attestation.json").write_text("{}")
    process = psutil.Process()
    before = process.memory_info().rss
    assert admit_sources({"fixture": path}, TrainingInputPolicy()) > 2 * 1024**3
    large = TokenShardReader(path)
    assert large.read_tokens_mmap(size // 2 - 32, 32) == [0] * 32
    # Also exercise NumPy's 64-bit file offset on Windows, beyond signed 32-bit bytes.
    record = {
        "token_start": size // 2 - 32,
        "token_count": 32,
        "byte_count": 0,
        "covered_bytes": 0,
        "bos_positions": [],
        "eos_positions": [],
    }
    assert large.with_byte_spans(record)["token_byte_spans"] == [[0, 0]] * 32
    with pytest.raises(ValueError, match="window"):
        large.read_tokens_mmap(0, 1_048_577)
    delta = process.memory_info().rss - before
    assert delta < 64 * 1024**2
    print(
        json.dumps(
            {
                "sparse_bytes": size,
                "rss_delta_bytes": delta,
                "hash_verification": "NOT RUN: authored sparse layout/window test",
            }
        )
    )


def test_511580_target_shape_stays_small_and_exact(
    tmp_path: Path, tokenizer: ByteLevelBPETokenizer
) -> None:
    # Alphabet-only BPE guarantees one ID per ASCII byte for this authored long shape.
    v1 = tmp_path / "long-v1"
    TokenShardWriter(v1, "fixture", "fixture", tokenizer).write_documents(
        [document("x" * 511579)], True
    )
    first = TokenShardReader(v1)
    assert first.manifest.num_tokens == 511581
    assert (v1 / "offsets.jsonl").stat().st_size > 8 * 1024**2
    second = convert_v2(v1, tmp_path / "long-v2", tokenizer)
    assert (second.directory / "offsets.jsonl").stat().st_size < 2048
    validate_training_index(second)
    old = next(first.iter_document_offsets())
    assert second.with_byte_spans(next(second.iter_document_offsets())) == old
    memory = psutil.Process().memory_info()
    print(
        json.dumps(
            {
                "long_doc_tokens": 511581,
                "v1_index_bytes": (v1 / "offsets.jsonl").stat().st_size,
                "v2_index_bytes": (second.directory / "offsets.jsonl").stat().st_size,
                "process_peak_rss_bytes": getattr(memory, "peak_wset", memory.rss),
            }
        )
    )
    with pytest.raises(ValueError, match="byte limit"):
        validate_training_index(first)


def batcher(source: Any) -> MixtureBatcher:
    return MixtureBatcher(
        source.recipe,
        source.readers,
        context_length=32,
        global_batch_valid_targets=256,
        microbatch_sequences=2,
        pad_token_id=0,
        bos_token_id=1,
        eos_token_id=2,
    )


def test_resolved_v1_v2_policy_chain_batches_trace_and_resume(
    flow: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.c05_authored_pilot import KEY
    from scripts.c05_synthetic_flow import ISSUER

    from xlm.data.exclusion.freezefast import freeze_fast
    from xlm.data.sampling.prefetch import PrefetchingBatcher, ProducerSpec

    root = flow["root"]
    sources = []
    for name, shards, policy in (
        ("old", flow["reference"], None),
        ("new", root / "shards", POLICY_ID),
    ):
        freeze_fast(
            root / "proof.json",
            root / "selection",
            shards,
            root / "tokenizer",
            tmp_path / name,
            ISSUER,
            KEY.encode(),
            workers=1,
            inline=True,
            training_input_policy=policy,
        )
        data = json.loads((tmp_path / name / "training-data.json").read_bytes())
        sources.append(resolve_training_input(data, ArtifactPaths(root=tmp_path))[0])
    from xlm.training.input_preflight import main

    arguments = ["input_preflight", "--training-data", str(tmp_path / "new/training-data.json")]
    monkeypatch.setattr("sys.argv", arguments)
    main()
    diagnostic = json.loads(capsys.readouterr().out)
    assert diagnostic["verified"] and not diagnostic["training_started"]
    assert not diagnostic["production"] and diagnostic["policy"] == POLICY_ID
    monkeypatch.setattr("sys.argv", [*arguments, "--production"])
    with pytest.raises(SystemExit) as refused:
        main()
    assert refused.value.code == 1
    assert json.loads(capsys.readouterr().err)["refused"]
    a, b = map(batcher, sources)
    try:
        for _ in range(5):
            assert a.next_step_microbatches() == b.next_step_microbatches()
            a.commit()
            b.commit()
        assert a.get_state() == b.get_state()
        # JSON is the checkpoint's primitive state boundary; prefetched work is speculative.
        checkpoint = json.loads(json.dumps(a.get_state()))
        resumed = batcher(sources[1])
        resumed.load_state(checkpoint)
        prefetched = PrefetchingBatcher(ProducerSpec.from_batcher(resumed), checkpoint)
        try:
            for _ in range(4):
                expected = a.next_step_microbatches()
                assert expected == b.next_step_microbatches() == resumed.next_step_microbatches()
                got = prefetched.next_step_microbatches()
                # Rehydrate the public compact provenance, then compare every field.
                pending = prefetched.pending_update
                assert pending is not None
                assert len(got) == len(expected)
                for index, (actual, reference) in enumerate(zip(got, expected, strict=True)):
                    provenance = pending.microbatch_provenance(index)
                    expanded = dict(vars(actual))
                    expanded["source_attribution"] = provenance.pop("source_attribution")
                    expanded["metadata"] = {**actual.metadata, **provenance}
                    assert json.loads(
                        json.dumps(expanded, default=lambda x: x.tolist())
                    ) == json.loads(json.dumps(vars(reference)))
                for instance in (a, b, resumed, prefetched):
                    instance.commit()
                assert (
                    a.get_state() == b.get_state() == resumed.get_state() == prefetched.get_state()
                )
        finally:
            prefetched.close()
            resumed.close()
    finally:
        a.close()
        b.close()


def test_huge_v2_metadata_refuses(pair: tuple[TokenShardReader, TokenShardReader]) -> None:
    _, reader = pair
    (reader.directory / "offsets.jsonl").write_bytes(b" " * 2049)
    with pytest.raises(ValueError, match="byte limit"):
        validate_training_index(reader)


def test_v2_big_endian_is_not_silently_reinterpreted(
    pair: tuple[TokenShardReader, TokenShardReader],
) -> None:
    from dataclasses import replace

    _, reader = pair
    record = next(reader.iter_document_offsets())
    reader.manifest = replace(reader.manifest, endianness="big")
    with pytest.raises(ValueError, match="little-endian"):
        reader.with_byte_spans(record)


@pytest.mark.parametrize("length", [2047, 2048, 2049])
def test_metadata_record_boundary(
    pair: tuple[TokenShardReader, TokenShardReader], length: int
) -> None:
    _, reader = pair
    path = reader.directory / "offsets.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    first = lines[0].rstrip()
    lines[0] = first + b" " * (length - len(first) - 1) + b"\n"
    path.write_bytes(b"".join(lines))
    if length <= 2048:
        validate_training_index(reader)
    else:
        with pytest.raises(ValueError, match="byte limit"):
            validate_training_index(reader)


def test_readiness_planner_uses_explicit_policy() -> None:
    from xlm.experiments.science_pilot import frozen_input_report

    policy = TrainingInputPolicy()
    sizes = small_sizes(policy)
    sizes["tokens.bin"] = 2 * 1024**3 + 2
    assert frozen_input_report({"a": sizes})["exceeds_aggregate_cap"]
    report = frozen_input_report({"a": sizes}, policy.binding())
    assert not report["exceeds_aggregate_cap"]
    assert report["caps"]["aggregate_bytes"] == policy.aggregate_bytes


def test_signed_policy_cannot_be_injected_into_legacy_freeze(flow: dict[str, Any]) -> None:
    from xlm.data.exclusion.freeze import verify_training_freeze

    root = flow["root"]
    data = json.loads((root / "freeze/training-data.json").read_bytes())
    data["training_input_policy"] = TrainingInputPolicy().binding()
    with pytest.raises(ValueError, match="differs from signed freeze"):
        verify_training_freeze(
            data, {c: Path(p) for c, p in data["sources"].items()}, production=False
        )


def test_prefetch_counter_snapshot_refuses_table_substitution(
    flow: dict[str, Any], tmp_path: Path
) -> None:
    from xlm.data.sampling import MixtureRecipe
    from xlm.data.sampling.prefetch import PrefetchProducerError, ProducerSpec

    root = flow["root"]
    copied = tmp_path / "shards"
    shutil.copytree(root / "shards", copied)
    data = json.loads((root / "freeze/training-data.json").read_bytes())
    readers = {c: TokenShardReader(copied / c) for c in data["sources"]}
    b = MixtureBatcher(MixtureRecipe.model_validate(data["mixture"]), readers)
    spec = ProducerSpec.from_batcher(b)
    b.close()
    path = readers[sorted(readers)[0]].directory / "shard_counters.json"
    value = json.loads(path.read_bytes())
    value["token_bytes_sha256"] = "0" * 64
    path.write_text(json.dumps(value))
    with pytest.raises(PrefetchProducerError, match="counters changed"):
        spec.build()
