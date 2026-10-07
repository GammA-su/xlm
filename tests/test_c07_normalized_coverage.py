"""Authored original-text v1 oracles for normalized v2 coverage and training traces."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from test_c07_consumer_v2 import convert_v2
from test_c07_consumer_v2 import tokenizer as tokenizer_fixture
from test_performance_tokenization import document
from xlm.data.exclusion.selection import SelectionGate
from xlm.data.input_validation import validate_training_index
from xlm.data.normalization import canonical_normalize
from xlm.data.sampling import MixtureBatcher, MixtureComponent, MixtureRecipe
from xlm.data.tokens import TokenShardReader, TokenShardWriter
from xlm.tokenizers.bpe import ByteLevelBPETokenizer

tokenizer = tokenizer_fixture

CASES = [
    pytest.param("caf\u00e9\n", "caf\u00e9\n", id="normalized"),
    pytest.param("e\u0301", "\u00e9", id="nfc"),
    pytest.param("a\r\nb", "a\nb", id="crlf"),
    pytest.param("a\rb", "a\nb", id="cr"),
    pytest.param("e\u0301\r\na\rb", "\u00e9\na\nb", id="combined"),
]


@pytest.mark.parametrize("text,normalized", [*CASES, pytest.param("", "", id="empty")])
@pytest.mark.parametrize("prefix", [False, True], ids=["full-eos", "selected-prefix"])
def test_original_text_v1_v2_training_equivalence(
    tmp_path: Path, tokenizer: ByteLevelBPETokenizer, text: str, normalized: str, prefix: bool
) -> None:
    # Do not use the shared helper's normalized text or its byte count as this oracle.
    raw = text.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    doc = replace(
        document(""), text=text, utf8_byte_count=len(raw), raw_hash=digest, clean_hash=digest
    )
    assert doc.text == text and doc.utf8_byte_count == len(raw)
    assert canonical_normalize(text) == normalized
    full_ids, full_spans = tokenizer.encode_with_offsets(doc.text, True)
    writer = TokenShardWriter(tmp_path / "v1", "fixture", "fixture", tokenizer)
    selected_count = len(full_ids)
    if prefix:
        # Authored selection lookup only; exercise the existing v1 writer's real slicing.
        # Empty framed text has no positive-target proper prefix, so remains full EOS.
        selected_count = max(2, len(full_ids) - 2)
        selection = Mock(spec=SelectionGate)
        selection.digest = "authored-selection-lookup"
        selection.expect.return_value = (len(full_ids) - 1, selected_count - 1)
        writer.selection = selection
    writer.write_documents([doc], True)
    first = TokenShardReader(tmp_path / "v1")
    second = convert_v2(first.directory, tmp_path / "v2", tokenizer)
    old = next(first.iter_document_offsets())
    compact = next(second.iter_document_offsets())
    assert first.read_tokens() == second.read_tokens() == full_ids[:selected_count]
    assert old["byte_count"] == compact["byte_count"] == len(raw)
    expected_spans = [list(span) for span in full_spans[:selected_count]]
    assert old["token_byte_spans"] == expected_spans
    covered = sum(end - start for start, end in expected_spans)
    assert old["covered_bytes"] == compact["covered_bytes"] == covered
    if selected_count == len(full_ids):
        assert covered == len(normalized.encode("utf-8"))
        assert old["eos_positions"] == [selected_count - 1]
        assert expected_spans[-1] == [covered, covered]
    else:
        assert not old["eos_positions"]
        assert covered <= len(normalized.encode("utf-8"))
    if not text:
        assert expected_spans == [[0, 0], [0, 0]]
    # This full NFC/CRLF/combined path fails on source commit 947002d.
    validate_training_index(first)
    validate_training_index(second)
    assert second.with_byte_spans(compact) == old
    recipe = MixtureRecipe(
        mixture_id="normalization", components=[MixtureComponent(source_id="fixture", weight=1)]
    )
    batchers = [
        MixtureBatcher(
            recipe,
            {"fixture": reader},
            context_length=8,
            global_batch_valid_targets=selected_count - 1,
            microbatch_sequences=2,
            pad_token_id=tokenizer.pad_token_id,
            bos_token_id=tokenizer.bos_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
        for reader in (first, second)
    ]
    try:
        a, b = batchers
        # Dataclass equality covers IDs, masks, spans, attribution and trace metadata.
        assert a.next_step_microbatches() == b.next_step_microbatches()
        a.commit()
        b.commit()
        assert a.get_state() == b.get_state()
    finally:
        for batcher in batchers:
            batcher.close()


@pytest.mark.parametrize(
    "damage",
    [
        "negative-id",
        "wide-id",
        "count",
        "ceiling",
        "override",
        "covered",
        "negative-bytes",
        "overflow-coverage",
        "bos-position",
        "eos-position",
        "bos-payload",
        "eos-payload",
    ],
)
def test_normalized_coverage_keeps_other_guards(
    tmp_path: Path, tokenizer: ByteLevelBPETokenizer, damage: str
) -> None:
    doc = replace(document(""), text="e\u0301", utf8_byte_count=3)
    writer = TokenShardWriter(tmp_path / "v1", "fixture", "fixture", tokenizer)
    writer.write_documents([doc], True)
    reader = convert_v2(writer.output_dir, tmp_path / "v2", tokenizer)
    record = copy.deepcopy(next(reader.iter_document_offsets()))
    ids = np.asarray(reader.read_tokens(), dtype=np.int64)
    match damage:
        case "negative-id":
            ids[1] = -1
        case "wide-id":
            ids[1] = len(reader.token_byte_table())
        case "count":
            record["token_count"] -= 1
        case "ceiling":
            record["token_count"] = 1_048_577
        case "override":
            record["token_byte_spans"] = []
        case "covered":
            record["covered_bytes"] -= 1
        case "negative-bytes":
            record["byte_count"] = -1
        case "overflow-coverage":
            record["byte_count"] = 1
        case "bos-position":
            record["bos_positions"] = [1]
        case "eos-position":
            record["eos_positions"] = [0]
        case "bos-payload" | "eos-payload":
            ids[0 if damage == "bos-payload" else -1] = ids[1]
            record["covered_bytes"] = int(reader.token_byte_table()[ids].sum())
            record["byte_count"] = record["covered_bytes"] + 1
    with pytest.raises(ValueError):
        reader.validate_v2_ids(record, ids)
