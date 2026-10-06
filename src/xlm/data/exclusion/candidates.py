"""Read-only contamination-matcher candidates over the frozen C05 protected index.

Root cause being audited (c05-matcher-v3/v4, ``streaming.patterns``): every rendered
variant of at most ``span_tokens`` (13) normalized tokens is emitted WHOLE as one exact
pattern when it passes its kind's floor. The frozen floors are prompt 4 tokens / 16
characters / 3 distinct two-letter tokens, sentence 3 / 12 / 3 and item fallback
4 / 16 / 3 (answer and combined: 8 / 40 / 5). The compiled matcher then marks a document
on ANY contiguous normalized token-boundary occurrence of ANY pattern anywhere in the
document, and one hit excludes the whole lineage family. A 4-token prompt therefore
behaves like a common English 4-gram whose hit probability grows with document length.

Candidates here are evaluated WITHOUT rebuilding the protected index:

* ``standalone`` patterns: frozen patterns that pass the candidate's floor for at least
  one of their provenance kinds. Every frozen pattern of 13+ tokens passes any floor of
  at most 13 tokens, and a whole short variant passes exactly when a rebuilt index would
  emit it, so this is the pattern set a rebuild would emit for those kinds; a rebuilt
  c05-matcher-v4 could additionally emit NEW whole-item fallback patterns for items whose
  normal signatures all vanish. That is not evaluated (stated limitation; it can only
  raise recall).
* an optional ``same-item-colocated-pair-v1`` rule: a document also counts as a hit when
  two DIFFERENT non-standalone (short) patterns of ONE benchmark item occur without
  overlapping, within ``window_tokens`` normalized tokens, covering at least
  ``min_covered_tokens`` tokens together (for example both sentences of a BLiMP pair, or
  a short question next to its short options). A single common phrase, or the same
  phrase repeated, never qualifies.

Nothing here prints or returns benchmark text, tokens, provenance or item identities:
results are counts, kinds, length buckets, task/split names and render-slot names.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

import numpy as np
import numpy.typing as npt
from pydantic import Field

from xlm.data.dedup.matchview import match_tokens
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import compact
from xlm.data.exclusion.compact import CANDIDATE_BATCH, CompactExactMatcher
from xlm.data.exclusion.policy import C05Error, FrozenModel, Informativeness, MatcherPolicyV4
from xlm.data.exclusion.streaming import composite

KINDS: Final = ("prompt", "sentence", "answer", "combined", "item_fallback")
KIND_BIT: Final = {kind: 1 << n for n, kind in enumerate(KINDS)}
NAME_RE: Final = re.compile(r"^[a-z0-9_]{1,32}$")
I64 = npt.NDArray[np.int64]

# The frozen c05-matcher-v4 floors (production policy-value.json) and the uniform floor
# the candidates raise short kinds to: the frozen answer/combined floor. Exact-overlap
# contamination screens in the literature use the same order of span (8-13 word n-grams,
# or ~50-character substrings) for exactly this specificity reason.
V4: Final = MatcherPolicyV4()
FROZEN_FLOORS: Final[Mapping[str, Informativeness]] = {
    "prompt": V4.prompt,
    "sentence": V4.sentence,
    "answer": V4.answer,
    "combined": V4.combined,
    "item_fallback": V4.fallback,
}
FLOOR8: Final = Informativeness(tokens=8, characters=40, distinct=5)


class PairRule(FrozenModel):
    """Same-item co-located short-pattern pair (see the module docstring)."""

    version: Literal["same-item-colocated-pair-v1"] = "same-item-colocated-pair-v1"
    window_tokens: int = Field(default=64, ge=2, le=4096)
    min_covered_tokens: int = Field(default=8, ge=2, le=64)


class Candidate(FrozenModel):
    """One data-only matcher candidate. ``floors=None``: every frozen pattern stands alone."""

    name: str
    description: str
    floors: dict[str, Informativeness] | None = None
    pair: PairRule | None = None

    def identity(self) -> str:
        return canonical.digest(self.model_dump(mode="json"))


CANDIDATES: Final[tuple[Candidate, ...]] = (
    Candidate(
        name="current",
        description="frozen c05-matcher-v4 as produced: every index pattern triggers alone",
    ),
    Candidate(
        name="prompt8",
        description=(
            "diagnostic: prompt and item-fallback patterns need the answer/combined floor "
            "(8 tokens, 40 characters, 5 distinct); sentence floor unchanged"
        ),
        floors={**FROZEN_FLOORS, "prompt": FLOOR8, "item_fallback": FLOOR8},
    ),
    Candidate(
        name="floor8",
        description="every pattern kind needs 8 tokens, 40 characters, 5 distinct to trigger",
        floors=dict.fromkeys(KINDS, FLOOR8),
    ),
    Candidate(
        name="floor8_pair",
        description=(
            "floor8, plus two different short patterns of one benchmark item co-located "
            "without overlap within 64 tokens covering at least 8 tokens"
        ),
        floors=dict.fromkeys(KINDS, FLOOR8),
        pair=PairRule(),
    ),
)


def candidate_named(name: str) -> Candidate:
    for candidate in CANDIDATES:
        if candidate.name == name:
            return candidate
    raise C05Error("unknown matcher candidate")


# -- pattern features ------------------------------------------------------------------------


def features(tokens: Sequence[str]) -> tuple[int, int, int]:
    """(tokens, characters of the space-joined view, distinct two-letter tokens).

    Identical to the quantities ``streaming.informative`` compares with a floor.
    """
    return (
        len(tokens),
        len(" ".join(tokens)),
        len({t for t in tokens if sum(c.isalpha() for c in t) >= 2}),
    )


def standalone_mask(
    length: I64, characters: I64, distinct: I64, kinds: npt.NDArray[np.uint8], candidate: Candidate
) -> npt.NDArray[np.bool_]:
    """Patterns that trigger alone under ``candidate`` (vectorized over a pattern table)."""
    if candidate.floors is None:
        return np.ones(length.size, dtype=bool)
    keep = np.zeros(length.size, dtype=bool)
    for kind, floor in candidate.floors.items():
        has = (kinds & KIND_BIT[kind]) != 0
        keep |= (
            has
            & (length >= floor.tokens)
            & (characters >= floor.characters)
            & (distinct >= floor.distinct)
        )
    return keep


def length_bucket(tokens: int) -> str:
    if tokens <= 2:
        return "1-2"
    if tokens <= 5:
        return str(tokens)
    if tokens <= 7:
        return "6-7"
    if tokens <= 12:
        return "8-12"
    if tokens == 13:
        return "13"
    return ">13"


# -- identities of compiled patterns ---------------------------------------------------------


def word_encodings(words: Sequence[str]) -> list[bytes]:
    """Canonical JSON string encoding of each vocabulary word (for bulk identities)."""
    return [json.dumps(w, ensure_ascii=False).encode("utf-8") for w in words]


def identities(
    encoded: Sequence[bytes], tokens: npt.NDArray[np.uint32], offsets: npt.NDArray[Any]
) -> bytes:
    """32-byte ``canonical.digest(tokens)`` of each pattern, concatenated.

    ``canonical_bytes`` of a list of strings is ``[`` + comma-joined JSON strings + ``]``
    (no spaces); ``tokens`` holds 1-based vocabulary ids as in the compiled matcher.
    """
    out = bytearray()
    bounds = np.asarray(offsets, dtype=np.int64).tolist()
    ids = np.asarray(tokens, dtype=np.int64).tolist()
    for begin, end in zip(bounds[:-1], bounds[1:], strict=True):
        body = b",".join(encoded[i - 1] for i in ids[begin:end])
        out += hashlib.sha256(b"[" + body + b"]").digest()
    return bytes(out)


# -- all exact occurrences -------------------------------------------------------------------


class OccurrenceMatcher(CompactExactMatcher):
    """The production compiled matcher, reporting EVERY exact occurrence.

    Uses the same verified arrays and anchor probe as ``match``; it only drops the early
    stop after the historical best hit. Each occurrence of pattern P at start s has its
    unique anchor (P, o) at s + o, so every occurrence is produced exactly once and
    nothing is accepted without exact id-by-id equality.
    """

    def words(self) -> list[str]:
        """Protected vocabulary in id order (private; never reported)."""
        return list(self._words)

    def flat_patterns(self) -> tuple[npt.NDArray[np.uint32], I64]:
        """(flattened 1-based token ids, pattern offsets) of every unique pattern."""
        return self._tokens, np.asarray(self._offsets, dtype=np.int64)

    def occurrences(self, tokens: Sequence[str]) -> tuple[I64, I64, I64]:
        """(compiled pattern index, start, exclusive end), ordered by (end, -length)."""
        empty = np.zeros(0, np.int64)
        n = len(tokens)
        if n < self._minimum:
            return empty, empty, empty
        lookup = self._vocabulary.get
        ids = np.fromiter((lookup(token, 0) for token in tokens), dtype=np.uint32, count=n)
        unknown = np.zeros(n + 1, np.int64)
        np.cumsum(ids == 0, out=unknown[1:])
        if unknown[-1] == n:
            return empty, empty, empty
        mixed = self._mixed[ids]
        window = np.zeros(n, np.uint64)
        found_at: list[I64] = []
        found_bucket: list[I64] = []
        for length in range(1, self._q + 1):
            width = n - length + 1
            if width <= 0:
                break
            window = window[:width] * compact._B + mixed[length - 1 : length - 1 + width]
            span = self._ranges.get(length)
            if span is None:
                continue
            clean = np.flatnonzero(unknown[length : length + width] == unknown[:width])
            if not clean.size:
                continue
            probe = window[clean] & self._mask if self._masked else window[clean]
            low, high = span
            keys = self._keys[low:high]
            slot = np.minimum(np.searchsorted(keys, probe), high - low - 1)
            hit = keys[slot] == probe
            if hit.any():
                found_at.append(clean[hit])
                found_bucket.append(slot[hit].astype(np.int64) + low)
        if not found_at:
            return empty, empty, empty
        at = np.concatenate(found_at)
        bucket = np.concatenate(found_bucket)
        entry_low = self._starts[bucket].astype(np.int64)
        sizes = self._starts[bucket + 1].astype(np.int64) - entry_low
        cumulative = np.cumsum(sizes)
        self._ensure_powers(n + 1)
        prefix = np.zeros(n + 1, np.uint64)
        np.cumsum(mixed * self._inverse[:n], out=prefix[1:])
        patterns: list[I64] = []
        starts: list[I64] = []
        ends: list[I64] = []
        i, count = 0, int(at.size)
        while i < count:
            base = int(cumulative[i - 1]) if i else 0
            j = max(int(np.searchsorted(cumulative, base + CANDIDATE_BATCH, side="right")), i + 1)
            size = sizes[i:j]
            owner = np.repeat(np.arange(j - i), size)
            entry = entry_low[i:j][owner] + (
                np.arange(int(size.sum())) - (np.cumsum(size) - size)[owner]
            )
            pattern = self._patterns[entry].astype(np.int64)
            start = at[i:j][owner] - self._anchor_offsets[entry].astype(np.int64)
            begin = self._offsets[pattern].astype(np.int64)
            end = start + (self._offsets[pattern + 1].astype(np.int64) - begin)
            keep = np.flatnonzero((start >= 0) & (end <= n))
            pattern, start, begin, end = pattern[keep], start[keep], begin[keep], end[keep]
            keep = np.flatnonzero(unknown[end] == unknown[start])
            pattern, start, begin, end = pattern[keep], start[keep], begin[keep], end[keep]
            full = (prefix[end] - prefix[start]) * self._power[end - 1]
            if self._masked:
                full &= self._mask
            keep = np.flatnonzero(full == self._hashes[pattern])
            pattern, start, begin, end = pattern[keep], start[keep], begin[keep], end[keep]
            exact = np.fromiter(
                (
                    np.array_equal(ids[s:e], self._tokens[b : b + (e - s)])
                    for s, e, b in zip(start.tolist(), end.tolist(), begin.tolist(), strict=True)
                ),
                dtype=bool,
                count=int(pattern.size),
            )
            patterns.append(pattern[exact])
            starts.append(start[exact])
            ends.append(end[exact])
            i = j
        pattern = np.concatenate(patterns)
        start = np.concatenate(starts)
        end = np.concatenate(ends)
        order = np.lexsort((start - end, end))  # (end, -length): the historical first hit
        return pattern[order], start[order], end[order]


# -- per-document rule evaluation ------------------------------------------------------------


@dataclass(frozen=True)
class ShortItems:
    """Benchmark items of the short (non-standalone) patterns of one pair candidate.

    ``patterns`` are sorted compiled pattern indices; items of ``patterns[k]`` are
    ``items[starts[k]:starts[k + 1]]`` (opaque item numbers, never reported).
    """

    patterns: npt.NDArray[np.int64]
    starts: npt.NDArray[np.int64]
    items: npt.NDArray[np.int64]


def colocated_pair(pattern: I64, start: I64, end: I64, short: ShortItems, rule: PairRule) -> bool:
    """``same-item-colocated-pair-v1`` over one document's occurrences."""
    if pattern.size < 2 or short.patterns.size == 0:
        return False
    slot = np.searchsorted(short.patterns, pattern)
    slot = np.minimum(slot, short.patterns.size - 1)
    picked = np.flatnonzero(short.patterns[slot] == pattern)
    if picked.size < 2:
        return False
    k = slot[picked]
    counts = short.starts[k + 1] - short.starts[k]
    owner = np.repeat(picked, counts)
    offset = np.arange(int(counts.sum())) - np.repeat(np.cumsum(counts) - counts, counts)
    item = short.items[np.repeat(short.starts[k], counts) + offset]
    order = np.lexsort((start[owner], item))
    item, owner = item[order], owner[order]
    bounds = np.flatnonzero(np.r_[True, item[1:] != item[:-1], True])
    window, cover = rule.window_tokens, rule.min_covered_tokens
    for lo, hi in zip(bounds[:-1].tolist(), bounds[1:].tolist(), strict=True):
        if hi - lo < 2:
            continue
        members = owner[lo:hi].tolist()
        for a in range(len(members)):
            i = members[a]
            si, ei, pi = int(start[i]), int(end[i]), int(pattern[i])
            for b in range(a + 1, len(members)):
                j = members[b]
                sj, ej = int(start[j]), int(end[j])
                if sj - si > window:
                    break
                if (
                    int(pattern[j]) != pi
                    and sj >= ei
                    and ej - si <= window
                    and (ei - si) + (ej - sj) >= cover
                ):
                    return True
    return False


def candidate_hits(
    pattern: I64,
    start: I64,
    end: I64,
    standalone: npt.NDArray[np.uint8],
    short: Sequence[ShortItems | None],
    candidates: Sequence[Candidate] = CANDIDATES,
) -> int:
    """Bitmask over ``candidates``: bit c set when the document is a hit under candidate c.

    ``standalone[p]`` holds bit c when compiled pattern p triggers alone under candidate c.
    """
    bits = 0
    present = int(np.bitwise_or.reduce(standalone[pattern])) if pattern.size else 0
    for c, candidate in enumerate(candidates):
        if present >> c & 1:
            bits |= 1 << c
        elif candidate.pair is not None:
            table = short[c]
            if table is not None and colocated_pair(pattern, start, end, table, candidate.pair):
                bits |= 1 << c
    return bits


# -- slot-labelled rendering (mirror of streaming.render; equality is tested) ---------------


def _text(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise C05Error("benchmark text field absent or invalid")
    return value


def _options(row: Mapping[str, Any], name: str) -> list[str]:
    value = row.get(name)
    if isinstance(value, dict):
        value = value.get("text")
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(x, str) or not x.strip() for x in value)
    ):
        raise C05Error("benchmark answer list absent or invalid")
    return list(value)


def _hellaswag_pre(text: str) -> str:
    return re.sub(r"\[.*?\]", "", text.strip().replace(" [title]", ". ")).replace("  ", " ")


def render_slots(task: str, row: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """``streaming.render`` with a content-free render-slot label: (kind, slot, text)."""
    if task == "blimp":
        return [
            ("sentence", "blimp.sentence_good", _text(row, "sentence_good")),
            ("sentence", "blimp.sentence_bad", _text(row, "sentence_bad")),
        ]
    prompts: list[tuple[str, str]]
    options: list[tuple[str, str]]
    if task == "arc_easy":
        prompts = [("arc.question", _text(row, "question"))]
        options = [("arc.choice", c) for c in _options(row, "choices")]
    elif task == "piqa":
        prompts = [("piqa.goal", _text(row, "goal"))]
        options = [("piqa.sol1", _text(row, "sol1")), ("piqa.sol2", _text(row, "sol2"))]
    elif task == "hellaswag":
        prompts = [("hellaswag.ctx", _text(row, "ctx"))] if "ctx" in row else []
        if "ctx_a" in row and "ctx_b" in row:
            a = _text(row, "ctx_a")
            b = row["ctx_b"]
            if not isinstance(b, str):
                raise C05Error("HellaSwag context suffix invalid")
            prompts.extend(
                [
                    ("hellaswag.ctx_a", a),
                    ("hellaswag.ctx_b", b),
                    ("hellaswag.ctx_a+ctx_b", f"{a} {b}"),
                ]
            )
        if not prompts:
            raise C05Error("HellaSwag context missing")
        endings = [("hellaswag.ending", e) for e in _options(row, "endings")]
        prompts.extend((slot + "/pre", _hellaswag_pre(p)) for slot, p in list(prompts))
        activity = row.get("activity_label")
        if activity is not None:
            if not isinstance(activity, str):
                raise C05Error("HellaSwag activity label invalid")
            prompts.extend(
                ("activity+" + slot + "/pre", _hellaswag_pre(activity + ": " + p))
                for slot, p in list(prompts)
            )
        seen: dict[str, str] = {}
        for slot, text in [
            *endings,
            *(("hellaswag.ending/pre", _hellaswag_pre(e)) for _, e in endings),
        ]:
            seen.setdefault(text, slot)
        options = [(slot, text) for text, slot in seen.items()]
        kept: dict[str, str] = {}
        for slot, text in prompts:
            if text.strip():
                kept.setdefault(text, slot)
        prompts = [(slot, text) for text, slot in kept.items()]
    else:
        raise C05Error("unsupported benchmark task")
    return (
        [("prompt", s, p) for s, p in prompts]
        + [("answer", s, a) for s, a in options]
        + [("combined", f"{ps}+{s}", f"{p} {a}") for ps, p in prompts for s, a in options]
        + [("combined", f"{ps}+all", " ".join([p, *(a for _, a in options)])) for ps, p in prompts]
    )


#: Render slots that are a complete published content field; the others are dataset
#: sub-fields (HellaSwag ``ctx_a``/``ctx_b`` split one context), lm-eval preprocessing
#: or composites built by the renderer.
COMPLETE_FIELD_SLOTS: Final = frozenset(
    {
        "arc.question",
        "arc.choice",
        "piqa.goal",
        "piqa.sol1",
        "piqa.sol2",
        "blimp.sentence_good",
        "blimp.sentence_bad",
        "hellaswag.ctx",
        "hellaswag.ending",
    }
)


def slot_class(slot: str) -> str:
    if slot in COMPLETE_FIELD_SLOTS:
        return "complete_published_field"
    if slot in ("hellaswag.ctx_a", "hellaswag.ctx_b"):
        return "published_subfield_fragment"
    if "+" in slot:
        return "renderer_composite"
    return "preprocessed_variant"


def injection_forms(task: str, row: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Exact-copy forms injected into neutral filler for the recall audit: (form, text).

    ``item_composite``: the label-free whole item in publisher field order (c05 v4
    ``item-composite-v1``). ``prompt_only``: the complete prompt field alone (no options).
    ``single_sentence``: one BLiMP sentence alone (the grammatical one).
    """
    forms = [("item_composite", composite(task, row))]
    if task == "blimp":
        forms.append(("single_sentence", _text(row, "sentence_good")))
    elif task == "arc_easy":
        forms.append(("prompt_only", _text(row, "question")))
    elif task == "piqa":
        forms.append(("prompt_only", _text(row, "goal")))
    elif task == "hellaswag":
        if "ctx" in row:
            forms.append(("prompt_only", _text(row, "ctx")))
        else:
            forms.append(("prompt_only", f"{_text(row, 'ctx_a')} {row['ctx_b']}".strip()))
    return forms


FILLER: Final = "\x00c05-candidate-filler"


def injected(text: str, vocabulary: Mapping[str, int], pad: int = 16) -> list[str]:
    """Normalized ``text`` between ``pad`` filler tokens that no pattern can contain."""
    filler = FILLER
    while filler in vocabulary:
        filler += "x"
    return [filler] * pad + match_tokens(text) + [filler] * pad


# -- index-level recall audit ----------------------------------------------------------------


def item_recall(
    items: I64,
    rows: I64,
    flags: npt.NDArray[np.uint8],
    short_flags: npt.NDArray[np.uint8],
    item_group: I64,
    group_names: Sequence[str],
    candidates: Sequence[Candidate] = CANDIDATES,
) -> dict[str, Any]:
    """Per candidate x (task/split group): items with a standalone signature, items whose
    only evidence is >= 2 short patterns (pair-capable under a pair rule), unsigned items.

    ``items``/``rows`` hold one entry per (benchmark item, pattern-table row), sorted by
    item; ``flags[row]`` has bit c when the row stands alone under candidate c and
    ``short_flags[row]`` bit c when it is a short pattern of pair candidate c. Items with
    no pattern at all count as unsigned.
    """
    groups = len(group_names)
    totals = np.bincount(item_group, minlength=groups)
    if items.size:
        bounds = np.flatnonzero(np.r_[True, items[1:] != items[:-1]])
        owner = item_group[items[bounds]]
        any_flags = np.bitwise_or.reduceat(flags[rows], bounds)
    tallies: dict[str, Any] = {}
    for c, candidate in enumerate(candidates):
        alone = np.zeros(groups, np.int64)
        pair = np.zeros(groups, np.int64)
        if items.size:
            standalone = ((any_flags >> c) & 1).astype(bool)
            alone = np.bincount(owner[standalone], minlength=groups)
            if candidate.pair is not None:
                shorts = np.add.reduceat(((short_flags[rows] >> c) & 1).astype(np.int64), bounds)
                pair = np.bincount(owner[~standalone & (shorts >= 2)], minlength=groups)
        tallies[candidate.name] = {
            g: {
                "items": int(totals[n]),
                "standalone": int(alone[n]),
                "pair_only": int(pair[n]),
                "unsigned": int(totals[n] - alone[n] - pair[n]),
            }
            for n, g in enumerate(group_names)
        }
    return tallies


BUCKETS: Final = ("1-2", "3", "4", "5", "6-7", "8-12", "13", ">13")


def bucket_codes(length: I64) -> I64:
    """Index into :data:`BUCKETS` of each token length (vectorized ``length_bucket``)."""
    return np.searchsorted(np.asarray([2, 3, 4, 5, 7, 12, 13]), length, side="left").astype(
        np.int64
    )
