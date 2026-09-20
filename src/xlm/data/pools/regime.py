"""Research-regime and campaign freeze manifests.

A **research regime** binds the things that must hold constant across a data search:
the cleaned pool, the frozen tokenizer, the diagnostic-validation and exclusion
policies, and the reference training and evaluation settings. It deliberately does
*not* name a mixture.

That separation is the point. If the regime included a mixture, then selecting a
different mixture during data search would look like it had already been frozen, and
a trial could appear to have been run under a settled regime when it was not. A
**campaign freeze** is the later, stricter object that binds the regime together with
the winning mixture and its exposure plan.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REGIME_MANIFEST_VERSION = "1"


class RegimeValidationError(ValueError):
    """Raised when a regime or campaign freeze is incomplete or inconsistent."""


@dataclass
class DiagnosticCorpusFreeze:
    """The frozen diagnostic corpus and its nested quick subset.

    Frozen once for the campaign. Its membership does not follow the winning mixture,
    so a source trial cannot improve its validation metric by changing what it is
    validated on.
    """

    diagnostic_doc_ids: list[str]
    quick_doc_ids: list[str]
    diagnostic_bytes: int
    quick_bytes: int
    membership_digest: str = ""

    def __post_init__(self) -> None:
        if not self.membership_digest:
            self.membership_digest = self.compute_digest()

    def compute_digest(self) -> str:
        payload = (
            "|".join(sorted(self.diagnostic_doc_ids))
            + "#quick:"
            + "|".join(sorted(self.quick_doc_ids))
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def validate(self) -> None:
        """Check the quick subset is genuinely nested and nothing is empty."""
        if not self.diagnostic_doc_ids:
            raise RegimeValidationError("diagnostic corpus is empty")
        stray = sorted(set(self.quick_doc_ids) - set(self.diagnostic_doc_ids))
        if stray:
            raise RegimeValidationError(
                f"quick subset is not nested inside the diagnostic corpus: {stray[:5]}"
            )
        if self.membership_digest != self.compute_digest():
            raise RegimeValidationError(
                "diagnostic membership digest does not match its document IDs"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReferenceSettings:
    """Reference training and evaluation settings the regime holds constant."""

    context_length: int = 512
    global_batch_valid_targets: int = 65_536
    precision_profile: str = "bf16_autocast_fp32_master"
    packing_policy: str = "causal_stream_eos_boundaries"
    evaluation_suite: str = "search"
    objective: str = "cross_entropy"
    optimizer: str = "adamw"
    schedule: str = "warmup_cosine"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchRegimeManifest:
    """The frozen research regime, deliberately without a mixture."""

    regime_id: str
    manifest_version: str
    created_at: str
    pool_id: str
    pool_content_digest: str
    tokenizer_fingerprint: str
    tokenizer_fit_id: str
    diagnostic: DiagnosticCorpusFreeze
    exclusion_policy_identity: str
    split_policy_identity: str
    reference: ReferenceSettings
    pool_tier: str = "demo_pilot"
    notes: list[str] = field(default_factory=list)

    @property
    def binds_a_mixture(self) -> bool:
        """A regime never binds a mixture; a campaign freeze does."""
        return False

    def validate(self) -> None:
        self.diagnostic.validate()
        for name in ("pool_id", "pool_content_digest", "tokenizer_fingerprint", "tokenizer_fit_id"):
            if not getattr(self, name):
                raise RegimeValidationError(f"regime is incomplete: '{name}' is empty")

    def identity_payload(self) -> str:
        body = self.to_dict()
        body.pop("created_at", None)
        body.pop("notes", None)
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["diagnostic"] = self.diagnostic.to_dict()
        data["reference"] = self.reference.to_dict()
        return data

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(path)
        return path

    @staticmethod
    def load(path: Path) -> ResearchRegimeManifest:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["diagnostic"] = DiagnosticCorpusFreeze(**data["diagnostic"])
        data["reference"] = ReferenceSettings(**data["reference"])
        return ResearchRegimeManifest(**data)


def build_regime(
    pool_id: str,
    pool_content_digest: str,
    tokenizer_fingerprint: str,
    tokenizer_fit_id: str,
    diagnostic: DiagnosticCorpusFreeze,
    exclusion_policy_identity: str,
    split_policy_identity: str,
    reference: ReferenceSettings | None = None,
    pool_tier: str = "demo_pilot",
    notes: list[str] | None = None,
) -> ResearchRegimeManifest:
    """Build and validate a research regime manifest."""
    manifest = ResearchRegimeManifest(
        regime_id="regime_pending",
        manifest_version=REGIME_MANIFEST_VERSION,
        created_at=datetime.now(UTC).isoformat(),
        pool_id=pool_id,
        pool_content_digest=pool_content_digest,
        tokenizer_fingerprint=tokenizer_fingerprint,
        tokenizer_fit_id=tokenizer_fit_id,
        diagnostic=diagnostic,
        exclusion_policy_identity=exclusion_policy_identity,
        split_policy_identity=split_policy_identity,
        reference=reference or ReferenceSettings(),
        pool_tier=pool_tier,
        notes=list(notes or []),
    )
    manifest.validate()
    manifest.regime_id = (
        "regime_" + hashlib.sha256(manifest.identity_payload().encode("utf-8")).hexdigest()[:20]
    )
    return manifest


@dataclass
class CampaignFreeze:
    """A regime plus the selected mixture and exposure plan.

    Created only after data search has chosen a mixture. Keeping this separate from
    the regime is what stops an in-progress search from being reported as a settled
    campaign.
    """

    campaign_id: str
    created_at: str
    regime_id: str
    regime_identity_digest: str
    mixture_id: str
    exposure_plan_id: str
    selection_evidence: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def binds_a_mixture(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(path)
        return path

    @staticmethod
    def load(path: Path) -> CampaignFreeze:
        return CampaignFreeze(**json.loads(path.read_text(encoding="utf-8")))


def freeze_campaign(
    regime: ResearchRegimeManifest,
    mixture_id: str,
    exposure_plan_id: str,
    selection_evidence: dict[str, Any] | None = None,
) -> CampaignFreeze:
    """Bind a validated regime to a selected mixture and exposure plan."""
    regime.validate()
    if not mixture_id or not exposure_plan_id:
        raise RegimeValidationError(
            "a campaign freeze requires both a mixture_id and an exposure_plan_id"
        )

    regime_digest = hashlib.sha256(regime.identity_payload().encode("utf-8")).hexdigest()
    campaign_id = (
        "campaign_"
        + hashlib.sha256(
            f"{regime.regime_id}:{mixture_id}:{exposure_plan_id}".encode()
        ).hexdigest()[:20]
    )

    return CampaignFreeze(
        campaign_id=campaign_id,
        created_at=datetime.now(UTC).isoformat(),
        regime_id=regime.regime_id,
        regime_identity_digest=regime_digest,
        mixture_id=mixture_id,
        exposure_plan_id=exposure_plan_id,
        selection_evidence=dict(selection_evidence or {}),
    )


def regime_accepts_mixture_change(
    before: ResearchRegimeManifest,
    after: ResearchRegimeManifest,
) -> bool:
    """Whether two regimes are the same, so a mixture change reuses pool and tokenizer.

    Changing only a mixture must leave the regime identical: the same pool, the same
    tokenizer and the same diagnostic corpus are reused rather than rebuilt (C06, C07).
    """
    return (
        before.regime_id == after.regime_id
        and before.pool_content_digest == after.pool_content_digest
        and before.tokenizer_fingerprint == after.tokenizer_fingerprint
        and before.diagnostic.membership_digest == after.diagnostic.membership_digest
    )
