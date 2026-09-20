"""Data cleaning, normalization, extraction, quality filtering, and reporting module."""

from __future__ import annotations

from xlm.core.registry import transforms
from xlm.data.cleaning.base import (
    BaseTransform,
    BlockedCapabilityError,
    CleaningBudgetExhaustedError,
    IncompatiblePipelineError,
)
from xlm.data.cleaning.boilerplate import BoilerplateConfig, BoilerplateTransform
from xlm.data.cleaning.html import HtmlExtractionConfig, HtmlExtractionTransform
from xlm.data.cleaning.language import LanguageConfig, LanguageFilter
from xlm.data.cleaning.length_noise import (
    LengthConfig,
    LengthFilter,
    NoiseConfig,
    NoiseFilter,
)
from xlm.data.cleaning.normalization import (
    CanonicalNormalizationConfig,
    CanonicalNormalizationTransform,
)
from xlm.data.cleaning.pii import PiiConfig, PiiSecretFilter, redact_sensitive_text
from xlm.data.cleaning.pipeline import (
    CleaningPipeline,
    PipelineExecutionSummary,
    StageStats,
    create_pipeline_preset,
)
from xlm.data.cleaning.quarantine import QuarantineManager, QuarantinePolicy
from xlm.data.cleaning.repetition import RepetitionConfig, RepetitionFilter
from xlm.data.cleaning.reporting import QualityReporter
from xlm.data.cleaning.structured import (
    StructuredExampleRenderTransform,
    StructuredRenderConfig,
)
from xlm.data.cleaning.types import (
    QualityMetrics,
    TextSpan,
    TransformAction,
    TransformResult,
)


def _register_default_transforms() -> None:
    """Register standard cleaning transforms in global transforms registry."""
    if not transforms.has("canonical_normalization", "1"):
        transforms.register(
            identifier="canonical_normalization",
            version="1",
            config_schema=CanonicalNormalizationConfig,
            capabilities={"mutates_text": True, "idempotent": True},
            factory=CanonicalNormalizationTransform,
        )

    if not transforms.has("html_extraction", "1"):
        transforms.register(
            identifier="html_extraction",
            version="1",
            config_schema=HtmlExtractionConfig,
            capabilities={"mutates_text": True, "content_type_gated": True},
            factory=HtmlExtractionTransform,
        )

    if not transforms.has("boilerplate_removal", "1"):
        transforms.register(
            identifier="boilerplate_removal",
            version="1",
            config_schema=BoilerplateConfig,
            capabilities={"mutates_text": True, "paragraph_aware": True},
            factory=BoilerplateTransform,
        )

    if not transforms.has("repetition_filter", "1"):
        transforms.register(
            identifier="repetition_filter",
            version="1",
            config_schema=RepetitionConfig,
            capabilities={"mutates_text": False, "bounded_compute": True},
            factory=RepetitionFilter,
        )

    if not transforms.has("language_filter", "1"):
        transforms.register(
            identifier="language_filter",
            version="1",
            config_schema=LanguageConfig,
            capabilities={"mutates_text": False, "heuristic": True, "classifier_plugin": True},
            factory=LanguageFilter,
        )

    if not transforms.has("length_filter", "1"):
        transforms.register(
            identifier="length_filter",
            version="1",
            config_schema=LengthConfig,
            capabilities={"mutates_text": False, "educational_exemption": True},
            factory=LengthFilter,
        )

    if not transforms.has("noise_filter", "1"):
        transforms.register(
            identifier="noise_filter",
            version="1",
            config_schema=NoiseConfig,
            capabilities={"mutates_text": False, "encoding_detection": True},
            factory=NoiseFilter,
        )

    if not transforms.has("pii_secret_filter", "1"):
        transforms.register(
            identifier="pii_secret_filter",
            version="1",
            config_schema=PiiConfig,
            capabilities={"mutates_text": True, "quarantine_omission": True},
            factory=PiiSecretFilter,
        )

    if not transforms.has("structured_render", "1"):
        transforms.register(
            identifier="structured_render",
            version="1",
            config_schema=StructuredRenderConfig,
            capabilities={"mutates_text": True, "answer_spans": True},
            factory=StructuredExampleRenderTransform,
        )


_register_default_transforms()

__all__ = [
    "BaseTransform",
    "BlockedCapabilityError",
    "BoilerplateConfig",
    "BoilerplateTransform",
    "CanonicalNormalizationConfig",
    "CanonicalNormalizationTransform",
    "CleaningBudgetExhaustedError",
    "CleaningPipeline",
    "HtmlExtractionConfig",
    "HtmlExtractionTransform",
    "IncompatiblePipelineError",
    "LanguageConfig",
    "LanguageFilter",
    "LengthConfig",
    "LengthFilter",
    "NoiseConfig",
    "NoiseFilter",
    "PipelineExecutionSummary",
    "PiiConfig",
    "PiiSecretFilter",
    "QualityMetrics",
    "QualityReporter",
    "QuarantineManager",
    "QuarantinePolicy",
    "RepetitionConfig",
    "RepetitionFilter",
    "StageStats",
    "StructuredExampleRenderTransform",
    "StructuredRenderConfig",
    "TextSpan",
    "TransformAction",
    "TransformResult",
    "create_pipeline_preset",
    "redact_sensitive_text",
]
