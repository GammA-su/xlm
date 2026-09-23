"""Explicit adapter-to-Parquet-column contracts for selected-record projection.

Each entry lists exactly the top-level upstream columns an adapter reads:
required render fields plus every optional field it preserves when present.
Derived line-by-line from the certified adapter implementations — never by
guessing from arbitrary rows. Projecting to these columns lets acquisition
skip unrelated column chunks (e.g. SYNTH ``synthetic_reasoning``) without
changing adapter-visible behavior.

Parameterized adapters are keyed ``(adapter_id, config)`` so a config is
never ambiguous: ``("nemotron_organic", "High-Quality")``,
``("essential_web", "essential_science")``. Use :func:`columns_for` to
resolve, never a bare adapter id for parameterized families.
"""

from __future__ import annotations

#: (adapter_id, config or None) -> required top-level Parquet/JSON columns.
ADAPTER_COLUMN_CONTRACTS: dict[tuple[str, str | None], tuple[str, ...]] = {
    ("essential_web", "essential_science"): (
        "text",
        "eai_taxonomy",
        "quality_signals",
        "id",
        "pid",
        "metadata",
    ),
    ("essential_web", "essential_practical"): (
        "text",
        "eai_taxonomy",
        "quality_signals",
        "id",
        "pid",
        "metadata",
    ),
    ("essential_web", "essential_prose"): (
        "text",
        "eai_taxonomy",
        "quality_signals",
        "id",
        "pid",
        "metadata",
    ),
    ("nemotron_organic", "High-Quality"): ("text", "quality_category"),
    ("nemotron_organic", "Medium-High-Quality"): ("text", "quality_category"),
    ("synth_en", None): (
        "synth_id",
        "language",
        "query",
        "query_seed_text",
        "synthetic_answer",
        "seed_license",
        "exercise",
        "model",
        "words",
        "query_seed_url",
        "additional_seed_url",
    ),
    ("wiki_rewrite", None): ("text", "license", "metadata", "uuid"),
    ("finewiki_en", None): ("title", "text", "in_language"),
    ("finepdfs_en", None): (
        "text",
        "language",
        "extractor",
        "is_truncated",
        "token_count",
        "full_doc_lid",
        "full_doc_lid_score",
        "page_average_lid",
        "page_average_lid_score",
    ),
    ("ifm_general", None): ("text", "token_count"),
    ("ifm_planning", None): ("text", "token_count"),
    ("common_pile", None): ("text",),
    ("simple_stories", None): (
        "story",
        "topic",
        "theme",
        "style",
        "feature",
        "grammar",
        "persona",
        "initial_word_type",
        "initial_letter",
        "word_count",
        "character_count",
        "num_paragraphs",
        "avg_word_length",
        "avg_sentence_length",
        "flesch_reading_ease",
        "flesch_kincaid_grade",
        "dale_chall_readability_score",
        "num_stories_in_completion",
        "expected_num_stories_in_completion",
        "generation_id",
        "model",
    ),
    ("txt360_web", None): ("text", "quality_band"),
}

#: Adapters that require an explicit config to resolve columns.
PARAMETERIZED_ADAPTERS: frozenset[str] = frozenset({"essential_web", "nemotron_organic"})


def columns_for(adapter_id: str, config: str | None = None) -> tuple[str, ...]:
    """Resolve projected columns for an adapter spec, failing closed."""
    if adapter_id in PARAMETERIZED_ADAPTERS and not config:
        raise ValueError(
            f"adapter '{adapter_id}' requires an explicit config "
            "(e.g. 'nemotron_organic:High-Quality'); refusing ambiguous projection"
        )
    try:
        return ADAPTER_COLUMN_CONTRACTS[(adapter_id, config)]
    except KeyError:
        if config is not None:
            try:
                return ADAPTER_COLUMN_CONTRACTS[(adapter_id, None)]
            except KeyError:
                pass
        raise ValueError(
            f"no certified column contract for adapter '{adapter_id}'"
            + (f" config '{config}'" if config else "")
        ) from None


def parse_adapter_spec(spec: str) -> tuple[str, str | None]:
    """Parse ``adapter_id[:config]`` specs; empty parts are refused."""
    adapter_id, separator, config = spec.partition(":")
    if not adapter_id or (separator and not config):
        raise ValueError(f"adapter spec must be 'adapter_id' or 'adapter_id:config', got '{spec}'")
    return adapter_id, config or None
