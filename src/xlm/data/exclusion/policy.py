"""Versioned, data-only production C05 policies and resource ceilings."""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
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


@lru_cache(maxsize=1 << 20)
def _two_letters(token: str) -> bool:
    """``tokens-with-two-letters-v1``: the token has at least two alphabetic characters."""
    return sum(c.isalpha() for c in token) >= 2


def _distinct_at_least(tokens: tuple[str, ...], needed: int) -> bool:
    """Whether at least ``needed`` distinct two-letter tokens occur (early stop)."""
    seen: set[str] = set()
    for token in tokens:
        if token not in seen and _two_letters(token):
            seen.add(token)
            if len(seen) >= needed:
                return True
    return len(seen) >= needed


FLOOR_8_40_5 = Informativeness(tokens=8, characters=40, distinct=5)
TRIGGER_KINDS = ("prompt", "sentence", "answer", "combined", "item_fallback")


class TriggerPolicy(FrozenModel):
    """c05-trigger-floors-v1: which frozen protected-index patterns may exclude alone.

    The protected index is still generated by its receipt-bound matcher policy
    (c05-matcher-v4, unchanged). A pattern is an ACTIVE exclusion trigger iff, for at
    least one of its provenance kinds, its normalized tokens meet that kind's floor here
    (tokens, characters of the space-joined view, distinct two-letter tokens; the
    ``streaming.informative`` rule). Inactive patterns are never compiled.

    Frozen floors: prompt and item fallback 8/40/5 (the frozen answer/combined floor);
    sentence 3/12/3, answer 8/40/5 and combined 8/40/5 unchanged from c05-matcher-v4.
    Rationale: whole 4-7-token prompt variants (e.g. a HellaSwag ``ctx_b`` fragment found
    in 425,170 production documents) are not discriminative alone; short BLiMP sentence
    protection is kept.

    ``reviewed_items_without_active_trigger`` has no default: the operator states the
    exact number of benchmark items left with no active pattern (an accepted, measured
    recall loss); the C05 run recomputes it from the verified index and refuses on any
    difference.
    """

    version: Literal["c05-trigger-floors-v1"] = "c05-trigger-floors-v1"
    rule: Literal["any-provenance-kind-meets-its-floor-v1"] = (
        "any-provenance-kind-meets-its-floor-v1"
    )
    distinct_rule: Literal["tokens-with-two-letters-v1"] = "tokens-with-two-letters-v1"
    prompt: Informativeness = FLOOR_8_40_5
    sentence: Informativeness = Informativeness(tokens=3, characters=12, distinct=3)
    answer: Informativeness = FLOOR_8_40_5
    combined: Informativeness = FLOOR_8_40_5
    item_fallback: Informativeness = FLOOR_8_40_5
    reviewed_items_without_active_trigger: int = Field(ge=0)

    def floor(self, kind: str) -> Informativeness:
        if kind not in TRIGGER_KINDS:
            raise C05Error("protected pattern provenance kind is unknown")
        value: Informativeness = getattr(self, kind)
        return value

    def active(self, tokens: tuple[str, ...], kinds: Iterable[str]) -> bool:
        """True when the pattern meets the floor of at least one of its kinds."""
        size = len(tokens)
        characters = sum(map(len, tokens)) + size - 1 if size else 0  # == len(" ".join())
        for kind in kinds:
            floor = self.floor(kind)
            if (
                size >= floor.tokens
                and characters >= floor.characters
                and _distinct_at_least(tokens, floor.distinct)
            ):
                return True
        return False

    def identity(self) -> str:
        return digest(self.model_dump(mode="json"))


class ProductionPolicyV3(ProductionPolicy):
    """c05-production-v3: separate contamination exclusion from split-leakage grouping.

    * ``matcher``: the protected index GENERATION policy, bound to the benchmark
      receipt (must be c05-matcher-v4; the index is generated unchanged).
    * ``trigger``: which of those patterns exclude a document (:class:`TriggerPolicy`).
    * ``exclusion_lineage`` = ``query-seed-derivation-family-v1``: the CONTAMINATION
      family is the closure over exact/near duplicates, existing parent documents and
      every known-lineage-v3 key EXCEPT keys that only a SYNTH row's
      ``additional_seed_url`` contributes. A hit excludes its whole contamination
      family; ``additional_seed_url`` never merges contamination families and never
      propagates exclusion.
    * ``lineage`` = ``known-lineage-v3``: the SPLIT-LEAKAGE family (unchanged, every key
      including ``additional_seed_url``). Only split families without any excluded
      member are eligible for diagnostic/audit; split grouping never changes which
      documents are excluded.

    Membership rows carry ``split_group`` and ``exclusion_group`` (c05_membership_v3);
    there is no shared ``lineage_group`` field.
    """

    version: Literal["c05-production-v3"] = "c05-production-v3"  # type: ignore[assignment]
    stage_order: Literal[
        "hash-match-facts/group-exclude-by-derivation-split-by-lineage/publish-v2"
    ] = "hash-match-facts/group-exclude-by-derivation-split-by-lineage/publish-v2"  # type: ignore[assignment]
    matcher: MatcherPolicyV4 = MatcherPolicyV4()
    trigger: TriggerPolicy
    exclusion_lineage: Literal["query-seed-derivation-family-v1"] = (
        "query-seed-derivation-family-v1"
    )


AnyProductionPolicy = Annotated[
    ProductionPolicy | ProductionPolicyV3, Field(discriminator="version")
]
_PRODUCTION_ADAPTER: TypeAdapter[ProductionPolicy | ProductionPolicyV3] = TypeAdapter(
    AnyProductionPolicy
)


def production_policy(data: Any) -> ProductionPolicy:
    """Validate a frozen production policy by its explicit version (v2 or v3)."""
    return _PRODUCTION_ADAPTER.validate_python(data)


def scoped(policy: ProductionPolicy) -> bool:
    """True for policies that separate exclusion families from split families."""
    return isinstance(policy, ProductionPolicyV3)


def trigger_of(policy: ProductionPolicy) -> TriggerPolicy | None:
    return policy.trigger if isinstance(policy, ProductionPolicyV3) else None


class Resources(FrozenModel):
    """Proposed ceilings; an operator decision must restate every field.

    The field set and defaults are unchanged by the compact parallel engine; two
    meanings are restated there (``capacity.storage_bounds``):

    * ``index_bytes`` is the hard ceiling of the whole compact working index
      (fact units, their staging, grouping arrays, band index and sort spills),
      enforced by a byte ledger before every write.
    * ``journal_bytes`` must still cover the derived rollback-journal bound of a
      store of ``index_bytes``; that SQLite store exists only when heuristic
      review is enabled.
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
    # Reviewed maximum of worker processes: protected benchmark preparation
    # (build-local), the C05 scan and grouping/publication jobs (1 = in-process).
    # Never a CLI override; results never depend on it.
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
