"""Versioned, data-only production C05 policies and resource ceilings."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from xlm.data.dedup.minhash import MinHashConfig
from xlm.data.evidence_v2.canonical import digest


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Informativeness(FrozenModel):
    tokens: int = Field(ge=1, le=64)
    characters: int = Field(ge=1, le=1024)
    distinct: int = Field(ge=1, le=64)


class _MatcherFields(FrozenModel):
    """Normal-signature fields shared by every matcher version (identity is sorted-key)."""

    renderer: Literal["task-render-v3"] = "task-render-v3"
    normalization: Literal["match-view-v1"] = "match-view-v1"
    prompt: Informativeness = Informativeness(tokens=4, characters=16, distinct=3)
    sentence: Informativeness = Informativeness(tokens=3, characters=12, distinct=3)
    answer: Informativeness = Informativeness(tokens=8, characters=40, distinct=5)
    combined: Informativeness = Informativeness(tokens=8, characters=40, distinct=5)
    span_tokens: int = Field(default=13, ge=5, le=64)
    stride: int = Field(default=6, ge=1, le=64)
    spans_per_variant: int = Field(default=32, ge=1, le=512)
    max_variant_bytes: int = Field(default=65536, ge=1)
    max_item_variants: int = Field(default=256, ge=1, le=1024)
    # Independently frozen background; never inferred from the screened corpus.
    background: tuple[str, ...] = ()
    fuzzy_auto_exclusion: Literal[False] = False
    distinct_rule: Literal["tokens-with-two-letters-v1"] = "tokens-with-two-letters-v1"

    def identity(self) -> str:
        return digest(self.model_dump(mode="json"))


class MatcherPolicy(_MatcherFields):
    """Historical c05-matcher-v3: normal signatures only, never an item fallback."""

    version: Literal["c05-matcher-v3"] = "c05-matcher-v3"


class MatcherPolicyV4(_MatcherFields):
    """c05-matcher-v4: v3 normal signatures plus one exact whole-item fallback.

    The fallback is consulted only for an item whose normal v3 signatures are
    empty; it is one exact pattern over the label-free item composite, never
    sliding windows.
    """

    version: Literal["c05-matcher-v4"] = "c05-matcher-v4"
    fallback_renderer: Literal["item-composite-v1"] = "item-composite-v1"
    fallback_mode: Literal["exact-whole-item-only"] = "exact-whole-item-only"
    fallback: Informativeness = Informativeness(tokens=4, characters=16, distinct=3)


AnyMatcherPolicy = Annotated[MatcherPolicy | MatcherPolicyV4, Field(discriminator="version")]
_MATCHER_ADAPTER: TypeAdapter[MatcherPolicy | MatcherPolicyV4] = TypeAdapter(AnyMatcherPolicy)


def matcher_policy(data: Any) -> MatcherPolicy | MatcherPolicyV4:
    """Validate a frozen matcher policy by its explicit version; no default version."""
    return _MATCHER_ADAPTER.validate_python(data)


class ReviewPolicy(FrozenModel):
    version: Literal["c05-token-overlap-review-v1"] = "c05-token-overlap-review-v1"
    disposition: Literal["HEURISTIC_REVIEW_ONLY"] = "HEURISTIC_REVIEW_ONLY"
    enabled: bool = False
    min_tokens: int = Field(default=8, ge=5, le=64)
    min_distinct: int = Field(default=6, ge=3, le=64)
    matched_fraction: float = Field(default=0.75, gt=0, le=1)
    query_tokens: int = Field(default=128, ge=1, le=4096)
    postings_per_token: int = Field(default=64, ge=1, le=4096)
    comparisons_per_document: int = Field(default=128, ge=1, le=4096)
    candidates_per_document: int = Field(default=4, ge=1, le=64)
    candidates_per_benchmark: int = Field(default=100, ge=1)

    def identity(self) -> str:
        return digest(self.model_dump(mode="json"))


class ProductionPolicy(FrozenModel):
    version: Literal["c05-production-v2"] = "c05-production-v2"
    stage_order: Literal["hash-match-facts/group-propagate-split/publish-v1"] = (
        "hash-match-facts/group-propagate-split/publish-v1"
    )
    matcher: AnyMatcherPolicy = MatcherPolicy()
    lineage: Literal["known-lineage-v3"] = "known-lineage-v3"
    dedup_version: Literal["disk-minhash-v2"] = "disk-minhash-v2"
    shingle_size: int = 5
    permutations: int = 128
    bands: int = 32
    seed: int = 20260919
    near_threshold: float = Field(default=0.8, gt=0, le=1)
    max_candidates_per_document: int = Field(default=64, ge=1, le=4096)
    max_bucket_size: int = Field(default=256, ge=2, le=4096)
    oversized_bucket: Literal["skip_and_count"] = "skip_and_count"
    survivor: Literal["longest-source-doc-v1"] = "longest-source-doc-v1"
    split_version: Literal["group-hash-v2"] = "group-hash-v2"
    diagnostic_bytes: int = Field(default=50 * 1024**2, ge=0)
    quick_bytes: int = Field(default=5 * 1024**2, ge=0)
    audit_bytes: int = Field(default=5 * 1024**2, ge=0)
    gutenberg: Literal["known_groups_only", "require_book_ids"] = "known_groups_only"
    review: ReviewPolicy = Field(default_factory=lambda: ReviewPolicy())

    def minhash(self) -> MinHashConfig:
        return MinHashConfig(
            shingle_size=self.shingle_size,
            num_permutations=self.permutations,
            bands=self.bands,
            seed=self.seed,
            jaccard_threshold=self.near_threshold,
        )

    def identity(self) -> str:
        self.minhash()
        if self.quick_bytes > self.diagnostic_bytes:
            raise ValueError("quick split exceeds diagnostic target")
        return digest(self.model_dump(mode="json"))


class Resources(FrozenModel):
    """Proposed ceilings; an operator decision must restate every field.

    Defaults are internally consistent for a measured 512 B journal header: the
    derived rollback-journal bound for a 128 GiB database is about 160.25 GiB and
    the worst-case aggregate about 338.3 GiB (``capacity.storage_bounds``).
    """

    ram_bytes: int = Field(default=24 * 1024**3, ge=1)
    scratch_bytes: int = Field(default=352 * 1024**3, ge=1)
    index_bytes: int = Field(default=128 * 1024**3, ge=4096)
    journal_bytes: int = Field(default=161 * 1024**3, ge=1)
    output_bytes: int = Field(default=32 * 1024**3, ge=1)
    decision_bytes: int = Field(default=16 * 1024**3, ge=1)
    free_bytes: int = Field(default=32 * 1024**3, ge=0)
    document_bytes: int = Field(default=64 * 1024**2, ge=1)
    document_tokens: int = Field(default=2_000_000, ge=1)
    records: int = Field(default=16_000_000, ge=1)
    attempted_records: int = Field(default=32_000_000, ge=1)
    files: int = Field(default=4096, ge=1)
    comparisons: int = Field(default=1_000_000_000, ge=1)
    bytes_read: int = Field(default=512 * 1024**3, ge=1)
    oversized_buckets: int = Field(default=1_000_000_000, ge=0)
    benchmark_bytes: int = Field(default=2 * 1024**3, ge=1)
    # Pattern/provenance records that reach index.jsonl (and StreamingMatcher):
    # emissions after exact per-item (tokens, provenance) dedup. Raw candidates are
    # bounded per item by the matcher policy (max_item_variants x spans_per_variant).
    benchmark_patterns: int = Field(default=2_000_000, ge=1)
    automaton_nodes: int = Field(default=8_000_000, ge=1)
    review_candidates: int = Field(default=100_000, ge=0)
    # Protected benchmark preparation (build-local) worker processes; the C05
    # scan runner remains single-process. A reviewed value, never overridden.
    workers: int = Field(default=1, ge=1, le=16)
    stage_seconds: float = Field(default=86400, gt=0)
    overall_seconds: float = Field(default=259200, gt=0)


class C05Error(ValueError):
    """An identity, budget or authorization check failed closed."""


# Engineering acceptance (independent audit, 2026-10-02): hard aggregate storage
# admission (capacity.py) and the exact quota selection / selected-training-membership
# / schema-3 receipt chain (selection.py, freeze.py, bridge.py) are implemented and
# tested. Add an entry here to re-close protected plans and runs.
ENGINEERING_BLOCKERS: tuple[str, ...] = ()


def require_engine_acceptance(mode: str) -> None:
    """Refuse protected plans/runs while any recorded engineering blocker remains.

    This gate covers engineering only. Protected execution still requires the
    protected benchmark receipt, signed operator decisions (Gutenberg lineage,
    resources, policy), trusted keys and a plan-digest-bound authorization.
    """
    if mode != "authored" and ENGINEERING_BLOCKERS:
        raise C05Error("protected engine acceptance incomplete: " + "; ".join(ENGINEERING_BLOCKERS))
