"""Offline tests for the multi-view requirement split (authored fixtures only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from xlm.data.acquisition import source_plan as planner

QUOTAS = {
    "first_pass_headroom_quotas": {"ifm_behaviors_general_planning": 330000000},
    "final_quotas": {"ifm_behaviors_general_planning": 300000000},
    "first_pass_headroom_ratio": 1.1,
}
ESTIMATE = {
    "assumptions": {
        "bytes_per_token_base": 4.0,
        "bytes_per_token_low": 3.0,
        "bytes_per_token_high": 5.0,
        "safety_margin": 1.15,
    },
    "sources": {
        "ifm_behaviors_general_planning": {
            "final_exact_token_quota": 300000000,
            "first_pass_usable_token_target": 330000000,
            "status": "ESTIMATED",
            "required_canonical_bytes_base": 1320000000,
            "required_transferred_bytes": {"low": 1, "base": 2, "high": 3},
        }
    },
}
COMPONENT = "ifm_behaviors_general_planning"


def _split(**over: Any) -> dict[str, Any]:
    return planner.build_view_split(
        component_id=COMPONENT,
        quotas=QUOTAS,
        estimate=ESTIMATE,
        quotas_sha256="q" * 64,
        estimate_sha256="e" * 64,
        view_tokens={"general": 165000000, "planning": 165000000},
        operator="op",
        rationale="fifty fifty",
        **over,
    )


def test_fifty_fifty_means_165m_first_pass_and_150m_final() -> None:
    split = _split()
    assert split["views"]["general"]["first_pass_tokens"] == 165000000
    assert split["views"]["general"]["final_tokens"] == 150000000
    assert split["views"]["general"]["required_canonical_bytes"] == 660000000
    assert split["views"]["planning"]["first_pass_tokens"] == 165000000
    assert split["views"]["planning"]["final_tokens"] == 150000000
    assert split["component_first_pass_tokens"] == 330000000
    assert split["component_final_tokens"] == 300000000


def test_split_digest_deterministic_and_verifies() -> None:
    assert _split()["digest"] == _split()["digest"]
    planner.check_view_split(_split(), QUOTAS, ESTIMATE)


def test_requirement_from_split_carries_view_binding() -> None:
    req = planner.requirement_from_split(_split(), "general", quotas=QUOTAS, estimate=ESTIMATE)
    assert req.component_id == COMPONENT
    assert req.first_pass_tokens == 165000000
    assert req.required_canonical_bytes == 660000000
    assert req.view_id == "general"
    assert req.split_digest == _split()["digest"]


def test_plain_requirement_has_no_split_binding() -> None:
    req = planner.requirement_from(
        COMPONENT, QUOTAS, ESTIMATE, quotas_sha256="q", estimate_sha256="e"
    )
    assert req.view_id is None and req.split_digest is None
    assert req.first_pass_tokens == 330000000


def test_split_refuses_bad_sums() -> None:
    with pytest.raises(planner.PlanError, match="sum exactly"):
        planner.build_view_split(
            component_id=COMPONENT,
            quotas=QUOTAS,
            estimate=ESTIMATE,
            quotas_sha256="q",
            estimate_sha256="e",
            view_tokens={"general": 165000000, "planning": 110000000},
            operator="op",
            rationale="short",
        )


def test_split_refuses_single_view_and_bad_tokens() -> None:
    with pytest.raises(planner.PlanError, match="at least two views"):
        planner.build_view_split(
            component_id=COMPONENT,
            quotas=QUOTAS,
            estimate=ESTIMATE,
            quotas_sha256="q",
            estimate_sha256="e",
            view_tokens={"general": 330000000},
            operator="op",
            rationale="solo",
        )
    with pytest.raises(planner.PlanError, match="positive integer"):
        planner.build_view_split(
            component_id=COMPONENT,
            quotas=QUOTAS,
            estimate=ESTIMATE,
            quotas_sha256="q",
            estimate_sha256="e",
            view_tokens={"general": 0, "planning": 330000000},
            operator="op",
            rationale="zero",
        )


def test_split_refuses_unknown_view_and_tamper() -> None:
    with pytest.raises(planner.PlanError, match="no allocation"):
        planner.requirement_from_split(_split(), "reasoning", quotas=QUOTAS, estimate=ESTIMATE)
    tampered = dict(_split())
    tampered["views"] = {
        "general": {
            "first_pass_tokens": 1,
            "final_tokens": 1,
            "required_canonical_bytes": 4,
        },
        "planning": {
            "first_pass_tokens": 329999999,
            "final_tokens": 299999999,
            "required_canonical_bytes": 1319999996,
        },
    }
    with pytest.raises(planner.PlanError, match="digest does not verify"):
        planner.check_view_split(tampered, QUOTAS, ESTIMATE)


def test_cli_split_record_and_show_roundtrip(tmp_path: Path) -> None:
    import importlib.util
    import sys

    quotas = tmp_path / "quotas.yaml"
    quotas.write_text(yaml.safe_dump(QUOTAS), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "mix01_source.py"
    spec = importlib.util.spec_from_file_location("mix01_source_split", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # Point the driver at a scratch repo carrying our fixture quotas file.
    module.QUOTAS = quotas
    data_root = tmp_path / "data"
    (data_root / "calib").mkdir(parents=True)
    (data_root / "calib" / "headroom_estimate.json").write_text(
        json.dumps(ESTIMATE), encoding="utf-8"
    )
    out = data_root / "calib" / "requirement_splits" / f"{COMPONENT}.json"
    assert (
        module.main(
            [
                "requirement",
                "split-record",
                "--component",
                COMPONENT,
                "--view-tokens",
                "general=165000000,planning=165000000",
                "--operator",
                "op",
                "--rationale",
                "fifty fifty",
                "--data-root",
                str(data_root),
            ]
        )
        == 0
    )
    assert out.is_file()
    assert (
        module.main(
            [
                "requirement",
                "split-show",
                "--component",
                COMPONENT,
                "--data-root",
                str(data_root),
            ]
        )
        == 0
    )
    # Identical re-record is a no-op; a divergent one refuses.
    assert (
        module.main(
            [
                "requirement",
                "split-record",
                "--component",
                COMPONENT,
                "--view-tokens",
                "general=165000000,planning=165000000",
                "--operator",
                "op",
                "--rationale",
                "fifty fifty",
                "--data-root",
                str(data_root),
            ]
        )
        == 0
    )
    assert (
        module.main(
            [
                "requirement",
                "split-record",
                "--component",
                COMPONENT,
                "--view-tokens",
                "general=100000000,planning=230000000",
                "--operator",
                "op",
                "--rationale",
                "other",
                "--data-root",
                str(data_root),
            ]
        )
        == 1
    )
