"""Source denial policies, license evaluation rules, and legal guardrails adhering to C04."""

from __future__ import annotations

import re
from enum import StrEnum

LEGAL_DISCLAIMER = (
    "The software records evidence and policy status; it must not purport to certify legal rights."
)

# Standardized lowercase identifiers for directly denied source repositories
DENIED_DIRECT_SOURCES: frozenset[str] = frozenset(
    [
        "huggingfacefw/fineweb",
        "huggingfacefw/fineweb-edu",
    ]
)


class DirectSourceDeniedError(RuntimeError):
    """Raised when an attempt is made to access, probe, or admit a denied direct source."""


class SilentFallbackDeniedError(RuntimeError):
    """Raised when an automated fallback or silent source substitution is attempted."""


class LicenseUsagePolicy(StrEnum):
    """Declared policy choice for license evaluation."""

    STRICT_RESEARCH = "strict_research"
    STRICT_COMMERCIAL = "strict_commercial"


class LicenseReviewStatus(StrEnum):
    """Review states for source licensing and provenance."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESTRICTED = "restricted"


class BenchmarkContaminationRisk(StrEnum):
    """C04 source risk, never proof of benchmark cleanliness (C04 risk v2).

    CLEAN is the legacy acquisition classification; it makes no zero-
    contamination claim. Known possible contamination uses SUSPECT_WITH_MITIGATION.
    """

    CLEAN = "clean"
    SUSPECT = "suspect"
    SUSPECT_WITH_MITIGATION = "suspect_with_mitigation"
    DISABLED_PENDING_AUDIT = "disabled_pending_audit"


def normalize_repo_id(repo_or_url: str) -> str:
    """Normalize a repository identifier or URL to a canonical lowercase path.

    Examples:
        'HuggingFaceFW/fineweb' -> 'huggingfacefw/fineweb'
        'https://huggingface.co/datasets/HuggingFaceFW/fineweb-edu/' -> 'huggingfacefw/fineweb-edu'
        'hf.co/datasets/nvidia/Nemotron-CC-v2.1' -> 'nvidia/nemotron-cc-v2.1'
    """
    clean = repo_or_url.strip().lower()
    # Strip URL schemes and domain prefixes
    clean = re.sub(r"^https?://", "", clean)
    clean = re.sub(r"^(?:www\.)?(?:huggingface\.co|hf\.co)/datasets/", "", clean)
    clean = re.sub(r"^(?:www\.)?(?:huggingface\.co|hf\.co)/", "", clean)
    clean = clean.strip("/")
    return clean


def is_denied_source(repo_or_url: str) -> bool:
    """Determine whether a repository matches the explicit direct source denylist.

    Note: FineWiki (HuggingFaceFW/finewiki) and FinePDFs-Edu (HuggingFaceFW/finepdfs-edu)
    are legitimate shortlisted candidates and are NOT denied.
    """
    normalized = normalize_repo_id(repo_or_url)
    return normalized in DENIED_DIRECT_SOURCES


def check_denial_policy(repo_or_url: str) -> None:
    """Raise DirectSourceDeniedError if repository is directly denied."""
    if is_denied_source(repo_or_url):
        raise DirectSourceDeniedError(
            f"Direct source '{repo_or_url}' is explicitly denied by XLM Contract C04 and A13. "
            "FineWeb and FineWeb-Edu cannot be used as sources or fallbacks."
        )


def check_silent_fallback(requested_source: str, candidate_fallback: str) -> None:
    """Raise SilentFallbackDeniedError if an automatic fallback or substitution is attempted."""
    if requested_source != candidate_fallback:
        raise SilentFallbackDeniedError(
            f"Attempted silent substitution of '{requested_source}' with '{candidate_fallback}'. "
            "Contract C04 strictly prohibits silent fallback or renormalization."
        )


def evaluate_license_review(
    declared_license: str | None,
    review_status: str,
    usage_policy: LicenseUsagePolicy = LicenseUsagePolicy.STRICT_RESEARCH,
) -> tuple[bool, str]:
    """Evaluate license compliance against policy.

    Unknown licenses are never auto-approved. Permissive tags cannot erase
    unreviewed upstream restrictions.
    """
    status_lower = review_status.strip().lower()
    if status_lower != LicenseReviewStatus.APPROVED:
        return False, f"License review status is '{review_status}', not 'approved'."

    if not declared_license or declared_license.strip().lower() in ("unknown", "other", "none"):
        return False, "Unknown or unreviewed license cannot be auto-approved."

    return True, "License approved under declared usage policy."
