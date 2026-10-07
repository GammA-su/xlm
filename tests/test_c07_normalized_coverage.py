"""Authored original-text oracles for C07 normalized coverage, v1 spans and v2 IDs.

``byte_count`` is the original text's UTF-8 length; ``covered_bytes`` and every span
are in ``canonical_normalize`` coordinates. Fixtures are deliberately NOT normalized
before building the ``CanonicalDocument``: that would hide the coordinate difference.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import numpy as np
import pytest

from test_c07_consumer_v2 import convert_v2
from test_c07_consumer_v2 import tokenizer as tokenizer_fixture
from test_performance_tokenization import document
from xlm.core.contracts import CanonicalDocument
from xlm.data.exclusion.selection import SelectionGate
from xlm.data.input_validation import validate_training_index, validate_v1_spans
from xlm.data.normalization import canonical_normalize
from xlm.data.sampling import MixtureBatcher, MixtureComponent, MixtureRecipe
from xlm.data.tokens import MAX_NORMALIZED_UTF8_GROWTH, TokenShardReader, TokenShardWriter
from xlm.tokenizers.bpe import ByteLevelBPETokenizer

tokenizer = tokenizer_fixture

# (original text, authored canonical_normalize result, normalized vs original UTF-8 bytes)
CASES = [
    pytest.param("café\n", "café\n", "==", id="normalized"),
    pytest.param("é", "é", "<", id="nfc-contraction"),
    pytest.param("K", "K", "<", id="nfc-singleton-contraction"),
    pytest.param("̈́", "̈́", ">", id="nfc-expansion"),
    pytest.param("\U0001d160", "\U0001d158\U0001d165\U0001d16e", ">", id="nfc-max-expansion"),
    pytest.param("a\r\nb", "a\nb", "<", id="crlf"),
    pytest.param("a\rb", "a\nb", "==", id="cr"),
    pytest.param("é\r\na\rb", "é\na\nb", "<", id="combined"),
    pytest.param("̈́\r\n̈́\r", "̈́\n̈́\n", ">", id="combined-expansion"),
]
EMPTY = pytest.param("", "", "==", id="empty")
RELATION = {-1: "<", 0: "==", 1: ">"}


def original_document(text: str) -> CanonicalDocument:
    """A C02 document of exactly ``text``; never the shared helper's normalized text."""
    raw = text.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    doc = replace(
        document(""), text=text, utf8_byte_count=len(raw), raw_hash=digest, clean_hash=digest
    )
    assert doc.text == text and doc.utf8_byte_count == len(raw)
    return doc


def test_original_and_normalized_bytes_take_all_three_relations(
    tokenizer: ByteLevelBPETokenizer,
) -> None:
    seen = set()
    for case in [*CASES, EMPTY]:
        text, normalized, relation = case.values
        assert canonical_normalize(text) == normalized
        spans = tokenizer.encode_with_offsets(text, True)[1]
        covered, original = spans[-1][1], len(text.encode("utf-8"))
        assert covered == len(normalized.encode("utf-8"))
        assert RELATION[(covered > original) - (covered < original)] == relation
        seen.add(relation)
    assert seen == {"<", "==", ">"}


def test_normalization_growth_bound_is_exhaustive_and_tight() -> None:
    """``canonical_normalize`` grows UTF-8 at most 3x under the pinned Unicode database.

    NFC composes NFD, and no canonical composite is longer than its two-code-point
    mapping (Hangul: 6 -> 3 bytes), so NFC <= NFD bytes; NFD is per code point; newline
    normalization only replaces or deletes. Hence covered <= 3 * byte_count.
    """
    worst = 0.0
    for point in range(0x110000):
        if 0xD800 <= point <= 0xDFFF:
            continue
        char = chr(point)
        size = len(char.encode("utf-8"))
        worst = max(worst, len(unicodedata.normalize("NFD", char).encode("utf-8")) / size)
        mapping = unicodedata.decomposition(char).split()
        if len(mapping) == 2 and not mapping[0].startswith("<"):
            assert size <= len("".join(chr(int(m, 16)) for m in mapping).encode("utf-8"))
    assert worst == MAX_NORMALIZED_UTF8_GROWTH
    assert len(canonical_normalize("각").encode("utf-8")) == 3  # 9 -> 3
    tight = "\U0001d160"
    assert len(canonical_normalize(tight).encode("utf-8")) == 3 * len(tight.encode("utf-8"))
    # Deterministic sequences mixing decomposable, combining, Hangul and newline points.
    pool = [
        chr(p)
        for p in range(0x110000)
        if not 0xD800 <= p <= 0xDFFF and unicodedata.decomposition(chr(p))[:1] not in ("", "<")
    ]
    pool += ["\r", "\n", "\r\n", "́", "̈", "ᄀ", "ᅡ", "ᆨ", "a"]
    rng = random.Random(20261007)
    for _ in range(4000):
        text = "".join(rng.choice(pool) for _ in range(rng.randint(1, 12)))
        normalized = len(canonical_normalize(text).encode("utf-8"))
        assert 0 < normalized <= MAX_NORMALIZED_UTF8_GROWTH * len(text.encode("utf-8"))


@pytest.mark.parametrize("text,normalized,relation", [*CASES, EMPTY])
@pytest.mark.parametrize("prefix", [False, True], ids=["full-eos", "selected-prefix"])
def test_original_text_v1_v2_training_equivalence(
    tmp_path: Path,
    tokenizer: ByteLevelBPETokenizer,
    text: str,
    normalized: str,
    relation: str,
    prefix: bool,
) -> None:
    doc = original_document(text)
    raw = text.encode("utf-8")
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
        assert RELATION[(covered > len(raw)) - (covered < len(raw))] == relation
        assert old["eos_positions"] == [selected_count - 1]
        assert expected_spans[-1] == [covered, covered]
    else:
        assert not old["eos_positions"]
        assert covered <= len(normalized.encode("utf-8"))
    if not text:
        assert expected_spans == [[0, 0], [0, 0]]
    # Full NFC/CRLF/combined fails on 947002d; NFC expansion fails on a3574cb.
    validate_training_index(first)
    validate_training_index(second)
    validate_v1_spans(old, selected_count)
    second.validate_v2_ids(compact, np.asarray(second.read_tokens(), dtype=np.int64))
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


@pytest.fixture
def expanded(
    tmp_path: Path, tokenizer: ByteLevelBPETokenizer
) -> tuple[TokenShardReader, TokenShardReader]:
    """U+0344 x2: 4 original bytes, 8 normalized bytes (covered > byte_count)."""
    writer = TokenShardWriter(tmp_path / "v1", "fixture", "fixture", tokenizer)
    writer.write_documents([original_document("̈́̈́")], True)
    first = TokenShardReader(writer.output_dir)
    record = next(first.iter_document_offsets())
    assert (record["byte_count"], record["covered_bytes"]) == (4, 8)
    return first, convert_v2(writer.output_dir, tmp_path / "v2", tokenizer)


def damage_common(record: dict[str, Any], damage: str) -> None:
    """Damage a field v1 and v2 records share, independent of the span representation."""
    match damage:
        case "negative-bytes":
            record["byte_count"] = -1
        case "float-bytes":
            record["byte_count"] = float(record["byte_count"])
        case "string-bytes":
            record["byte_count"] = str(record["byte_count"])
        case "bool-bytes":
            record["byte_count"] = True
        case "missing-bytes":
            del record["byte_count"]
        case "growth-bound":
            record["byte_count"] = 2  # 8 normalized bytes > 3 * 2 original bytes
        case "zero-original":
            record["byte_count"] = 0
        case "float-covered":
            record["covered_bytes"] = float(record["covered_bytes"])
        case "string-covered":
            record["covered_bytes"] = str(record["covered_bytes"])
        case "missing-covered":
            del record["covered_bytes"]
        case "covered":
            record["covered_bytes"] -= 1
        case "bos-position":
            record["bos_positions"] = [1]
        case "bool-position":
            record["bos_positions"] = [False]
        case "eos-position":
            record["eos_positions"] = [0]
        case "duplicate-eos":
            record["eos_positions"] = record["eos_positions"] * 2
        case "positions-type":
            record["eos_positions"] = None
        case _:
            raise AssertionError(damage)


#: Shared damage -> the refusal both v1 and v2 must give for it.
COMMON_DAMAGES = {
    **dict.fromkeys(
        ["negative-bytes", "float-bytes", "string-bytes", "bool-bytes", "missing-bytes"],
        "byte_count must be a nonnegative integer",
    ),
    **dict.fromkeys(["growth-bound", "zero-original"], "canonical normalization bound"),
    **dict.fromkeys(
        ["float-covered", "string-covered", "missing-covered", "covered"],
        "canonical byte coverage mismatch",
    ),
    **dict.fromkeys(
        ["bos-position", "bool-position", "eos-position", "duplicate-eos", "positions-type"],
        "protected token prefix",
    ),
}


def test_expansion_is_admitted_identically_by_v1_and_v2(
    expanded: tuple[TokenShardReader, TokenShardReader],
) -> None:
    first, second = expanded
    validate_training_index(first)
    validate_training_index(second)
    compact = next(second.iter_document_offsets())
    assert second.with_byte_spans(compact) == next(first.iter_document_offsets())


@pytest.mark.parametrize("damage", COMMON_DAMAGES)
def test_v1_and_v2_refuse_the_same_shared_damage(
    expanded: tuple[TokenShardReader, TokenShardReader], damage: str
) -> None:
    first, second = expanded
    old = copy.deepcopy(next(first.iter_document_offsets()))
    compact = copy.deepcopy(next(second.iter_document_offsets()))
    damage_common(old, damage)
    damage_common(compact, damage)
    with pytest.raises(ValueError, match=COMMON_DAMAGES[damage]):
        validate_v1_spans(old, len(old["token_byte_spans"]))
    with pytest.raises(ValueError, match=COMMON_DAMAGES[damage]):
        second.validate_v2_ids(compact, np.asarray(second.read_tokens(), dtype=np.int64))


def shift(record: dict[str, Any], start: int, delta: int) -> None:
    """Move spans ``start:`` by ``delta`` and keep ``covered_bytes`` at the final end."""
    for span in record["token_byte_spans"][start:]:
        span[0] += delta
        span[1] += delta
    record["covered_bytes"] = record["token_byte_spans"][-1][1]


@pytest.mark.parametrize(
    "damage",
    [
        "not-list",
        "short",
        "tuple-shape",
        "float-end",
        "bool-end",
        "nonzero-origin",
        "negative-origin",
        "gap",
        "overlap",
        "reversed",
        "bos-length",
        "eos-length",
    ],
)
def test_v1_spans_must_be_contiguous_framed_normalized_payloads(
    expanded: tuple[TokenShardReader, TokenShardReader], damage: str
) -> None:
    reason = "structural byte spans" if damage.endswith("-length") else "canonical byte spans"
    first, _ = expanded
    record = copy.deepcopy(next(first.iter_document_offsets()))
    spans = record["token_byte_spans"]
    count = len(spans)
    content = next(i for i, (start, end) in enumerate(spans) if end > start)
    match damage:
        case "not-list":
            record["token_byte_spans"] = {"0": spans[0]}
        case "short":
            spans.pop(content)
        case "tuple-shape":
            spans[content].append(spans[content][1])
        case "float-end":
            spans[content][1] = float(spans[content][1])
        case "bool-end":
            spans[0][1] = False
        case "nonzero-origin":
            shift(record, 0, 1)
        case "negative-origin":
            shift(record, 0, -1)
        case "gap":
            shift(record, content + 1, 1)
        case "overlap":
            shift(record, content + 1, -1)
        case "reversed":
            spans[content].reverse()
        case "bos-length":
            spans[0][1] = 1
            shift(record, 1, 1)
        case "eos-length":
            spans[-1][1] += 1
            record["covered_bytes"] += 1
    with pytest.raises(ValueError, match=reason):
        validate_v1_spans(record, count)


def test_v1_training_index_refuses_a_damaged_span_line(
    expanded: tuple[TokenShardReader, TokenShardReader],
) -> None:
    first, _ = expanded
    path = first.directory / "offsets.jsonl"
    record = json.loads(path.read_bytes())
    shift(record, 2, 1)
    path.write_bytes((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
    with pytest.raises(ValueError, match="canonical byte spans"):
        validate_training_index(first)


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
        "growth-bound",
        "bos-position",
        "eos-position",
        "bos-payload",
        "eos-payload",
        "complete-without-coverage",
    ],
)
def test_normalized_coverage_keeps_other_guards(
    tmp_path: Path, tokenizer: ByteLevelBPETokenizer, damage: str
) -> None:
    writer = TokenShardWriter(tmp_path / "v1", "fixture", "fixture", tokenizer)
    writer.write_documents([original_document("é")], True)
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
        case "growth-bound":
            record["byte_count"] = 0  # 2 normalized bytes from an empty original
        case "bos-position":
            record["bos_positions"] = [1]
        case "eos-position":
            record["eos_positions"] = [0]
        case "bos-payload" | "eos-payload":
            ids[0 if damage == "bos-payload" else -1] = ids[1]
            record["covered_bytes"] = int(reader.token_byte_table()[ids].sum())
        case "complete-without-coverage":
            ids = np.asarray([tokenizer.bos_token_id, tokenizer.eos_token_id], dtype=np.int64)
            record.update(token_count=2, covered_bytes=0, eos_positions=[1])
    with pytest.raises(ValueError):
        reader.validate_v2_ids(record, ids)
