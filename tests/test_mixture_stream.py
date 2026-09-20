"""Acceptance tests for token shards and the multi-source mixture batcher.

Covers C07: validated token dtype with overflow caught, document offsets with byte
coverage and structural markers, memory-mapped bounded reads, shard reuse across
mixtures, tokenizer change invalidating shards, exact token stopping, and an
interrupted run resuming to identical IDs and target masks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.normalization import compute_sha256
from xlm.data.sampling import (
    ExhaustionPolicy,
    MixtureBatcher,
    MixtureComponent,
    MixtureRecipe,
    MixtureStreamError,
    PackingPolicy,
    SourceAvailability,
    SourceExhaustedError,
    compile_matched_plan,
    validate_mixture,
)
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.byte import ByteTokenizer

BODIES = {
    "encyclopedia": "Tidal patterns along the northern coast shift with lunar declination. ",
    "web_forum": "Has anyone tried adjusting the hydration ratio before proofing? ",
    "code_repo": "def solve(values):\n    return sum(values)\n",
}


def make_doc(doc_id: str, text: str, source_id: str) -> CanonicalDocument:
    raw = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id=source_id,
        source_revision="rev_1",
        source_file="shard.jsonl",
        source_row=0,
        raw_hash=compute_sha256(raw),
        clean_hash=compute_sha256(raw),
        text=text,
        utf8_byte_count=len(raw),
        language="en",
        language_confidence=1.0,
        document_kind="code" if source_id == "code_repo" else "prose",
        source_metadata={},
        parent_ids=[],
        license_reference="cc-by-4.0",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={"duplicate_cluster": f"dup_{source_id}"},
        split="train",
    )


def write_shards(
    root: Path, sizes: dict[str, int], tokenizer: ByteTokenizer
) -> dict[str, TokenShardReader]:
    """Write one shard per source, with deliberately unequal sizes."""
    readers: dict[str, TokenShardReader] = {}
    for source_id, count in sizes.items():
        directory = root / source_id
        writer = TokenShardWriter(
            directory,
            shard_id=f"shard_{source_id}",
            source_id=source_id,
            tokenizer=tokenizer,
            pool_hash="pool_test",
        )
        documents = [
            make_doc(f"{source_id}_{i}", BODIES[source_id] * 2, source_id) for i in range(count)
        ]
        writer.write_documents(documents, add_special_tokens=True)
        readers[source_id] = TokenShardReader(directory)
    return readers


@pytest.fixture
def tokenizer() -> ByteTokenizer:
    return ByteTokenizer()


@pytest.fixture
def shards(tmp_path: Path, tokenizer: ByteTokenizer) -> dict[str, TokenShardReader]:
    return write_shards(
        tmp_path / "shards",
        {"encyclopedia": 40, "web_forum": 20, "code_repo": 10},
        tokenizer,
    )


def mixture(**weights: float) -> MixtureRecipe:
    return MixtureRecipe(
        mixture_id="mix_test",
        components=[MixtureComponent(source_id=s, weight=w) for s, w in weights.items()],
    )


def batcher(
    recipe: MixtureRecipe,
    shards: dict[str, TokenShardReader],
    tokenizer: ByteTokenizer,
    **kwargs: object,
) -> MixtureBatcher:
    defaults: dict[str, object] = {
        "context_length": 64,
        "global_batch_valid_targets": 256,
        "microbatch_sequences": 2,
        "pad_token_id": tokenizer.pad_token_id,
        "bos_token_id": tokenizer.bos_token_id,
        "eos_token_id": tokenizer.eos_token_id,
    }
    defaults.update(kwargs)
    return MixtureBatcher(recipe, dict(shards), **defaults)  # type: ignore[arg-type]


# ------------------------------------------------------------------ shard format


def test_shard_records_offsets_markers_and_coverage(shards: dict[str, TokenShardReader]) -> None:
    """C07: document offsets, byte spans, structural markers and coverage."""
    reader = shards["encyclopedia"]
    reader.verify_integrity()

    assert reader.manifest.byte_coverage_ratio == pytest.approx(1.0)
    assert reader.manifest.token_dtype in ("uint16", "uint32")
    assert reader.manifest.endianness == "little"

    records = list(reader.iter_document_offsets())
    assert len(records) == reader.manifest.num_documents

    first = records[0]
    for field in (
        "doc_id",
        "source_id",
        "token_start",
        "token_count",
        "byte_start",
        "byte_end",
        "valid_targets",
        "bos_positions",
        "eos_positions",
        "lineage_id",
    ):
        assert field in first, f"offset record missing '{field}'"

    assert first["lineage_id"] == "dup_encyclopedia"
    assert first["eos_positions"], "EOS marker must be recorded"
    assert first["valid_targets"] == first["token_count"] - 1

    # Document token ranges must tile the shard without gaps.
    cursor = 0
    for record in records:
        assert record["token_start"] == cursor
        cursor += record["token_count"]
    assert cursor == reader.manifest.num_tokens


def test_shard_counters_report_targets_and_structural_tokens(
    shards: dict[str, TokenShardReader],
) -> None:
    counters = shards["encyclopedia"].counters
    assert counters["valid_targets"] > 0
    assert counters["eos_tokens"] > 0
    assert counters["content_tokens"] == counters["num_tokens"] - counters["eos_tokens"]
    assert counters["canonical_bytes"] > 0


def test_mmap_reads_match_direct_reads(shards: dict[str, TokenShardReader]) -> None:
    """The bounded memory-mapped path must agree with the eager one."""
    reader = shards["web_forum"]
    assert reader.read_tokens_mmap(0, 50) == reader.read_tokens(0, 50)
    assert reader.read_tokens_mmap(10, 25) == reader.read_tokens(10, 25)
    assert reader.read_tokens_mmap(0, 10**9) == reader.read_tokens(0)

    with pytest.raises(ValueError, match="Invalid start offset"):
        reader.read_tokens_mmap(-1, 5)


def test_token_dtype_overflow_is_caught(tmp_path: Path) -> None:
    """A token ID that does not fit the declared dtype must fail loudly (C07)."""

    class OverflowingTokenizer(ByteTokenizer):
        def encode_with_offsets(
            self, text: str, add_special_tokens: bool = False
        ) -> tuple[list[int], list[tuple[int, int]]]:
            # An ID far beyond uint16, which the declared dtype cannot represent.
            return [70_000], [(0, 1)]

    bad = OverflowingTokenizer()
    writer = TokenShardWriter(tmp_path / "bad", "shard_bad", "src", bad, pool_hash="p")
    with pytest.raises(ValueError, match="cannot fit in declared dtype"):
        writer.write_documents([make_doc("d0", "text", "src")])


def test_corrupted_shard_fails_verification(shards: dict[str, TokenShardReader]) -> None:
    """Checksums must catch a modified shard."""
    reader = shards["code_repo"]
    reader.verify_integrity()

    path = reader.directory / "tokens.bin"
    data = bytearray(path.read_bytes())
    data[0] = (data[0] + 1) % 256
    path.write_bytes(bytes(data))

    with pytest.raises(ValueError, match="checksum mismatch"):
        TokenShardReader(reader.directory).verify_integrity()


# ------------------------------------------------------------------- shard reuse


def test_a_second_mixture_reuses_the_same_shards(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    """The headline reuse property: a ratio change must not retokenize (C07)."""
    fingerprints = {s: r.manifest.checksum_sha256 for s, r in shards.items()}

    first = mixture(encyclopedia=0.6, web_forum=0.3, code_repo=0.1)
    second = mixture(encyclopedia=0.2, web_forum=0.5, code_repo=0.3)
    assert first.identity() != second.identity(), "a ratio change must change mixture identity"

    for recipe in (first, second):
        stream = batcher(recipe, shards, tokenizer)
        assert stream.next_step_microbatches()

    # The shards themselves are untouched by either mixture.
    assert {s: r.manifest.checksum_sha256 for s, r in shards.items()} == fingerprints
    for reader in shards.values():
        reader.verify_integrity()


def test_a_second_tokenizer_invalidates_the_shards(
    tmp_path: Path, tokenizer: ByteTokenizer
) -> None:
    """A tokenizer change must produce different shards, not reuse the old ones."""
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    pytest.importorskip("tokenizers")
    documents = [make_doc(f"d{i}", BODIES["encyclopedia"] * 3, "src") for i in range(10)]

    byte_dir = tmp_path / "byte"
    TokenShardWriter(byte_dir, "s", "src", tokenizer, pool_hash="p").write_documents(documents)
    byte_reader = TokenShardReader(byte_dir)

    bpe = ByteLevelBPETokenizer.train_from_documents(documents, target_vocab_size=300)
    bpe_dir = tmp_path / "bpe"
    TokenShardWriter(bpe_dir, "s", "src", bpe, pool_hash="p").write_documents(documents)
    bpe_reader = TokenShardReader(bpe_dir)

    assert byte_reader.manifest.tokenizer_hash != bpe_reader.manifest.tokenizer_hash
    assert byte_reader.manifest.checksum_sha256 != bpe_reader.manifest.checksum_sha256
    assert byte_reader.manifest.num_tokens != bpe_reader.manifest.num_tokens


def test_matched_byte_plan_holds_raw_exposure_across_real_tokenizers(
    tmp_path: Path, tokenizer: ByteTokenizer
) -> None:
    """C07 matched plans must keep raw-text exposure fixed across tokenizers.

    The same 30 documents are tokenized with the byte and BPE tokenizers. Their
    canonical byte counts are identical; their token counts are not. A
    matched-canonical-byte plan compiled at one budget over each shard must plan the
    same bytes and report a different derived token budget.
    """
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    pytest.importorskip("tokenizers")
    documents = [make_doc(f"d{i}", BODIES["encyclopedia"] * 3, "corpus") for i in range(30)]

    byte_dir = tmp_path / "byte"
    TokenShardWriter(byte_dir, "s", "corpus", tokenizer, pool_hash="p").write_documents(documents)
    bpe = ByteLevelBPETokenizer.train_from_documents(documents, target_vocab_size=300)
    bpe_dir = tmp_path / "bpe"
    TokenShardWriter(bpe_dir, "s", "corpus", bpe, pool_hash="p").write_documents(documents)

    def availability(reader: TokenShardReader) -> dict[str, SourceAvailability]:
        counters = reader.counters
        return {
            "corpus": SourceAvailability(
                source_id="corpus",
                shard_id=reader.manifest.shard_id,
                valid_targets=int(counters["valid_targets"]),
                content_tokens=int(counters["content_tokens"]),
                eos_tokens=int(counters["eos_tokens"]),
                canonical_bytes=int(counters["canonical_bytes"]),
                num_documents=reader.manifest.num_documents,
                token_dtype=reader.manifest.token_dtype,
            )
        }

    byte_avail = availability(TokenShardReader(byte_dir))
    bpe_avail = availability(TokenShardReader(bpe_dir))
    assert byte_avail["corpus"].canonical_bytes == bpe_avail["corpus"].canonical_bytes
    assert byte_avail["corpus"].valid_targets != bpe_avail["corpus"].valid_targets

    mixture = MixtureRecipe(
        mixture_id="tok_compare",
        components=[MixtureComponent(source_id="corpus", weight=1.0)],
    )
    budget_bytes = byte_avail["corpus"].canonical_bytes // 2
    byte_plan = compile_matched_plan(
        mixture, validate_mixture(mixture, byte_avail), budget_bytes, basis="canonical_bytes"
    )
    bpe_plan = compile_matched_plan(
        mixture, validate_mixture(mixture, bpe_avail), budget_bytes, basis="canonical_bytes"
    )

    assert byte_plan.total_planned_bytes == bpe_plan.total_planned_bytes == budget_bytes
    assert byte_plan.token_budget != bpe_plan.token_budget
    assert byte_plan.mixture_identity == bpe_plan.mixture_identity


# ---------------------------------------------------------------------- batching


def test_observed_shares_match_configured_weights_on_unequal_sources(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    """Sources of very different sizes must still hit their configured shares."""
    recipe = mixture(encyclopedia=0.55, web_forum=0.30, code_repo=0.15)
    stream = batcher(recipe, shards, tokenizer)

    for _ in range(8):
        if not stream.next_step_microbatches():
            break
        stream.commit()

    report = stream.share_report()
    assert report["within_bound"], f"drift {report['max_drift']} exceeded bound"
    for source_id, expected in (("encyclopedia", 0.55), ("web_forum", 0.30), ("code_repo", 0.15)):
        assert report["target_shares"][source_id] == pytest.approx(expected, abs=0.02)


def test_exact_token_stopping_at_a_nonmultiple_budget(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    """A run must stop at exactly the allowed target count, not a window boundary."""
    stream = batcher(
        mixture(encyclopedia=0.5, web_forum=0.5),
        {k: v for k, v in shards.items() if k != "code_repo"},
        tokenizer,
        global_batch_valid_targets=1000,
    )
    budget = 137  # deliberately not a multiple of the context length
    batches = stream.next_step_microbatches(remaining_budget=budget)

    produced = sum(b.metadata["valid_targets"] for b in batches)
    assert produced == budget, f"expected exactly {budget} targets, produced {produced}"


def test_zero_or_negative_budget_produces_no_batches(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    stream = batcher(mixture(encyclopedia=1.0), {"encyclopedia": shards["encyclopedia"]}, tokenizer)
    assert stream.next_step_microbatches(remaining_budget=0) == []
    assert stream.next_step_microbatches(remaining_budget=-5) == []


def test_batches_carry_masks_positions_and_source_attribution(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    stream = batcher(mixture(encyclopedia=0.5, web_forum=0.5), shards, tokenizer)
    batches = stream.next_step_microbatches()
    assert batches

    batch = batches[0]
    # These optional fields must be populated by the mixture packer, not left None.
    assert batch.position_ids is not None
    assert batch.segment_ids is not None
    assert batch.source_attribution is not None

    for sequence_index, mask in enumerate(batch.loss_mask):
        assert len(mask) == 64
        assert len(batch.input_ids[sequence_index]) == 64
        assert len(batch.labels[sequence_index]) == 64
        assert len(batch.position_ids[sequence_index]) == 64
        assert len(batch.source_attribution[sequence_index]) == 64
    assert batch.metadata["packing_mode"] == "causal_stream"


def test_isolated_packing_mode_is_honoured(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    # code_repo documents are shorter than the context window, so a single window
    # spans more than one document and the segment structure is observable.
    recipe = MixtureRecipe(
        mixture_id="iso",
        components=[MixtureComponent(source_id="code_repo", weight=1.0)],
        packing=PackingPolicy(mode="isolated_document", cross_document_attention=False),
    )
    # code_repo documents are ~86 tokens, so a 200-token window spans several.
    stream = batcher(recipe, {"code_repo": shards["code_repo"]}, tokenizer, context_length=200)
    batches = stream.next_step_microbatches()

    assert batches
    assert batches[0].metadata["packing_mode"] == "isolated_document"
    assert batches[0].segment_ids is not None
    segments = batches[0].segment_ids[0]
    assert len(set(segments)) > 1, "isolated packing must produce multiple segments"

    # A target spanning two segments must be masked out under isolation.
    masks = batches[0].loss_mask[0]
    boundaries = [
        i
        for i in range(len(segments) - 1)
        if segments[i] != segments[i + 1] and segments[i + 1] != -1
    ]
    assert boundaries, "fixture must contain a segment boundary"
    assert all(masks[i] == 0 for i in boundaries)


# ------------------------------------------------------------------------ resume


def test_interrupted_run_resumes_to_identical_ids_and_masks(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    """The core replay guarantee: resume must reproduce the same committed data."""
    recipe = mixture(encyclopedia=0.6, web_forum=0.4)
    sources = {k: v for k, v in shards.items() if k != "code_repo"}

    reference = batcher(recipe, sources, tokenizer)
    uninterrupted: list[list[list[int]]] = []
    for _ in range(4):
        batches = reference.next_step_microbatches()
        if not batches:
            break
        uninterrupted.append([b.input_ids for b in batches])
        reference.commit()

    # Run two steps, checkpoint, then resume into a fresh batcher.
    interrupted = batcher(recipe, sources, tokenizer)
    replayed: list[list[list[int]]] = []
    for _ in range(2):
        batches = interrupted.next_step_microbatches()
        replayed.append([b.input_ids for b in batches])
        interrupted.commit()
    checkpoint = interrupted.get_state()

    resumed = batcher(recipe, sources, tokenizer)
    resumed.load_state(checkpoint)
    for _ in range(2):
        batches = resumed.next_step_microbatches()
        if not batches:
            break
        replayed.append([b.input_ids for b in batches])
        resumed.commit()

    assert replayed == uninterrupted, "resumed run diverged from the uninterrupted run"


def test_resume_restores_masks_and_scheduler_counters(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    recipe = mixture(encyclopedia=0.5, web_forum=0.5)
    sources = {k: v for k, v in shards.items() if k != "code_repo"}

    original = batcher(recipe, sources, tokenizer)
    for _ in range(3):
        original.next_step_microbatches()
        original.commit()

    checkpoint = original.get_state()
    report_before = original.share_report()

    resumed = batcher(recipe, sources, tokenizer)
    resumed.load_state(checkpoint)

    assert resumed.get_state() == checkpoint
    assert resumed.share_report() == report_before

    next_original = [b.loss_mask for b in original.next_step_microbatches()]
    next_resumed = [b.loss_mask for b in resumed.next_step_microbatches()]
    assert next_original == next_resumed, "target masks diverged after resume"


def test_rollback_discards_speculative_progress(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    """A prefetched-but-uncommitted step must not advance the committed cursor."""
    recipe = mixture(encyclopedia=0.5, web_forum=0.5)
    sources = {k: v for k, v in shards.items() if k != "code_repo"}
    stream = batcher(recipe, sources, tokenizer)

    stream.next_step_microbatches()
    stream.commit()
    committed = stream.get_state()

    speculative = [b.input_ids for b in stream.next_step_microbatches()]
    assert stream.get_state() == committed, "an uncommitted step changed committed state"

    stream.rollback()
    replayed = [b.input_ids for b in stream.next_step_microbatches()]
    assert replayed == speculative, "rollback did not replay the same data"


def test_resume_refuses_a_checkpoint_from_a_different_mixture(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    """A changed mixture is a new data lineage, not a resumable run."""
    sources = {k: v for k, v in shards.items() if k != "code_repo"}
    first = batcher(mixture(encyclopedia=0.5, web_forum=0.5), sources, tokenizer)
    first.next_step_microbatches()
    first.commit()
    checkpoint = first.get_state()

    other = batcher(mixture(encyclopedia=0.8, web_forum=0.2), sources, tokenizer)
    with pytest.raises(MixtureStreamError, match="new data lineage"):
        other.load_state(checkpoint)


def test_batching_is_independent_of_microbatch_grouping(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    """Microbatch size is an execution detail: committed order must not change."""
    recipe = mixture(encyclopedia=0.5, web_forum=0.5)
    sources = {k: v for k, v in shards.items() if k != "code_repo"}

    def sequences(microbatch: int) -> list[list[int]]:
        stream = batcher(recipe, sources, tokenizer, microbatch_sequences=microbatch)
        collected: list[list[int]] = []
        for _ in range(3):
            for batch in stream.next_step_microbatches():
                collected.extend(batch.input_ids)
            stream.commit()
        return collected

    assert sequences(1) == sequences(2) == sequences(4)


# -------------------------------------------------------------------- exhaustion


def test_exhaustion_raises_when_repeat_is_disabled(
    tmp_path: Path, tokenizer: ByteTokenizer
) -> None:
    """repeat=false must fail rather than silently repeat or resample."""
    tiny = write_shards(tmp_path / "tiny", {"code_repo": 2}, tokenizer)
    recipe = MixtureRecipe(
        mixture_id="tiny",
        components=[MixtureComponent(source_id="code_repo", weight=1.0)],
    )
    stream = batcher(recipe, tiny, tokenizer, global_batch_valid_targets=100_000)

    with pytest.raises(SourceExhaustedError):
        for _ in range(50):
            if not stream.next_step_microbatches():
                break
            stream.commit()


def test_bounded_repeat_counts_repeated_targets_separately(
    tmp_path: Path, tokenizer: ByteTokenizer
) -> None:
    """Repetition is allowed only when declared, and it is counted (C07)."""
    tiny = write_shards(tmp_path / "tiny", {"code_repo": 3}, tokenizer)
    recipe = MixtureRecipe(
        mixture_id="tiny_repeat",
        components=[MixtureComponent(source_id="code_repo", weight=1.0)],
        exhaustion=ExhaustionPolicy(repeat=True, max_epochs=20),
    )
    stream = batcher(recipe, tiny, tokenizer, global_batch_valid_targets=128)

    for _ in range(4):
        if not stream.next_step_microbatches():
            break
        stream.commit()

    report = stream.share_report()
    assert report["total_repeated_targets"] > 0, "a second epoch must be counted as repeated"
    counters = stream.schedule_state.counters["code_repo"]
    assert counters.epoch >= 1
    assert counters.unique_targets < counters.valid_targets


def test_missing_shard_for_a_weighted_source_is_refused(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    with pytest.raises(MixtureStreamError, match="no shard was supplied"):
        batcher(mixture(encyclopedia=0.5, missing_source=0.5), shards, tokenizer)


def test_invalid_batcher_configuration_is_rejected(
    shards: dict[str, TokenShardReader], tokenizer: ByteTokenizer
) -> None:
    with pytest.raises(ValueError, match="context_length must be positive"):
        batcher(
            mixture(encyclopedia=1.0),
            {"encyclopedia": shards["encyclopedia"]},
            tokenizer,
            context_length=0,
        )
    with pytest.raises(ValueError, match="global_batch_valid_targets must be positive"):
        batcher(
            mixture(encyclopedia=1.0),
            {"encyclopedia": shards["encyclopedia"]},
            tokenizer,
            global_batch_valid_targets=0,
        )
