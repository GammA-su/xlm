"""Exact hash-chain specialization and bounded index lookup after budget trimming."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from test_performance_tokenization import document
from xlm.artifacts.manifest import MAX_MANIFEST_BYTES, identity_digest
from xlm.data.sampling import MixtureBatcher, MixtureComponent, MixtureRecipe
from xlm.data.sampling.trace import target_trace_digest
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
