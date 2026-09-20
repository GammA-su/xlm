"""Authored offline benchmark-shaped fixtures complying with Contract C11 and Amendment 6.

All datasets and items in this module are strictly marked as synthetic/offline fixtures
for unit testing, development evaluation, and offline vertical-slice verification.
They must not register themselves or be represented as official benchmark suites.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class InvalidChoiceError(ValueError):
    """Raised when an answer choice is invalid (e.g. empty string dividing by zero)."""


@dataclass(frozen=True)
class MultipleChoiceItem:
    """A multiple-choice evaluation item complying with Amendment 6."""

    item_id: str
    context: str
    choices: list[str]
    gold_index: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.choices:
            raise InvalidChoiceError(f"MultipleChoiceItem '{self.item_id}' has empty choices list.")
        if self.gold_index < 0 or self.gold_index >= len(self.choices):
            raise InvalidChoiceError(
                f"MultipleChoiceItem '{self.item_id}' gold_index={self.gold_index} "
                f"out of range for {len(self.choices)} choices."
            )
        for i, ch in enumerate(self.choices):
            if len(ch) == 0:
                raise InvalidChoiceError(
                    f"MultipleChoiceItem '{self.item_id}' choice at index {i} is empty, "
                    "which would divide by zero under character normalization."
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "context": self.context,
            "choices": list(self.choices),
            "gold_index": self.gold_index,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MinimalPairItem:
    """A minimal-pair linguistic acceptability item complying with Amendment 6."""

    item_id: str
    good_sentence: str
    bad_sentence: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.good_sentence.strip():
            raise ValueError(f"MinimalPairItem '{self.item_id}' has empty good_sentence.")
        if not self.bad_sentence.strip():
            raise ValueError(f"MinimalPairItem '{self.item_id}' has empty bad_sentence.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "good_sentence": self.good_sentence,
            "bad_sentence": self.bad_sentence,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class BenchmarkFixtureDataset:
    """A container for offline evaluation fixtures."""

    dataset_id: str
    kind: str  # 'multiple_choice' | 'minimal_pair'
    items: list[MultipleChoiceItem] | list[MinimalPairItem]
    is_offline_fixture: bool = True
    is_official_benchmark: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.is_offline_fixture:
            raise ValueError("BenchmarkFixtureDataset must have is_offline_fixture=True")
        if self.is_official_benchmark:
            raise ValueError("Synthetic fixture cannot register as an official benchmark suite.")

    def to_jsonl(self, path: Path) -> None:
        """Export items to JSON Lines file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(item.to_dict()) for item in self.items]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_synthetic_multiple_choice_fixture() -> BenchmarkFixtureDataset:
    """Return an authored synthetic multiple-choice evaluation dataset.

    Visibly labeled synthetic/offline. Uses varied choice lengths and distinct vocabulary
    to test both raw accuracy and character-normalized accuracy (acc_norm).
    """
    items = [
        MultipleChoiceItem(
            item_id="syn_mc_01",
            context="The capital of France is",
            choices=[" Paris", " London", " Berlin", " Rome"],
            gold_index=0,
            metadata={"domain": "geography"},
        ),
        MultipleChoiceItem(
            item_id="syn_mc_02",
            context="Water at sea level boils at",
            choices=[
                " zero degrees",
                " one hundred degrees Celsius",
                " fifty degrees",
                " ten degrees",
            ],
            gold_index=1,
            metadata={"domain": "science"},
        ),
        MultipleChoiceItem(
            item_id="syn_mc_03",
            context="The programmer fixed the syntax",
            choices=[" error in the code", " sandwich on the plate", " bicycle in the yard"],
            gold_index=0,
            metadata={"domain": "code"},
        ),
        MultipleChoiceItem(
            item_id="syn_mc_04",
            context="Causal language models predict the",
            choices=[" next token", " previous token", " random noise"],
            gold_index=0,
            metadata={"domain": "nlp"},
        ),
    ]
    return BenchmarkFixtureDataset(
        dataset_id="synthetic_offline_multiple_choice_v1",
        kind="multiple_choice",
        items=items,
        metadata={
            "description": "Authored synthetic multiple-choice fixture for P06 verification."
        },
    )


def get_synthetic_minimal_pair_fixture() -> BenchmarkFixtureDataset:
    """Return an authored synthetic minimal-pair evaluation dataset.

    Visibly labeled synthetic/offline. Tests relative sentence likelihood conditioned on BOS.
    """
    items = [
        MinimalPairItem(
            item_id="syn_pair_01",
            good_sentence="The cat sat on the comfortable mat.",
            bad_sentence="The cat sat on mat comfortable the.",
            metadata={"phenomenon": "word_order"},
        ),
        MinimalPairItem(
            item_id="syn_pair_02",
            good_sentence="She reads a new book every week.",
            bad_sentence="She read a new book every week she.",
            metadata={"phenomenon": "agreement"},
        ),
        MinimalPairItem(
            item_id="syn_pair_03",
            good_sentence="The compiler produces an executable binary.",
            bad_sentence="Compiler the binary executable produces an.",
            metadata={"phenomenon": "syntax"},
        ),
    ]
    return BenchmarkFixtureDataset(
        dataset_id="synthetic_offline_minimal_pair_v1",
        kind="minimal_pair",
        items=items,
        metadata={"description": "Authored synthetic minimal-pair fixture for P06 verification."},
    )
