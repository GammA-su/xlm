"""P35 microbatch evidence hardening: pure receipt checks (NumPy only, no torch).

Real authored token shards through the real ``MixtureBatcher`` and the P34
producer's own ``_encode``. The consumed microbatches here are the list-mode
batches (the torch zero-copy path is exercised in
``test_p35_hardening_runtime.py``). SYNTHETIC fixtures; nothing is trained.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from test_p35_readiness_payload import digest, readers, updates  # noqa: F401
from xlm.config.science import ScientificPolicyError
from xlm.data.sampling import update_payload as up
from xlm.data.sampling.prefetch import _encode
from xlm.data.sampling.update_payload import (
    CHAIN_GENESIS,
    MAX_CHAIN_BYTES,
    MAX_CHAIN_ROW_BYTES,
    MAX_CHAIN_ROWS,
    PayloadReceiptError,
    UpdatePayloadChain,
    bind_consumed,
    canonical_from_microbatches,
    canonical_from_prepared,
    canonical_update,
    chain_digest,
    verify_chain,
)
from xlm.evaluation.cadence import update_arithmetic
from xlm.training.checkpoint import MAX_SCIENCE_STATE_BYTES
from xlm.training.science import MAX_LR_RECEIPTS, check_receipt_history

#: Digests of the first three authored updates, computed by a clean export of the
#: UNCHANGED starting commit d7942ba (evidence/P35-MICROBATCH-HARDENING/
#: v1_parity_base.json; identical for B8/B16/B32 and direct/producer paths): the
#: hardening keeps ``global_update_payload_digest_v1`` byte-identical.
V1_GOLDEN = {
    "updates": [
        "0aac900579991698f5f01d9df14043f79b983a1f8922a4697de033361cb60aba",
        "35becd5a0d7b48f9f1f58e3c36ac9706c2a875ba9a10697f1293cd96f7135a12",
        "c362afc84603f21dbe24352619c80030c107f66f38b720968923c743d7940b4d",
    ],
    "head": "23469f8395ff19aa45ba5ef8b4079692be789f1d59c662648154cbd2bfaaf121",
}


def pending_for(batches: list[Any]) -> Any:
    return _encode(batches, 0, 0, None, "start", None, 0.0)


def one_update(readers: Any) -> list[Any]:  # noqa: F811
    [batches] = updates(readers, 8, count=1)
    return batches


# ------------------------------------------------ consumed-payload binding


def test_the_consumed_update_binds_and_keeps_the_direct_digest(readers: Any) -> None:  # noqa: F811
    batches = one_update(readers)
    pending = pending_for(batches)
    bind_consumed(pending, batches)
    consumed = copy.deepcopy(batches)  # a detached copy with identical values is the same payload
    bind_consumed(pending, consumed)
    assert canonical_update(SimpleNamespace(pending_update=pending), consumed).digest() == digest(
        batches
    )


FIELDS = {
    "input_ids": lambda mb: mb.input_ids,
    "labels": lambda mb: mb.labels,
    "loss_mask": lambda mb: mb.loss_mask,
    "position_ids": lambda mb: mb.position_ids,
    "segment_ids": lambda mb: mb.segment_ids,
    "attention_mask": lambda mb: mb.metadata["input_attention_mask"],
}


@pytest.mark.parametrize("field", sorted(FIELDS))
def test_one_changed_consumed_value_is_refused(readers: Any, field: str) -> None:  # noqa: F811
    batches = one_update(readers)
    pending = pending_for(batches)
    consumed = copy.deepcopy(batches)
    rows = FIELDS[field](consumed[1])
    rows[0][3] = (
        (0 if rows[0][3] else 1) if field in ("loss_mask", "attention_mask") else (rows[0][3] + 1)
    )
    with pytest.raises(PayloadReceiptError, match=f"microbatch 1 {field} differs"):
        canonical_update(SimpleNamespace(pending_update=pending), consumed)


def test_a_replaced_whole_field_with_the_same_shape_is_refused(readers: Any) -> None:  # noqa: F811
    batches = one_update(readers)
    pending = pending_for(batches)
    consumed = copy.deepcopy(batches)
    consumed[0].labels = [[4] * len(row) for row in consumed[0].labels]  # provenance untouched
    with pytest.raises(PayloadReceiptError, match="not the pending prepared update"):
        canonical_update(SimpleNamespace(pending_update=pending), consumed)


@pytest.mark.parametrize("change", ["swap", "regroup", "drop", "metadata"])
def test_reordered_regrouped_or_relabelled_microbatches_are_refused(
    readers: Any,  # noqa: F811
    change: str,
) -> None:
    batches = one_update(readers)
    pending = pending_for(batches)
    consumed = copy.deepcopy(batches)
    if change == "swap":
        consumed[0], consumed[1] = consumed[1], consumed[0]
    elif change == "regroup":
        [consumed] = updates(readers, 16, count=1)  # same payload, other partition
        assert digest(consumed) == digest(batches)
    elif change == "drop":
        consumed = consumed[:-1]
    else:
        consumed[0].metadata["packing_mode"] = "isolated_document"
    with pytest.raises(PayloadReceiptError, match="not the pending prepared update"):
        canonical_update(SimpleNamespace(pending_update=pending), consumed)


def without_provenance(batches: list[Any]) -> list[Any]:
    """What the producer hands the trainer: tensors only, provenance stays on the pending update."""
    consumed = copy.deepcopy(batches)
    for mb in consumed:
        mb.source_attribution = None
        for key in ("target_doc_ids", "target_lineage_ids", "target_byte_spans"):
            mb.metadata.pop(key)
        mb.metadata.pop("target_token_offsets")
    return consumed


def test_provenance_shifted_after_sealing_is_refused(readers: Any) -> None:  # noqa: F811
    batches = one_update(readers)
    pending = pending_for(batches)
    shifted = replace(
        pending,
        provenance=replace(pending.provenance, doc_ids=np.roll(pending.provenance.doc_ids, 1, 0)),
    )  # tensors unchanged and correct; one row of provenance moved
    consumed = without_provenance(batches)
    bind_consumed(pending, consumed)  # the unshifted update binds
    with pytest.raises(PayloadReceiptError, match="producer seal"):
        canonical_update(SimpleNamespace(pending_update=shifted), consumed)
    with pytest.raises(PayloadReceiptError, match="target_doc_ids differs"):
        canonical_update(SimpleNamespace(pending_update=shifted), batches)  # carried copy


def test_carried_provenance_must_equal_its_prepared_rows(readers: Any) -> None:  # noqa: F811
    batches = one_update(readers)
    pending = pending_for(batches)
    consumed = copy.deepcopy(batches)
    docs = consumed[0].metadata["target_doc_ids"]
    docs[0], docs[1] = docs[1], docs[0]
    assert docs[0] != docs[1]
    with pytest.raises(PayloadReceiptError, match="target_doc_ids differs"):
        canonical_update(SimpleNamespace(pending_update=pending), consumed)


# ------------------------------------------------------------ alias rule


def test_the_stock_encoder_table_is_canonical_and_accepted(readers: Any) -> None:  # noqa: F811
    batches = one_update(readers)
    pending = pending_for(batches)
    assert len(set(pending.provenance.strings)) == len(pending.provenance.strings)
    up.check_compact_table(pending.provenance)
    assert canonical_from_prepared(pending).digest() == digest(batches)


def test_a_duplicate_alias_is_refused_precisely(readers: Any) -> None:  # noqa: F811
    batches = one_update(readers)
    pending = pending_for(batches)
    provenance = pending.provenance
    codes = provenance.doc_ids.copy()
    old = int(codes[0, 0])
    codes[0, 0] = len(provenance.strings)
    alias = replace(
        pending,
        provenance=replace(
            provenance, strings=(*provenance.strings, provenance.strings[old]), doc_ids=codes
        ),
    )
    assert alias.microbatch_provenance(0) == pending.microbatch_provenance(0)  # same decoded
    with pytest.raises(
        PayloadReceiptError,
        match=f"repeats one string at aliases {old} and {len(provenance.strings)}",
    ):
        canonical_from_prepared(alias)


@pytest.mark.parametrize("code", [-1, 10**6])
def test_an_out_of_table_code_is_refused(readers: Any, code: int) -> None:  # noqa: F811
    pending = pending_for(one_update(readers))
    codes = pending.provenance.lineage_ids.copy()
    codes[0, 0] = code  # -1 would silently decode as the table's last alias
    bad = replace(pending, provenance=replace(pending.provenance, lineage_ids=codes))
    with pytest.raises(PayloadReceiptError, match="outside its"):
        canonical_from_prepared(bad)


def test_a_semantically_different_decoded_value_changes_the_digest(readers: Any) -> None:  # noqa: F811
    batches = one_update(readers)
    pending = pending_for(batches)
    provenance = pending.provenance
    codes = provenance.doc_ids.copy()
    codes[0, 0] = len(provenance.strings)
    changed = replace(
        pending,
        provenance=replace(
            provenance, strings=(*provenance.strings, "authored-other-doc"), doc_ids=codes
        ),
    )
    assert canonical_from_prepared(changed).digest() != canonical_from_prepared(pending).digest()


def test_v1_digests_are_unchanged_from_the_starting_commit(readers: Any) -> None:  # noqa: F811
    chain = UpdatePayloadChain()
    before = 0
    for step, (batches, golden) in enumerate(
        zip(updates(readers, 8, count=3), V1_GOLDEN["updates"], strict=True), start=1
    ):
        pending = pending_for(batches)
        assert digest(batches) == golden
        assert canonical_update(SimpleNamespace(pending_update=pending), batches).digest() == golden
        valid = canonical_from_microbatches(batches).valid_targets
        chain.stage(step=step, committed_before=before, valid_targets=valid, payload=golden)
        chain.commit()
        before += valid
    assert chain.head == V1_GOLDEN["head"]


# ----------------------------------------------------------------- bounds


@pytest.mark.parametrize(
    ("label", "targets"),
    [
        ("pilot_32m", 32_000_000),
        ("50m_1b", 1_000_000_000),
        ("150m_3b", 3_000_000_000),
        ("300m_6b", 6_000_000_000),
        ("1b_param_20_targets_per_param_headroom", 20_000_000_000),
    ],
)
def test_planned_update_counts_fit_the_receipt_bounds(label: str, targets: int) -> None:
    arithmetic = update_arithmetic(targets, 65_536)
    updates_needed = arithmetic["total_updates"]
    assert updates_needed <= MAX_CHAIN_ROWS <= MAX_LR_RECEIPTS
    last_before = arithmetic["full_updates"] * 65_536 - (
        0 if arithmetic["final_update_targets"] else 65_536
    )
    worst = [updates_needed, last_before, 65_536, "f" * 64, "f" * 64]
    assert len(json.dumps(worst)) <= MAX_CHAIN_ROW_BYTES


def test_the_row_bound_keeps_science_json_loadable() -> None:
    """At MAX_CHAIN_ROWS lock-step LR + payload rows the file stays under the read bound."""
    chain_row = [MAX_CHAIN_ROWS, 10**15 - 1, 2**31 - 1, "f" * 64, "f" * 64]
    lr_row = [MAX_CHAIN_ROWS, 10**15 - 1, 2**31 - 1, 10**15 - 1, [1.2345678901234567e-05] * 2]
    assert len(json.dumps(chain_row)) <= MAX_CHAIN_ROW_BYTES
    rows_bytes = MAX_CHAIN_ROWS * (MAX_CHAIN_ROW_BYTES + 2 + len(json.dumps(lr_row)) + 2)
    ledger_allowance = 8 * 1024**2  # evaluation/checkpoint ledgers and runtime receipts
    assert MAX_CHAIN_BYTES < MAX_SCIENCE_STATE_BYTES
    assert rows_bytes + ledger_allowance < MAX_SCIENCE_STATE_BYTES


def test_stage_refuses_at_the_row_bound_before_any_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(up, "MAX_CHAIN_ROWS", 2)
    chain = UpdatePayloadChain()
    for step in (1, 2):
        chain.stage(step=step, committed_before=16 * (step - 1), valid_targets=16, payload="a" * 64)
        chain.commit()
    with pytest.raises(PayloadReceiptError, match="at its bound of 2"):
        chain.stage(step=3, committed_before=32, valid_targets=16, payload="a" * 64)
    assert chain.staged is None and len(chain.rows) == 2


@pytest.mark.parametrize("payload", ["A" * 64, "a" * 63, "g" * 64, 7, "a" * 64 + "\n"])
def test_stage_refuses_a_malformed_payload_digest(payload: Any) -> None:
    with pytest.raises(PayloadReceiptError, match="64 lowercase hex"):
        UpdatePayloadChain().stage(step=1, committed_before=0, valid_targets=1, payload=payload)


def test_stage_refuses_non_integer_counters() -> None:
    with pytest.raises(PayloadReceiptError, match="non-negative int"):
        UpdatePayloadChain().stage(step=True, committed_before=0, valid_targets=1, payload="a" * 64)


def committed(sizes: list[int]) -> UpdatePayloadChain:
    chain = UpdatePayloadChain()
    before = 0
    for step, size in enumerate(sizes, start=1):
        chain.stage(step=step, committed_before=before, valid_targets=size, payload=f"{step:064x}")
        chain.commit()
        before += size
    return chain


@pytest.mark.parametrize("damage", ["too_many_rows", "bool_step", "string_count", "huge_count"])
def test_an_absurd_saved_chain_is_refused(monkeypatch: pytest.MonkeyPatch, damage: str) -> None:
    saved = committed([16, 16, 16]).to_dict()
    if damage == "too_many_rows":
        monkeypatch.setattr(up, "MAX_CHAIN_ROWS", 2)  # fixture-only lowered bound
    elif damage == "bool_step":
        saved["rows"][0][0] = True
    elif damage == "string_count":
        saved["rows"][0][1] = "0"
    else:
        saved["rows"][2][1] = 10**200
    with pytest.raises(PayloadReceiptError):
        verify_chain(saved)


def test_commit_never_fails_on_what_stage_preflighted() -> None:
    chain = committed([16])
    chain.stage(step=2, committed_before=16, valid_targets=5, payload="b" * 64)
    row = chain.prepare_commit(2, 16, 5)
    assert chain.rows[-1][4] != row[4] and chain.staged == [2, 16, 5, "b" * 64]  # nothing mutated
    with pytest.raises(PayloadReceiptError, match="not the committed update"):
        chain.prepare_commit(2, 16, 6)
    assert chain.commit() == row
    assert row[4] == chain_digest(chain.rows[0][4], 2, 16, 5, "b" * 64)


# ------------------------------------------ chain / LR / data consistency


def history(sizes: list[int]) -> dict[str, Any]:
    chain = committed(sizes)
    lr, before = [], 0
    for step, size in enumerate(sizes, start=1):
        lr.append([step, before, size, before + size, [1e-5]])
        before += size
    return {
        "lr_receipts": {
            "columns": ["step", "committed_before", "valid_targets", "schedule_counter", "lr_used"],
            "rows": lr,
        },
        "update_payloads": chain.to_dict(),
    }


def check(saved: dict[str, Any], step: int, c: int, data: Any) -> None:
    check_receipt_history(saved, step=step, committed=c, data_committed=data)


def test_consistent_histories_and_a_final_partial_update_validate() -> None:
    check(history([16, 16, 16]), 3, 48, 48)
    check(history([16, 16, 8]), 3, 40, 40)  # final partial update, exact
    check(history([]), 0, 0, 0)


def extended(sizes: list[int], extra: int) -> dict[str, Any]:
    """A self-consistent LR + chain history one update beyond ``sizes``."""
    return history([*sizes, extra])


@pytest.mark.parametrize(
    ("name", "saved", "step", "c", "data", "message"),
    [
        ("chain_and_lr_ahead_A7", extended([16], 16), 1, 16, 16, "ahead of the committed data"),
        ("history_ahead_at_zero_A7", history([16]), 0, 0, 0, "ahead of the committed data"),
        ("data_ahead", history([16, 16]), 2, 32, 48, "without its required receipts"),
        ("chain_behind_data", history([16]), 2, 32, 32, "without its required receipts"),
        ("meta_ahead", history([16, 16]), 2, 48, 32, "checkpoint metadata records"),
        ("step_off", history([16, 16]), 3, 32, 32, "checkpoint step 3"),
        ("partial_off_by_one", history([16, 16, 8]), 3, 40, 39, "ahead of the committed data"),
        ("partial_meta_off_by_one", history([16, 16, 8]), 3, 41, 40, "metadata records C=41"),
        ("data_count_missing", history([16]), 1, 16, None, "records no committed_valid_targets"),
        ("data_count_bool", history([16]), 1, 16, True, "records no committed_valid_targets"),
    ],
)
def test_history_and_committed_state_must_agree(
    name: str, saved: dict[str, Any], step: int, c: int, data: Any, message: str
) -> None:
    with pytest.raises(ScientificPolicyError, match=message):
        check(saved, step, c, data)


@pytest.mark.parametrize("damage", ["lr_extra_row", "lr_counter", "lr_gap", "chain_link"])
def test_lr_history_must_be_contiguous_and_match_the_chain(damage: str) -> None:
    saved = history([16, 16])
    rows = saved["lr_receipts"]["rows"]
    if damage == "lr_extra_row":
        rows.append([3, 32, 16, 48, [1e-5]])
        match = "update by update"
    elif damage == "lr_counter":
        rows[1][3] = 33
        match = "contiguous"
    elif damage == "lr_gap":
        rows[1][1] = 17
        match = "contiguous"
    else:
        saved["update_payloads"]["rows"][0][3] = "f" * 64
        match = "unreadable update payload chain"
    with pytest.raises(ScientificPolicyError, match=match):
        check(saved, 2, 32, 32)


def test_a_duplicated_row_is_refused_on_load() -> None:
    """Adversary 7 (load side): a replayed payload row can never be read back."""
    saved = history([16, 16])
    rows = saved["update_payloads"]["rows"]
    rows.insert(1, list(rows[0]))
    lr = saved["lr_receipts"]["rows"]
    lr.insert(1, list(lr[0]))
    with pytest.raises(ScientificPolicyError, match="unreadable update payload chain"):
        check(saved, 3, 32, 32)


def test_the_genesis_and_version_are_unchanged() -> None:
    assert up.PAYLOAD_VERSION == "global_update_payload_digest_v1"
    assert up.CHAIN_VERSION == "xlm-update-payload-chain-v1"
    assert UpdatePayloadChain().head == CHAIN_GENESIS


# ------------------------------------- microbatch_grouping_v2 evidence ambiguity


def evidence_chain(sizes: list[int]) -> dict[str, Any]:
    return committed(sizes).to_dict()


@pytest.mark.parametrize(
    ("chain_present", "declared", "message"),
    [
        (True, None, "presence disagrees"),  # an undeclared chain
        (False, "global_update_payload_digest_v1", "presence disagrees"),  # declared, missing
        (True, "global_update_payload_digest_v2", "version differs"),
    ],
)
def test_evidence_refuses_a_receipt_that_disagrees_with_its_declaration(
    tmp_path: Any, chain_present: bool, declared: str | None, message: str
) -> None:
    from test_p35_m4_evidence import BATCH, BUDGET, _count, publish_run
    from xlm.comparison.science_evidence import EvidenceError, extract_run_evidence

    chain = evidence_chain([BATCH] * (BUDGET // BATCH)) if chain_present else None
    ckpt = publish_run(tmp_path, "r", (1, 2, 3), update_payloads=chain, declare_receipt=declared)
    with pytest.raises(EvidenceError, match=message):
        extract_run_evidence(ckpt, parameter_counter=_count)


def test_evidence_refuses_a_history_beyond_the_committed_state(tmp_path: Any) -> None:
    """A7 at evidence level: LR + chain one update beyond the checkpoint's committed count."""
    from test_p35_m4_evidence import BATCH, BUDGET, _count, publish_run
    from xlm.comparison.science_evidence import EvidenceError, extract_run_evidence

    updates_n = BUDGET // BATCH + 1
    lr = [[i + 1, i * BATCH, BATCH, (i + 1) * BATCH, [1e-6]] for i in range(updates_n)]
    ckpt = publish_run(
        tmp_path, "r", (1, 2, 3), lr_rows=lr, update_payloads=evidence_chain([BATCH] * updates_n)
    )
    with pytest.raises(EvidenceError, match="LR receipts account for"):
        extract_run_evidence(ckpt, parameter_counter=_count)
