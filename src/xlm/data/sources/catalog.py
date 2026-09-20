"""Catalog schemas and models for dataset discovery sources adhering to C01 and C04."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model forbidding unexpected keys and validating assignments."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )


class CandidateSourceEntry(StrictModel):
    """A candidate dataset repository shortlisted for discovery and evaluation."""

    candidate_number: int = Field(gt=0, description="Sequential candidate number from shortlist")
    source_id: str = Field(min_length=1, description="Unique short identifier in XLM")
    provider: str = Field(min_length=1, description="Source provider: huggingface, https, local")
    repository: str = Field(min_length=1, description="Repository identifier or remote path")
    revision: str | None = Field(
        default=None, description="Pinned immutable commit hash or content digest"
    )
    subset_hints: list[str] = Field(
        default_factory=list, description="Discovery hints, not executable selectors"
    )
    actual_split: str | None = Field(default=None, description="Verified target split")
    selected_files: list[str] = Field(
        default_factory=list, description="Selected file inventory paths"
    )
    schema_fingerprint: str | None = Field(
        default=None, description="Verified schema and metadata fingerprint"
    )
    adapter_id: str | None = Field(
        default=None, description="Identifier of verified extractor adapter"
    )
    license_review: str = Field(default="pending", description="License review status")
    provenance_review: str = Field(
        default="pending", description="Lineage/provenance review status"
    )
    operator_approved: bool = Field(default=False, description="Explicit operator approval boolean")
    live_pilot_verified: bool = Field(default=False, description="Pilot verification flag")
    notes: str = Field(default="", description="Operator guidance and discovery notes")


class DatasetCatalogDraft(StrictModel):
    """The dataset discovery catalog manifest conforming to manifests/datasets.catalog.yaml."""

    schema_version: int = Field(default=1, description="Catalog manifest schema version")
    kind: str = Field(default="source_catalog_draft", description="Catalog schema kind identifier")
    catalog_id: str = Field(min_length=1, description="Identifier of discovery catalog")
    status: str = Field(default="discovery_only", description="Catalog operational status")
    notes: str = Field(default="", description="Catalog-level guidance")
    deny_direct_sources: list[str] = Field(
        default_factory=lambda: ["HuggingFaceFW/fineweb", "HuggingFaceFW/fineweb-edu"],
        description="Direct repositories forbidden by policy",
    )
    silent_fallback_allowed: bool = Field(
        default=False,
        description="Strict policy prohibiting silent substitutions or fallback",
    )
    sources: list[CandidateSourceEntry] = Field(
        default_factory=list,
        description="Candidate source entries in the catalog",
    )

    @field_validator("silent_fallback_allowed")
    @classmethod
    def validate_no_silent_fallback(cls, v: bool) -> bool:
        if v is not False:
            raise ValueError(
                "silent_fallback_allowed must strictly be False per XLM Contract C04 and A13."
            )
        return v

    @model_validator(mode="after")
    def validate_unique_identifiers(self) -> DatasetCatalogDraft:
        source_ids: set[str] = set()
        candidate_numbers: set[int] = set()
        for src in self.sources:
            if src.source_id in source_ids:
                raise ValueError(f"Duplicate source_id in catalog: '{src.source_id}'")
            source_ids.add(src.source_id)

            if src.candidate_number in candidate_numbers:
                raise ValueError(f"Duplicate candidate_number in catalog: {src.candidate_number}")
            candidate_numbers.add(src.candidate_number)
        return self

    def get_source(self, source_id: str) -> CandidateSourceEntry | None:
        """Find candidate entry by source_id."""
        for src in self.sources:
            if src.source_id == source_id:
                return src
        return None

    def get_source_by_number(self, number: int) -> CandidateSourceEntry | None:
        """Find candidate entry by candidate number."""
        for src in self.sources:
            if src.candidate_number == number:
                return src
        return None


def load_catalog(path: Path | str) -> DatasetCatalogDraft:
    """Load and strictly validate a dataset catalog YAML file."""
    catalog_path = Path(path)
    if not catalog_path.is_file():
        raise FileNotFoundError(f"Dataset catalog file not found: {catalog_path}")

    raw_text = catalog_path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw_text)
    if not isinstance(data, dict):
        raise ValueError(f"Catalog at {catalog_path} must be a YAML mapping")

    return DatasetCatalogDraft.model_validate(data)
