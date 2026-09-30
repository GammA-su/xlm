"""Authored offline readiness regressions. No dataset access or network."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from xlm.cli.data_cmd import app
from xlm.data.acquisition.plan import AcquisitionMode, AcquisitionPlan, save_acquisition_plan
from xlm.data.acquisition.progress import AcquisitionState, FileProgress, ResourceAccount
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.adapters.malformed import MalformedCounter, MalformedLimitError
from xlm.data.sources import essential_web_readiness as ready
from xlm.data.sources.admission import AdmissionDecision, AdmissionGate
from xlm.data.sources.prober import EvidenceType, ProbeEvidenceRecord, ProbeOutcome
from xlm.data.sources.schema import FieldDescriptor, ViewSchema


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("offline test attempted network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def physical() -> list[dict[str, Any]]:
    return [
        {
            "file": f"data/crawl={i}/fixture.parquet",
            "crawl": str(i),
            "file_rows": 50000,
            "group_rows": 10000,
            "remote_length": 100000000,
            "strong_etag": '"authored"',
            "footer_sha256": "0" * 64,
            "confirmation_window": [9000, 9512],
            "footer_bytes": 1000,
            "projected_compressed_bytes": 20000000,
            "selector_compressed_bytes": 1000000,
        }
        for i in range(8)
    ]


@pytest.mark.parametrize("view", selector.ADMITTED_COMPONENTS)
@pytest.mark.parametrize("drift", [None, "policy", "revision", "adapter", "repository", "view"])
def test_admission_binds_every_view(view: str, drift: str | None) -> None:
    # Authored real-observed-shaped record exercises gate logic, never claims live compatibility.
    evidence = ProbeEvidenceRecord(
        source_id="essential_web",
        view_id=view,
        provider="huggingface",
        repository=ready.REPOSITORY,
        immutable_revision=selector.SOURCE_REVISION,
        outcome=ProbeOutcome.ACCESSIBLE,
        evidence_type=EvidenceType.REAL_OBSERVED,
        probe_fingerprint="authored",
        verified_schema=ViewSchema(
            view_id=view, fields={"text": FieldDescriptor(name="text", type_name="string")}
        ),
        declared_license="odc-by",
    )
    decision = AdmissionDecision(
        source_id=evidence.source_id,
        view_id=view,
        provider=evidence.provider,
        repository=evidence.repository,
        immutable_revision=selector.SOURCE_REVISION,
        adapter_id="essential_web_bnormal",
        selector_binding=selector.selector_identity(),
        probe_fingerprint="authored",
        license_review="approved",
        provenance_review="approved",
        operator_approved=True,
    )
    if drift == "policy":
        assert decision.selector_binding is not None
        decision.selector_binding["policy_digest"] = "0" * 64
    elif drift == "revision":
        decision.immutable_revision = "0" * 40
    elif drift == "adapter":
        decision.adapter_id = "essential_web"
    elif drift == "repository":
        decision.repository = "wrong/repo"
    elif drift == "view":
        decision.view_id = "wrong_view"
    assert AdmissionGate.evaluate(evidence, decision).admitted == (drift is None)
    evidence.evidence_type = EvidenceType.SYNTHETIC_FIXTURE
    assert not AdmissionGate.evaluate(evidence, decision).admitted


def test_deterministic_cost_decision() -> None:
    files = physical()
    model = ready.transfer_model(files)
    assert model == ready.transfer_model(copy.deepcopy(files))
    assert model["full_text_first_bytes"] == 8 * (20000000 + 1000 + 65540)
    assert model["metadata_first_bytes"] == model["full_text_first_bytes"]
    assert model["strategy"] == "full_text_first"
    assert model["expected_saving_fraction"] == 0
    for file in files:
        file["group_rows"] = 1
    assert ready.transfer_model(files)["strategy"] == "metadata_first"


def test_calibration_is_new_frozen_16384_rows() -> None:
    files = physical()
    plan = ready.calibration_plan(files)
    assert plan == ready.calibration_plan(copy.deepcopy(files))
    assert plan["expected_rows"] == sum(w["rows"] for w in plan["windows"]) == 16384
    assert len({w["crawl"] for w in plan["windows"]}) == 8
    assert all(w["range"] == [0, 2048] for w in plan["windows"])
    assert plan["selector"] == selector.selector_identity()
    files[0]["confirmation_window"] = [1000, 1512]
    with pytest.raises(ValueError, match="overlaps"):
        ready.calibration_plan(files)


def test_capacity_never_invents_science_tokens() -> None:
    quotas = {
        "final_quotas": {"essential_science": 600000000},
        "first_pass_headroom_quotas": {"essential_science": 660000000},
    }
    result = ready.science_capacity(quotas, 100, physical())
    assert result["source_capacity_sufficient"] is None
    assert result["required_rows"] is None
    assert result["scanned_rows_per_admitted_science_row"] == 4096 / 24
    measured = ready.science_capacity(
        quotas,
        100,
        physical(),
        {"input_rows": 16384, "science_canonical_bytes": 98304, "transferred_bytes": 100000000},
    )
    assert measured["required_rows"] == 440000000
    assert measured["required_files"] == 8800
    assert measured["final_science_exact_token_quota"] == 600000000


def test_malformed_threshold_is_strict_and_waits_for_100_rows() -> None:
    counter = MalformedCounter()
    counter.observe(True)
    for _ in range(99):
        counter.observe(False)
    assert counter.malformed == 1
    with pytest.raises(MalformedLimitError):
        counter.observe(True)
    early = MalformedCounter()
    early.observe(True)
    early.observe(True)
    for _ in range(97):
        early.observe(False)
    with pytest.raises(MalformedLimitError):
        early.observe(False)


def row() -> dict[str, Any]:
    return {
        "text": "Authored fixture science paragraph.",
        "id": 7,
        "pid": "authored",
        "metadata": {},
        "quality_signals": {"fasttext": {"english": 0.95}},
        "eai_taxonomy": {
            "free_decimal_correspondence": {"primary": {"code": "510"}},
            **{
                key: {"primary": {"label": value}}
                for key, value in {
                    "document_type_v2": "Academic Writing",
                    "bloom_knowledge_domain": "Conceptual",
                    "extraction_artifacts": "No Artifacts",
                    "missing_content": "No missing content",
                    "technical_correctness": "Highly Correct",
                }.items()
            },
        },
    }


@pytest.mark.parametrize("fault", ["one_bad", "threshold", "revision", "path", "broken_json"])
def test_actual_adapt_row_quarantine_and_structural_stop(tmp_path: Path, fault: str) -> None:
    source_file = "data/authored.parquet"
    n = 101 if fault == "threshold" else 2
    plan = AcquisitionPlan(
        plan_id="authored_readiness",
        source_id="essential_web",
        view_id="essential_science",
        provider="huggingface",
        repository=ready.REPOSITORY,
        revision=selector.SOURCE_REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=[source_file],
        row_ranges={source_file: (0, n)},
        output_artifact_id="authored_raw",
    )
    path = tmp_path / "plan.json"
    save_acquisition_plan(plan, path)
    rows = []
    for i in range(n):
        record = row()
        record["_xlm_acquisition"] = {
            "source_id": "essential_web",
            "source_file": source_file,
            "revision": plan.revision,
            "row_index": i,
            "selection_hash": plan.compute_selection_hash(),
        }
        if i == 0 or (fault == "threshold" and i == 100):
            record["text"] = ""
        rows.append(record)
    if fault in ("revision", "path"):
        rows[1]["_xlm_acquisition"]["revision" if fault == "revision" else "source_file"] = "wrong"
    raw = tmp_path / "raw.jsonl"
    raw.write_text(
        "\n".join(json.dumps(r) for r in rows) + ("\n{" if fault == "broken_json" else "\n"),
        encoding="utf-8",
    )
    out = tmp_path / "canonical"
    result = CliRunner().invoke(
        app,
        [
            "adapt",
            "--plan",
            str(path),
            "--adapter",
            "essential_web_bnormal",
            "--adapter-config",
            "essential_science",
            "--input",
            str(raw),
            "--output-dir",
            str(out),
            "--on-reject",
            "record",
        ],
    )
    if fault == "one_bad":
        assert result.exit_code == 0, result.output
        summary = json.loads((out / "adaptation_summary.json").read_bytes())
        assert summary["accepted_records"] == summary["rejected_records"] == 1
        reject = json.loads((out / "adaptation_rejections.jsonl").read_bytes())
        assert reject["rejection_category"] == "malformed"
        assert reject["reason"] == "essential_web_unusable_record"
        assert reject["rejection_code"] == "EssentialWebMalformedRowError"
    else:
        assert result.exit_code != 0
        assert not (out / "adaptation_summary.json").exists()


@pytest.mark.parametrize(
    "key,value", [("revision", "bad"), ("selector", {}), ("repository", "bad")]
)
def test_dry_binding_refuses_drift(key: str, value: Any) -> None:
    binding = ready.source_binding()
    ready.check_binding(binding)
    binding[key] = value
    with pytest.raises(ValueError, match="binding mismatch"):
        ready.check_binding(binding)


def test_shared_measurement_counts_transfer_once_and_allows_empty_views(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    tool = importlib.import_module("essential_web_measure")
    root, freeze = tmp_path / "run", tmp_path / "freeze"
    freeze.mkdir()
    unit = root / "probe-00"
    raw_dir = unit / "raw"
    raw_dir.mkdir(parents=True)
    file = "data/authored.parquet"
    plan = AcquisitionPlan(
        plan_id="authored_measure",
        source_id="essential_web",
        view_id="essential_science",
        provider="huggingface",
        repository=ready.REPOSITORY,
        revision=selector.SOURCE_REVISION,
        mode=AcquisitionMode.SELECTED_RECORDS,
        selected_files=[file],
        row_ranges={file: (0, 256)},
        output_artifact_id="authored_raw",
    ).with_computed_hash()
    for parent in (root, freeze):
        save_acquisition_plan(plan, parent / "probe-00.plan.json")
    (freeze / "calibration-plan.json").write_text(
        json.dumps(
            {
                "windows": [
                    {
                        "file": file,
                        "crawl": "authored",
                        "strong_etag": '"authored"',
                        "remote_length": 1000000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    records = [
        {
            **row(),
            "_xlm_acquisition": {
                "source_id": "essential_web",
                "source_file": file,
                "row_index": i,
                "revision": plan.revision,
                "selection_hash": plan.compute_selection_hash(),
            },
        }
        for i in range(256)
    ]
    raw = raw_dir / "selected_records.jsonl"
    raw.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    state = AcquisitionState(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        status="COMPLETED",
        transferred_bytes=500000,
        decompressed_bytes=1000000,
        records_acquired=256,
        accounting=ResourceAccount(consumed={"records_scanned": 256}),
        source_validators={file: {"etag": '"authored"', "length": 1000000}},
        file_progress={
            "selected_records.jsonl": FileProgress(
                file_path="selected_records.jsonl",
                status="completed",
                record_count=256,
                content_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
            )
        },
    )
    journal = unit / "scratch/journals" / f"{plan.plan_id}.progress.json"
    journal.parent.mkdir(parents=True)
    journal.write_text(state.model_dump_json(), encoding="utf-8")
    for view in selector.ADMITTED_COMPONENTS:
        result = CliRunner().invoke(
            app,
            [
                "adapt",
                "--plan",
                str(root / "probe-00.plan.json"),
                "--adapter",
                "essential_web_bnormal",
                "--adapter-config",
                view,
                "--input",
                str(raw),
                "--output-dir",
                str(unit / view),
                "--on-reject",
                "record",
            ],
        )
        assert result.exit_code == 0, result.output
    result = tool.measure(root, freeze, "probe")
    assert result["physical_response_body_bytes"] == 500000
    assert result["selector_counts"] == {
        "essential_science": 256,
        "essential_practical": 0,
        "essential_prose": 0,
        "unassigned": 0,
        "rejected": 0,
    }
    assert result["total_retained_documents"] == 256
    assert result["retention_and_cost"]["essential_practical"]["estimated_tokens"] == 0
    assert (
        result["retention_and_cost"]["essential_practical"]["transfer_bytes_per_estimated_token"]
        is None
    )
    raw.write_bytes(raw.read_bytes() + b"{}\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        tool.measure(root, freeze, "probe")


def test_inventory_order_revision_and_duplicate_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    inventory = importlib.import_module("mix01_inventory")
    names = [f"data/crawl=AUTHORED/train-{i}.parquet" for i in range(3)]
    args = ("essential_web", ready.REPOSITORY, selector.SOURCE_REVISION, ready.SEED)
    frozen = inventory.freeze_inventory(*args, names, {names[0]: 4096})
    assert frozen == inventory.freeze_inventory(*args, list(reversed(names)), {names[0]: 4096})
    assert frozen["revision"] == selector.SOURCE_REVISION
    assert all(set(e) == {"file", "size_bytes", "order_key"} for e in frozen["files"])
    with pytest.raises(ValueError, match="distinct"):
        inventory.freeze_inventory(*args, [names[0], names[0]], {})
    other = inventory.freeze_inventory(
        "essential_web", ready.REPOSITORY, "0" * 40, ready.SEED, names, {}
    )
    assert other["inventory_digest"] != frozen["inventory_digest"]


def test_future_command_paths_share_config_and_cover_three_views(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo / "scripts"))
    tool = importlib.import_module("essential_web_readiness")
    (tmp_path / "probe-00.plan.json").write_text(
        json.dumps({"plan_hash": "a" * 64}), encoding="utf-8"
    )
    command = tool.command_script(tmp_path, physical(), "probe")
    assert command.count("xlm data fetch") == 1
    assert command.count("xlm data adapt") == 3
    assert all(f"--adapter-config {view}" in command for view in selector.ADMITTED_COMPONENTS)
    assert "X:\\XLM" not in command
    config = json.loads((repo / "recipes/operator/storage.json").read_bytes())
    assert config["data_root"] == "G:\\XLM"
    assert "recipes/operator/storage.json" in command
    assert "$env:XLM_DATA_ROOT" in command
    driver = (repo / "scripts/operator_calibrate_remaining.ps1").read_text(encoding="utf-8")
    assert "[string]$DataRoot = $env:XLM_DATA_ROOT" in driver
    assert "X:\\XLM" not in driver


def test_operator_root_setup_in_powershell() -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell unavailable")
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ". ./scripts/operator_storage.ps1; "
            "@($env:XLM_DATA_ROOT,$env:XLM_HOME,$env:HF_HOME,$env:TEMP) | ConvertTo-Json -Compress",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "G:\\XLM",
        "G:\\XLM\\xlm-home",
        "G:\\XLM\\hf-cache",
        "G:\\XLM\\temp",
    ]
    refused = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/operator_calibrate_remaining.ps1",
            "-Unit",
            "essential_science",
            "-DataRoot",
            "G:\\XLM",
            "-Stage",
            "Status",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert refused.returncode != 0
    assert "legacy driver cannot run Essential calibration" in refused.stderr
