"""Authored checks of the offline allocation preview's arithmetic and refusal boundaries."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


def analysis() -> Any:
    directory = Path(__file__).resolve().parents[1] / (
        "docs/implementation/evidence/COMMON-PILE-POST-CALIBRATION"
    )
    for name in ("verify_calibration", "analyze_calibration"):
        spec = importlib.util.spec_from_file_location(name, directory / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def test_capacity_capped_core_exact_totals_and_residue() -> None:
    tool = analysis()
    capacity = {
        "core_a": 115_000_000.0,
        "core_b": 11_500_000.0,
        "core_c": 23_000_000.0,
        "core_d": 115_000_000.0,
        "core_e": 115_000_000.0,
        "project_gutenberg": 1_000_000_000.0,
    }
    result = tool.allocate_c(capacity)
    assert result == tool.allocate_c(dict(reversed(list(capacity.items()))))
    assert result["core_a"]["first_pass_tokens"] == 55_000_000
    assert result["core_b"]["first_pass_tokens"] == 10_000_000
    assert result["core_c"]["first_pass_tokens"] == 20_000_000
    assert sum(v["first_pass_tokens"] for v in result.values()) == 330_000_000
    assert sum(v["final_tokens"] for v in result.values()) == 300_000_000
    for c, share in result.items():
        assert 0 < share["final_tokens"] <= share["first_pass_tokens"]
        assert 1.15 * share["first_pass_tokens"] <= capacity[c]


@pytest.mark.parametrize("capacity", [0.0, float("nan"), float("inf"), 1.0])
def test_insufficient_or_invalid_capacity_is_never_repeated_or_renormalized(
    capacity: float,
) -> None:
    with pytest.raises(ValueError):
        analysis().allocate_c({"core": 10.0, "project_gutenberg": capacity})


def test_selected_prefix_uses_each_components_own_yield() -> None:
    tool = analysis()
    result = tool.summarize_files(
        [
            {"file": "core/a.jsonl.gz", "size_bytes": 100},
            {"file": "project_gutenberg/b.jsonl.gz", "size_bytes": 200},
        ],
        {"core": 4.0, "project_gutenberg": 2.0},
    )
    assert result["projected_canonical_bytes"] == 800
    assert result["projected_tokens"] == 200
    assert result["gutenberg_percent"] == 50
