"""Versioned research idea cards with honest status tracking (P18, A33).

A card records the hypothesis, its alleged originality, the closest prior art
found so far, falsifiable predictions and the experiment that could kill it.
Statuses distinguish every stage of review; none of them certifies that nobody
has ever tried the idea, and validation actively refuses certainty phrasing
about novelty.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, field_validator

from xlm.config.schemas import StrictConfigModel

IDEA_CARD_VERSION = "1"


class IdeaStatus(StrEnum):
    """Review lifecycle of a research idea. No value certifies novelty."""

    PROPOSED = "proposed"
    PRIOR_ART_UNREVIEWED = "prior-art-unreviewed"
    POSSIBLE_REDISCOVERY = "possible-rediscovery"
    NO_CLOSE_PRIOR_ART_FOUND = "no-close-prior-art-found"
    IMPLEMENTED = "implemented"
    INCONCLUSIVE = "inconclusive"
    PROMISING = "promising"
    REPLICATED = "replicated"


class ResultStatus(StrEnum):
    """Outcome of the idea's experimental program, kept separate from novelty."""

    NOT_RUN = "not-run"
    INCONCLUSIVE = "inconclusive"
    FALSIFIED = "falsified"
    SUPPORTED = "supported"


# Phrasing that claims universal novelty. Validation refuses it: a card may
# record "no close prior art found" with dated sources and search terms, which
# is a search result, never a proof nobody tried it.
NOVELTY_CERTAINTY_PATTERNS = (
    r"\bfirst\s+(ever|known|published)\b",
    r"\bproven?\s+novel\b",
    r"\bcertified\s+novel\b",
    r"\bnovelty\s+(certified|proven|guaranteed)\b",
    r"\bnobody\s+has\s+ever\b",
    r"\bnever\s+(been\s+)?tried\s+before\b",
)


class PriorArtEntry(StrictConfigModel):
    """One dated prior-art record with the search that found it."""

    description: str = Field(min_length=1)
    sources: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    date: str = Field(default="")


class CostRecord(StrictConfigModel):
    """Parameter/compute costs of the idea versus its baseline."""

    parameters: str = Field(default="")
    compute: str = Field(default="")
    extra_state: str = Field(default="")


class IdeaCard(StrictConfigModel):
    """A versioned research idea card. All fields are required by validation."""

    card_version: str = Field(default=IDEA_CARD_VERSION)
    idea_id: str = Field(min_length=1)
    idea_version: str = Field(default="1")
    title: str = Field(default="")
    category: str = Field(default="")
    status: IdeaStatus = Field(default=IdeaStatus.PROPOSED)
    bottleneck: str = Field(default="")
    mechanism: str = Field(default="")
    original_element: str = Field(default="")
    prior_art: list[PriorArtEntry] = Field(default_factory=list)
    prior_art_status: str = Field(default="")
    predictions: list[str] = Field(default_factory=list)
    falsification_experiment: str = Field(default="")
    allowed_information: str = Field(default="")
    costs: CostRecord = Field(default_factory=CostRecord)
    baselines: list[str] = Field(default_factory=list)
    ablations: list[str] = Field(default_factory=list)
    tuning_allowance: str = Field(default="")
    kill_rules: list[str] = Field(default_factory=list)
    promotion_rules: list[str] = Field(default_factory=list)
    result_status: ResultStatus = Field(default=ResultStatus.NOT_RUN)

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        allowed = ("architecture", "objective", "optimizer", "tokenizer")
        if value and value not in allowed:
            raise ValueError(f"category must be one of {allowed}, got '{value}'")
        return value


class IdeaValidationError(ValueError):
    """Raised when an idea card is incomplete or claims too much."""


def blank_card(idea_id: str, title: str = "", category: str = "") -> IdeaCard:
    """Create an empty proposed card. Validation fails until it is filled."""
    return IdeaCard(idea_id=idea_id, title=title, category=category)


def load_card(path: Path | str) -> IdeaCard:
    """Load and strictly validate an idea card file."""
    card_path = Path(path)
    if not card_path.is_file():
        raise IdeaValidationError(f"idea card not found: {card_path}")
    data = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise IdeaValidationError(f"idea card at {card_path} must be a mapping")
    try:
        return IdeaCard.model_validate(data)
    except Exception as exc:
        raise IdeaValidationError(f"idea card failed schema validation: {exc}") from exc


def save_card(card: IdeaCard, path: Path | str) -> Path:
    """Persist an idea card atomically."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(card.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    tmp.replace(target)
    return target


def validate_card(card: IdeaCard) -> list[str]:
    """Return the list of problems blocking an idea card. Empty means valid.

    Missing definitions are blockers. Certainty phrasing about universal
    novelty is refused outright.
    """
    problems: list[str] = []
    required_text = {
        "title": card.title,
        "category": card.category,
        "bottleneck": card.bottleneck,
        "mechanism": card.mechanism,
        "original_element": card.original_element,
        "falsification_experiment": card.falsification_experiment,
        "allowed_information": card.allowed_information,
        "tuning_allowance": card.tuning_allowance,
    }
    for field_name, value in required_text.items():
        if not value.strip():
            problems.append(f"missing required field '{field_name}'")
    required_lists: dict[str, list[str]] = {
        "predictions": card.predictions,
        "baselines": card.baselines,
        "ablations": card.ablations,
        "kill_rules": card.kill_rules,
        "promotion_rules": card.promotion_rules,
    }
    for field_name, values in required_lists.items():
        if not values or not any(v.strip() for v in values):
            problems.append(f"missing required list '{field_name}'")
    if not card.prior_art:
        problems.append(
            "missing required list 'prior_art' (record the search even if empty-handed)"
        )
    else:
        for entry in card.prior_art:
            if not entry.search_terms:
                problems.append(
                    f"prior-art entry '{entry.description[:40]}' records no search terms"
                )
    if not card.costs.parameters.strip() or not card.costs.compute.strip():
        problems.append("missing cost record 'costs.parameters'/'costs.compute'")

    checked_text = f"{card.original_element}\n{card.mechanism}\n{card.prior_art_status}"
    for pattern in NOVELTY_CERTAINTY_PATTERNS:
        if re.search(pattern, checked_text, flags=re.IGNORECASE):
            problems.append(
                "novelty certainty phrasing refused: a card may record "
                "'no close prior art found' with dated sources, never a proof "
                "that nobody tried the idea"
            )
            break
    return problems


def validate_card_file(path: Path | str) -> list[str]:
    """Load and validate, returning problems (load errors become problems)."""
    try:
        return validate_card(load_card(path))
    except IdeaValidationError as exc:
        return [str(exc)]
