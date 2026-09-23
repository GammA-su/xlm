"""Abstract base class and execution protocols for versioned document transforms."""

from __future__ import annotations

import hashlib
import inspect
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from typing import Any

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.features import TextFeatures
from xlm.data.cleaning.types import TextSpan, TransformResult
from xlm.data.normalization import compute_sha256


class BlockedCapabilityError(RuntimeError):
    """Raised when an optional capability (e.g. missing classifier weights) is blocked."""


class IncompatiblePipelineError(ValueError):
    """Raised when an incompatible transform sequence is detected."""


class CleaningBudgetExhaustedError(RuntimeError):
    """Raised when processing budgets or memory bounds are breached."""


class BaseTransform(ABC):
    """Abstract base class for all versioned, composable data transforms."""

    transform_id: str
    version: str = "1"
    mutates_text: bool = False

    def __init__(self, config: StrictConfigModel) -> None:
        self.config = config

    def compute_code_hash(self) -> str:
        """Compute deterministic SHA-256 digest of transform implementation code."""
        try:
            src = inspect.getsource(self.__class__)
        except (OSError, TypeError):
            src = f"{self.__class__.__module__}.{self.__class__.__qualname__}"
        return hashlib.sha256(src.encode("utf-8")).hexdigest()

    def compute_transform_hash(self) -> str:
        """Compute composite behavioral hash of transform identity, code, and configuration."""
        code_h = self.compute_code_hash()
        cfg_json = self.config.model_dump_json()
        payload = f"{self.transform_id}:{self.version}:{code_h}:{cfg_json}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @abstractmethod
    def apply(
        self, doc: CanonicalDocument, features: TextFeatures | None = None
    ) -> TransformResult:
        """Apply transform to a single document. Must NOT mutate the input document.

        ``features`` is an optional per-document shared cache (P27B-G); when
        None the stage computes every characterization directly, exactly as
        the reference implementation does.
        """

    def apply_batch(self, docs: Iterable[CanonicalDocument]) -> Iterator[TransformResult]:
        """Apply transform to an iterable of documents in streaming fashion."""
        for doc in docs:
            yield self.apply(doc)

    def create_transformed_copy(
        self,
        doc: CanonicalDocument,
        new_text: str,
        stage_name: str,
        reasons: list[str] | None = None,
        metadata_updates: dict[str, Any] | None = None,
        cluster_ids: dict[str, str] | None = None,
        spans: list[TextSpan] | None = None,
    ) -> CanonicalDocument:
        """Create a fresh CanonicalDocument copy with updated clean text and transform log.

        Adheres strictly to Amendment 2:
        - Never mutates the caller's input record in place.
        - Preserves raw identity (raw_hash, source_file, source_row), source metadata,
          license reference, parent IDs, and split.
        - Updates clean_hash and utf8_byte_count to match new_text exactly.
        - Appends transform stage to transform_log.

        Throughput note (P27B-P): when the stage leaves the text unchanged,
        the hash and byte count are by definition identical to the input's,
        so they are reused instead of re-encoded and re-hashed. The emitted
        record (log entry included) is exactly the record the naive copy
        produces.
        """
        text_unchanged = new_text == doc.text
        if text_unchanged:
            utf8_bytes = doc.utf8_byte_count
            clean_h = doc.clean_hash
        else:
            utf8_bytes = len(new_text.encode("utf-8"))
            clean_h = compute_sha256(new_text)

        new_transform_log = list(doc.transform_log)
        stage_entry: dict[str, Any] = {
            "stage": stage_name,
            "transform_id": self.transform_id,
            "transform_version": self.version,
            "text_mutated": not text_unchanged,
            "prev_byte_count": doc.utf8_byte_count,
            "new_byte_count": utf8_bytes,
        }
        if spans:
            stage_entry["spans_count"] = len(spans)
        new_transform_log.append(stage_entry)

        new_quality_reasons = list(doc.quality_reasons)
        if reasons:
            for r in reasons:
                if r not in new_quality_reasons:
                    new_quality_reasons.append(r)

        new_metadata = dict(doc.source_metadata)
        if metadata_updates:
            new_metadata.update(metadata_updates)
        if spans is not None:
            new_metadata["answer_spans"] = [s.to_dict() for s in spans]

        new_clusters = dict(doc.cluster_ids)
        if cluster_ids:
            new_clusters.update(cluster_ids)

        return CanonicalDocument(
            doc_id=doc.doc_id,
            source_id=doc.source_id,
            source_revision=doc.source_revision,
            source_file=doc.source_file,
            source_row=doc.source_row,
            raw_hash=doc.raw_hash,
            clean_hash=clean_h,
            text=new_text,
            utf8_byte_count=utf8_bytes,
            language=doc.language,
            language_confidence=doc.language_confidence,
            document_kind=doc.document_kind,
            source_metadata=new_metadata,
            parent_ids=list(doc.parent_ids),
            license_reference=doc.license_reference,
            transform_log=new_transform_log,
            quality_reasons=new_quality_reasons,
            cluster_ids=new_clusters,
            split=doc.split,
        )
