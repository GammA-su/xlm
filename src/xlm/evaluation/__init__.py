"""Evaluation subsystem providing native likelihood, benchmark fixtures, and diagnostics.

Public names are imported lazily (PEP 562): importing a pure submodule such as
``xlm.evaluation.cadence`` or ``xlm.evaluation.recoverability`` no longer imports
torch through this package. ``from xlm.evaluation import X`` behaves as before.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS: dict[str, str] = {
    "BenchmarkFixtureDataset": "fixtures",
    "BenchmarkFixtureScorer": "scorer",
    "BoundaryPolicy": "likelihood",
    "ConditionalLikelihoodScorer": "likelihood",
    "ContinuationTooLongError": "likelihood",
    "CrossBoundaryMergeError": "likelihood",
    "DocumentScoringResult": "likelihood",
    "FixedDiagnosticProbe": "diagnostics",
    "InvalidChoiceError": "fixtures",
    "MinimalPairItem": "fixtures",
    "MinimalPairItemResult": "scorer",
    "MultipleChoiceItem": "fixtures",
    "MultipleChoiceItemResult": "scorer",
    "RawLikelihoodCache": "scorer",
    "ScoringRequest": "likelihood",
    "ScoringResult": "likelihood",
    "SourceDiagnostic": "diagnostics",
    "TokenPair": "likelihood",
    "WindowTruncationPolicy": "likelihood",
    "ZeroScoredTokensError": "likelihood",
    "evaluate_fixed_diagnostic_probes": "diagnostics",
    "evaluate_validation_sources": "diagnostics",
    "get_synthetic_minimal_pair_fixture": "fixtures",
    "get_synthetic_multiple_choice_fixture": "fixtures",
    "prepare_token_pair": "likelihood",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
