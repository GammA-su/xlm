"""Corpus diagnostics, validation metrics, and fixed diagnostic probes complying with C11.

Adheres strictly to Amendment 4:
- Aggregates NLL sums, scored token counts, and scored byte counts across the corpus
  BEFORE computing final perplexity and BPB.
- Excludes structural tokens from named text BPB.
- Author fixed diagnostic probes independent of training data mixtures.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.evaluation.likelihood import ConditionalLikelihoodScorer


@dataclass(frozen=True)
class SourceDiagnostic:
    """Aggregated validation diagnostics for a specific data source."""

    source_id: str
    doc_count: int
    total_text_tokens: int
    total_canonical_bytes: int
    total_nll_sum: float
    perplexity: float
    bits_per_byte: float
    eos_inclusive_nll_sum: float
    eos_inclusive_tokens: int
    text_and_eos_bpb: float
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FixedDiagnosticProbe:
    """An immutable, fixed diagnostic probe evaluating model predictions over time."""

    probe_id: str
    context: str
    continuation: str
    category: str
    description: str


DEFAULT_FIXED_PROBES = [
    FixedDiagnosticProbe(
        probe_id="probe_syntax_01",
        context="In Python, functions are defined using the keyword",
        continuation=" def",
        category="syntax",
        description="Core keyword prediction",
    ),
    FixedDiagnosticProbe(
        probe_id="probe_math_01",
        context="Two plus two is equal to",
        continuation=" four",
        category="math",
        description="Simple arithmetic relation",
    ),
    FixedDiagnosticProbe(
        probe_id="probe_fact_01",
        context="The Earth revolves around the",
        continuation=" Sun",
        category="factual",
        description="Common astronomical fact",
    ),
    FixedDiagnosticProbe(
        probe_id="probe_logic_01",
        context="If all men are mortal, and Socrates is a man, then Socrates is",
        continuation=" mortal",
        category="logic",
        description="Classical syllogism completion",
    ),
]


def evaluate_validation_sources(
    scorer: ConditionalLikelihoodScorer,
    sources: dict[str, list[CanonicalDocument | str]],
) -> dict[str, SourceDiagnostic]:
    """Evaluate per-source validation metrics, aggregating totals before computing ratios."""
    results: dict[str, SourceDiagnostic] = {}

    for source_id, docs in sources.items():
        doc_count = len(docs)
        if doc_count == 0:
            results[source_id] = SourceDiagnostic(
                source_id=source_id,
                doc_count=0,
                total_text_tokens=0,
                total_canonical_bytes=0,
                total_nll_sum=0.0,
                perplexity=float("nan"),
                bits_per_byte=float("nan"),
                eos_inclusive_nll_sum=0.0,
                eos_inclusive_tokens=0,
                text_and_eos_bpb=float("nan"),
            )
            continue

        total_text_nll = 0.0
        total_text_tokens = 0
        total_bytes = 0
        total_eos_inclusive_nll = 0.0
        total_eos_tokens = 0

        for doc in docs:
            doc_res = scorer.score_document(doc)
            total_text_nll += doc_res.text_token_nll_sum
            total_text_tokens += doc_res.text_token_count
            total_bytes += doc_res.scored_canonical_utf8_bytes
            total_eos_inclusive_nll += doc_res.eos_inclusive_nll_sum
            total_eos_tokens += doc_res.eos_inclusive_token_count

        ln2 = math.log(2.0)
        text_bpb = (total_text_nll / (ln2 * total_bytes)) if total_bytes > 0 else float("nan")
        text_ppl = (
            math.exp(total_text_nll / total_text_tokens) if total_text_tokens > 0 else float("nan")
        )
        text_and_eos_bpb = (
            (total_eos_inclusive_nll / (ln2 * total_bytes)) if total_bytes > 0 else float("nan")
        )

        results[source_id] = SourceDiagnostic(
            source_id=source_id,
            doc_count=doc_count,
            total_text_tokens=total_text_tokens,
            total_canonical_bytes=total_bytes,
            total_nll_sum=total_text_nll,
            perplexity=text_ppl,
            bits_per_byte=text_bpb,
            eos_inclusive_nll_sum=total_eos_inclusive_nll,
            eos_inclusive_tokens=total_eos_tokens,
            text_and_eos_bpb=text_and_eos_bpb,
        )

    return results


def evaluate_fixed_diagnostic_probes(
    scorer: ConditionalLikelihoodScorer,
    probes: list[FixedDiagnosticProbe] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate a fixed set of diagnostic probes to observe progression across checkpoints."""
    probe_list = probes or DEFAULT_FIXED_PROBES
    reports: list[dict[str, Any]] = []

    for probe in probe_list:
        res = scorer.score_continuation(probe.context, probe.continuation, item_id=probe.probe_id)
        reports.append(
            {
                "probe_id": probe.probe_id,
                "category": probe.category,
                "context": probe.context,
                "continuation": probe.continuation,
                "log_likelihood": res.log_likelihood,
                "nll_sum": res.nll_sum,
                "is_greedy": res.is_greedy,
                "token_count": res.token_count,
                "byte_count": res.byte_count,
                "bits_per_byte": res.bits_per_byte,
            }
        )

    return reports
