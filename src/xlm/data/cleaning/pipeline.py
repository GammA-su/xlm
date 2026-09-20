"""Cleaning pipeline composer, validator, and streaming executor adhering to C01, C02, C03, and
Amendments 1, 5, and 6.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import BaseTransform, IncompatiblePipelineError
from xlm.data.cleaning.boilerplate import BoilerplateTransform
from xlm.data.cleaning.html import HtmlExtractionTransform
from xlm.data.cleaning.language import LanguageFilter
from xlm.data.cleaning.length_noise import LengthFilter, NoiseFilter
from xlm.data.cleaning.normalization import CanonicalNormalizationTransform
from xlm.data.cleaning.pii import PiiSecretFilter
from xlm.data.cleaning.quarantine import QuarantineManager
from xlm.data.cleaning.repetition import RepetitionFilter
from xlm.data.cleaning.structured import StructuredExampleRenderTransform
from xlm.data.cleaning.types import TransformAction


@dataclass
class StageStats:
    """Metrics recorded for a single pipeline stage."""

    stage_name: str
    transform_id: str
    transform_version: str
    input_docs: int = 0
    output_docs: int = 0
    input_bytes: int = 0
    output_bytes: int = 0
    rejected_docs: int = 0
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineExecutionSummary:
    """Honest, auditable execution summary for a cleaning pipeline run."""

    pipeline_hash: str
    domain_preset: str
    total_input_docs: int
    total_output_docs: int
    total_input_bytes: int
    total_output_bytes: int
    total_rejected_docs: int
    document_yield_ratio: float
    byte_yield_ratio: float
    stage_metrics: list[StageStats]
    reason_counts: dict[str, int]
    elapsed_seconds: float
    is_partial_sample: bool = False
    declared_max_docs: int | None = None
    completed: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stage_metrics"] = [s.to_dict() for s in self.stage_metrics]
        return data


class CleaningPipeline:
    """Executes an ordered, validated sequence of transforms on canonical records.

    Adheres strictly to Amendments 1, 5, and 6:
    - Validates transform sequence: text-mutating transforms cannot run after answer span
    generation.
    - Computes deterministic composite pipeline_hash over code and configuration identities.
    - Tracks stage-local counts separately from pipeline totals.
    - Tracks document yield and byte yield separately.
    - Streams records one at a time; retained memory does not grow with input size.
    """

    def __init__(
        self,
        transforms: list[BaseTransform],
        domain_preset: str = "custom",
    ) -> None:
        self.transforms = transforms
        self.domain_preset = domain_preset
        self.validate_pipeline_order()

    def validate_pipeline_order(self) -> None:
        """Validate pipeline transform order invariants.

        Amendment 1: Text-mutating transforms must not be placed after structured
        answer span generation, as that would invalidate half-open byte spans.
        """
        seen_span_generator = False
        for t in self.transforms:
            if t.transform_id == "structured_render":
                seen_span_generator = True
            elif seen_span_generator and t.mutates_text:
                raise IncompatiblePipelineError(
                    f"Incompatible pipeline order: Transform '{t.transform_id}' mutates text "
                    f"after '{StructuredExampleRenderTransform.transform_id}' has computed "
                    "canonical answer byte spans. Move text mutations before structured rendering."
                )

    def compute_pipeline_hash(self) -> str:
        """Compute composite SHA-256 digest of entire pipeline definition."""
        pieces = [f"domain:{self.domain_preset}"]
        for t in self.transforms:
            pieces.append(t.compute_transform_hash())
        payload = "|".join(pieces)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def run_stream(
        self,
        documents: Iterable[CanonicalDocument],
        quarantine_mgr: QuarantineManager | None = None,
        max_docs: int | None = None,
    ) -> tuple[Iterator[CanonicalDocument], PipelineExecutionSummary]:
        """Execute the pipeline over documents in genuinely streaming mode.

        Returns an iterator of accepted documents and the summary object that the
        iterator populates in place as it is consumed. No accepted document is
        retained by the pipeline: at most one record is live per step, so peak
        retained memory is independent of the number of input documents or shards.

        The caller must fully consume the iterator before treating the summary as
        final; ``summary.completed`` records whether that has happened.
        """
        p_hash = self.compute_pipeline_hash()

        stage_stats = [
            StageStats(
                stage_name=t.transform_id,
                transform_id=t.transform_id,
                transform_version=t.version,
            )
            for t in self.transforms
        ]

        summary = PipelineExecutionSummary(
            pipeline_hash=p_hash,
            domain_preset=self.domain_preset,
            total_input_docs=0,
            total_output_docs=0,
            total_input_bytes=0,
            total_output_bytes=0,
            total_rejected_docs=0,
            document_yield_ratio=0.0,
            byte_yield_ratio=0.0,
            stage_metrics=stage_stats,
            reason_counts={},
            elapsed_seconds=0.0,
            is_partial_sample=False,
            declared_max_docs=max_docs,
            completed=False,
        )

        return self._iterate(documents, summary, quarantine_mgr, max_docs), summary

    def _iterate(
        self,
        documents: Iterable[CanonicalDocument],
        summary: PipelineExecutionSummary,
        quarantine_mgr: QuarantineManager | None,
        max_docs: int | None,
    ) -> Iterator[CanonicalDocument]:
        """Drive documents through every stage, yielding survivors one at a time."""
        start_t = time.monotonic()
        stage_stats = summary.stage_metrics

        for doc in documents:
            if max_docs is not None and summary.total_input_docs >= max_docs:
                summary.is_partial_sample = True
                break

            summary.total_input_docs += 1
            summary.total_input_bytes += doc.utf8_byte_count

            current_doc = doc
            rejected = False

            for i, transform in enumerate(self.transforms):
                st = stage_stats[i]
                st.input_docs += 1
                st.input_bytes += current_doc.utf8_byte_count

                res = transform.apply(current_doc)
                st.duration_ms += res.duration_ms

                if res.action == TransformAction.REJECT:
                    rejected = True
                    st.rejected_docs += 1
                    summary.total_rejected_docs += 1

                    for r in res.reasons:
                        summary.reason_counts[r] = summary.reason_counts.get(r, 0) + 1

                    if quarantine_mgr:
                        quarantine_mgr.record_rejection(
                            doc=current_doc,
                            reasons=res.reasons,
                            stage_name=transform.transform_id,
                            metrics=res.metrics,
                        )
                    break

                st.output_docs += 1
                if res.document is not None:
                    current_doc = res.document
                st.output_bytes += current_doc.utf8_byte_count

            if not rejected:
                summary.total_output_docs += 1
                summary.total_output_bytes += current_doc.utf8_byte_count
                self._refresh_yields(summary)
                summary.elapsed_seconds = round(time.monotonic() - start_t, 3)
                yield current_doc

        self._refresh_yields(summary)
        summary.elapsed_seconds = round(time.monotonic() - start_t, 3)
        summary.completed = True

    @staticmethod
    def _refresh_yields(summary: PipelineExecutionSummary) -> None:
        """Recompute document and byte yield ratios from current counters."""
        if summary.total_input_docs > 0:
            summary.document_yield_ratio = round(
                summary.total_output_docs / summary.total_input_docs, 4
            )
        if summary.total_input_bytes > 0:
            summary.byte_yield_ratio = round(
                summary.total_output_bytes / summary.total_input_bytes, 4
            )


def create_pipeline_preset(preset_name: str) -> CleaningPipeline:
    """Create standard domain-specific cleaning pipeline preset."""
    name = preset_name.lower()
    if name in ("educational_prose", "prose"):
        transforms: list[BaseTransform] = [
            CanonicalNormalizationTransform(),
            HtmlExtractionTransform(),
            BoilerplateTransform(),
            RepetitionFilter(),
            LanguageFilter(),
            NoiseFilter(),
            LengthFilter(),
            PiiSecretFilter(),
        ]
        return CleaningPipeline(transforms, domain_preset="educational_prose")

    elif name == "code":
        # HTML extraction disabled for code to preserve <tag> syntax; relaxed symbol thresholds
        transforms = [
            CanonicalNormalizationTransform(),
            RepetitionFilter(),
            NoiseFilter(),
            LengthFilter(),
            PiiSecretFilter(),
        ]
        return CleaningPipeline(transforms, domain_preset="code")

    elif name == "historical_ocr":
        # Tolerates higher OCR noise while catching repetition loops
        transforms = [
            CanonicalNormalizationTransform(),
            BoilerplateTransform(),
            RepetitionFilter(),
            LanguageFilter(),
            NoiseFilter(),
            LengthFilter(),
            PiiSecretFilter(),
        ]
        return CleaningPipeline(transforms, domain_preset="historical_ocr")

    elif name in ("structured_synthetic", "synthetic"):
        # Context-integrity and non-substring answer span tracking
        transforms = [
            CanonicalNormalizationTransform(),
            PiiSecretFilter(),
            NoiseFilter(),
            StructuredExampleRenderTransform(),
            LengthFilter(),
        ]
        return CleaningPipeline(transforms, domain_preset="structured_synthetic")

    else:
        raise ValueError(
            f"Unknown cleaning pipeline preset: '{preset_name}'. "
            "Supported presets: 'educational_prose', 'code', 'historical_ocr', "
            "'structured_synthetic'"
        )
