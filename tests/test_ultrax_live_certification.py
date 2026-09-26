"""UltraX live certification against REAL pinned rows (operator check, read-only).

Reads the operator-produced ``adapter-cert-ultrax01`` directory without
mutating it: ``probe_receipt.json`` (the bounded schema-probe receipt) plus
``real-records.jsonl`` (a very small bounded sample saved by the probe with
``_cert_source_file`` / ``_cert_source_row`` locators). Absent evidence
SKIPS (a skip is never a pass); present evidence must satisfy the exact
revision pin, the UltraX-Ultra-FineWeb config, the five-field schema and the
cleaned-content-only adapter contract. This is an OPERATOR check, separate
from the authored offline certification in ``test_ultrax_ultrafineweb.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from xlm.data.adapters.mix01_adapters import (
    MissingFieldError,
    RecordRejectedError,
    UltraXUltraFineWebAdapter,
)
from xlm.data.adapters.source_ids import canonical_source_doc_id

CERT_DIR = Path(
    os.environ.get(
        "XLM_ULTRAX_CERT_DIR",
        "D:/Project/xlm-operator-pilot/adapter-cert-ultrax01",
    )
)
RECEIPT_NAME = "probe_receipt.json"
RECORDS_NAME = "real-records.jsonl"


def _require_evidence() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    receipt_path = CERT_DIR / RECEIPT_NAME
    records_path = CERT_DIR / RECORDS_NAME
    if not receipt_path.is_file() or not records_path.is_file():
        pytest.skip("live UltraX certification evidence is absent")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert receipt and rows
    return receipt, rows


def test_receipt_pins_exact_revision_and_config() -> None:
    receipt, _ = _require_evidence()
    revision = receipt["revision_sha"]
    assert isinstance(revision, str) and len(revision) == 40
    assert revision not in ("main", "latest", "master", "head", "")
    assert receipt["config"] == "UltraX-Ultra-FineWeb"
    assert receipt["config_verified"] is True
    assert sorted(receipt["field_names"]) == sorted(
        ["uid", "raw_content", "cleaned_content", "processed_functions", "source"]
    )


def test_real_rows_adapt_to_cleaned_content_only() -> None:
    receipt, rows = _require_evidence()
    revision = receipt["revision_sha"]
    adapter = UltraXUltraFineWebAdapter()
    for row in rows:
        source_file = row["_cert_source_file"]
        source_row = row["_cert_source_row"]
        upstream = {k: v for k, v in row.items() if not k.startswith("_cert_")}
        try:
            doc = adapter.adapt(
                upstream,
                source_file=source_file,
                source_row=source_row,
                source_revision=revision,
            )
        except RecordRejectedError:
            assert not str(upstream.get("cleaned_content") or "").strip()
            continue
        assert doc.text == upstream["cleaned_content"]
        assert doc.source_revision == revision
        assert doc.source_file == source_file
        assert doc.source_row == source_row
        assert doc.doc_id == canonical_source_doc_id("ultrax_ultrafineweb", source_file, source_row)
        assert doc.source_metadata["uid"] == upstream["uid"]
        again = adapter.adapt(
            upstream,
            source_file=source_file,
            source_row=source_row,
            source_revision=revision,
        )
        assert again.to_dict() == doc.to_dict()


def test_real_rows_never_fall_back_to_raw() -> None:
    _, rows = _require_evidence()
    adapter = UltraXUltraFineWebAdapter()
    for row in rows:
        upstream = {k: v for k, v in row.items() if not k.startswith("_cert_")}
        if not str(upstream.get("cleaned_content") or "").strip():
            with pytest.raises(RecordRejectedError, match="never used as fallback"):
                adapter.adapt(
                    upstream,
                    source_file=row["_cert_source_file"],
                    source_row=row["_cert_source_row"],
                    source_revision="r",
                )
        else:
            doc = adapter.adapt(
                upstream,
                source_file=row["_cert_source_file"],
                source_row=row["_cert_source_row"],
                source_revision="r",
            )
            assert doc.text != upstream.get("raw_content") or (
                upstream.get("raw_content") == upstream.get("cleaned_content")
            )


def test_missing_cleaned_content_refused() -> None:
    adapter = UltraXUltraFineWebAdapter()
    with pytest.raises(MissingFieldError, match="'cleaned_content'"):
        adapter.adapt(
            {"uid": "u", "source": "s"}, source_file="f", source_row=0, source_revision="r"
        )
