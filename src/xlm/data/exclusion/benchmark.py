"""Development-mode benchmark contamination matching.

Contract C05 and EVALUATION_POLICY: benchmark splits are excluded from gradient and
tokenizer training, matching full examples and sufficiently informative spans, while
avoiding the removal of ordinary English that merely shares a common short phrase.

Two matchers run together:

* **Full-example hash** -- the whole benchmark example, rendered as prompt plus every
  candidate answer, hashed over the match view. Exact and cheap.
* **Informative span** -- contiguous n-grams from the example that are rare enough to
  be diagnostic. Independently frozen background phrases may be excluded; corpus
  repetition never suppresses benchmark evidence.

This is a *development-mode* facility operating on locally available benchmark text.
It cannot prove the absence of paraphrased benchmark material, and it never produces
a sealed-final claim; see :mod:`xlm.data.exclusion.receipt` for that boundary.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.dedup.matchview import MATCH_VIEW_VERSION, match_normalize, match_tokens

EXCLUSION_POLICY_VERSION = "2"


class ExclusionConfig(StrictConfigModel):
    """Frozen benchmark-exclusion policy."""

    span_length: int = Field(
        default=13,
        ge=5,
        le=64,
        description="Token length of informative spans. Short spans match ordinary English.",
    )
    min_span_characters: int = Field(
        default=40,
        ge=1,
        description="Minimum normalized character length for a span to count as informative.",
    )
    max_spans_per_example: int = Field(default=32, ge=1)
    max_corpus_span_frequency: int = Field(
        default=3,
        ge=1,
        description=("Historical identity field; v2 never suppresses based on corpus frequency."),
    )
    background_phrases: tuple[str, ...] = ()
    match_full_example: bool = Field(default=True)
    match_informative_spans: bool = Field(default=True)

    def identity(self) -> str:
        payload = (
            f"exclusion:v{EXCLUSION_POLICY_VERSION}:view{MATCH_VIEW_VERSION}:"
            f"span={self.span_length}:minchars={self.min_span_characters}:"
            f"maxspans={self.max_spans_per_example}:freq={self.max_corpus_span_frequency}:"
            f"full={self.match_full_example}:spans={self.match_informative_spans}:"
            f"background={self.background_phrases!r}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass
class BenchmarkExample:
    """One development benchmark example.

    ``candidate_answers`` holds *all* candidates, not just the gold one: contamination
    means the corpus contains the answer-bearing text, regardless of which candidate
    is correct. The gold label is deliberately not a field here.
    """

    example_id: str
    task_id: str
    prompt: str
    candidate_answers: list[str] = field(default_factory=list)

    def rendered(self) -> str:
        """Render prompt plus every candidate answer for full-example matching."""
        return " ".join([self.prompt, *self.candidate_answers]).strip()

    def span_segments(self) -> list[str]:
        """Return the text segments that informative spans are drawn from.

        Spans come from the prompt alone and from prompt-plus-each-answer, rather
        than from the concatenated rendering. A span drawn across the boundary
        between two unrelated candidate answers is an artefact of concatenation and
        would never occur in a contaminated document.
        """
        segments = [self.prompt]
        segments.extend(f"{self.prompt} {answer}".strip() for answer in self.candidate_answers)
        segments.extend(a for a in self.candidate_answers if a.strip())
        return [s for s in segments if s.strip()]

    def full_hash(self) -> str:
        return hashlib.sha256(match_normalize(self.rendered()).encode("utf-8")).hexdigest()


@dataclass
class ExclusionHit:
    """A corpus document matched against a benchmark example."""

    doc_id: str
    example_id: str
    task_id: str
    match_kind: str
    evidence_digest: str
    span_token_length: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExclusionReport:
    """Aggregate, snippet-free contamination report."""

    policy_identity: str
    documents_scanned: int
    examples_indexed: int
    hits: list[ExclusionHit]
    excluded_doc_ids: list[str]
    spans_indexed: int = 0
    spans_suppressed_as_common: int = 0
    coverage_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_identity": self.policy_identity,
            "documents_scanned": self.documents_scanned,
            "examples_indexed": self.examples_indexed,
            "spans_indexed": self.spans_indexed,
            "spans_suppressed_as_common": self.spans_suppressed_as_common,
            "hit_count": len(self.hits),
            "hits": [h.to_dict() for h in self.hits],
            "excluded_doc_ids": self.excluded_doc_ids,
            "coverage_notes": self.coverage_notes,
        }


def _spans(text: str, span_length: int, min_characters: int, limit: int) -> list[str]:
    """Return candidate informative spans from ``text``."""
    tokens = match_tokens(text)
    if len(tokens) < span_length:
        joined = " ".join(tokens)
        return [joined] if len(joined) >= min_characters else []

    collected: list[str] = []
    seen: set[str] = set()
    # Stride by half a span so overlapping paraphrase boundaries still get covered
    # without generating one span per token position.
    stride = max(1, span_length // 2)
    for start in range(0, len(tokens) - span_length + 1, stride):
        span = " ".join(tokens[start : start + span_length])
        if len(span) < min_characters or span in seen:
            continue
        seen.add(span)
        collected.append(span)
        if len(collected) >= limit:
            break
    return collected


def _digest(value: str) -> str:
    """Digest a matched span so reports carry evidence without carrying text."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


class BenchmarkExclusionMatcher:
    """Matches corpus documents against development benchmark examples."""

    def __init__(self, config: ExclusionConfig | None = None) -> None:
        self.config = config or ExclusionConfig()
        self._full_hashes: dict[str, BenchmarkExample] = {}
        self._span_index: dict[str, list[BenchmarkExample]] = {}
        self.spans_indexed = 0
        self._suppressed = 0
        self._examples = 0

    def index_examples(self, examples: Iterable[BenchmarkExample]) -> None:
        """Index benchmark examples for matching."""
        for example in examples:
            self._examples += 1
            if self.config.match_full_example:
                self._full_hashes[example.full_hash()] = example
            if self.config.match_informative_spans:
                collected = 0
                for segment in example.span_segments():
                    if collected >= self.config.max_spans_per_example:
                        break
                    for span in _spans(
                        segment,
                        self.config.span_length,
                        self.config.min_span_characters,
                        self.config.max_spans_per_example - collected,
                    ):
                        background = [match_normalize(p) for p in self.config.background_phrases]
                        if any(
                            b and (f" {span} " in f" {b} " or f" {b} " in f" {span} ")
                            for b in background
                        ):
                            self._suppressed += 1
                            continue
                        if span not in self._span_index:
                            collected += 1
                        self._span_index.setdefault(span, []).append(example)
        self.spans_indexed = len(self._span_index)

    def suppress_common_spans(self, documents: Sequence[CanonicalDocument]) -> int:
        """Compatibility method: corpus frequency never suppresses benchmark evidence.

        Only independently frozen ``background_phrases`` act at index construction.
        The historical frequency option is retained in identity for explicit migration.
        """
        return 0

    def scan(self, documents: Iterable[CanonicalDocument]) -> ExclusionReport:
        """Scan ``documents`` and report contamination hits."""
        scanned = 0

        hits: list[ExclusionHit] = []
        excluded: set[str] = set()

        for doc in documents:
            scanned += 1
            normalized = match_normalize(doc.text)
            doc_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

            example = self._full_hashes.get(doc_hash)
            if example is not None:
                hits.append(
                    ExclusionHit(
                        doc_id=doc.doc_id,
                        example_id=example.example_id,
                        task_id=example.task_id,
                        match_kind="full_example",
                        evidence_digest=_digest(doc_hash),
                    )
                )
                excluded.add(doc.doc_id)
                continue

            for span, examples in self._span_index.items():
                if f" {span} " in f" {normalized} ":
                    matched = examples[0]
                    hits.append(
                        ExclusionHit(
                            doc_id=doc.doc_id,
                            example_id=matched.example_id,
                            task_id=matched.task_id,
                            match_kind="informative_span",
                            evidence_digest=_digest(span),
                            span_token_length=len(span.split(" ")),
                        )
                    )
                    excluded.add(doc.doc_id)
                    break

        hits.sort(key=lambda h: (h.doc_id, h.example_id))
        return ExclusionReport(
            policy_identity=self.config.identity(),
            documents_scanned=scanned,
            examples_indexed=self._examples,
            hits=hits,
            excluded_doc_ids=sorted(excluded),
            spans_indexed=self.spans_indexed,
            spans_suppressed_as_common=self._suppressed,
            coverage_notes=[
                "Development-mode matching over locally available benchmark text only.",
                "Exact and informative-span matching cannot detect paraphrased benchmark "
                "material; absence of hits is not proof of an uncontaminated corpus.",
                "Reports carry span digests, never benchmark text or labels.",
            ],
        )
