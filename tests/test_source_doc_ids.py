"""Centralized file-row document IDs: collision safety and determinism.

Offline only. Every file-row adapter must give different files different
IDs for the same file-local row, stable IDs across reruns, and IDs that do
not depend on workers, attempts, or OS path spellings.
"""

from __future__ import annotations

import hashlib

import pytest

from xlm.data.adapters.jsonl import JsonlAdapter
from xlm.data.adapters.mix01_adapters import (
    CommonPileAdapter,
    EssentialWebAdapter,
    FinePdfsAdapter,
    FineWikiAdapter,
    IfmGeneralAdapter,
    IfmPlanningAdapter,
    NemotronOrganicAdapter,
    SimpleStoriesAdapter,
    SynthExplanationsAdapter,
    Txt360WebAdapter,
    WikiRewriteAdapter,
)
from xlm.data.adapters.source_ids import (
    SOURCE_DOC_ID_VERSION,
    SOURCE_FILE_KEY_HEX_CHARS,
    canonical_source_doc_id,
    normalize_source_file,
    source_file_key,
)

FILE_A = "data/train/000.parquet"
FILE_B = "data/eval/000.parquet"


def _records() -> dict[str, dict[str, object]]:
    return {
        "essential_web": {
            "text": "t",
            "taxonomy": "science",
            "quality_tier": "q",
            "language": "en",
        },
        "nemotron": {"text": "t", "quality_category": "High-Quality"},
        "synth": {
            "context": "c",
            "question": "q",
            "explanation": "e",
            "language": "en",
        },
        "wiki_rewrite": {"text": "t"},
        "finewiki": {"title": "T", "text": "body", "in_language": "en"},
        "finepdfs": {"text": "t", "language": "eng_Latn", "extractor": "docling"},
        "ifm": {"text": "t"},
        "common_pile": {"text": "t"},
        "simple_stories": {"story": "once upon a time"},
        "txt360": {"text": "t", "quality_band": "web-high-medium"},
    }


def _adapters() -> dict[str, tuple[object, str, str]]:
    records = _records()
    return {
        "essential_web": (
            EssentialWebAdapter(),
            records["essential_web"],
            "essential_web",
        ),
        "nemotron": (
            NemotronOrganicAdapter("High-Quality"),
            records["nemotron"],
            "nemotron_cc21:High-Quality",
        ),
        "synth": (SynthExplanationsAdapter(), records["synth"], "synth"),
        "wiki_rewrite": (WikiRewriteAdapter(), records["wiki_rewrite"], "wiki_rewrite"),
        "finewiki": (FineWikiAdapter(), records["finewiki"], "finewiki"),
        "finepdfs": (FinePdfsAdapter(), records["finepdfs"], "finepdfs"),
        "ifm_general": (IfmGeneralAdapter(), records["ifm"], "ifm_behaviors:general"),
        "ifm_planning": (IfmPlanningAdapter(), records["ifm"], "ifm_behaviors:planning"),
        "common_pile": (CommonPileAdapter(), records["common_pile"], "common_pile"),
        "simple_stories": (
            SimpleStoriesAdapter(),
            records["simple_stories"],
            "simple_stories",
        ),
        "txt360": (Txt360WebAdapter(), records["txt360"], "txt360"),
    }


def test_version_contract_is_explicit() -> None:
    assert SOURCE_DOC_ID_VERSION == 1
    assert SOURCE_FILE_KEY_HEX_CHARS == 16
    doc_id = canonical_source_doc_id("ns", FILE_A, 3)
    assert doc_id == f"ns:v{SOURCE_DOC_ID_VERSION}:{source_file_key(FILE_A)}:3"
    namespace, version, key, row = doc_id.rsplit(":", 3)
    assert (namespace, version, row) == ("ns", "v1", "3")
    assert len(key) == SOURCE_FILE_KEY_HEX_CHARS


def test_file_key_is_sha256_not_builtin_hash() -> None:
    expected = hashlib.sha256(FILE_A.encode("utf-8")).hexdigest()[:16]
    assert source_file_key(FILE_A) == expected
    assert source_file_key(FILE_A) != source_file_key(FILE_B)


def test_separator_normalization() -> None:
    assert source_file_key("a\\b\\000.parquet") == source_file_key("a/b/000.parquet")
    assert normalize_source_file(".\\a\\\\b//000.parquet") == "a/b/000.parquet"
    assert normalize_source_file("./a/000.parquet") == "a/000.parquet"


def test_same_stem_different_directories_differ() -> None:
    assert source_file_key("dir_a/000.parquet") != source_file_key("dir_b/000.parquet")
    assert canonical_source_doc_id("ns", "dir_a/000.parquet", 0) != (
        canonical_source_doc_id("ns", "dir_b/000.parquet", 0)
    )


def test_invalid_inputs_refused() -> None:
    with pytest.raises(ValueError):
        canonical_source_doc_id("", FILE_A, 0)
    with pytest.raises(ValueError):
        canonical_source_doc_id("has space", FILE_A, 0)
    with pytest.raises(ValueError):
        canonical_source_doc_id("ns", "", 0)
    with pytest.raises(ValueError):
        canonical_source_doc_id("ns", FILE_A, -1)
    with pytest.raises(ValueError):
        canonical_source_doc_id("ns", FILE_A, True)


def test_every_adapter_separates_same_row_across_files() -> None:
    for name, (adapter, record, _namespace) in _adapters().items():
        first = adapter.adapt(record, source_file=FILE_A, source_row=0, source_revision="r")  # type: ignore[attr-defined]
        second = adapter.adapt(record, source_file=FILE_B, source_row=0, source_revision="r")  # type: ignore[attr-defined]
        assert first.doc_id != second.doc_id, name
        again = adapter.adapt(record, source_file=FILE_A, source_row=0, source_revision="r")  # type: ignore[attr-defined]
        assert again.doc_id == first.doc_id, name
        assert again.to_dict() == first.to_dict(), name


def test_every_adapter_uses_central_format() -> None:
    for name, (adapter, record, namespace) in _adapters().items():
        doc = adapter.adapt(record, source_file=FILE_A, source_row=7, source_revision="r")  # type: ignore[attr-defined]
        assert doc.doc_id == canonical_source_doc_id(namespace, FILE_A, 7), name


def test_ifm_subsets_stay_distinct() -> None:
    record = _records()["ifm"]
    general = IfmGeneralAdapter().adapt(
        record, source_file=FILE_A, source_row=0, source_revision="r"
    )
    planning = IfmPlanningAdapter().adapt(
        record, source_file=FILE_A, source_row=0, source_revision="r"
    )
    assert general.doc_id != planning.doc_id
    assert general.doc_id.startswith("ifm_behaviors:general:v1:")
    assert planning.doc_id.startswith("ifm_behaviors:planning:v1:")


def test_lineage_fields_unchanged_by_new_ids() -> None:
    record = _records()["finewiki"]
    doc = FineWikiAdapter().adapt(record, source_file=FILE_A, source_row=4, source_revision="rev-1")
    assert doc.source_id == "finewiki"
    assert doc.source_revision == "rev-1"
    assert doc.source_file == FILE_A
    assert doc.source_row == 4
    assert doc.raw_hash == doc.clean_hash
    assert len(doc.raw_hash) == 64


def test_jsonl_upstream_id_preserved_and_fallback_central() -> None:
    adapter = JsonlAdapter(source_id="local", source_revision="r")
    kept = adapter.process_line(
        b'{"id": "upstream-9", "text": "t", "split": "train"}',
        source_file="a.jsonl",
        source_row=0,
        default_split="train",
    )
    assert kept.doc_id == "upstream-9"
    fallback = adapter.process_line(
        b'{"text": "t", "split": "train"}',
        source_file="dir_a/f.jsonl",
        source_row=0,
        default_split="train",
    )
    assert fallback.doc_id == canonical_source_doc_id("local", "dir_a/f.jsonl", 0)
    other = JsonlAdapter(source_id="local", source_revision="r").process_line(
        b'{"text": "t", "split": "train"}',
        source_file="dir_b/f.jsonl",
        source_row=0,
        default_split="train",
    )
    assert other.doc_id != fallback.doc_id
