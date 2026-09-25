"""Exact hash-chain specialization and bounded index lookup after budget trimming."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from test_performance_tokenization import document
from xlm.artifacts.manifest import MAX_MANIFEST_BYTES, identity_digest
from xlm.data.sampling import MixtureBatcher, MixtureComponent, MixtureRecipe
from xlm.data.sampling.mixture import ExhaustionPolicy
from xlm.data.sampling.trace import extend_target_trace_chain, target_trace_digest
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.byte import ByteTokenizer


def test_target_chain_matches_generic_canonical_validation() -> None:
    old = new = ""
    for i in range(100):
        trace = {
            "source_id": "fixture\x00",
            "doc_id": f'quoted " \U0001f642 {i}',
            "lineage_id": "e\u0301",
            "token_offset": i,
            "label": i + 4,
            "byte_span": [i, i + 1],
            "epoch": 0,
        }
        old = identity_digest({"previous": old, "target": trace})
        new = target_trace_digest(new, trace)
        assert old == new


@pytest.mark.parametrize("value", [float("nan"), object(), [[[1]]] * 100001])
def test_target_chain_keeps_malformed_input_rejection(value: object) -> None:
    trace = {
        "source_id": "s",
        "doc_id": "d",
        "lineage_id": "l",
        "token_offset": 0,
        "label": 4,
        "byte_span": [0, value],
        "epoch": 0,
    }
    with pytest.raises(ValueError):
        identity_digest({"previous": "", "target": trace})
    with pytest.raises(ValueError):
        target_trace_digest("", trace)


def test_target_chain_keeps_encoded_byte_limit() -> None:
    trace = {
        "source_id": "s",
        "doc_id": "x" * MAX_MANIFEST_BYTES,
        "lineage_id": "l",
        "token_offset": 0,
        "label": 4,
        "byte_span": [0, 1],
        "epoch": 0,
    }
    with pytest.raises(ValueError):
        identity_digest({"previous": "", "target": trace})
    with pytest.raises(ValueError):
        target_trace_digest("", trace)


def _fold(previous: str, source: str, epoch: object, rows: list[tuple[Any, ...]]) -> str:
    for doc, lineage, offset, label, span in rows:
        previous = target_trace_digest(
            previous,
            {
                "source_id": source,
                "doc_id": doc,
                "lineage_id": lineage,
                "token_offset": offset,
                "label": label,
                "byte_span": list(span),
                "epoch": epoch,
            },
        )
    return previous


def _columns(rows: list[tuple[Any, ...]]) -> list[list[Any]]:
    return [list(column) for column in zip(*rows, strict=True)]


@pytest.mark.parametrize("previous", ["", "0" * 64, 'odd "start" %s é'])
def test_window_fold_is_byte_identical_for_adversarial_strings(previous: str) -> None:
    documents = [
        ("synthetic_0", ""),
        ('q"uote\\slash\n%d%%s', "lin%"),
        ("\U0001f642 é \x00", " "),
        ("", "x" * 300),
    ]
    rows = []
    for index in range(400):
        doc, lineage = documents[(index // 37) % len(documents)]
        start = -1 if index % 11 == 0 else index * 3
        rows.append((doc, lineage, index - 1, (index * 7919) % 70000, (start, start + index % 5)))
    doc_ids, lineage_ids, offsets, labels, spans = _columns(rows)
    kept = [i for i in range(len(rows)) if i % 13]
    for epoch in (0, 7, 10**20):
        expected = _fold(previous, "src%é", epoch, [rows[i] for i in kept])
        assert expected == extend_target_trace_chain(
            previous, "src%é", epoch, doc_ids, lineage_ids, offsets, labels, spans, kept
        )
    assert extend_target_trace_chain(previous, "s", 0, [], [], [], [], [], []) == previous


@pytest.mark.parametrize(
    "column, value",
    [(3, True), (2, 1.0), (4, (0, 1.5)), (4, (0,)), (0, 5), (3, 2**70)],
)
def test_window_fold_falls_back_outside_the_proven_domain(column: int, value: object) -> None:
    rows = [["d", "l", i, i + 4, (i, i + 1)] for i in range(5)]
    rows[3][column] = value
    doc_ids, lineage_ids, offsets, labels, spans = _columns([tuple(r) for r in rows])
    indices = list(range(5))
    try:
        expected: object = _fold("", "s", 0, [tuple(r) for r in rows])
    except (ValueError, IndexError, TypeError) as exc:
        expected = type(exc)
    try:
        actual: object = extend_target_trace_chain(
            "", "s", 0, doc_ids, lineage_ids, offsets, labels, spans, indices
        )
    except (ValueError, IndexError, TypeError) as exc:
        actual = type(exc)
    assert actual == expected


def test_window_fold_keeps_encoded_byte_limit() -> None:
    doc = "x" * MAX_MANIFEST_BYTES
    with pytest.raises(ValueError, match="byte limit"):
        extend_target_trace_chain("", "s", 0, [doc], [""], [0], [4], [(0, 1)], [0])


@pytest.mark.parametrize("global_targets", [7, 300])
def test_batcher_state_matches_per_target_reference(tmp_path: Path, global_targets: int) -> None:
    """Chain, 256-record trace, truncation and byte coverage match the old loop."""
    import xlm.data.sampling.stream as stream

    TokenShardWriter(tmp_path, "s", "fixture", ByteTokenizer()).write_documents(
        [document("abc" * (1 + i % 9), i) for i in range(60)], True
    )

    def reference_fold(previous, source, epoch, docs, lineages, offsets, labels, spans, kept):
        rows = [(docs[i], lineages[i], offsets[i], labels[i], spans[i]) for i in kept]
        return _fold(previous, source, epoch, rows)

    states = []
    for fold in (None, reference_fold):
        recipe = MixtureRecipe(
            mixture_id="fixture",
            components=[MixtureComponent(source_id="fixture", weight=1.0)],
            exhaustion=ExhaustionPolicy(repeat=True, max_epochs=8),  # nonzero trace epochs
        )
        batcher = MixtureBatcher(
            recipe,
            {"fixture": TokenShardReader(tmp_path)},
            context_length=64,
            global_batch_valid_targets=global_targets,
        )
        with patch.object(stream, "extend_target_trace_chain", fold or extend_target_trace_chain):
            trail = []
            for _ in range(6):
                batcher.next_step_microbatches()
                batcher.commit()
                trail.append(batcher.get_state())
        states.append(trail)
    assert states[0] == states[1]
    assert states[0][-1]["last_step_trace_truncated"] is (global_targets > 256)


def test_budget_trim_does_not_restart_index_scan(tmp_path: Path) -> None:
    TokenShardWriter(tmp_path, "s", "fixture", ByteTokenizer()).write_documents(
        [document("abc", i) for i in range(200)], True
    )
    reader = TokenShardReader(tmp_path)
    recipe = MixtureRecipe(
        mixture_id="fixture", components=[MixtureComponent(source_id="fixture", weight=1.0)]
    )
    batcher = MixtureBatcher(
        recipe, {"fixture": reader}, context_length=64, global_batch_valid_targets=7
    )
    original = json.loads
    visits = []

    def loads(*args, **kwargs):
        result = original(*args, **kwargs)
        if isinstance(result, dict) and "doc_id" in result:
            visits.append(result["doc_id"])
        return result

    labels = []
    with patch("json.loads", loads):
        for _ in range(100):
            for batch in batcher.next_step_microbatches():
                for row, mask in zip(batch.labels, batch.loss_mask, strict=True):
                    labels.extend(label for label, keep in zip(row, mask, strict=True) if keep)
            batcher.commit()
    assert visits.count("0") == 1
    assert labels == [i for i in reader.read_tokens()[1:] if i not in (0, 1)][:700]
    assert batcher.get_state()["committed_valid_targets"] == 700
