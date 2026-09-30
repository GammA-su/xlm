"""Calibration yield arithmetic and sealed-evidence checks. Offline; no dataset access."""

from __future__ import annotations

import importlib
import json
import socket
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from xlm.data.adapters import essential_web_selector as selector
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_calibration as calibration

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-CALIBRATION"
QUOTAS = {
    "final_quotas": {
        "essential_science": 600,
        "essential_practical": 600,
        "essential_prose": 300,
    },
    "first_pass_headroom_quotas": {
        "essential_science": 660,
        "essential_practical": 660,
        "essential_prose": 330,
    },
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("offline test attempted network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def measurement() -> dict[str, Any]:
    """Authored totals: 1,000 rows, 10/40/150 retained documents."""
    retained = {
        "essential_science": (10, 8000),
        "essential_practical": (40, 20000),
        "essential_prose": (150, 90000),
    }
    return {
        "input_rows": 1000,
        "physical_response_body_bytes": 4_000_000,
        "decompressed_bytes": 6_000_000,
        "malformed_rows": 2,
        "elapsed_wall_seconds_including_restart_downtime": 25.5,
        "selector_counts": {
            **{view: docs for view, (docs, _) in retained.items()},
            "unassigned": 50,
            "rejected": 750,
        },
        "retention_and_cost": {
            view: {"documents": docs, "canonical_bytes": size, "characters": size - 1}
            for view, (docs, size) in retained.items()
        },
    }


def test_yields_are_exact_fractions_of_the_measured_counts() -> None:
    yields = calibration.component_yields(measurement())
    science = yields["components"]["essential_science"]
    assert science["estimated_tokens_per_input_row"]["central"] == {"fraction": "2/1", "value": 2.0}
    assert science["estimated_tokens_per_input_row"]["low"]["fraction"] == "8/3"
    assert science["estimated_tokens_per_input_row"]["high"]["fraction"] == "8/5"
    assert science["average_canonical_bytes_per_document"]["value"] == 800
    assert science["estimated_tokens_per_document"]["value"] == 200
    assert science["input_rows_per_retained_document"]["value"] == 100
    assert science["transfer_bytes_per_estimated_token"]["value"] == 2000
    shared = yields["shared"]
    assert shared["physical_transfer_bytes_per_input_row"]["value"] == 4000
    assert shared["decompressed_bytes_per_input_row"]["value"] == 6000
    assert shared["elapsed_seconds_per_input_row"]["fraction"] == "51/2000"
    assert shared["malformed_rate"]["fraction"] == "1/500"
    assert "estimated tokens only" in yields["token_method"]


def test_yields_refuse_inconsistent_counts() -> None:
    broken = measurement()
    broken["selector_counts"]["rejected"] += 1
    with pytest.raises(calibration.CalibrationError, match="conserve"):
        calibration.component_yields(broken)
    broken = measurement()
    broken["retention_and_cost"]["essential_science"]["documents"] = 9
    with pytest.raises(calibration.CalibrationError, match="selector count"):
        calibration.component_yields(broken)


def test_zero_yield_stays_unknown_not_zero_cost() -> None:
    empty = measurement()
    empty["selector_counts"]["rejected"] += 10
    empty["selector_counts"]["essential_science"] = 0
    empty["retention_and_cost"]["essential_science"] = {
        "documents": 0,
        "canonical_bytes": 0,
        "characters": 0,
    }
    science = calibration.component_yields(empty)["components"]["essential_science"]
    assert "transfer_bytes_per_estimated_token" not in science
    with pytest.raises(calibration.CalibrationError, match="positive"):
        calibration.rows_required(660, 0, 1000, 4)


def test_bottleneck_is_computed_per_component_and_quotas_are_untouched() -> None:
    quotas = json.loads(json.dumps(QUOTAS))
    result = calibration.component_requirements(measurement(), quotas)
    # rows = ceil(target * 4 * 1000 / canonical bytes)
    assert result["components"]["essential_science"]["required_input_rows"] == 330
    assert result["components"]["essential_practical"]["required_input_rows"] == 132
    assert result["components"]["essential_prose"]["required_input_rows"] == 15
    assert result["bottleneck"] == "essential_science"
    assert result["bottleneck_required_input_rows"] == 330
    practical = result["components"]["essential_practical"]
    assert practical["expected_estimated_tokens_at_bottleneck_scan"] == 1650
    assert practical["ratio_to_first_pass_target"] == 2.5
    assert quotas == QUOTAS and result["quotas_changed"] is False
    scarce = measurement()
    scarce["retention_and_cost"]["essential_prose"]["canonical_bytes"] = 100
    assert calibration.component_requirements(scarce, quotas)["bottleneck"] == "essential_prose"


def unit(crawl: str, science: tuple[int, int]) -> dict[str, Any]:
    return {
        "crawl": crawl,
        "file": f"data/{crawl}/authored.parquet",
        "input_rows": 100,
        "malformed_rows": 0,
        "transferred_bytes": 1000,
        "essential_science": {"documents": science[0], "canonical_bytes": science[1]},
        "essential_practical": {"documents": 4, "canonical_bytes": 400},
        "essential_prose": {"documents": 9, "canonical_bytes": 900},
    }


def test_crawl_dispersion_is_descriptive_and_never_an_interval() -> None:
    units = [unit("a", (1, 100)), unit("b", (2, 300)), unit("c", (0, 0)), unit("d", (3, 400))]
    result = calibration.crawl_dispersion(units, 1000)
    assert result["crawls"] == 4 and "no confidence interval" in result["interval_statement"]
    assert result["science_rows_required_pooled"] == 2000  # 1000 * 4 * 400 / 800
    rows = {
        e["left_out_crawl"]: e["required_input_rows"]
        for e in result["science_rows_required_leave_one_crawl_out"]
    }
    assert rows == {"a": 1715, "b": 2400, "c": 1500, "d": 3000}
    assert result["science_rows_required_leave_one_out_range"] == [1500, 3000]
    assert result["science_leave_one_out_max_over_pooled"] == 1.5
    spread = result["spread"]["essential_science"]["estimated_tokens_per_input_row"]
    assert spread["min"] == 0 and spread["max"] == 1 and spread["max_over_min"] is None
    with pytest.raises(calibration.CalibrationError, match="distinct crawls"):
        calibration.crawl_dispersion([unit("a", (1, 1)), unit("a", (1, 1))], 1000)


def test_malformed_reason_codes_never_carry_free_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    sealer = importlib.import_module("essential_web_calibration_seal")
    assert sealer.reason_codes("essential_web_unusable_record") == (
        "essential_web_unusable_record",
    )
    assert sealer.reason_codes(
        "essential_web_selector_value: invalid_fdc_syntax, unknown_label:k"
    ) == (
        "essential_web_selector_value:invalid_fdc_syntax",
        "essential_web_selector_value:unknown_label:k",
    )
    assert sealer.reason_codes("Some Title: with prose, and text") == ("other", "other")
    assert sealer.reason_codes("A sentence of record text.") == ("other",)
    assert sealer.reason_codes(None) == ("other",)


def walk(value: Any) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def test_committed_seal_reproduces_its_digest_and_holds_no_text() -> None:
    seal = json.loads((EVIDENCE / "calibration-seal.json").read_bytes())
    body = {key: value for key, value in seal.items() if key != "seal_digest"}
    assert canonical.digest(body) == seal["seal_digest"]
    assert seal["contains_document_text"] is False
    assert seal["selector"] == selector.selector_identity()
    assert seal["source_revision"] == selector.SOURCE_REVISION
    assert seal["binding"]["adapter_id"] == seal["adapter"]["adapter_id"] == "essential_web_bnormal"
    assert not any(key in ("text", "reason") for key, _ in walk(seal))
    assert all(len(value) <= 300 for _, value in walk(seal) if isinstance(value, str))
    totals = seal["totals"]
    assert len(seal["units"]) == totals["crawls"] == totals["files"] == 8
    assert sum(unit["raw_rows"] for unit in seal["units"]) == totals["input_rows"]
    assert sum(totals["selector_counts"].values()) == totals["input_rows"]
    assert sum(totals["malformed_rows_by_reason_codes"].values()) == totals["malformed_rows"]
    for view in calibration.VIEWS:
        assert totals["retained"][view]["documents"] == sum(
            unit["views"][view]["accepted_records"] for unit in seal["units"]
        )
        assert totals["retained"][view]["canonical_bytes"] == sum(
            unit["views"][view]["canonical_bytes"] for unit in seal["units"]
        )
        assert all(unit["views"][view]["reproduced_by_current_adapter"] for unit in seal["units"])
        assert seal["admission"][view]["admitted"] is True
        assert seal["admission"][view]["benchmark_risk"] == "suspect_with_mitigation"
    assert len({unit["plan_hash"] for unit in seal["units"]}) == 8
    assert len({unit["raw_sha256"] for unit in seal["units"]}) == 8


def test_committed_yields_follow_from_the_sealed_totals() -> None:
    seal = json.loads((EVIDENCE / "calibration-seal.json").read_bytes())
    yields = json.loads((EVIDENCE / "calibration-yields.json").read_bytes())
    crawls = json.loads((EVIDENCE / "crawl-yields.json").read_bytes())
    assert yields["seal_digest"] == crawls["seal_digest"] == seal["seal_digest"]
    totals = seal["totals"]
    rows = totals["input_rows"]
    for view in calibration.VIEWS:
        size = totals["retained"][view]["canonical_bytes"]
        recorded = yields["components"][view]["estimated_tokens_per_input_row"]["central"]
        assert Fraction(recorded["fraction"]) == Fraction(size, 4 * rows)
        need = yields["requirements"]["components"][view]
        target = need["first_pass_estimated_token_target"]
        assert need["required_input_rows"] == calibration.rows_required(target, size, rows, 4)
        assert sum(unit[view]["canonical_bytes"] for unit in crawls["per_crawl"]) == size
    requirements = yields["requirements"]
    assert requirements["bottleneck"] == max(
        calibration.VIEWS,
        key=lambda view: requirements["components"][view]["required_input_rows"],
    )
    assert crawls["science_rows_required_pooled"] == requirements["bottleneck_required_input_rows"]
    assert sum(unit["input_rows"] for unit in crawls["per_crawl"]) == rows
    assert (
        sum(unit["transferred_bytes"] for unit in crawls["per_crawl"])
        == (totals["physical_response_body_bytes"])
    )
