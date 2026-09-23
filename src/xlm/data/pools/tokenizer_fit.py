"""Frozen tokenizer-fit sampling manifest over the training partition.

Contract C06: freeze a balanced tokenizer-training sample from the admitted training
pool *before* mixture search, and never retrain the tokenizer for each mixture. The
sample must not be adapted using validation or benchmark text.

Balance here means declared **raw-byte shares** across views, not document counts: a
family with long documents would otherwise dominate the fit simply by having fewer,
larger records. Selection is deterministic given the manifest's seed, so the same
pool reproduces the same fit sample.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import Field, model_validator

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument

TOKENIZER_FIT_POLICY_VERSION = "1"


class NonTrainingFitError(RuntimeError):
    """Raised when tokenizer-fit sampling would touch non-training text."""


class TokenizerFitConfig(StrictConfigModel):
    """Frozen policy for selecting the tokenizer-fit sample."""

    target_sample_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1,
        description="Total canonical bytes to draw for the fit, across all views.",
    )
    seed: int = Field(default=20260919, ge=0)
    max_document_bytes: int | None = Field(
        default=None,
        ge=1,
        description="Optional cap so one very long document cannot dominate a view's share.",
    )
    min_documents_per_view: int = Field(
        default=1,
        ge=0,
        description="Draw at least this many documents from each view with a declared share.",
    )

    @model_validator(mode="after")
    def validate_caps(self) -> TokenizerFitConfig:
        if (
            self.max_document_bytes is not None
            and self.max_document_bytes > self.target_sample_bytes
        ):
            raise ValueError(
                "max_document_bytes exceeds target_sample_bytes; the cap would never bind"
            )
        return self

    def identity(self) -> str:
        payload = (
            f"tokfit:v{TOKENIZER_FIT_POLICY_VERSION}:bytes={self.target_sample_bytes}:"
            f"seed={self.seed}:maxdoc={self.max_document_bytes}:"
            f"mindocs={self.min_documents_per_view}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass
class TokenizerFitManifest:
    """A frozen, reproducible record of which documents fit the tokenizer."""

    fit_id: str
    pool_id: str
    policy_identity: str
    seed: int
    doc_ids: list[str]
    bytes_by_view: dict[str, int]
    documents_by_view: dict[str, int]
    declared_shares: dict[str, float]
    achieved_shares: dict[str, float]
    total_bytes: int
    sample_digest: str
    notes: list[str] = field(default_factory=list)

    @property
    def document_count(self) -> int:
        return len(self.doc_ids)

    def max_share_deviation(self) -> float:
        """Largest absolute gap between a declared and an achieved byte share.

        Reported rather than asserted away: a small pool may simply not contain
        enough text in a view to meet its declared share.
        """
        if not self.declared_shares:
            return 0.0
        return max(
            abs(self.declared_shares[view] - self.achieved_shares.get(view, 0.0))
            for view in self.declared_shares
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "max_share_deviation": self.max_share_deviation()}


def restrict_membership_to_split(
    doc_ids_by_view: dict[str, list[str]],
    documents: Iterable[CanonicalDocument],
    split: str = "train",
) -> dict[str, list[str]]:
    """Narrow view membership to one split before tokenizer-fit selection.

    This is the ergonomic path for a pool that legitimately holds several splits.
    It does not replace the guard in :func:`build_tokenizer_fit_manifest`, which
    still refuses non-training text if a caller skips this step.
    """
    allowed = {doc.doc_id for doc in documents if doc.split == split}
    return {
        view_id: sorted(d for d in ids if d in allowed) for view_id, ids in doc_ids_by_view.items()
    }


def _order_key(seed: int, doc_id: str) -> str:
    """Deterministic, content-derived draw order for one document."""
    return hashlib.blake2b(f"{seed}:{doc_id}".encode(), digest_size=16).hexdigest()


@dataclass(frozen=True, slots=True)
class _FitCandidate:
    split: str
    utf8_byte_count: int


def build_tokenizer_fit_manifest(
    documents: Iterable[CanonicalDocument],
    doc_ids_by_view: dict[str, list[str]],
    declared_shares: dict[str, float],
    pool_id: str,
    config: TokenizerFitConfig | None = None,
) -> TokenizerFitManifest:
    """Select a balanced, training-only tokenizer-fit sample.

    Raises :class:`NonTrainingFitError` if any candidate document is outside the
    training partition. Tokenizer fitting on validation text would make every later
    diagnostic comparison unsound (C05, C06).
    """
    cfg = config or TokenizerFitConfig()
    # Selection never reads text or provenance. Keep last-ID-wins behavior while
    # releasing each caller-owned document as the input iterator advances.
    by_id = {doc.doc_id: _FitCandidate(doc.split, doc.utf8_byte_count) for doc in documents}

    view_of_doc: dict[str, str] = {}
    for view_id, ids in doc_ids_by_view.items():
        for doc_id in ids:
            view_of_doc.setdefault(doc_id, view_id)

    # Guard first: refuse before selecting anything, so a leak cannot half-happen.
    offending = sorted(
        doc_id for doc_id in view_of_doc if doc_id in by_id and by_id[doc_id].split != "train"
    )
    if offending:
        raise NonTrainingFitError(
            f"tokenizer-fit sampling touched {len(offending)} non-training document(s): "
            f"{offending[:5]}. Only the training partition may fit a tokenizer."
        )

    total_declared = sum(declared_shares.values())
    if total_declared <= 0:
        # No declared shares: fall back to equal shares across views that have text.
        populated = [v for v, ids in doc_ids_by_view.items() if ids]
        declared_shares = {v: 1.0 / len(populated) for v in populated} if populated else {}
        total_declared = sum(declared_shares.values())

    normalized = (
        {v: s / total_declared for v, s in declared_shares.items()} if total_declared else {}
    )

    selected: list[str] = []
    bytes_by_view: dict[str, int] = {}
    documents_by_view: dict[str, int] = {}
    notes: list[str] = []

    for view_id in sorted(doc_ids_by_view):
        share = normalized.get(view_id, 0.0)
        view_budget = int(cfg.target_sample_bytes * share)
        candidates = sorted(
            (d for d in doc_ids_by_view[view_id] if d in by_id),
            key=lambda doc_id: (_order_key(cfg.seed, doc_id), doc_id),
        )

        taken_bytes = 0
        taken_docs = 0
        for doc_id in candidates:
            doc = by_id[doc_id]
            if cfg.max_document_bytes is not None and doc.utf8_byte_count > cfg.max_document_bytes:
                continue
            if taken_bytes >= view_budget and taken_docs >= cfg.min_documents_per_view:
                break
            selected.append(doc_id)
            taken_bytes += doc.utf8_byte_count
            taken_docs += 1

        bytes_by_view[view_id] = taken_bytes
        documents_by_view[view_id] = taken_docs

        if share > 0 and taken_bytes < view_budget:
            notes.append(
                f"view '{view_id}' supplied {taken_bytes:,} of {view_budget:,} requested "
                "bytes; the pool does not hold enough text in this view to meet its "
                "declared share."
            )

    total_bytes = sum(bytes_by_view.values())
    achieved = (
        {v: b / total_bytes for v, b in bytes_by_view.items()}
        if total_bytes
        else dict.fromkeys(bytes_by_view, 0.0)
    )

    selected_sorted = sorted(set(selected))
    sample_digest = hashlib.sha256("|".join(selected_sorted).encode("utf-8")).hexdigest()
    fit_id = (
        "tokfit_"
        + hashlib.sha256(f"{pool_id}:{cfg.identity()}:{sample_digest}".encode()).hexdigest()[:20]
    )

    return TokenizerFitManifest(
        fit_id=fit_id,
        pool_id=pool_id,
        policy_identity=cfg.identity(),
        seed=cfg.seed,
        doc_ids=selected_sorted,
        bytes_by_view=bytes_by_view,
        documents_by_view=documents_by_view,
        declared_shares=normalized,
        achieved_shares=achieved,
        total_bytes=total_bytes,
        sample_digest=sample_digest,
        notes=notes,
    )


def tokenizer_fit_resource_plan(
    manifest: TokenizerFitManifest,
    target_vocab_size: int,
) -> dict[str, Any]:
    """Describe the cost of fitting a tokenizer on this sample.

    C13 requires a resource plan before a large fit. This returns the plan; it does
    not authorize or start anything.
    """
    # Byte-level BPE training holds the corpus and the merge frontier in memory.
    # The multiplier is a rough planning figure, not a measurement.
    estimated_peak_bytes = int(manifest.total_bytes * 3.5) + target_vocab_size * 4096

    return {
        "fit_id": manifest.fit_id,
        "pool_id": manifest.pool_id,
        "target_vocab_size": target_vocab_size,
        "sample_documents": manifest.document_count,
        "sample_bytes": manifest.total_bytes,
        "estimated_peak_memory_bytes": estimated_peak_bytes,
        "estimated_peak_memory_gib": round(estimated_peak_bytes / (1024**3), 3),
        "network_required": False,
        "basis": (
            "Planning estimate from sample size and vocabulary target. Not a measured "
            "benchmark; actual peak memory depends on the tokenizers library build."
        ),
        "authorization_required": manifest.total_bytes > 256 * 1024 * 1024,
    }
