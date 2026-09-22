"""SimpleStories live certification against 6 REAL pinned rows (read-only).

Reads ``D:\\Project\\xlm-operator-pilot\\adapter-cert-simple-stories01`` without
mutating it: 3 test rows plus 3 train rows. Test rows certify the adapter;
train rows prove the production-shape contract. Neither file is training input.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xlm.data.adapters.mix01_adapters import MissingFieldError, SimpleStoriesAdapter
from xlm.data.adapters.source_ids import canonical_source_doc_id

CERT_DIR = Path("D:/Project/xlm-operator-pilot/adapter-cert-simple-stories01")
REVISION = "e63b8adc3b1a1bdc7cac5b500d150b71346b0628"

EXPECTED_OPTIONAL_STRINGS = (
    "topic",
    "theme",
    "style",
    "feature",
    "grammar",
    "persona",
    "initial_word_type",
    "initial_letter",
    "generation_id",
    "model",
)
EXPECTED_OPTIONAL_NUMERICS = (
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
)


def _read(name: str, count: int) -> list[dict[str, Any]]:
    path = CERT_DIR / name
    assert path.is_file(), "live certification evidence is absent"
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(rows) == count, f"certification file {name} must hold {count} real rows"
    return rows


def _test_rows() -> list[dict[str, Any]]:
    return _read("real-records.jsonl", 3)


def _train_rows() -> list[dict[str, Any]]:
    return _read("real-train-records.jsonl", 3)


def _adapt(row: dict[str, Any], source_file: str, source_row: int) -> Any:
    upstream = {k: v for k, v in row.items() if not k.startswith("_cert_")}
    return SimpleStoriesAdapter().adapt(
        upstream, source_file=source_file, source_row=source_row, source_revision=REVISION
    )


def _check_certified_row(doc: Any, row: dict[str, Any], source_file: str, source_row: int) -> None:
    assert doc.text == row["story"]
    assert len(doc.text) == len(row["story"])
    assert doc.language == "en"
    assert doc.license_reference == "mit"
    assert doc.source_id == "simple_stories"
    assert doc.source_revision == REVISION
    assert doc.source_file == source_file
    assert doc.source_row == source_row
    assert doc.doc_id == canonical_source_doc_id("simple_stories", source_file, source_row)
    metadata = doc.source_metadata
    assert metadata["mix01_component"] == "simple_stories"
    assert "downstream language cleaning" in metadata["language_provenance"]
    assert "dataset-level MIT" in metadata["license_provenance"]
    for key in EXPECTED_OPTIONAL_STRINGS:
        assert metadata[key] == row[key], key
        assert isinstance(metadata[key], str)
    for key in EXPECTED_OPTIONAL_NUMERICS:
        assert metadata[key] == row[key], key
        assert isinstance(metadata[key], (int, float)) and not isinstance(metadata[key], bool)
    assert "story" not in metadata


def test_all_three_real_test_rows_accepted() -> None:
    for index, row in enumerate(_test_rows()):
        assert row["_cert_source_file"] == "data/test-00000-of-00001.parquet"
        assert row["_cert_source_row"] == index
        doc = _adapt(row, row["_cert_source_file"], row["_cert_source_row"])
        _check_certified_row(doc, row, row["_cert_source_file"], row["_cert_source_row"])
        again = _adapt(row, row["_cert_source_file"], row["_cert_source_row"])
        assert again.to_dict() == doc.to_dict()


def test_all_three_real_train_rows_accepted() -> None:
    for index, row in enumerate(_train_rows()):
        assert row["_cert_source_file"] == "data/train-00003-of-00007.parquet"
        assert row["_cert_source_row"] == index
        doc = _adapt(row, row["_cert_source_file"], row["_cert_source_row"])
        _check_certified_row(doc, row, row["_cert_source_file"], row["_cert_source_row"])


def test_certified_ids_unique_across_rows_and_files() -> None:
    seen: set[str] = set()
    for row in _test_rows() + _train_rows():
        doc = _adapt(row, row["_cert_source_file"], row["_cert_source_row"])
        assert doc.doc_id not in seen
        seen.add(doc.doc_id)
    assert len(seen) == 6


def test_missing_story_refused() -> None:
    adapter = SimpleStoriesAdapter()
    with pytest.raises(MissingFieldError, match="'story'"):
        adapter.adapt({}, source_file="f", source_row=0, source_revision="r")


def test_malformed_story_refused_without_coercion() -> None:
    adapter = SimpleStoriesAdapter()
    for bad in (None, 42, ["x"], {"x": 1}, "", True):
        with pytest.raises(MissingFieldError, match="'story'|non-empty string"):
            adapter.adapt({"story": bad}, source_file="f", source_row=0, source_revision="r")


def test_story_preserved_exactly_with_whitespace() -> None:
    adapter = SimpleStoriesAdapter()
    text = "  padded\n\nnewlines\tand\ttabs  "
    doc = adapter.adapt({"story": text}, source_file="f", source_row=0, source_revision="r")
    assert doc.text == text


def test_malformed_optional_metadata_refused() -> None:
    adapter = SimpleStoriesAdapter()
    with pytest.raises(MissingFieldError, match="'topic'"):
        adapter.adapt(
            {"story": "t", "topic": ["not", "a", "string"]},
            source_file="f",
            source_row=0,
            source_revision="r",
        )
    with pytest.raises(MissingFieldError, match="'word_count'"):
        adapter.adapt(
            {"story": "t", "word_count": "many"},
            source_file="f",
            source_row=0,
            source_revision="r",
        )
    with pytest.raises(MissingFieldError, match="'word_count'"):
        adapter.adapt(
            {"story": "t", "word_count": True},
            source_file="f",
            source_row=0,
            source_revision="r",
        )


def test_optional_metadata_absent_or_null() -> None:
    adapter = SimpleStoriesAdapter()
    doc = adapter.adapt({"story": "t"}, source_file="f", source_row=0, source_revision="r")
    for key in EXPECTED_OPTIONAL_STRINGS + EXPECTED_OPTIONAL_NUMERICS:
        assert key not in doc.source_metadata
    nulled = adapter.adapt(
        {"story": "t", "topic": None, "word_count": None},
        source_file="f",
        source_row=0,
        source_revision="r",
    )
    assert "topic" not in nulled.source_metadata
    assert "word_count" not in nulled.source_metadata


def test_empty_optional_strings_preserved() -> None:
    adapter = SimpleStoriesAdapter()
    doc = adapter.adapt(
        {"story": "t", "grammar": "", "persona": ""},
        source_file="f",
        source_row=0,
        source_revision="r",
    )
    assert doc.source_metadata["grammar"] == ""
    assert doc.source_metadata["persona"] == ""


def test_generation_provenance_preserved_not_identity() -> None:
    adapter = SimpleStoriesAdapter()
    first = adapter.adapt(
        {"story": "t", "generation_id": "gen-a", "model": "m"},
        source_file="f",
        source_row=0,
        source_revision="r",
    )
    assert first.source_metadata["generation_id"] == "gen-a"
    assert first.source_metadata["model"] == "m"
    second = adapter.adapt(
        {"story": "t", "generation_id": "gen-b", "model": "m"},
        source_file="f",
        source_row=0,
        source_revision="r",
    )
    assert second.doc_id == first.doc_id
