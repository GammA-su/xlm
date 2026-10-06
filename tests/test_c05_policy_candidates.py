"""Matcher candidates of the C05 contamination-policy audit: unit behaviour.

Authored patterns and documents only; no protected material, real corpus or network.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import candidates as cand
from xlm.data.exclusion.compact import CompactExactMatcher, compile_index
from xlm.data.exclusion.policy import Informativeness, MatcherPolicyV4
from xlm.data.exclusion.streaming import informative, render

WORDS = [f"w{n}" for n in range(40)] + ["the", "a", "of", "and", "to", "in"]


def compiled(tmp_path: Path, patterns: list[list[str]]) -> tuple[Path, str, int]:
    index = tmp_path / "index.jsonl"
    index.write_bytes(
        b"".join(
            canonical.canonical_bytes({"provenance": [f"r{n}:prompt"], "tokens": p}) + b"\n"
            for n, p in enumerate(patterns)
        )
    )
    raw = index.read_bytes()
    sha, size = hashlib.sha256(raw).hexdigest(), len(raw)
    directory = tmp_path / "matcher"
    directory.mkdir()
    compile_index(
        index,
        directory,
        index_sha256=sha,
        index_bytes=size,
        max_record=1 << 20,
        max_records=None,
        max_logical_nodes=None,
    )
    return directory, sha, size


def brute(tokens: list[str], patterns: list[tuple[str, ...]]) -> set[tuple[int, int, int]]:
    found = set()
    for p, pattern in enumerate(patterns):
        n = len(pattern)
        for s in range(len(tokens) - n + 1):
            if tuple(tokens[s : s + n]) == pattern:
                found.add((p, s, s + n))
    return found


def test_occurrences_equal_brute_force_and_first_equals_historical_match(tmp_path: Path) -> None:
    rng = random.Random(7)
    raw = [[rng.choice(WORDS) for _ in range(rng.randint(1, 9))] for _ in range(300)]
    raw += [["w1", "w2"], ["w1", "w2", "w3"], ["w2", "w3"], ["w1", "w2", "w3", "w4", "w5", "w6"]]
    directory, sha, size = compiled(tmp_path, raw)
    with cand.OccurrenceMatcher(directory, index_sha256=sha, index_bytes=size) as matcher:
        unique = int(matcher.manifest["counts"]["unique_patterns"])
        by_tokens = {tuple(matcher.tokens(p)): p for p in range(unique)}
        patterns = [tuple(matcher.tokens(p)) for p in range(unique)]
        historical = CompactExactMatcher(directory, index_sha256=sha, index_bytes=size)
        try:
            for trial in range(150):
                doc = [rng.choice(WORDS + ["zz", "qq"]) for _ in range(rng.randint(0, 120))]
                if trial % 10 == 0:
                    doc += ["w1", "w2", "w3", "w4", "w5", "w6"]
                pattern, start, end = matcher.occurrences(doc)
                got = set(zip(pattern.tolist(), start.tolist(), end.tolist(), strict=True))
                assert got == brute(doc, patterns)
                assert len(got) == pattern.size  # each occurrence exactly once
                best = historical.match(doc)
                if pattern.size:
                    assert matcher.identity(int(pattern[0])) == best
                else:
                    assert best is None
            assert len(by_tokens) == unique
        finally:
            historical.close()


def test_identities_equal_canonical_digests(tmp_path: Path) -> None:
    raw = [["a", "b"], ["é", '"q"', "x\\y"], ["w1", "w2", "w3"]]
    directory, sha, size = compiled(tmp_path, raw)
    with cand.OccurrenceMatcher(directory, index_sha256=sha, index_bytes=size) as matcher:
        tokens, offsets = matcher.flat_patterns()
        out = cand.identities(cand.word_encodings(matcher.words()), tokens, offsets)
        unique = offsets.size - 1
        assert len(out) == 32 * unique
        for p in range(unique):
            assert out[32 * p : 32 * p + 32].hex() == canonical.digest(matcher.tokens(p))


def test_standalone_mask_equals_the_frozen_informative_rule() -> None:
    rng = random.Random(3)
    rows = [
        [rng.choice(WORDS + ["x", "7", "ab"]) for _ in range(rng.randint(1, 16))]
        for _ in range(400)
    ]
    feats = np.asarray([cand.features(r) for r in rows], dtype=np.int64)
    kinds = np.asarray([cand.KIND_BIT[rng.choice(cand.KINDS)] for _ in rows], dtype=np.uint8)
    for candidate in cand.CANDIDATES:
        mask = cand.standalone_mask(feats[:, 0], feats[:, 1], feats[:, 2], kinds, candidate)
        for row, kind_bits, got in zip(rows, kinds.tolist(), mask.tolist(), strict=True):
            if candidate.floors is None:
                assert got is True
                continue
            kind = next(k for k in cand.KINDS if cand.KIND_BIT[k] == kind_bits)
            assert got == informative(tuple(row), candidate.floors[kind])


def test_frozen_floors_are_the_production_v4_floors_and_floor8_is_answer_floor() -> None:
    v4 = MatcherPolicyV4()
    assert cand.FROZEN_FLOORS["prompt"] == Informativeness(tokens=4, characters=16, distinct=3)
    assert cand.FROZEN_FLOORS["sentence"] == Informativeness(tokens=3, characters=12, distinct=3)
    assert cand.FROZEN_FLOORS["item_fallback"] == v4.fallback
    assert cand.FLOOR8 == v4.answer == v4.combined
    assert [c.name for c in cand.CANDIDATES] == ["current", "prompt8", "floor8", "floor8_pair"]
    assert len({c.identity() for c in cand.CANDIDATES}) == len(cand.CANDIDATES)


def pair(occurrences: list[tuple[int, int, int]], items: dict[int, list[int]]) -> bool:
    pattern = np.asarray([o[0] for o in occurrences], dtype=np.int64)
    start = np.asarray([o[1] for o in occurrences], dtype=np.int64)
    end = np.asarray([o[2] for o in occurrences], dtype=np.int64)
    keys = sorted(items)
    starts = np.r_[0, np.cumsum([len(items[k]) for k in keys])].astype(np.int64)
    flat = np.asarray([i for k in keys for i in items[k]], dtype=np.int64)
    table = cand.ShortItems(np.asarray(keys, np.int64), starts, flat)
    return cand.colocated_pair(pattern, start, end, table, cand.PairRule())


@pytest.mark.parametrize(
    ("occurrences", "items", "expected"),
    [
        ([(1, 0, 4), (2, 5, 9)], {1: [10], 2: [10]}, True),  # both sentences of one item
        ([(1, 0, 4), (2, 5, 9)], {1: [10], 2: [11]}, False),  # different items
        ([(1, 0, 4), (1, 30, 34)], {1: [10]}, False),  # the same phrase twice
        ([(1, 0, 4), (2, 2, 6)], {1: [10], 2: [10]}, False),  # overlapping
        ([(1, 0, 4), (2, 70, 74)], {1: [10], 2: [10]}, False),  # beyond the 64-token window
        ([(1, 0, 3), (2, 3, 7)], {1: [10], 2: [10]}, False),  # covers 7 < 8 tokens
        ([(1, 0, 4), (3, 4, 8)], {1: [10], 2: [10]}, False),  # pattern 3 is not short
        ([(1, 0, 4), (2, 60, 64)], {1: [10, 12], 2: [12]}, True),  # shared item, edge of window
    ],
)
def test_same_item_colocated_pair_rule(
    occurrences: list[tuple[int, int, int]], items: dict[int, list[int]], expected: bool
) -> None:
    assert pair(occurrences, items) is expected


HELLASWAG: dict[str, Any] = {
    "ctx_a": "A man [title] is on a ladder.",
    "ctx_b": "then the man",
    "ctx": "A man [title] is on a ladder. then the man",
    "activity_label": "Painting",
    "endings": ["paints [step] the wall.", "paints the wall.", "falls down slowly."],
}


@pytest.mark.parametrize(
    ("task", "row"),
    [
        ("hellaswag", HELLASWAG),
        ("hellaswag", {k: v for k, v in HELLASWAG.items() if k != "ctx"}),
        ("hellaswag", {"ctx": "One ctx.", "endings": ["a b", "c d"]}),
        ("hellaswag", {"ctx_a": "Lead.", "ctx_b": "", "endings": ["x y", "x y"]}),
        ("arc_easy", {"question": "Why?", "choices": {"text": ["a", "b", "a"]}}),
        ("piqa", {"goal": "Do it", "sol1": "one", "sol2": "two"}),
        ("blimp", {"sentence_good": "Owls sing.", "sentence_bad": "Owls sings."}),
    ],
)
def test_render_slots_mirror_the_frozen_renderer(task: str, row: dict[str, Any]) -> None:
    slots = cand.render_slots(task, row)
    assert [(k, t) for k, _, t in slots] == render(task, row)
    assert all(cand.slot_class(s) for _, s, _ in slots)


def test_slot_classes() -> None:
    assert cand.slot_class("hellaswag.ctx_b") == "published_subfield_fragment"
    assert cand.slot_class("piqa.goal") == "complete_published_field"
    assert cand.slot_class("hellaswag.ctx_b/pre") == "preprocessed_variant"
    assert cand.slot_class("arc.question+arc.choice") == "renderer_composite"


def test_item_recall_tallies() -> None:
    # Three items in two groups; patterns rows 0..3.
    # item 0: row 0 (standalone for every candidate)  item 1: rows 1, 2 (short)  item 2: row 3.
    items = np.asarray([0, 1, 1, 2], dtype=np.int64)
    rows = np.asarray([0, 1, 2, 3], dtype=np.int64)
    every = (1 << len(cand.CANDIDATES)) - 1
    flags = np.asarray([every, 1, 1, 1], dtype=np.uint8)  # rows 1-3 stand alone only for "current"
    short = np.asarray([0, 8, 8, 8], dtype=np.uint8)  # short for floor8_pair (bit 3)
    group = np.asarray([0, 0, 1, 1], dtype=np.int64)  # item 3 has no pattern at all
    tallies = cand.item_recall(items, rows, flags, short, group, ["g0", "g1"])
    assert tallies["current"]["g0"] == {"items": 2, "standalone": 2, "pair_only": 0, "unsigned": 0}
    assert tallies["current"]["g1"] == {"items": 2, "standalone": 1, "pair_only": 0, "unsigned": 1}
    assert tallies["floor8"]["g0"] == {"items": 2, "standalone": 1, "pair_only": 0, "unsigned": 1}
    assert tallies["floor8_pair"]["g0"] == {
        "items": 2,
        "standalone": 1,
        "pair_only": 1,
        "unsigned": 0,
    }
    assert tallies["floor8_pair"]["g1"]["unsigned"] == 2


def test_bucket_codes_equal_length_bucket() -> None:
    lengths = np.arange(1, 40, dtype=np.int64)
    codes = cand.bucket_codes(lengths)
    assert [cand.BUCKETS[c] for c in codes.tolist()] == [
        cand.length_bucket(n) for n in range(1, 40)
    ]


def test_injection_forms_and_filler(tmp_path: Path) -> None:
    forms = dict(cand.injection_forms("blimp", {"sentence_good": "A b.", "sentence_bad": "A c."}))
    assert forms == {"item_composite": "A b. A c.", "single_sentence": "A b."}
    tokens = cand.injected("Hello, World", {cand.FILLER: 1})
    assert tokens[16:18] == ["hello", "world"] and cand.FILLER not in tokens


@pytest.mark.parametrize("trailing", [True, False])
def test_wanted_span_rereads_exactly_the_wanted_rows(trailing: bool, tmp_path: Path) -> None:
    from xlm.data.exclusion.counterfactual import AuditError, LineSpan, wanted_span

    head = b"skipped line\n"
    rows = [f"row{n}".encode() * (n % 3 + 1) for n in range(7)]
    data = b"\n".join(rows) + (b"\n" if trailing else b"")
    path = tmp_path / "file.jsonl"
    path.write_bytes(head + data)
    wanted = np.asarray([0, 3, 6, 9, 12], dtype=np.int64)  # absolute rows; block starts at 3
    count, picked, at, starts, ends = wanted_span(data, 3, len(head), wanted)
    assert count == 7
    assert picked == [3, 6, 9]
    status = path.stat()
    span = LineSpan(str(path), status.st_size, status.st_mtime_ns, at, tuple(starts), tuple(ends))
    assert span.lines() == [rows[0], rows[3], rows[6]]
    assert wanted_span(data, 3, 0, np.asarray([1, 20], dtype=np.int64))[1] == []
    path.write_bytes(head + data + b"x")
    with pytest.raises(AuditError):
        span.lines()
