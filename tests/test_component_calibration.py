"""Authored six-component fixtures; no live source or network assertions."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from test_component_allowlist import PIN, REVISION, _freeze, _listing, _load
from xlm.data.acquisition import component_allowlist as allow
from xlm.data.acquisition import component_calibration as cc
from xlm.data.acquisition import jsonl_gz_sample as sampler
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import certified_evidence as ce
from xlm.data.sources import common_pile_evidence as evidence

COMPONENTS = (
    "libretexts",
    "news",
    "oercommons",
    "pressbooks",
    "project_gutenberg",
    "public_domain_review",
)


def redigest(value: dict[str, Any]) -> dict[str, Any]:
    value.pop("digest", None)
    value["digest"] = canonical.digest(value)
    return value


def fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    listing = _listing({c: (2, 10000 * (i + 1)) for i, c in enumerate(COMPONENTS)})
    matrix = {
        "kind": allow.MATRIX_KIND,
        "version": 1,
        "repository": PIN.repository,
        "revision": REVISION,
        "sources": {"fixture": {"url": "https://example.invalid"}},
        "components": {
            c: {
                "content_fit": "FIT",
                "license_class": "VERIFIED_OPEN_LICENSE",
                "provenance_confidence": "HIGH",
                "evidence": ["fixture"],
            }
            for c in COMPONENTS
        },
    }
    record = allow.build_allowlist(
        source_key="common_pile",
        component_id=PIN.component_id,
        listing=listing,
        matrix=matrix,
        matrix_sha256=hashlib.sha256(b"fixture").hexdigest(),
        included=COMPONENTS,
        accepted_flags=[],
        operator="fixture",
        rationale="unit test",
    )
    inventory = _freeze(listing, record)
    samples = []
    saved: list[bytes] = []
    for i, c in enumerate(COMPONENTS):
        name = f"{c}/{c}.chunk.00.jsonl.gz"
        texts = ["Authored synthetic prose. " * (i + 1), "   ", "Second fixture row."]
        lines = [json.dumps({"text": t}).encode() for t in texts]
        samples.append(
            sampler.PrefixSample(
                source_file=name,
                url="https://example.invalid/fixture",
                rows_requested=3,
                lines=lines,
                row_end_compressed=[32, 48, 64],
                decoded_bytes=1024,
                compressed_fed=64,
                etag='"fixture"',
                total_bytes=10000 * (i + 1),
                requests=2,
                transferred_bytes=128,
                seconds=0.25,
            )
        )
        saved.extend(
            json.dumps(
                {
                    "text": t,
                    "_cert_source_file": name,
                    "_cert_source_row": j,
                    "_cert_component": c,
                    "_cert_revision": REVISION,
                }
            ).encode()
            for j, t in enumerate(texts)
        )
    receipt = sampler.sample_receipt(
        samples,
        label="authored",
        source_id=PIN.source_id,
        repository=PIN.repository,
        revision=REVISION,
        adapter_id=PIN.adapter_id,
        bindings={
            "component_allowlist_digest": record["digest"],
            "production_inventory_digest": inventory["inventory_digest"],
        },
        limits=sampler.SampleLimits(1024, 8, 128, 1, 10, 1024),
    )
    return record, inventory, receipt, b"\n".join(saved) + b"\n"


def test_six_component_accounting_and_deterministic_layout() -> None:
    allowlist, inventory, receipt, _ = fixture()
    result = cc.build_calibration([receipt], allowlist=allowlist, inventory=inventory)
    assert cc.check_calibration(result, allowlist=allowlist, inventory=inventory) == result
    assert result["observed_transfer"] == {"requests": 12, "seconds": 1.5, "transferred_bytes": 768}
    for c in COMPONENTS:
        measured = result["components"][c]["measured"]
        assert (measured["rows"], measured["accepted"], measured["rejected"]) == (3, 2, 1)
        assert measured["transferred_bytes"] == 128
        assert measured["decoded_bytes"] == 1024
        assert measured["canonical_bytes_per_row"] == measured["canonical_bytes"] / 3
        assert measured["canonical_bytes_per_transferred_byte"] == measured["canonical_bytes"] / 128
        assert measured["decompression_amplification"] == measured["decoded_line_bytes"] / 64
    assert cc.layout_of(result, record_sha256="a" * 64) == cc.layout_of(
        result, record_sha256="a" * 64
    )
    assert json.dumps(result, sort_keys=True) == json.dumps(
        cc.build_calibration([receipt], allowlist=allowlist, inventory=inventory), sort_keys=True
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "extra",
        "zero",
        "revision",
        "allowlist",
        "inventory",
        "tamper",
        "file",
        "component",
    ],
)
def test_bad_receipts_refused(mutation: str) -> None:
    allowlist, inventory, receipt, _ = fixture()
    if mutation == "missing":
        receipt["files"].pop()
    elif mutation == "duplicate":
        receipt["files"].append(copy.deepcopy(receipt["files"][0]))
    elif mutation == "extra":
        receipt["files"][0]["file"] = "excluded/excluded.jsonl.gz"
    elif mutation == "zero":
        receipt["files"][0]["adapter"]["accepted"] = 0
    elif mutation == "revision":
        receipt["revision"] = "b" * 40
    elif mutation in ("allowlist", "inventory"):
        key = (
            "component_allowlist_digest"
            if mutation == "allowlist"
            else "production_inventory_digest"
        )
        receipt["bindings"][key] = "b" * 64
    elif mutation == "file":
        receipt["files"][0]["identity"]["total_bytes"] += 1
    elif mutation == "component":
        receipt["files"][0]["component"] = "excluded"
    else:
        receipt["label"] = "tampered"
    if mutation != "tamper":
        redigest(receipt)
    with pytest.raises(cc.CalibrationError):
        cc.build_calibration([receipt], allowlist=allowlist, inventory=inventory)


@pytest.mark.parametrize("key", ["revision", "repository", "source_id", "component_id"])
def test_stored_calibration_pin_is_checked(key: str) -> None:
    allowlist, inventory, receipt, _ = fixture()
    result = cc.build_calibration([receipt], allowlist=allowlist, inventory=inventory)
    result[key] = "foreign"
    redigest(result)
    with pytest.raises(cc.CalibrationError, match=key):
        cc.check_calibration(result, allowlist=allowlist, inventory=inventory)


def test_measurement_verifies_all_components_and_rejects_forged_counts(tmp_path: Path) -> None:
    tool = _load("mix01_inventory")
    allowlist, inventory, receipt, _ = fixture()
    result = cc.build_calibration([receipt], allowlist=allowlist, inventory=inventory)
    measured = cc.measurement(result)
    path = tmp_path / "measurement.json"
    path.write_text(json.dumps(measured), encoding="utf-8")
    assert tool._load_measurement(path) == measured
    measured["canonical_bytes"] += 1
    path.write_text(json.dumps(measured), encoding="utf-8")
    with pytest.raises(ValueError, match="reproduce"):
        tool._load_measurement(path)


def test_evidence_covers_six_without_inventing_license() -> None:
    allowlist, inventory, receipt, rows = fixture()
    facts = evidence.translate_component_samples(
        PIN, [(json.dumps(receipt).encode(), rows)], allowlist=allowlist, inventory=inventory
    )
    assert facts.declared_license is None
    assert facts.probe["components"] == list(COMPONENTS)
    assert len(facts.rows) == 18
    assert sorted(facts.schema) == ["text"]
    assert len(facts.observed_files) == 6


@pytest.mark.parametrize("mutation", ["receipt", "rows", "missing", "duplicate", "revision"])
def test_evidence_refuses_invalid_certification(mutation: str) -> None:
    allowlist, inventory, receipt, rows = fixture()
    if mutation == "receipt":
        receipt["label"] = "tampered"
    elif mutation == "rows":
        rows = rows.replace(b"Authored", b"Changed")
    elif mutation == "missing":
        receipt["files"].pop()
        redigest(receipt)
    elif mutation == "duplicate":
        rows += rows.splitlines()[0] + b"\n"
    else:
        receipt["revision"] = "b" * 40
        redigest(receipt)
    with pytest.raises(ce.BridgeRefusal):
        evidence.translate_component_samples(
            PIN, [(json.dumps(receipt).encode(), rows)], allowlist=allowlist, inventory=inventory
        )


def test_receipt_order_and_outlier_preserve_component_rates() -> None:
    allowlist, inventory, receipt, _ = fixture()
    first = copy.deepcopy(receipt)
    second = copy.deepcopy(receipt)
    first["label"], second["label"] = "first", "second"
    first["files"], second["files"] = receipt["files"][:3], receipt["files"][3:]
    for part in (first, second):
        part["totals"] = {"files": 3, "rows": 9, "requests": 6, "transferred_bytes": 384}
    # Large dense Gutenberg observation may increase only Gutenberg's rate;
    # it must not become the universal rate for the other five components.
    entry = second["files"][1]
    entry["rows_detail"][0]["text_utf8_bytes"] *= 100
    entry["rows_detail"][0]["line_bytes"] *= 100
    entry["transfer"]["decoded_bytes"] = 1_000_000
    entry["adapter"]["canonical_bytes"] = sum(
        d["text_utf8_bytes"] for d in entry["rows_detail"] if d["outcome"] == "accepted"
    )
    redigest(first)
    redigest(second)
    a = cc.build_calibration([first, second], allowlist=allowlist, inventory=inventory)
    b = cc.build_calibration([second, first], allowlist=allowlist, inventory=inventory)
    assert a == b
    assert a["components"]["project_gutenberg"]["measured"]["canonical_bytes_per_row"] > 1000
    assert a["components"]["news"]["measured"]["canonical_bytes_per_row"] < 100


def test_calibration_cli_build_show_and_current_inventory_measurement(tmp_path: Path) -> None:
    allowlist, inventory, receipt, _ = fixture()
    listing = _listing({c: (2, 10000 * (i + 1)) for i, c in enumerate(COMPONENTS)})
    documents = {
        "inventories/common_pile.discovery.listing.json": listing,
        "inventories/common_pile.inventory.json": inventory,
        "calib/component_allowlists/common_pile.json": allowlist,
        "sample.json": receipt,
    }
    for relative, value in documents.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    tool = _load("component_calibration")
    args = ["--source-key", "common_pile", "--data-root", str(tmp_path)]
    assert tool.main(["build", *args, "--receipt", str(tmp_path / "sample.json")]) == 0
    assert tool.main(["show", *args]) == 0
    assert tool.main(["build", *args, "--receipt", str(tmp_path / "sample.json")]) == 0
    inv = _load("mix01_inventory")
    args2 = [
        "record",
        "--source",
        "common_pile_prose",
        "--calibration",
        str(tmp_path / "calib/calibration.json"),
        "--measurement",
        str(tmp_path / "calib/common_pile_prose/measurement.json"),
    ]
    assert inv.main(args2) == 0
    estimate = tmp_path / "estimate.json"
    quotas = Path(__file__).resolve().parents[1] / "recipes/mixtures/mix01_quotas_6b.yaml"
    assert (
        inv.main(
            [
                "estimate",
                "--quotas",
                str(quotas),
                "--calibration",
                str(tmp_path / "calib/calibration.json"),
                "--output",
                str(estimate),
            ]
        )
        == 0
    )
    projected = json.loads(estimate.read_bytes())["sources"]["common_pile_prose"]
    measured = json.loads((tmp_path / "calib/common_pile_prose/measurement.json").read_bytes())
    assert projected["calibration_basis"] == cc.TRANSFER_BASIS
    assert projected["component_calibration_digest"] == measured["component_calibration_digest"]
    assert "not raw observed counts" in projected["measured"]["count_basis"]
    allowlist["revision"] = "b" * 40
    (tmp_path / "calib/component_allowlists/common_pile.json").write_text(
        json.dumps(allowlist), encoding="utf-8"
    )
    assert inv.main([*args2, "--replace"]) == 1
