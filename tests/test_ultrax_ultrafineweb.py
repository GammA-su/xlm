"""Authored certification for the UltraX Ultra-FineWeb adapter (offline only).

Uses the same certification machinery as the certified XLM adapters: direct
``adapt`` contracts, ``RowExtractorContract`` schema checks, centralized
file-row identity, canonical serialization roundtrips and bounded
record/byte accounting. Fixtures are authored synthetic records shaped like
the published UltraX-Ultra-FineWeb fields; they prove adapter logic, never
live compatibility. The real tiny operator probe is separate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xlm.data.adapters.columns import columns_for, parse_adapter_spec
from xlm.data.adapters.mix01_adapters import (
    ADAPTERS_BY_ID,
    MissingFieldError,
    RecordRejectedError,
    UltraXUltraFineWebAdapter,
)
from xlm.data.adapters.source_ids import canonical_source_doc_id
from xlm.data.sources.schema import FieldDescriptor, ViewSchema

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "mixture" / "views" / "ultrax_ultrafineweb.jsonl"


def read_fixture() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def test_adapter_is_registered() -> None:
    assert ADAPTERS_BY_ID["ultrax_ultrafineweb"] is UltraXUltraFineWebAdapter
    adapter = UltraXUltraFineWebAdapter()
    assert adapter.ADAPTER_ID == "ultrax_ultrafineweb"
    assert adapter.SOURCE_ID == "ultrax_ultrafineweb"
    assert adapter.CONFIG_NAME == "UltraX-Ultra-FineWeb"
    assert set(adapter.REQUIRED_FIELDS) == {"uid", "cleaned_content", "source"}


def test_keep_all_row_uses_cleaned_verbatim() -> None:
    adapter = UltraXUltraFineWebAdapter()
    row = read_fixture()[0]
    assert row["processed_functions"] == "keep_all"
    doc = adapter.adapt(row, source_file="f", source_row=0, source_revision="r")
    assert doc.text == row["cleaned_content"]
    assert doc.text == row["raw_content"]
    assert doc.source_id == "ultrax_ultrafineweb"
    assert doc.language == "en"
    assert doc.document_kind == "prose"
    assert doc.license_reference == "unknown"
    assert doc.source_metadata["mix01_component"] == "ultrax_ultrafineweb"
    assert doc.source_metadata["config_name"] == "UltraX-Ultra-FineWeb"
    assert doc.source_metadata["uid"] == "ultrax-uid-keep-001"
    assert doc.source_metadata["upstream_source"] == "commoncrawl"
    assert doc.source_metadata["processed_functions"] == "keep_all"
    assert "raw_content" not in doc.source_metadata
    assert "raw_content" not in doc.text or doc.text == row["cleaned_content"]
    assert doc.doc_id == canonical_source_doc_id("ultrax_ultrafineweb", "f", 0)


def test_edited_row_never_falls_back_to_raw() -> None:
    adapter = UltraXUltraFineWebAdapter()
    row = read_fixture()[1]
    assert row["cleaned_content"] != row["raw_content"]
    doc = adapter.adapt(row, source_file="f", source_row=1, source_revision="r")
    assert doc.text == row["cleaned_content"]
    assert doc.text != row["raw_content"]
    assert "CLICK HERE" not in doc.text
    assert doc.source_metadata["processed_functions"] == "remove_boilerplate; normalize_whitespace"


def test_empty_cleaned_content_is_counted_drop_not_fallback() -> None:
    adapter = UltraXUltraFineWebAdapter()
    row = read_fixture()[2]
    assert row["cleaned_content"] == ""
    assert row["raw_content"] != ""
    with pytest.raises(RecordRejectedError, match="remove_all|never used as fallback"):
        adapter.adapt(row, source_file="f", source_row=2, source_revision="r")
    whitespace = dict(row, cleaned_content="   \n\t  ")
    with pytest.raises(RecordRejectedError, match="remove_all|never used as fallback"):
        adapter.adapt(whitespace, source_file="f", source_row=2, source_revision="r")


def test_unicode_preserved_verbatim() -> None:
    adapter = UltraXUltraFineWebAdapter()
    row = read_fixture()[3]
    doc = adapter.adapt(row, source_file="f", source_row=3, source_revision="r")
    assert doc.text == row["cleaned_content"]
    assert "🌊" in doc.text and "日本語" in doc.text
    assert doc.utf8_byte_count == len(row["cleaned_content"].encode("utf-8"))


def test_very_long_text_within_bounds() -> None:
    adapter = UltraXUltraFineWebAdapter()
    row = read_fixture()[4]
    assert len(row["cleaned_content"]) == 124500
    assert len(row["cleaned_content"].encode("utf-8")) < 1024 * 1024
    doc = adapter.adapt(row, source_file="f", source_row=4, source_revision="r")
    assert doc.text == row["cleaned_content"]
    assert doc.utf8_byte_count == 124500


def test_missing_required_fields_refused() -> None:
    adapter = UltraXUltraFineWebAdapter()
    base = read_fixture()[0]
    for missing in ("uid", "cleaned_content", "source"):
        pruned = {k: v for k, v in base.items() if k != missing}
        with pytest.raises(MissingFieldError, match=missing):
            adapter.adapt(pruned, source_file="f", source_row=0, source_revision="r")
    nulled = dict(base, cleaned_content=None)
    with pytest.raises(MissingFieldError, match="cleaned_content"):
        adapter.adapt(nulled, source_file="f", source_row=0, source_revision="r")


def test_wrong_field_types_refused_without_coercion() -> None:
    adapter = UltraXUltraFineWebAdapter()
    base = read_fixture()[0]
    for bad_uid in (None, 42, ["x"], {"x": 1}, "", "   ", True):
        with pytest.raises(MissingFieldError, match="'uid'"):
            adapter.adapt(
                {**base, "uid": bad_uid}, source_file="f", source_row=0, source_revision="r"
            )
    for bad_text in (None, 42, ["x"], {"x": 1}, True):
        with pytest.raises(MissingFieldError, match="'cleaned_content'"):
            adapter.adapt(
                {**base, "cleaned_content": bad_text},
                source_file="f",
                source_row=0,
                source_revision="r",
            )
    for bad_source in (None, 42, ["x"], "", "  "):
        with pytest.raises(MissingFieldError, match="'source'"):
            adapter.adapt(
                {**base, "source": bad_source},
                source_file="f",
                source_row=0,
                source_revision="r",
            )
    for bad_functions in (42, ["keep_all"], {"op": 1}, True):
        with pytest.raises(MissingFieldError, match="'processed_functions'"):
            adapter.adapt(
                {**base, "processed_functions": bad_functions},
                source_file="f",
                source_row=0,
                source_revision="r",
            )


def test_raw_only_row_does_not_fallback() -> None:
    adapter = UltraXUltraFineWebAdapter()
    with pytest.raises(MissingFieldError, match="'cleaned_content'"):
        adapter.adapt(
            {"uid": "u1", "raw_content": "raw only", "source": "commoncrawl"},
            source_file="f",
            source_row=0,
            source_revision="r",
        )


def test_duplicate_uid_follows_file_row_policy() -> None:
    adapter = UltraXUltraFineWebAdapter()
    row = read_fixture()[0]
    first = adapter.adapt(row, source_file="a/000.parquet", source_row=0, source_revision="r")
    second = adapter.adapt(row, source_file="b/000.parquet", source_row=0, source_revision="r")
    assert first.source_metadata["uid"] == second.source_metadata["uid"] == row["uid"]
    assert first.doc_id != second.doc_id
    again = adapter.adapt(row, source_file="a/000.parquet", source_row=0, source_revision="r")
    assert again.doc_id == first.doc_id
    assert again.to_dict() == first.to_dict()


def test_deterministic_canonical_identity() -> None:
    adapter = UltraXUltraFineWebAdapter()
    row = read_fixture()[1]
    first = adapter.adapt(row, source_file="f", source_row=1, source_revision="rev-1")
    again = adapter.adapt(row, source_file="f", source_row=1, source_revision="rev-1")
    assert again.to_dict() == first.to_dict()
    assert first.raw_hash == first.clean_hash
    assert len(first.raw_hash) == 64


def test_provenance_records_exact_lineage() -> None:
    adapter = UltraXUltraFineWebAdapter()
    row = read_fixture()[1]
    doc = adapter.adapt(
        row, source_file="ultrafineweb/part-00001.parquet", source_row=7, source_revision="rev-9"
    )
    assert doc.source_id == "ultrax_ultrafineweb"
    assert doc.source_revision == "rev-9"
    assert doc.source_file == "ultrafineweb/part-00001.parquet"
    assert doc.source_row == 7
    assert doc.source_metadata["uid"] == row["uid"]
    assert doc.source_metadata["upstream_source"] == row["source"]
    assert doc.source_metadata["config_name"] == "UltraX-Ultra-FineWeb"
    assert "derived from source corpora" in doc.source_metadata["license_provenance"]


def test_optional_processed_functions_lifecycle() -> None:
    adapter = UltraXUltraFineWebAdapter()
    base = read_fixture()[0]
    bare = {k: v for k, v in base.items() if k != "processed_functions"}
    doc = adapter.adapt(bare, source_file="f", source_row=0, source_revision="r")
    assert "processed_functions" not in doc.source_metadata
    nulled = dict(base, processed_functions=None)
    doc2 = adapter.adapt(nulled, source_file="f", source_row=0, source_revision="r")
    assert "processed_functions" not in doc2.source_metadata
    empty = dict(base, processed_functions="")
    doc3 = adapter.adapt(empty, source_file="f", source_row=0, source_revision="r")
    assert doc3.source_metadata["processed_functions"] == ""


def test_serialization_roundtrip() -> None:
    from xlm.core.contracts import CanonicalDocument

    adapter = UltraXUltraFineWebAdapter()
    row = read_fixture()[1]
    doc = adapter.adapt(row, source_file="f", source_row=1, source_revision="r")
    payload = doc.to_dict()
    line = json.dumps(payload, ensure_ascii=False)
    restored = CanonicalDocument(**json.loads(line))
    assert restored.to_dict() == payload
    assert restored.text == row["cleaned_content"]
    assert restored.utf8_byte_count == len(row["cleaned_content"].encode("utf-8"))


def test_bounded_record_byte_accounting() -> None:
    adapter = UltraXUltraFineWebAdapter()
    rows = read_fixture()
    accepted = 0
    rejected = 0
    total_bytes = 0
    for index, row in enumerate(rows):
        try:
            doc = adapter.adapt(row, source_file="f", source_row=index, source_revision="r")
        except RecordRejectedError:
            rejected += 1
            continue
        accepted += 1
        total_bytes += doc.utf8_byte_count
        assert doc.utf8_byte_count == len(doc.text.encode("utf-8"))
        assert len(doc.text.encode("utf-8")) <= 1024 * 1024
    assert accepted == 4
    assert rejected == 1
    assert total_bytes == sum(len(rows[i]["cleaned_content"].encode("utf-8")) for i in (0, 1, 3, 4))


def test_contract_detects_missing_fields() -> None:
    adapter = UltraXUltraFineWebAdapter()
    keys: set[str] = set()
    for record in read_fixture():
        keys.update(record.keys())
    schema = ViewSchema(
        view_id="fixture",
        fields={k: FieldDescriptor(name=k, type_name="string") for k in keys},
        raw_schema_type="jsonl",
    )
    contract = adapter.contract()
    assert contract.adapter_id == "ultrax_ultrafineweb"
    assert contract.text_field == "cleaned_content"
    is_valid, _ = contract.evaluate_against_schema(schema)
    assert is_valid
    for required in ("uid", "cleaned_content", "source"):
        pruned = ViewSchema(
            view_id="fixture",
            fields={k: v for k, v in schema.fields.items() if k != required},
            raw_schema_type="jsonl",
        )
        still_valid, missing = contract.evaluate_against_schema(pruned)
        assert not still_valid and missing


def test_column_contract_and_spec() -> None:
    assert columns_for("ultrax_ultrafineweb") == (
        "uid",
        "cleaned_content",
        "source",
        "processed_functions",
    )
    assert parse_adapter_spec("ultrax_ultrafineweb") == ("ultrax_ultrafineweb", None)
