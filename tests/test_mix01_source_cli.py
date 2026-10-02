"""Operator CLI end to end on an authored store and calibration tree (offline).

evidence publish -> review show/record -> admit -> policy freeze -> plan (STOP)
-> authorize -> resume-check -> status, with the real catalog, view registry
and quota file. No command here reaches the network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from mix01_source_fixtures import (
    CAL_FILE,
    REPOSITORY,
    REVISION,
    calibration_files,
    metadata_probe,
    probe_files,
    ultrax_row,
)
from xlm.artifacts.store import ArtifactStore
from xlm.core.paths import ArtifactPaths
from xlm.data.acquisition import source_plan as planner
from xlm.data.sources.admission import load_admission_decision

REPO = Path(__file__).resolve().parents[1]
ROWS = [ultrax_row(i, empty=i == 3) for i in range(40)]
#: Real-shaped UltraX layout numbers (one measured file, one 1,000-row group).
FILE_BYTES = 1_055_787_705
GROUP_BYTES = 7_867_072


def load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "mix01_source", REPO / "scripts" / "mix01_source.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value if isinstance(value, bytes) else json.dumps(value, indent=2).encode("utf-8")
    path.write_bytes(data)
    return path


def authored_tree(root: Path, probe_dir: Path) -> None:
    calib = root / "calib"
    evidence = calibration_files(ROWS)
    journal = json.loads(evidence.journal)
    journal["source_validators"][CAL_FILE]["length"] = FILE_BYTES
    write(calib / "ultrax_plan.json", evidence.plan)
    write(calib / "ultrax" / "scratch" / "journals" / "x.progress.json", journal)
    write(calib / "ultrax" / "raw" / "selected_records.jsonl", evidence.records)
    write(calib / "ultrax" / "canonical" / "adaptation_summary.json", evidence.summary)
    write(
        calib / "ultrax" / "scratch" / "performance" / "x.perf.json",
        {
            "transferred_bytes": 3_326_258,
            "requests_made": 6,
            "telemetry": {
                "parquet_groups": 1,
                "projection_selected_bytes": 2_270_221,
                "coalesced_ranges": 2,
                "open_seconds": 1.859,
            },
        },
    )
    write(
        calib / "ultrax_rows.evidence.json",
        {"mode": "rowgroup", "blocks": [{"num_rows": 1000, "compressed_bytes": GROUP_BYTES}]},
    )
    write(
        calib / "calibration.json",
        {
            "sources": {
                "ultrax_ultrafineweb": {
                    "records_sampled": 1000,
                    "accepted_records": 994,
                    "canonical_bytes": 3_845_430,
                    "transferred_bytes": 3_326_258,
                }
            }
        },
    )
    write(
        calib / "headroom_estimate.json",
        {
            "assumptions": {"bytes_per_token_base": 4.0, "safety_margin": 1.15},
            "sources": {
                "ultrax_ultrafineweb": {
                    "status": "ESTIMATED",
                    "first_pass_usable_token_target": 1_320_000_000,
                    "required_canonical_bytes_base": 5_280_000_000.0,
                }
            },
        },
    )
    receipt, sample = probe_files(ROWS[:6])
    write(probe_dir / "probe_receipt.json", receipt)
    write(probe_dir / "real-records.jsonl", sample)
    names = [
        f"data/UltraX-Ultra-FineWeb/UltraX-Ultra-FineWeb-en-part-{i:04d}-of-0104.parquet"
        for i in range(1, 105)
    ]
    seed = 20260918
    entries = sorted(
        (
            {
                "file": name,
                "size_bytes": None,
                "order_key": hashlib.sha256(
                    f"{seed}|{REPOSITORY}|{REVISION}|{name}".encode()
                ).hexdigest(),
            }
            for name in names
        ),
        key=lambda e: (e["order_key"], e["file"]),
    )
    inventory: dict[str, Any] = {
        "inventory_version": 1,
        "source_id": "ultrax_ultrafineweb",
        "repository": REPOSITORY,
        "revision": REVISION,
        "seed": seed,
        "selection": "SHA-256(seed|repository|revision|file) ascending, filename tiebreak",
        "files": entries,
        "file_count": len(entries),
        "known_size_bytes": None,
    }
    inventory["inventory_digest"] = planner.inventory_digest(inventory)
    write(root / "inventories" / "ultrax.inventory.json", inventory)


def test_operator_cli_offline_path(
    tmp_path: Path,
    isolated_xlm_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = load_cli()
    data, scratch, probe_dir = tmp_path / "data", tmp_path / "scratch", tmp_path / "probe"
    authored_tree(data, probe_dir)
    store = ArtifactStore(ArtifactPaths(root=isolated_xlm_home))
    metadata_probe(store)
    # The checkout guard is unit-tested; here the authored inputs stand in for operator files.
    monkeypatch.setattr(cli, "REPO", tmp_path / "checkout")
    monkeypatch.setenv("XLM_DATA_ROOT", str(data))
    monkeypatch.setenv("XLM_SCRATCH_ROOT", str(scratch))
    common = ["--source-key", "ultrax"]

    def run(*args: str) -> str:
        code = cli.main([*args])
        captured = capsys.readouterr()
        assert code == 0, captured.err
        return captured.out

    # Unknown or blocked keys refuse.
    assert cli.main(["status", "--source-key", "common_pile"]) == 0
    assert '"plans": []' in capsys.readouterr().out

    out = run("evidence", "show", *common, "--probe-dir", str(probe_dir))
    assert "show only: nothing written" in out
    out = run("evidence", "publish", *common, "--probe-dir", str(probe_dir))
    assert "BRIDGE RECEIPT DIGEST" in out and "published" in out
    run("evidence", "verify", *common, "--probe-dir", str(probe_dir))

    facts = json.loads(run("review", "show", *common))
    assert facts["declared_repository_license"] == "apache-2.0" and facts["legal_advice"] is False
    reviews = tmp_path / "reviews"
    run(
        "review",
        "record",
        *common,
        "--review-dir",
        str(reviews),
        "--operator",
        "tester",
        "--license-decision",
        "approve_research_pretraining",
        "--provenance-decision",
        "approved",
        "--benchmark-risk",
        "suspect_with_mitigation",
        "--rationale",
        "authored offline test",
    )
    # Plans and benchmarks refuse before admission.
    assert cli.main(["plan", *common]) == 1
    capsys.readouterr()
    out = run("admit", *common, "--review-dir", str(reviews))
    assert "admitted ultrax_ultrafineweb:UltraX-Ultra-FineWeb" in out
    assert "identical admission" in run("admit", *common, "--review-dir", str(reviews))
    decision = load_admission_decision("ultrax_ultrafineweb", "UltraX-Ultra-FineWeb", store)
    assert decision is not None and decision.contract_version == "c04-benchmark-risk-v3"

    model = json.loads(run("policy", "model", *common).split("\nTRANSPORT")[0])
    assert model[0]["modes"]["whole_file_local"]["requests"] == 24
    assert model[0]["modes"]["whole_file_local"]["transfer_bytes"] == 12 * FILE_BYTES
    out = run("policy", "freeze", *common)
    assert "TRANSPORT POLICY whole_file_local (modeled)" in out

    out = run("plan", *common)
    assert "STOP - USER MUST REVIEW PLAN DIGEST BEFORE AUTHORIZATION" in out
    assert "12 whole files" in out and "next top-up cursor 12" in out
    digest = next(
        line.split(": ")[1] for line in out.splitlines() if line.startswith("PLAN DIGEST")
    )
    # The plan was only written; nothing ran.
    assert not (data / "plans" / "ultrax" / "p01" / "authorization.json").exists()
    assert (
        cli.main(["authorize", *common, "--plan", "1", "--digest", "0" * 64, "--operator", "t"])
        == 1
    )
    capsys.readouterr()
    out = run("authorize", *common, "--plan", "1", "--digest", digest, "--operator", "tester")
    assert "authorized plan 1" in out
    check = json.loads(run("resume-check", *common, "--plan", "1"))
    assert check["restart"]["counts"]["fresh_download"] == 12 and check["sealed"] == 0
    status = json.loads(run("status", *common))
    assert status["plans"][0]["authorized"] and status["sufficiency"]["status"] == "INCOMPLETE"
    # Re-planning while plan 1 is incomplete refuses (no silent top-up).
    assert cli.main(["plan", *common]) == 1
    assert "INCOMPLETE" in capsys.readouterr().err


def test_measured_policy_with_range_reach_disposition(
    tmp_path: Path,
    isolated_xlm_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """benchmark range-reach -> policy freeze --basis measured --range-reach (offline)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from xlm.data.evidence_v2 import canonical

    cli = load_cli()
    data, scratch, probe_dir = tmp_path / "data", tmp_path / "scratch", tmp_path / "probe"
    authored_tree(data, probe_dir)
    monkeypatch.setattr(cli, "REPO", tmp_path / "checkout")
    monkeypatch.setenv("XLM_DATA_ROOT", str(data))
    monkeypatch.setenv("XLM_SCRATCH_ROOT", str(scratch))
    common = ["--source-key", "ultrax"]
    pin = cli.pin_of(cli.spec_of("ultrax")).as_dict()

    def run(*args: str, code: int = 0) -> str:
        found = cli.main([*args])
        captured = capsys.readouterr()
        assert found == code, captured.err
        return captured.out + captured.err

    # One small reachable group, then one group whose logical size exceeds 32 MiB.
    local = tmp_path / "local.parquet"
    big = "x" * (17 * 1024 * 1024)
    pq.write_table(
        pa.table({"text": ["a", "b", big, big + "y"]}), local, row_group_size=2, compression="zstd"
    )
    local_sha = hashlib.sha256(local.read_bytes()).hexdigest()
    bench_dir = data / "plans" / "ultrax" / "benchmarks" / "b9"
    benchmark: dict[str, Any] = {
        "kind": "mix01_source_benchmark_plan",
        "label": "b9",
        "source": pin,
        "files": [{"file": CAL_FILE, "rank": None, "reason": "named calibration file"}],
        "inputs": {"admission": {}},
    }
    benchmark["digest"] = canonical.digest(benchmark)
    write(bench_dir / "benchmark.json", benchmark)

    reach_args = ["benchmark", "range-reach", *common, "--label", "b9", "--local-file", str(local)]
    out = run(*reach_args, "--expected-sha256", "0" * 64, code=1)
    assert "differs from the expected identity" in out
    out = run(*reach_args, "--expected-sha256", local_sha)
    assert "range reach non_comparable: 1/2 groups reachable" in out
    reach_path = bench_dir / "range-reach.json"
    audit = json.loads(reach_path.read_text(encoding="utf-8"))
    assert audit["refused_groups"] == [1] and audit["file"]["sha256"] == local_sha
    assert audit["bounds"]["max_parser_bytes"] == planner.MAX_PARSER_BYTES

    def receipt(file: str) -> Path:
        body: dict[str, Any] = {
            "kind": "mix01_source_performance_receipt",
            "benchmark": {"digest": benchmark["digest"], "files": [{"file": file}], "label": "b9"},
            "plan": {"digest": benchmark["digest"], "plan_hash": "5" * 64, "sequence": None},
            "source": pin,
            "outcome": {"status": "completed"},
            "transport_mode": "whole_file_local",
            "concurrency": {"download_workers": 1, "process_workers": 1},
            "transfer": {
                "files": 1,
                "file_bytes": FILE_BYTES,
                "transferred_bytes": FILE_BYTES,
                "requests": 2,
                "wall_seconds": 60.0,
                "sha256_independently_verified": 1,
            },
            "processing": {
                "units": 1,
                "rows": 300_000,
                "documents": 290_000,
                "rejected": 10_000,
                "canonical_bytes": 1_200_000_000,
                "rows_per_process_second": 2000.0,
            },
        }
        body["digest"] = canonical.digest(body)
        return write(tmp_path / f"receipt-{len(file)}.json", body)

    whole = receipt(CAL_FILE)
    freeze = ["policy", "freeze", *common, "--basis", "measured", "--whole-receipt", str(whole)]
    assert "exactly one of" in run(*freeze, code=1)
    out = run(*freeze, "--range-reach", str(reach_path), "--range-receipt", str(whole), code=1)
    assert "exactly one of" in out
    # The audit must cover the file the whole-file benchmark measured.
    other = receipt("data/other.parquet")
    out = run(
        "policy",
        "freeze",
        *common,
        "--basis",
        "measured",
        "--whole-receipt",
        str(other),
        "--range-reach",
        str(reach_path),
        code=1,
    )
    assert "did not measure" in out
    policy_path = data / "plans" / "ultrax" / "transport-policy.json"
    assert not policy_path.exists()

    out = run(*freeze, "--range-reach", str(reach_path))
    assert "SIZING mix01-whole-file-sizing-v1" in out and "300,000 rows/file" in out
    assert "DISPOSITION range_selected non_comparable (range-v1-reach-v1)" in out
    assert "TRANSPORT POLICY whole_file_local (measured)" in out
    record = json.loads(policy_path.read_text(encoding="utf-8"))
    assert record["policy"] == "mix01-transport-policy-v2"
    assert set(record["inputs"]) == {"whole", "range_reach"}
    assert record["dispositions"][0]["evidence_digest"] == audit["digest"]
    assert record["sizing"]["derived"]["rows_per_file"] == 300_000
    assert "not a speed comparison" in record["reason"]
    # Re-freezing identical inputs is a no-op; the record is write-once.
    assert "TRANSPORT POLICY whole_file_local (measured)" in run(
        *freeze, "--range-reach", str(reach_path)
    )
