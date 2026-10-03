"""Immutable token Aho-Corasick matcher; protected patterns never enter reports.

Memory is proportional to the explicitly capped benchmark automaton and one
bounded document, never corpus membership. Corpus frequency cannot mutate it.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from xlm.data.dedup.matchview import match_tokens
from xlm.data.evidence_v2.canonical import digest, loads_bytes_strict
from xlm.data.exclusion.policy import C05Error, Informativeness, MatcherPolicy, MatcherPolicyV4


@dataclass(frozen=True)
class Pattern:
    tokens: tuple[str, ...]
    provenance: tuple[str, ...]


def index_record(raw: bytes, max_record: int) -> Pattern:
    """One strict ``protected-pattern-jsonl-v2`` line; shared by every matcher backend."""
    if len(raw) > max_record:
        raise C05Error("index record ceiling")
    item = loads_bytes_strict(raw)
    if not isinstance(item, dict) or set(item) != {"tokens", "provenance"}:
        raise C05Error("index record schema")
    if any(
        not isinstance(item[k], list)
        or not item[k]
        or any(not isinstance(s, str) or not s for s in item[k])
        for k in ("tokens", "provenance")
    ):
        raise C05Error("index record values")
    return Pattern(tuple(item["tokens"]), tuple(item["provenance"]))


def _string(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise C05Error("benchmark text field absent or invalid")
    return value


def _answers(row: Mapping[str, Any], name: str) -> list[str]:
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


def render(task: str, row: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Task render v3; all options plus raw/0.4.13-preprocessed HellaSwag variants."""

    def string(name: str) -> str:
        return _string(row, name)

    def answers(name: str) -> list[str]:
        return _answers(row, name)

    if task == "blimp":
        return [("sentence", string("sentence_good")), ("sentence", string("sentence_bad"))]
    if task == "arc_easy":
        prompts, options = [string("question")], answers("choices")
    elif task == "piqa":
        prompts, options = [string("goal")], [string("sol1"), string("sol2")]
    elif task == "hellaswag":
        prompts = [string("ctx")] if "ctx" in row else []
        if "ctx_a" in row and "ctx_b" in row:
            a = string("ctx_a")
            b = row["ctx_b"]
            if not isinstance(b, str):
                raise C05Error("HellaSwag context suffix invalid")
            prompts.extend([a, b, f"{a} {b}"])
        if not prompts:
            raise C05Error("HellaSwag context missing")
        options = answers("endings")

        # lm-eval 0.4.13 hellaswag/utils.py preprocessing, inspected offline.
        # Retain raw variants too. No labels are read or selected.
        def preprocess(text: str) -> str:
            return re.sub(r"\[.*?\]", "", text.strip().replace(" [title]", ". ")).replace("  ", " ")

        prompts.extend(preprocess(p) for p in list(prompts))
        activity = row.get("activity_label")
        if activity is not None:
            if not isinstance(activity, str):
                raise C05Error("HellaSwag activity label invalid")
            prompts.extend(preprocess(activity + ": " + p) for p in list(prompts))
        options = list(dict.fromkeys([*options, *(preprocess(a) for a in options)]))
        prompts = list(dict.fromkeys(p for p in prompts if p.strip()))
    else:
        raise C05Error("unsupported benchmark task")
    return (
        [("prompt", p) for p in prompts]
        + [("answer", a) for a in options]
        + [("combined", f"{p} {a}") for p in prompts for a in options]
        + [("combined", " ".join([p, *options])) for p in prompts]
    )


def informative(tokens: tuple[str, ...], floor: Informativeness) -> bool:
    return (
        len(tokens) >= floor.tokens
        and len(" ".join(tokens)) >= floor.characters
        and len({t for t in tokens if sum(c.isalpha() for c in t) >= 2}) >= floor.distinct
    )


def patterns(
    variants: Iterable[tuple[str, str]],
    reference: str,
    policy: MatcherPolicy | MatcherPolicyV4,
) -> Iterator[Pattern]:
    background = {tuple(match_tokens(s)) for s in policy.background}
    for variant_no, (kind, text) in enumerate(variants):
        if variant_no >= policy.max_item_variants or len(text.encode()) > policy.max_variant_bytes:
            raise C05Error("benchmark variant limit exceeded")
        tokens = tuple(match_tokens(text))
        floor = getattr(policy, kind)
        if not informative(tokens, floor):
            continue
        candidates = (
            [tokens]
            if len(tokens) <= policy.span_tokens
            else [
                tokens[i : i + policy.span_tokens]
                for i in range(0, len(tokens) - policy.span_tokens + 1, policy.stride)
            ][: policy.spans_per_variant]
        )
        for candidate in candidates:
            if candidate in background or any(b and _contains(candidate, b) for b in background):
                continue
            if informative(candidate, floor):
                yield Pattern(candidate, (f"{reference}:{kind}",))


def composite(task: str, row: Mapping[str, Any]) -> str:
    """item-composite-v1: label-free whole-item text in publisher field order.

    Reads only content fields (never answerKey/label/scores). Fields are joined
    by one space, which match-view normalization treats as an ordinary token
    boundary, so naturally copied item text can still match exactly.
    """
    if task == "arc_easy":
        parts = [_string(row, "question"), *_answers(row, "choices")]
    elif task == "blimp":
        parts = [_string(row, "sentence_good"), _string(row, "sentence_bad")]
    elif task == "piqa":
        parts = [_string(row, "goal"), _string(row, "sol1"), _string(row, "sol2")]
    elif task == "hellaswag":
        if "ctx" in row:
            parts = [_string(row, "ctx")]
        elif "ctx_a" in row and "ctx_b" in row:
            suffix = row["ctx_b"]
            if not isinstance(suffix, str):
                raise C05Error("HellaSwag context suffix invalid")
            parts = [_string(row, "ctx_a"), suffix]
        else:
            raise C05Error("HellaSwag context missing")
        parts.extend(_answers(row, "endings"))
    else:
        raise C05Error("unsupported benchmark task")
    return " ".join(part for part in parts if part.strip())


def item_patterns(
    task: str,
    row: Mapping[str, Any],
    reference: str,
    policy: MatcherPolicy | MatcherPolicyV4,
) -> tuple[list[tuple[str, str]], list[Pattern], int, bool]:
    """Return (rendered variants, unique patterns, raw candidate emissions, fallback used).

    Exact per-item dedup on (tokens, provenance) is lossless: the protected index
    stores one line per distinct (pattern identity, provenance) and every
    provenance embeds this item's globally unique reference. The v4 fallback is
    consulted only when the v3 normal signatures are empty.
    """
    rendered = render(task, row)
    raw = list(patterns(rendered, reference, policy))
    unique = list(dict.fromkeys(raw))
    if unique or not isinstance(policy, MatcherPolicyV4):
        return rendered, unique, len(raw), False
    text = composite(task, row)
    if len(text.encode()) > policy.max_variant_bytes:
        raise C05Error("benchmark variant limit exceeded")
    tokens = tuple(match_tokens(text))
    background = {tuple(match_tokens(s)) for s in policy.background}
    if not informative(tokens, policy.fallback) or any(
        b and _contains(tokens, b) for b in background
    ):
        return rendered, [], 0, False
    return rendered, [Pattern(tokens, (f"{reference}:item_fallback",))], 1, True


def _contains(tokens: tuple[str, ...], part: tuple[str, ...]) -> bool:
    return any(tokens[i : i + len(part)] == part for i in range(len(tokens) - len(part) + 1))


class StreamingMatcher:
    """Frozen automaton. Hits are opaque pattern digests; provenance stays protected."""

    def __init__(
        self,
        entries: Iterable[Pattern],
        *,
        max_patterns: int,
        max_nodes: int,
        check: Callable[[], None] = lambda: None,
    ) -> None:
        self._next: list[dict[str, int]] = [{}]
        self._failure = [0]
        self._terminal: list[str | None] = [None]
        self._output_link = [0]
        provenance: dict[str, set[str]] = {}
        for count, entry in enumerate(entries, 1):
            check()
            if count > max_patterns or not entry.tokens:
                raise C05Error("benchmark pattern limit or empty pattern")
            identity = digest(entry.tokens)
            provenance.setdefault(identity, set()).update(entry.provenance)
            state = 0
            for token in entry.tokens:
                if token not in self._next[state]:
                    if len(self._next) >= max_nodes:
                        raise C05Error("benchmark automaton node ceiling")
                    self._next[state][token] = len(self._next)
                    self._next.append({})
                    self._failure.append(0)
                    self._terminal.append(None)
                    self._output_link.append(0)
                state = self._next[state][token]
            self._terminal[state] = identity
        self.provenance = MappingProxyType({k: tuple(sorted(v)) for k, v in provenance.items()})
        queue = deque(self._next[0].values())
        while queue:
            check()
            state = queue.popleft()
            for token, child in self._next[state].items():
                queue.append(child)
                fail = self._failure[state]
                while fail and token not in self._next[fail]:
                    fail = self._failure[fail]
                fail = self._next[fail].get(token, 0)
                self._failure[child] = fail
                self._output_link[child] = fail if self._terminal[fail] else self._output_link[fail]
        self.identity = digest(dict(self.provenance))

    @property
    def nodes(self) -> int:
        """Allocated automaton states, root included (the ``automaton_nodes`` measure)."""
        return len(self._next)

    def match(self, tokens: Iterable[str]) -> str | None:
        """Return first exact normalized token-boundary hit; no corpus statistics."""
        state = 0
        for token in tokens:
            while state and token not in self._next[state]:
                state = self._failure[state]
            state = self._next[state].get(token, 0)
            terminal = self._terminal[state] or self._terminal[self._output_link[state]]
            if terminal:
                return terminal
        return None
