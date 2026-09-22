"""Core types, enums, and metric structures for data cleaning and filtering."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from xlm.core.contracts import CanonicalDocument


class TransformAction(StrEnum):
    """Action outcome from a transform stage."""

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


@dataclass(frozen=True)
class TextSpan:
    """Half-open UTF-8 byte span [start_byte, end_byte) into canonical rendered text."""

    start_byte: int
    end_byte: int
    label: str = "answer"

    def __post_init__(self) -> None:
        if self.start_byte < 0:
            raise ValueError(f"start_byte cannot be negative: {self.start_byte}")
        if self.end_byte < self.start_byte:
            raise ValueError(
                f"end_byte ({self.end_byte}) cannot be less than start_byte ({self.start_byte})"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QualityMetrics:
    """Finite, typed quality and characterization metrics for a document."""

    utf8_byte_count: int = 0
    word_count: int = 0
    line_count: int = 0
    symbol_ratio: float = 0.0
    digit_ratio: float = 0.0
    duplicate_line_ratio: float = 0.0
    max_ngram_repetition_ratio: float = 0.0
    language: str = "unknown"
    language_confidence: float | None = None
    language_method: str = "none"
    is_educational_density: bool = False
    has_html_markup: bool = False
    boilerplate_paragraphs_removed: int = 0
    detected_secrets: list[str] = field(default_factory=list)
    reserved_email_placeholders_ignored: int = 0
    spans: list[TextSpan] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["spans"] = [s.to_dict() for s in self.spans]
        return data


@dataclass
class TransformResult:
    """Result of applying a single transform to a CanonicalDocument."""

    action: TransformAction
    document: CanonicalDocument | None
    reasons: list[str] = field(default_factory=list)
    metrics: QualityMetrics = field(default_factory=QualityMetrics)
    duration_ms: float = 0.0
