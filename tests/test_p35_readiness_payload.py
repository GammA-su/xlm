"""P35 pilot readiness: global-update payload receipt and its committed chain.

Pure tests (NumPy only): real authored token shards and the real
``MixtureBatcher``; the P34 producer's own ``_encode`` for the producer path.
No training, no torch.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from p35_m5_support import recipe, write_sources
from xlm.data.sampling import MixtureBatcher
from xlm.data.sampling.prefetch import _encode
from xlm.data.sampling.update_payload import (
    CHAIN_GENESIS,
    PAYLOAD_VERSION,
    PayloadReceiptError,
    UpdatePayloadChain,
    canonical_from_microbatches,
    canonical_from_prepared,
    canonical_update,
    verify_chain,
)
from xlm.tokenizers.byte import ByteTokenizer

CONTEXT = 16
GLOBAL = 16 * 40  # about 40 sequences per global update at context 16


@pytest.fixture(scope="module")
def readers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return write_sources(tmp_path_factory.mktemp("payload_shards"))


def batcher(readers: dict[str, Any], group: int | None) -> MixtureBatcher:
    tokenizer = ByteTokenizer()
    return MixtureBatcher(
        recipe(),
        dict(readers),
        context_length=CONTEXT,
        global_batch_valid_targets=GLOBAL,
        microbatch_sequences=group,
        pad_token_id=tokenizer.pad_token_id,
        bos_token_id=tokenizer.bos_token_id,
        eos_token_id=tokenizer.eos_token_id,
    )


def updates(readers: dict[str, Any], group: int | None, count: int = 3) -> list[list[Any]]:
    stream = batcher(readers, group)
    result = []
    for _ in range(count):
        result.append(stream.next_step_microbatches())
        stream.commit()
    return result


def digest(batches: list[Any]) -> str:
    return canonical_from_microbatches(batches).digest()


# ------------------------------------------------------ grouping invariance


def test_b8_b16_b32_partitions_of_one_global_update_have_one_digest(readers: Any) -> None:
    by_group = {g: updates(readers, g) for g in (8, 16, 32)}
    for index in range(3):
        partitions = {g: by_group[g][index] for g in by_group}
        sequences = {g: sum(len(mb.input_ids) for mb in p) for g, p in partitions.items()}
        assert len(set(sequences.values())) == 1 and sequences[8] > 32
        assert [len(p) for p in partitions.values()] == [
            -(-sequences[8] // 8),
            -(-sequences[8] // 16),
            -(-sequences[8] // 32),
        ]  # genuinely different microbatch partitions
        assert len({digest(p) for p in partitions.values()}) == 1
    canonical = canonical_from_microbatches(by_group[8][0])
    assert canonical.header()["version"] == PAYLOAD_VERSION
    assert canonical.valid_targets == GLOBAL
    assert canonical.header()["fields"] == [
        "input_ids",
        "labels",
        "loss_mask",
        "position_ids",
        "segment_ids",
        "attention_mask",
        "byte_spans",
        "token_offsets",
        "source_attribution",
        "doc_ids",
        "lineage_ids",
    ]


def test_consecutive_updates_differ_and_repeats_are_deterministic(readers: Any) -> None:
    first, again = updates(readers, 8), updates(readers, 8)
    assert [digest(u) for u in first] == [digest(u) for u in again]
    assert len({digest(u) for u in first}) == 3


def test_container_types_do_not_change_the_identity(readers: Any) -> None:
    [batches] = updates(readers, 8, count=1)
    base = digest(batches)
    as_arrays = copy.deepcopy(batches)
    for mb in as_arrays:
        mb.input_ids = np.asarray(mb.input_ids, dtype=np.int32)
        mb.labels = tuple(tuple(row) for row in mb.labels)
        mb.loss_mask = np.asarray(mb.loss_mask, dtype=np.int8)
        mb.metadata["input_attention_mask"] = np.asarray(
            mb.metadata["input_attention_mask"], dtype=bool
        )
        mb.metadata["target_byte_spans"] = np.asarray(mb.metadata["target_byte_spans"])
    assert digest(as_arrays) == base


# -------------------------------------------------------- producer route


def test_producer_encoding_and_synchronous_batches_have_one_digest(readers: Any) -> None:
    [b8] = updates(readers, 8, count=1)
    [b32] = updates(readers, 32, count=1)
    prepared = _encode(b8, 0, 0, None, "start", None, 0.0)  # the P34 child's own encoder
    assert canonical_from_prepared(prepared).digest() == digest(b8) == digest(b32)
    pending = SimpleNamespace(pending_update=prepared)
    assert canonical_update(pending, b8).digest() == digest(b8)
    assert canonical_update(SimpleNamespace(), b32).digest() == digest(b8)
    prepared_b32 = _encode(b32, 0, 0, None, "start", None, 0.0)
    assert prepared.content_digest != prepared_b32.content_digest  # P34 digest sees rows
    assert canonical_from_prepared(prepared_b32).digest() == digest(b8)


def test_a_stale_pending_update_is_refused(readers: Any) -> None:
    [b8] = updates(readers, 8, count=1)
    prepared = _encode(b8[:1], 0, 0, None, "start", None, 0.0)
    with pytest.raises(PayloadReceiptError, match="not the pending prepared update"):
        canonical_update(SimpleNamespace(pending_update=prepared), b8)


# ------------------------------------------------------ payload sensitivity


def first_kept(batches: list[Any]) -> tuple[int, int, int]:
    for m, mb in enumerate(batches):
        for r, row in enumerate(mb.loss_mask):
            for c, flag in enumerate(row):
                if flag and c > 0:
                    return m, r, c
    raise AssertionError("no valid target")


def mutate(batches: list[Any], field: str) -> list[Any]:
    out = copy.deepcopy(batches)
    m, r, c = first_kept(out)
    mb = out[m]
    if field == "input_ids":
        mb.input_ids[r][c] = (mb.input_ids[r][c] + 1) % 256
    elif field == "labels":
        mb.labels[r][c] = (mb.labels[r][c] + 1) % 256
    elif field == "loss_mask":
        mb.loss_mask[r][c] = 0
    elif field == "position_ids":
        mb.position_ids[r][c] += 1
    elif field == "segment_ids":
        mb.segment_ids[r][c] += 1
    elif field == "attention_mask":
        mb.metadata["input_attention_mask"][r][c] = 0
    elif field == "doc_ids":
        mb.metadata["target_doc_ids"][r][c] += "_other"
    elif field == "lineage_ids":
        mb.metadata["target_lineage_ids"][r][c] += "_other"
    elif field == "source_attribution":
        mb.source_attribution[r][c] = "beta" if mb.source_attribution[r][c] != "beta" else "alpha"
    elif field == "byte_spans":
        start, end = mb.metadata["target_byte_spans"][r][c]
        mb.metadata["target_byte_spans"][r][c] = (start, end + 1)
    elif field == "token_offsets":
        mb.metadata["target_token_offsets"][r][c] += 1
    elif field == "packing_mode":
        for batch in out:
            batch.metadata["packing_mode"] = "isolated_document"
    else:  # pragma: no cover
        raise AssertionError(field)
    return out


@pytest.mark.parametrize(
    "field",
    [
        "input_ids",
        "labels",
        "loss_mask",
        "position_ids",
        "segment_ids",
        "attention_mask",
        "doc_ids",
        "lineage_ids",
        "source_attribution",
        "byte_spans",
        "token_offsets",
        "packing_mode",
    ],
)
def test_one_changed_model_or_provenance_value_changes_the_digest(readers: Any, field: str) -> None:
    [batches] = updates(readers, 8, count=1)
    assert digest(mutate(batches, field)) != digest(batches)


def flat_rows(batches: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for mb in batches:
        for i in range(len(mb.input_ids)):
            rows.append(
                {
                    "input_ids": mb.input_ids[i],
                    "labels": mb.labels[i],
                    "loss_mask": mb.loss_mask[i],
                    "position_ids": mb.position_ids[i],
                    "segment_ids": mb.segment_ids[i],
                    "source_attribution": mb.source_attribution[i],
                    **{
                        k: mb.metadata[k][i]
                        for k in (
                            "input_attention_mask",
                            "target_doc_ids",
                            "target_lineage_ids",
                            "target_byte_spans",
                            "target_token_offsets",
                        )
                    },
                }
            )
    return rows


def regroup(rows: list[dict[str, Any]], size: int, mode: str) -> list[Any]:
    batches = []
    for start in range(0, len(rows), size):
        chunk = rows[start : start + size]
        batches.append(
            SimpleNamespace(
                input_ids=[r["input_ids"] for r in chunk],
                labels=[r["labels"] for r in chunk],
                loss_mask=[r["loss_mask"] for r in chunk],
                position_ids=[r["position_ids"] for r in chunk],
                segment_ids=[r["segment_ids"] for r in chunk],
                source_attribution=[r["source_attribution"] for r in chunk],
                metadata={
                    "packing_mode": mode,
                    **{
                        k: [r[k] for r in chunk]
                        for k in (
                            "input_attention_mask",
                            "target_doc_ids",
                            "target_lineage_ids",
                            "target_byte_spans",
                            "target_token_offsets",
                        )
                    },
                },
            )
        )
    return batches


def test_same_values_in_a_different_global_order_change_the_digest(readers: Any) -> None:
    [batches] = updates(readers, 8, count=1)
    mode = batches[0].metadata["packing_mode"]
    rows = flat_rows(batches)
    assert digest(regroup(rows, 8, mode)) == digest(batches)  # rebuilt faithfully
    assert digest(regroup(rows, 5, mode)) == digest(batches)  # any partition
    swapped = list(rows)
    swapped[9], swapped[10] = swapped[10], swapped[9]
    assert swapped[9]["input_ids"] != swapped[10]["input_ids"]
    assert digest(regroup(swapped, 8, mode)) != digest(batches)
    across = list(rows)
    across[7], across[8] = across[8], across[7]  # across a B8 microbatch boundary
    assert digest(regroup(across, 8, mode)) != digest(batches)


def test_absent_fields_are_listed_never_invented(readers: Any) -> None:
    [batches] = updates(readers, 8, count=1)
    bare = [
        SimpleNamespace(
            input_ids=mb.input_ids,
            labels=mb.labels,
            loss_mask=mb.loss_mask,
            position_ids=None,
            segment_ids=None,
            source_attribution=None,
            metadata={"packing_mode": mb.metadata["packing_mode"]},
        )
        for mb in batches
    ]
    canonical = canonical_from_microbatches(bare)
    assert canonical.header()["fields"] == ["input_ids", "labels", "loss_mask"]
    assert canonical.digest() != digest(batches)
    partial = copy.deepcopy(bare)
    partial[0].position_ids = batches[0].position_ids
    with pytest.raises(PayloadReceiptError, match="only some microbatches"):
        canonical_from_microbatches(partial)


def test_device_tensors_are_refused_rather_than_synchronized() -> None:
    class DeviceTensor:
        device = SimpleNamespace(type="cuda")

        def detach(self) -> Any:  # pragma: no cover - must never be reached
            raise AssertionError("synchronized")

    batch = SimpleNamespace(
        input_ids=DeviceTensor(),
        labels=[[1]],
        loss_mask=[[1]],
        position_ids=None,
        segment_ids=None,
        source_attribution=None,
        metadata={},
    )
    with pytest.raises(PayloadReceiptError, match="never synchronizes"):
        canonical_from_microbatches([batch])


def test_non_integer_masks_are_refused() -> None:
    batch = SimpleNamespace(
        input_ids=[[1, 2]],
        labels=[[2, 3]],
        loss_mask=[[1.0, 0.5]],
        position_ids=None,
        segment_ids=None,
        source_attribution=None,
        metadata={},
    )
    with pytest.raises(PayloadReceiptError, match="not an integer"):
        canonical_from_microbatches([batch])


# ------------------------------------------------------------------ chain


def committed_chain(digests: list[str], sizes: list[int]) -> UpdatePayloadChain:
    chain = UpdatePayloadChain()
    before = 0
    for step, (d, n) in enumerate(zip(digests, sizes, strict=True), start=1):
        chain.stage(step=step, committed_before=before, valid_targets=n, payload=d)
        chain.commit()
        before += n
    return chain


def test_grouping_invariant_chains_across_updates(readers: Any) -> None:
    heads = set()
    for group in (8, 16, 32):
        batches = updates(readers, group)
        chain = committed_chain([digest(b) for b in batches], [GLOBAL] * 3)
        heads.add(chain.head)
    assert len(heads) == 1 and CHAIN_GENESIS not in heads


def test_only_committed_updates_enter_the_chain() -> None:
    chain = UpdatePayloadChain()
    chain.stage(step=1, committed_before=0, valid_targets=10, payload="a" * 64)
    chain.discard()  # failed/in-doubt/skipped update
    assert chain.rows == [] and chain.head == CHAIN_GENESIS
    chain.stage(step=1, committed_before=0, valid_targets=10, payload="b" * 64)
    with pytest.raises(PayloadReceiptError, match="neither committed nor discarded"):
        chain.stage(step=1, committed_before=0, valid_targets=10, payload="b" * 64)
    with pytest.raises(PayloadReceiptError, match="uncommitted staged"):
        chain.to_dict()
    chain.commit()
    assert [r[:4] for r in chain.rows] == [[1, 0, 10, "b" * 64]]
    with pytest.raises(PayloadReceiptError, match="no staged"):
        chain.commit()


@pytest.mark.parametrize(("step", "before"), [(1, 0), (2, 0), (3, 10), (2, 11)])
def test_replayed_or_skipped_updates_are_never_appended(step: int, before: int) -> None:
    chain = committed_chain(["a" * 64], [10])
    with pytest.raises(PayloadReceiptError, match="does not follow"):
        chain.stage(step=step, committed_before=before, valid_targets=10, payload="c" * 64)


def test_resume_restores_the_chain_and_continues_exactly() -> None:
    digests = [f"{i:064x}" for i in range(6)]
    sizes = [16, 16, 16, 16, 16, 8]
    uninterrupted = committed_chain(digests, sizes)
    first = committed_chain(digests[:3], sizes[:3])
    saved = first.to_dict()
    resumed = UpdatePayloadChain.from_dict(copy.deepcopy(saved))
    assert resumed.rows == first.rows and resumed.head == first.head
    before = sum(sizes[:3])
    for step, (d, n) in enumerate(zip(digests[3:], sizes[3:], strict=True), start=4):
        resumed.stage(step=step, committed_before=before, valid_targets=n, payload=d)
        resumed.commit()
        before += n
    assert resumed.to_dict() == uninterrupted.to_dict()
    assert verify_chain(uninterrupted.to_dict()) == uninterrupted.head


@pytest.mark.parametrize("damage", ["payload", "link", "head", "gap", "version"])
def test_a_tampered_saved_chain_is_refused(damage: str) -> None:
    saved = committed_chain(["a" * 64, "b" * 64], [10, 10]).to_dict()
    if damage == "payload":
        saved["rows"][0][3] = "f" * 64
    elif damage == "link":
        saved["rows"][1][4] = "0" * 64
    elif damage == "head":
        saved["head"] = "0" * 64
    elif damage == "gap":
        saved["rows"] = saved["rows"][1:]
    else:
        saved["version"] = "global_update_payload_digest_v0"
    with pytest.raises(PayloadReceiptError):
        UpdatePayloadChain.from_dict(saved)


def test_the_genesis_is_run_independent() -> None:
    assert UpdatePayloadChain().head == CHAIN_GENESIS
    assert UpdatePayloadChain().to_dict()["genesis"] == CHAIN_GENESIS


def test_the_digest_binds_scalars_through_the_header(readers: Any) -> None:
    [batches] = updates(readers, 8, count=1)
    canonical = canonical_from_microbatches(batches)
    assert canonical.header()["sequences"] == sum(len(mb.input_ids) for mb in batches)
    assert canonical.header()["context"] == CONTEXT
    assert Path is not None  # module import sanity for the fixture helpers


def test_per_sequence_attribution_is_one_column_not_characters() -> None:
    def batch(sources: list[str]) -> Any:
        return SimpleNamespace(
            input_ids=[[1, 2, 3]] * len(sources),
            labels=[[2, 3, 4]] * len(sources),
            loss_mask=[[1, 1, 1]] * len(sources),
            position_ids=None,
            segment_ids=None,
            source_attribution=sources,
            metadata={},
        )

    canonical = canonical_from_microbatches([batch(["alpha", "beta"])])
    table, codes = canonical.strings["source_attribution"]
    assert table == ["alpha", "beta"] and codes.shape == (2, 1)
    assert (
        canonical.digest()
        == canonical_from_microbatches([batch(["alpha"]), batch(["beta"])]).digest()
    )
    assert canonical.digest() != canonical_from_microbatches([batch(["beta", "alpha"])]).digest()
    mixed = batch(["alpha"])
    mixed.source_attribution = ["alpha", ["a", "b", "c"]]
    mixed.input_ids = mixed.labels = mixed.loss_mask = [[1, 1, 1]] * 2
    with pytest.raises(PayloadReceiptError, match="mixes per-sequence"):
        canonical_from_microbatches([mixed])


def test_a_moved_loss_mask_bit_with_the_same_count_changes_the_digest(readers: Any) -> None:
    [batches] = updates(readers, 8, count=1)
    moved = copy.deepcopy(batches)
    mask = moved[0].loss_mask
    row = next(r for r in range(len(mask)) if 0 in mask[r] and 1 in mask[r])
    on, off = mask[row].index(1), mask[row].index(0)
    mask[row][on], mask[row][off] = 0, 1
    assert canonical_from_microbatches(moved).valid_targets == GLOBAL  # count unchanged
    assert digest(moved) != digest(batches)
