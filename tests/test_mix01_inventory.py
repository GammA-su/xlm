"""Offline tests for the Mix-01 acquisition planning helper (no network).

Covers inventory determinism/digest/refusals, headroom math on synthetic
calibration (hand-checked), missing-calibration placeholders, zero-
acceptance blocking, unknown-source refusal, and sufficiency statuses.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml


def _load_script() -> Any:
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "mix01_inventory.py"
    spec = importlib.util.spec_from_file_location("mix01_inventory", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_tool = _load_script()

SHA = "a88527587389fd4ab352e9ad1273f4c0a234d8df"


def _freeze(
    tmp_path: Path,
    files: list[str],
    sizes: dict[str, int] | None = None,
    revision: str = SHA,
) -> tuple[int, Path]:
    file_list = tmp_path / "files.txt"
    file_list.write_text("\n".join(files) + "\n", encoding="utf-8")
    argv: list[str] = [
        "freeze",
        "--source",
        "ultrax_ultrafineweb",
        "--repo",
        "openbmb/UltraX-Preview",
        "--revision",
        revision,
        "--seed",
        "20260918",
        "--files",
        str(file_list),
        "--output",
        str(tmp_path / "inventory.json"),
    ]
    if sizes is not None:
        sizes_path = tmp_path / "sizes.json"
        sizes_path.write_text(json.dumps(sizes), encoding="utf-8")
        argv += ["--sizes", str(sizes_path)]
    return _tool.main(argv), tmp_path / "inventory.json"


def test_freeze_is_deterministic_with_digest(tmp_path: Path) -> None:
    files = ["b/part-02.parquet", "a/part-01.parquet", "c/part-03.parquet"]
    sizes = {name: 1000 + index for index, name in enumerate(files)}
    code, first = _freeze(tmp_path, files, sizes)
    assert code == 0
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["file_count"] == 3
    assert payload["known_size_bytes"] == sum(sizes.values())
    assert payload["revision"] == SHA
    ordered = [entry["file"] for entry in payload["files"]]
    expected = sorted(
        files,
        key=lambda n: (
            hashlib.sha256(f"20260918|openbmb/UltraX-Preview|{SHA}|{n}".encode()).hexdigest(),
            n,
        ),
    )
    assert ordered == expected
    assert len(payload["inventory_digest"]) == 64
    raw = first.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw
    code, _ = _freeze(tmp_path, files, sizes)
    assert code == 0
    assert (tmp_path / "inventory.json").read_bytes() == first.read_bytes()


def test_freeze_refuses_bad_inputs(tmp_path: Path) -> None:
    assert _freeze(tmp_path, [])[0] == 1
    assert _freeze(tmp_path, ["a.parquet", "a.parquet"])[0] == 1
    assert _freeze(tmp_path, ["a.parquet"], revision="main")[0] == 1
    assert _freeze(tmp_path, ["a.parquet"], sizes={"b.parquet": 10})[0] == 1
    assert _freeze(tmp_path, ["a.parquet"], sizes={"a.parquet": -5})[0] == 1


def _quotas(tmp_path: Path) -> Path:
    quotas = tmp_path / "quotas.yaml"
    quotas.write_text(
        yaml.safe_dump(
            {
                "final_quotas": {"ultrax_ultrafineweb": 1200000000, "finewiki_en": 300000000},
                "first_pass_headroom_quotas": {
                    "ultrax_ultrafineweb": 1320000000,
                    "finewiki_en": 330000000,
                },
            }
        ),
        encoding="utf-8",
    )
    return quotas


def _estimate(
    tmp_path: Path, calibration: dict[str, Any], extra: list[str] | None = None
) -> tuple[int, dict[str, Any]]:
    quotas = _quotas(tmp_path)
    calib = tmp_path / "calib.json"
    calib.write_text(json.dumps(calibration), encoding="utf-8")
    out = tmp_path / "estimate.json"
    argv = [
        "estimate",
        "--quotas",
        str(quotas),
        "--calibration",
        str(calib),
        "--output",
        str(out),
    ] + (extra or [])
    code = _tool.main(argv)
    if code != 0 or not out.is_file():
        return code, {}
    return code, json.loads(out.read_text(encoding="utf-8"))


def test_estimate_math_on_synthetic_calibration(tmp_path: Path) -> None:
    calibration = {
        "sources": {
            "ultrax_ultrafineweb": {
                "records_sampled": 1000,
                "accepted_records": 900,
                "rejected_records": 100,
                "transferred_bytes": 2000000,
                "canonical_bytes": 1000000,
                "avg_file_bytes": 50000000,
            }
        }
    }
    code, payload = _estimate(tmp_path, calibration)
    assert code == 0
    ultrax = payload["sources"]["ultrax_ultrafineweb"]
    assert ultrax["status"] == "ESTIMATED"
    assert ultrax["measured"]["acceptance_rate"] == pytest.approx(0.9)
    assert ultrax["measured"]["canonical_bytes_per_transferred_byte"] == pytest.approx(0.5)
    # Hand check: 1.32e9 usable * 4.0 B/token = 5.28e9 canonical;
    # / 0.5 yield = 1.056e10; * 1.15 safety = 1.2144e10 base.
    assert ultrax["required_transferred_bytes"]["base"] == int(1.32e9 * 4.0 / 0.5 * 1.15)
    assert ultrax["required_transferred_bytes"]["low"] == int(1.32e9 * 3.0 / 0.5 * 1.15)
    assert ultrax["required_transferred_bytes"]["high"] == int(1.32e9 * 5.0 / 0.5 * 1.15)
    assert ultrax["required_canonical_bytes_base"] == pytest.approx(1.32e9 * 4.0)
    assert ultrax["proposed_initial_files_base"] == 243
    assert payload["assumptions"]["bytes_per_token_base"] == 4.0
    # Uncalibrated source carries formulas, never numbers.
    finewiki = payload["sources"]["finewiki_en"]
    assert finewiki["status"] == "NEEDS_CALIBRATION"
    assert "formula" in finewiki


def test_estimate_refusals(tmp_path: Path) -> None:
    zero = {
        "sources": {
            "ultrax_ultrafineweb": {
                "records_sampled": 100,
                "accepted_records": 0,
                "rejected_records": 100,
                "transferred_bytes": 1000,
                "canonical_bytes": 10,
            }
        }
    }
    code, payload = _estimate(tmp_path, zero)
    assert code == 0
    assert payload["sources"]["ultrax_ultrafineweb"]["status"] == "BLOCKED"
    unknown = {"sources": {"mystery_source": {}}}
    code, _ = _estimate(tmp_path, unknown)
    assert code == 1


def test_sufficiency_statuses(tmp_path: Path) -> None:
    calibration = {
        "sources": {
            "ultrax_ultrafineweb": {
                "records_sampled": 1000,
                "accepted_records": 900,
                "rejected_records": 100,
                "transferred_bytes": 2000000,
                "canonical_bytes": 1000000,
            }
        }
    }
    code, estimate = _estimate(tmp_path, calibration)
    assert code == 0
    estimate_path = tmp_path / "estimate.json"
    estimate_path.write_text(json.dumps(estimate), encoding="utf-8")
    need = estimate["sources"]["ultrax_ultrafineweb"]["required_canonical_bytes_base"]
    acquired = tmp_path / "acquired.json"
    acquired.write_text(
        json.dumps(
            {
                "sources": {
                    "ultrax_ultrafineweb": {"canonical_bytes": need + 1},
                    "finewiki_en": {"canonical_bytes": 5},
                }
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "sufficiency.json"
    assert (
        _tool.main(
            [
                "sufficiency",
                "--estimate",
                str(estimate_path),
                "--acquired",
                str(acquired),
                "--output",
                str(out),
            ]
        )
        == 0
    )
    report = json.loads(out.read_text(encoding="utf-8"))["sources"]
    assert report["ultrax_ultrafineweb"]["status"] == "SUFFICIENT"
    assert report["finewiki_en"]["status"] == "UNKNOWN"
    acquired.write_text(
        json.dumps({"sources": {"ultrax_ultrafineweb": {"canonical_bytes": need - 10}}}),
        encoding="utf-8",
    )
    assert (
        _tool.main(
            [
                "sufficiency",
                "--estimate",
                str(estimate_path),
                "--acquired",
                str(acquired),
                "--output",
                str(out),
            ]
        )
        == 0
    )
    report = json.loads(out.read_text(encoding="utf-8"))["sources"]
    assert report["ultrax_ultrafineweb"]["status"] == "TOP_UP"
    assert report["ultrax_ultrafineweb"]["deficit_canonical_bytes"] == pytest.approx(10)
