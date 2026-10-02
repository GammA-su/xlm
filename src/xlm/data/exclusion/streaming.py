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
from xlm.data.evidence_v2.canonical import digest
from xlm.data.exclusion.policy import C05Error, Informativeness, MatcherPolicy


@dataclass(frozen=True)
class Pattern:
    tokens: tuple[str, ...]
    provenance: tuple[str, ...]


def render(task: str, row: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Task render v3; all options plus raw/0.4.13-preprocessed HellaSwag variants."""

    def string(name: str) -> str:
        value = row.get(name)
        if not isinstance(value, str) or not value.strip():
            raise C05Error("benchmark text field absent or invalid")
        return value

    def answers(name: str) -> list[str]:
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
    variants: Iterable[tuple[str, str]], reference: str, policy: MatcherPolicy
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
