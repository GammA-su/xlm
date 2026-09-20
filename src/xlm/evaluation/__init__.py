"""Evaluation subsystem providing native likelihood, benchmark fixtures, and diagnostics."""

from xlm.evaluation.diagnostics import (
    FixedDiagnosticProbe,
    SourceDiagnostic,
    evaluate_fixed_diagnostic_probes,
    evaluate_validation_sources,
)
from xlm.evaluation.fixtures import (
    BenchmarkFixtureDataset,
    InvalidChoiceError,
    MinimalPairItem,
    MultipleChoiceItem,
    get_synthetic_minimal_pair_fixture,
    get_synthetic_multiple_choice_fixture,
)
from xlm.evaluation.likelihood import (
    BoundaryPolicy,
    ConditionalLikelihoodScorer,
    ContinuationTooLongError,
    CrossBoundaryMergeError,
    DocumentScoringResult,
    ScoringRequest,
    ScoringResult,
    TokenPair,
    WindowTruncationPolicy,
    ZeroScoredTokensError,
    prepare_token_pair,
)
from xlm.evaluation.scorer import (
    BenchmarkFixtureScorer,
    MinimalPairItemResult,
    MultipleChoiceItemResult,
    RawLikelihoodCache,
)

__all__ = [
    "BenchmarkFixtureDataset",
    "BenchmarkFixtureScorer",
    "BoundaryPolicy",
    "ConditionalLikelihoodScorer",
    "ContinuationTooLongError",
    "CrossBoundaryMergeError",
    "DocumentScoringResult",
    "FixedDiagnosticProbe",
    "InvalidChoiceError",
    "MinimalPairItem",
    "MinimalPairItemResult",
    "MultipleChoiceItem",
    "MultipleChoiceItemResult",
    "RawLikelihoodCache",
    "ScoringRequest",
    "ScoringResult",
    "SourceDiagnostic",
    "TokenPair",
    "WindowTruncationPolicy",
    "ZeroScoredTokensError",
    "evaluate_fixed_diagnostic_probes",
    "evaluate_validation_sources",
    "get_synthetic_minimal_pair_fixture",
    "get_synthetic_multiple_choice_fixture",
    "prepare_token_pair",
]
