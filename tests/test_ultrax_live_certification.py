"""UltraX live certification against REAL pinned rows (operator check, read-only).

Reads the operator-produced ``adapter-cert-ultrax01`` directory without
mutating it: ``probe_receipt.json`` (the bounded schema-probe receipt) plus
``real-records.jsonl`` (a very small bounded sample saved by the probe with
``_cert_source_file`` / ``_cert_source_row`` / ``_cert_revision``
locators). Absent evidence SKIPS (a skip is never a pass).

The sample file must be the probe's own deterministic output (UTF-8
without BOM, LF newlines). A BOM is refused fail-closed with an explicit
regeneration message: the reader is never weakened to accept
manually-mutated evidence. This is an OPERATOR check, separate from the
authored offline certification in ``test_ultrax_ultrafineweb.py``; the
authored cert-fixture test below exercises the same verification logic
offline without network or real data.
"""

from __future__ import annotations

import json
import os
import re
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
        "D:/Project/xlm-operator-ultrax/adapter-cert-ultrax01",
    )
)
RECEIPT_NAME = "probe_receipt.json"
RECORDS_NAME = "real-records.jsonl"

EXPECTED_REPOSITORY = "openbmb/UltraX-Preview"
EXPECTED_CONFIG = "UltraX-Ultra-FineWeb"
EXPECTED_FIELDS = ["uid", "raw_content", "cleaned_content", "processed_functions", "source"]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _expected_locator(receipt: dict[str, Any]) -> str:
    return (
        f"hf-stream://{receipt['repository']}@{receipt['revision_sha']}"
        f"/{receipt['config']}/{receipt['split']}"
    )


def _require_receipt() -> dict[str, Any]:
    receipt_path = CERT_DIR / RECEIPT_NAME
    if not receipt_path.is_file():
        pytest.skip("live UltraX certification evidence is absent")
    return json.loads(receipt_path.read_text(encoding="utf-8"))


def _require_rows() -> list[dict[str, Any]]:
    records_path = CERT_DIR / RECORDS_NAME
    if not records_path.is_file():
        pytest.skip("live UltraX certification evidence is absent")
    raw = records_path.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        pytest.fail(
            "certification sample carries a UTF-8 BOM: it is not the probe's "
            "deterministic output. Regenerate it with "
            "scripts/ultrax_schema_probe.py --save-sample (UTF-8, no BOM, LF); "
            "the manually-mutated file is not authoritative evidence."
        )
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    assert rows
    return rows


def _require_evidence() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return _require_receipt(), _require_rows()


def _check_receipt(receipt: dict[str, Any]) -> str:
    """Pin, repository, config and schema of the probe receipt; return the revision."""
    revision = receipt["revision_sha"]
    assert isinstance(revision, str) and SHA_RE.fullmatch(revision)
    assert receipt["repository"] == EXPECTED_REPOSITORY
    assert receipt["config"] == EXPECTED_CONFIG
    assert receipt["config_verified"] is True
    assert sorted(receipt["field_names"]) == sorted(EXPECTED_FIELDS)
    assert isinstance(receipt.get("declared_license"), str) and receipt["declared_license"].strip()
    assert receipt["rows_sampled"] >= 1
    evidence = receipt.get("schema_evidence")
    assert isinstance(evidence, dict) and evidence.get("source") in (
        "builder_info",
        "streaming_features",
        "observed_rows",
    ), "receipt must honestly label its schema evidence source"
    assert receipt.get("schema_match") is True
    return revision


def _check_rows(receipt: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Every sample row: locator, revision equality, verbatim cleaned text, identity."""
    revision = receipt["revision_sha"]
    locator = _expected_locator(receipt)
    adapter = UltraXUltraFineWebAdapter()
    assert len(rows) >= 1
    for row in rows:
        assert row["_cert_source_file"] == locator
        assert row["_cert_revision"] == revision
        assert isinstance(row["_cert_source_row"], int) and row["_cert_source_row"] >= 0
        source_file = row["_cert_source_file"]
        source_row = row["_cert_source_row"]
        upstream = {k: v for k, v in row.items() if not k.startswith("_cert_")}
        assert set(upstream) >= {"uid", "cleaned_content", "source"}
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
        assert doc.source_metadata["upstream_source"] == upstream["source"]
        again = adapter.adapt(
            upstream,
            source_file=source_file,
            source_row=source_row,
            source_revision=revision,
        )
        assert again.to_dict() == doc.to_dict()


def test_receipt_pins_exact_revision_and_config() -> None:
    _check_receipt(_require_receipt())


def test_real_rows_adapt_to_cleaned_content_only() -> None:
    receipt, rows = _require_evidence()
    _check_rows(receipt, rows)


def test_real_rows_never_fall_back_to_raw() -> None:
    receipt, rows = _require_evidence()
    revision = receipt["revision_sha"]
    adapter = UltraXUltraFineWebAdapter()
    for row in rows:
        upstream = {k: v for k, v in row.items() if not k.startswith("_cert_")}
        assert row["_cert_revision"] == revision
        if not str(upstream.get("cleaned_content") or "").strip():
            with pytest.raises(RecordRejectedError, match="never used as fallback"):
                adapter.adapt(
                    upstream,
                    source_file=row["_cert_source_file"],
                    source_row=row["_cert_source_row"],
                    source_revision=revision,
                )
        else:
            doc = adapter.adapt(
                upstream,
                source_file=row["_cert_source_file"],
                source_row=row["_cert_source_row"],
                source_revision=revision,
            )
            assert doc.text == upstream["cleaned_content"]
            assert doc.text != upstream.get("raw_content") or (
                upstream.get("raw_content") == upstream.get("cleaned_content")
            )


def test_missing_cleaned_content_refused() -> None:
    adapter = UltraXUltraFineWebAdapter()
    with pytest.raises(MissingFieldError, match="'cleaned_content'"):
        adapter.adapt(
            {"uid": "u", "source": "s"}, source_file="f", source_row=0, source_revision="r"
        )


def _authored_cert_fixture(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Authored receipt + rows shaped like probe output (offline, no network)."""
    revision = "a88527587389fd4ab352e9ad1273f4c0a234d8df"
    receipt = {
        "repository": EXPECTED_REPOSITORY,
        "revision_sha": revision,
        "config": EXPECTED_CONFIG,
        "split": "train",
        "config_verified": True,
        "field_names": list(EXPECTED_FIELDS),
        "declared_license": "apache-2.0",
        "rows_sampled": 3,
        "schema_evidence": {"source": "observed_rows", "rows": 3},
        "schema_match": True,
    }
    locator = f"hf-stream://{EXPECTED_REPOSITORY}@{revision}/{EXPECTED_CONFIG}/train"
    rows = [
        {
            "uid": "authored-cert-keep-01",
            "raw_content": "Authored keep-all web text.",
            "cleaned_content": "Authored keep-all web text.",
            "processed_functions": "keep_all",
            "source": "Ultra-FineWeb",
            "_cert_source_file": locator,
            "_cert_source_row": 0,
            "_cert_revision": revision,
        },
        {
            "uid": "authored-cert-edited-02",
            "raw_content": "NOISE Authored edited web text.",
            "cleaned_content": "Authored edited web text.",
            "processed_functions": "remove_boilerplate",
            "source": "Ultra-FineWeb",
            "_cert_source_file": locator,
            "_cert_source_row": 1,
            "_cert_revision": revision,
        },
        {
            "uid": "authored-cert-remove-03",
            "raw_content": "Raw text that must never train.",
            "cleaned_content": "   ",
            "processed_functions": "remove_all",
            "source": "Ultra-FineWeb",
            "_cert_source_file": locator,
            "_cert_source_row": 2,
            "_cert_revision": revision,
        },
    ]
    (directory / RECEIPT_NAME).write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (directory / RECORDS_NAME).open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return receipt, rows


def test_authored_cert_fixture_satisfies_live_contract(tmp_path: Path) -> None:
    """The live verification logic itself, exercised offline on authored shape."""
    receipt, rows = _authored_cert_fixture(tmp_path)
    assert _check_receipt(receipt) == receipt["revision_sha"]
    _check_rows(receipt, rows)
    raw = (tmp_path / RECORDS_NAME).read_bytes()
    assert raw[:1] == b"{"
    assert b"\r" not in raw
