"""Validated comparison-recipe documents (P22 recipe set, P17 track rules).

A comparison recipe names its track, the two runs (null until evidence
exists), the allowed differences and the fixed fields. It never carries
scores: a draft must not masquerade as a result. The track name must be one
the eligibility checker understands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from xlm.comparison.tracks import TRACK_RULES
from xlm.config.schemas import StrictConfigModel


class ComparisonRecipe(StrictConfigModel):
    """A draft comparison declaration, validated but never a result."""

    schema_version: int = Field(default=1)
    kind: str = Field(default="comparison_recipe")
    id: str = Field(min_length=1)
    status: str = Field(default="draft_requires_evidence")
    track: str = Field(min_length=1)
    baseline_run: str | None = Field(default=None)
    candidate_run: str | None = Field(default=None)
    allowed_differences: list[str] = Field(default_factory=list)
    fixed: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def check_track_known(self) -> None:
        """Refuse tracks the eligibility checker cannot enforce."""
        if self.track not in TRACK_RULES:
            raise ValueError(
                f"comparison recipe '{self.id}' names unknown track '{self.track}'; "
                f"known: {sorted(TRACK_RULES)}"
            )


def load_comparison_recipe(path: Path | str) -> ComparisonRecipe:
    """Load, validate and track-check a comparison recipe file."""
    recipe_path = Path(path)
    if not recipe_path.is_file():
        raise FileNotFoundError(f"comparison recipe not found: {recipe_path}")
    data = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"comparison recipe at {recipe_path} must be a mapping")
    recipe = ComparisonRecipe.model_validate(data)
    recipe.check_track_known()
    return recipe


def recipe_to_dict(recipe: ComparisonRecipe) -> dict[str, Any]:
    """Serialize a validated recipe for reports."""
    return recipe.model_dump()
