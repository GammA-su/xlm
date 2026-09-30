"""Essential-Web bulk campaign: models, planning, accounting and gates.

Authored synthetic fixtures only. No dataset access and no network: a mock
fetch proves the campaign logic, not live dataset compatibility.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml
from typer.testing import CliRunner

from xlm.cli.data_cmd import app
from xlm.data.acquisition.plan import (
    AcquisitionLimits,
    AuthorizationRequiredError,
    load_acquisition_plan,
    plan_requires_production_admission,
    validate_plan_authorization,
)
from xlm.data.acquisition.progress import AcquisitionState, FileProgress, ResourceAccount
from xlm.data.acquisition.sampling import discover_layout_local
from xlm.data.adapters import essential_web_selector as selector
from xlm.data.adapters.mix01_adapters import ADAPTERS_BY_ID
from xlm.data.exclusion.receipt import ReceiptValidationError, verify_benchmark_claim
from xlm.data.sources import essential_web_bulk as bulk
from xlm.data.sources import essential_web_readiness as ready
from xlm.data.sources.admission import essential_contamination_mitigation

REPO = Path(__file__).resolve().parents[1]
MIB = 1024**2
SCIENCE, PRACTICAL, PROSE = "essential_science", "essential_practical", "essential_prose"
LIMITS: dict[str, Any] = {
    "restart_factor": 3,
    "metadata_bytes_per_file": MIB,
    "metadata_requests_per_file": 8,
    "request_margin": 1.25,
    "decode_expansion": 1.5,
    "raw_bytes_per_row_bound": 4096,
    "planning_group_rows": 5,
    "seconds_per_request": 0.25,
    "seconds_per_transfer_byte": 1e-6,
    "seconds_per_row": 1e-3,
    "deadline_factor": 8,
    "min_deadline_seconds": 3600,
    "per_request_timeout_seconds": 15.0,
    "max_retries": 5,
    "max_record_bytes": MIB,
    "max_parser_bytes": 32 * MIB,
    "max_decompression_ratio": 15.0,
}
KINDS = ("science", "practical", "prose", "unassigned", "rejected")
SHAPE = {
    "science": ("510", "Academic Writing", "Conceptual"),
    "practical": ("320", "Tutorial", "Conceptual"),
    "prose": ("320", "Personal Blog", "Conceptual"),
    "unassigned": ("320", "Academic Writing", "Conceptual"),
    "rejected": ("320", "Product Page", "Conceptual"),
    "malformed": ("320", "Tutorial", "Not A Frozen Label"),
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("offline test attempted network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def tool(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    return importlib.import_module("essential_web_bulk")


def fixture_row(kind: str, index: int) -> dict[str, Any]:
    code, doctype, knowledge = SHAPE[kind]
    labels = {
        "document_type_v2": doctype,
        "bloom_knowledge_domain": knowledge,
        "extraction_artifacts": "No Artifacts",
        "missing_content": "No missing content",
        "technical_correctness": "Highly Correct",
    }
    return {
        "text": f"Authored fixture paragraph number {index}. " * 3,
        "id": index,
        "pid": "authored",
        "metadata": {},
        "quality_signals": {"fasttext": {"english": 0.95}},
        "eai_taxonomy": {
            "free_decimal_correspondence": {"primary": {"code": code}},
            **{key: {"primary": {"label": value}} for key, value in labels.items()},
        },
    }


def canonical_bytes(kind: str, index: int) -> int:
    view = f"essential_{kind}"
    document = ADAPTERS_BY_ID["essential_web_bnormal"](view).adapt(
        fixture_row(kind, index),
        source_file="data/authored.parquet",
        source_row=index,
        source_revision=selector.SOURCE_REVISION,
    )
    return len(document.text.encode("utf-8"))


def layout(name: str, group_rows: list[int], refusal: str | None = None) -> dict[str, Any]:
    groups, start = [], 0
    for index, rows in enumerate(group_rows):
        groups.append(
            {
                "index": index,
                "start_row": start,
                "rows": rows,
                "projected_leaves": 6,
                "projected_compressed_bytes": 1000 * rows,
                "projected_uncompressed_bytes": 2000 * rows,
                "text_compressed_bytes": 600 * rows,
                "buffered_reads": 6,
                "refusal": refusal,
            }
        )
        start += rows
    return {"file": name, "rows": start, "groups": groups}


@dataclass
class World:
    tool: Any
    path: Path
    root: Path
    repo: Path
    config: dict[str, Any]

    def run(self, *argv: str) -> int:
        return int(
            self.tool.main(["--campaign", str(self.path), "--data-root", str(self.root), *argv])
        )

    def campaign(self) -> Any:
        return self.tool.load_campaign(self.path, self.root)

    def batch_dir(self, batch: int) -> Path:
        return self.root / "plans/ew-bulk" / f"b{batch:04d}"

    def write_layout(self, batch: int, group_rows: list[int]) -> list[str]:
        files: list[str] = self.campaign().members(batch)
        record = self.tool.self_digest(
            {
                "campaign": self.config["digest"],
                "batch": batch,
                "files": files,
                "revision": selector.SOURCE_REVISION,
                "layouts": {name: layout(name, group_rows) for name in files},
            }
        )
        self.tool.write_once(self.batch_dir(batch) / "layout.json", record)
        return files

    def prepare(self, batch: int, group_rows: list[int]) -> dict[str, Any]:
        self.write_layout(batch, group_rows)
        assert self.run("plan", "--batch", str(batch)) == 0
        record: dict[str, Any] = json.loads((self.batch_dir(batch) / "batch.json").read_bytes())
        return record

    def authorize(self, batch: int, digest: str) -> int:
        return self.run("authorize", "--batch", str(batch), "--digest", digest, "--operator", "t")

    def fetch(self, batch: int, index: int, bad: frozenset[int] = frozenset()) -> None:
        """Mock fetch: authored records and a COMPLETED journal for one authorized slice."""
        entry = self.tool.current_slices(self.campaign(), batch)[index]
        plan = load_acquisition_plan(entry["plan_path"])
        assert plan.row_ranges is not None
        lines = []
        for name in plan.selected_files:
            for row in range(*plan.row_ranges[name]):
                malformed = row in bad and name == plan.selected_files[0]
                kind = "malformed" if malformed else KINDS[row % len(KINDS)]
                record = fixture_row(kind, row)
                record["_xlm_acquisition"] = {
                    "source_id": "essential_web",
                    "source_file": name,
                    "revision": plan.revision,
                    "row_index": row,
                    "selection_hash": plan.compute_selection_hash(),
                }
                lines.append(json.dumps(record))
        entry["raw_dir"].mkdir(parents=True)
        raw = entry["raw_dir"] / "selected_records.jsonl"
        raw.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        state = AcquisitionState(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            status="COMPLETED",
            transferred_bytes=1000 * len(lines),
            decompressed_bytes=2000 * len(lines),
            requests_made=12,
            records_acquired=len(lines),
            accounting=ResourceAccount(
                consumed={"records_scanned": len(lines)}, deadline_at=time.time() + 3600
            ),
            storage_roots={
                "scratch": str(entry["scratch_dir"].resolve()),
                "output": str(entry["raw_dir"].resolve()),
            },
            source_validators={
                name: {"etag": f'"{name}"', "length": 1000} for name in plan.selected_files
            },
            file_progress={
                "selected_records.jsonl": FileProgress(
                    file_path="selected_records.jsonl",
                    status="completed",
                    bytes_downloaded=raw.stat().st_size,
                    record_count=len(lines),
                    content_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
                )
            },
        )
        entry["journal_path"].parent.mkdir(parents=True)
        entry["journal_path"].write_text(state.model_dump_json(), encoding="utf-8")

    def adapt(self, batch: int, index: int) -> list[int]:
        entry = self.tool.current_slices(self.campaign(), batch)[index]
        codes = []
        for view, directory in entry["canonical"].items():
            result = CliRunner().invoke(
                app,
                [
                    "adapt",
                    "--plan",
                    str(entry["plan_path"]),
                    "--adapter",
                    "essential_web_bnormal",
                    "--adapter-config",
                    view,
                    "--input",
                    str(entry["raw_dir"] / "selected_records.jsonl"),
                    "--output-dir",
                    str(directory),
                    "--on-reject",
                    "record",
                    "--max-input-bytes",
                    str(entry["limits"]["max_output_disk_bytes"]),
                ],
            )
            codes.append(result.exit_code)
        return codes

    def complete(self, batch: int, group_rows: list[int]) -> None:
        record = self.prepare(batch, group_rows)
        assert self.authorize(batch, record["authorization_digest"]) == 0
        for index in range(len(record["slices"])):
            self.fetch(batch, index)
            assert self.adapt(batch, index) == [0, 0, 0]
            assert self.run("seal-slice", "--batch", str(batch), "--slice", str(index)) == 0

    def status(self) -> dict[str, Any]:
        decision: dict[str, Any] = json.loads(
            (self.root / "plans/ew-bulk/campaign-status.json").read_bytes()
        )["decision"]
        return decision


def make_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: Any,
    required: dict[str, int],
    max_batches: int = 3,
) -> World:
    sealer = importlib.import_module("essential_web_calibration_seal")
    inventory_tool = importlib.import_module("mix01_inventory")
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(REPO / tool.QUOTAS, repo / "quotas.yaml")
    shutil.copy(REPO / tool.READY_DIR / "production-catalog.json", repo / "catalog.json")
    names = [f"data/crawl=AUTHORED/train-{index:05d}.parquet" for index in range(8)]
    inventory = inventory_tool.freeze_inventory(
        "essential_web", ready.REPOSITORY, selector.SOURCE_REVISION, ready.SEED, names, {}
    )
    (repo / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    config = bulk.campaign_config(
        binding=ready.source_binding(),
        adapter_code_sha256=sealer.code_identity(),
        seal_digest="0" * 64,
        freeze_digest="1" * 64,
        inventory=inventory,
        inventory_path="inventory.json",
        catalog_path="catalog.json",
        catalog_sha256=sealer.file_sha256(repo / "catalog.json"),
        quotas=yaml.safe_load((repo / "quotas.yaml").read_bytes()),
        quotas_path="quotas.yaml",
        quotas_sha256=sealer.file_sha256(repo / "quotas.yaml"),
        required_canonical_bytes=required,
        batch_files=2,
        max_batches=max_batches,
        limits=LIMITS,
        min_free_bytes=MIB,
        footprint_cap_bytes=1024 * MIB,
    )
    path = repo / "campaign.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(tool, "REPO", repo)
    monkeypatch.setenv("XLM_HOME", str(tmp_path / "home"))
    return World(tool=tool, path=path, root=tmp_path / "data", repo=repo, config=config)


def science_bytes_per_batch() -> int:
    """Two files, rows 0..9 each: science rows are 0 and 5."""
    return 2 * (canonical_bytes("science", 0) + canonical_bytes("science", 5))


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: Any) -> World:
    required = {SCIENCE: 2 * science_bytes_per_batch() - 1, PRACTICAL: 1, PROSE: 1}
    return make_world(tmp_path, monkeypatch, tool, required)


# ------------------------------------------------------------------- models


def physical_inputs() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    footers: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    for name, groups in (("data/a.parquet", [10, 10, 10]), ("data/b.parquet", [10, 10])):
        record = layout(name, groups)
        record.update(remote_length=50_000, footer_bytes=100)
        footers.append(record)
        units.append(
            {
                "file": name,
                "input_rows": 4,
                # non-text chunks + footer open + exactly two text buffers
                "transferred_bytes": 4000
                + 100
                + bulk.FOOTER_OPEN_BYTES
                + 2 * bulk.WINDOW_BUFFER_BYTES,
                "performance": {
                    "logical_requests": 10,
                    "open_seconds": 5.0,
                    "body_seconds": 1.0,
                    "wall_seconds": 7.0,
                },
            }
        )
    totals = {
        "input_rows": 8,
        "physical_response_body_bytes": sum(unit["transferred_bytes"] for unit in units),
        "decompressed_bytes": 2 * 20_000 * 4 // 10,
        "raw_bytes": 80_000,
        "rejections_file_bytes": 16_000,
        "documents_file_bytes": 8_000,
        "max_raw_record_bytes": 500_000,
        "elapsed_wall_seconds_including_restart_downtime": 16.0,
        "retained": {SCIENCE: {"documents": 2, "canonical_bytes": 64}},
    }
    return footers, units, totals


def test_physical_model_reads_whole_chunks_not_prefix_shares() -> None:
    footers, units, totals = physical_inputs()
    model = bulk.physical_cost(footers, units, totals)
    first = model["files"][0]
    assert first["projected_compressed_bytes"] == 30_000
    assert first["full_file_transfer_bytes"] == 30_000 + 3 * (100 + bulk.FOOTER_OPEN_BYTES)
    assert first["requests"] == 3 * (6 + 4)
    assert [v["implied_text_buffers"] for v in model["prefix_model_validation"]] == [2.0, 2.0]
    full = model["full_file"]
    assert full["rows"] == 50 and full["mean_rows_per_file"] == 25
    assert full["min_max_rows_per_file"] == [20, 30]
    assert full["max_row_groups_per_file"] == 3 and full["max_group_rows"] == 10
    assert full["transfer_bytes"] == 50_000 + 5 * (100 + bulk.FOOTER_OPEN_BYTES)
    assert full["decoded_bytes_per_projected_uncompressed_byte"] == 1.0
    timing = model["timing"]
    assert timing["seconds_per_request"] == 0.5
    assert timing["seconds_per_row"] == 0.25
    assert timing["seconds_per_transfer_byte"] == 2.0 / totals["physical_response_body_bytes"]
    # The prefix-linear cost per row is far above the full-file cost per row.
    assert model["prefix_linear"]["transfer_bytes_per_row"] > full["transfer_bytes_per_row"]
    cost = bulk.extrapolate(model, 100)
    assert cost["estimated_files"] == 4
    assert cost["full_file_transfer_bytes"] == pytest.approx(2 * full["transfer_bytes"], abs=1)
    assert cost["requests"] == 2 * (3 + 2) * 10
    assert cost["prefix_linear_transfer_bytes"] == 100 * totals["physical_response_body_bytes"] // 8
    assert cost["estimated_seconds_one_worker"] == pytest.approx(
        100 * 0.5 + cost["full_file_transfer_bytes"] * timing["seconds_per_transfer_byte"] + 25
    )
    with pytest.raises(bulk.BulkError, match="different files"):
        bulk.physical_cost(list(reversed(footers)), units, totals)


def test_science_capacity_keeps_the_frozen_target_across_token_sizings() -> None:
    totals = {
        "input_rows": 1000,
        "physical_response_body_bytes": 4_000_000,
        "elapsed_wall_seconds_including_restart_downtime": 25.0,
        "retained": {SCIENCE: {"documents": 10, "canonical_bytes": 8000}},
    }
    quotas = {
        "final_quotas": {SCIENCE: 600},
        "first_pass_headroom_quotas": {SCIENCE: 660},
    }
    dispersion = {"science_rows_required_leave_one_out_range": [300, 400]}
    model = bulk.science_capacity(totals, quotas, dispersion)
    rows = {name: case["required_input_rows"] for name, case in model["cases"].items()}
    assert rows == {"low": 248, "central": 330, "high": 413}
    assert model["cases"]["central"]["estimated_canonical_science_bytes"] == 2640
    assert model["cases"]["central"]["naive_linear_transfer_bytes"] == 1_320_000
    assert model["cases"]["central"]["naive_linear_elapsed_seconds"] == 8.25
    assert model["first_pass_estimated_token_target"] == 660 and model["quota_changed"] is False
    assert model["exact_token_break_even_bytes_per_token"] == pytest.approx(4.4)
    assert model["rows_for_final_quota_if_bytes_per_token_is_high"] == 375
    assert "NOT a guaranteed acquisition budget" in model["model"]


def timing_model(seconds_per_request: float) -> dict[str, Any]:
    return {
        "full_file": {
            "mean_rows_per_file": 80_000,
            "max_group_rows": 10_000,
            "max_group_projected_compressed_bytes": 30_000_000,
            "max_group_buffered_reads": 106,
            "max_row_groups_per_file": 10,
            "transfer_bytes_per_row": 2800.0,
            "transfer_bytes_per_row_min_max_file": [2600.0, 2900.0],
            "requests_per_row": 0.0115,
        },
        "timing": {
            "seconds_per_request": seconds_per_request,
            "seconds_per_transfer_byte": 4.6e-7,
            "seconds_per_row": 6.1e-4,
        },
        "prefix_linear": {"transfer_bytes_per_row": 6700.0},
    }


def test_batch_size_is_the_largest_restart_safe_option() -> None:
    totals = {
        "input_rows": 1000,
        "raw_bytes": 9_000_000,
        "retained": {SCIENCE: {"canonical_bytes": 40_000}},
    }
    policy = bulk.batch_policy(timing_model(0.27), totals, 64_000_000)
    assert policy["chosen_files_per_batch"] == 32
    assert policy["options"]["32"]["within_restart_unit_limits"] is True
    assert policy["options"]["64"]["within_restart_unit_limits"] is False
    assert policy["options"]["32"]["estimated_rows"] == 2_560_000
    assert policy["options"]["32"]["batches_to_science_target"] == 25
    assert policy["options"]["32"]["estimated_science_tokens"] == 25_600_000
    assert policy["options"]["32"]["slice_transfer_bytes"] == 960_000_000
    assert bulk.batch_policy(timing_model(0.6), totals, 64_000_000)["chosen_files_per_batch"] == 16


def test_disk_budget_counts_raw_ledgers_documents_and_staging() -> None:
    _, _, totals = physical_inputs()
    budget = bulk.disk_budget(
        totals, {"target": 1000}, 10, 40_000_000, 50_000_000, 1000, 13_200_000
    )
    assert budget["bytes_per_input_row"] == {
        "raw_selected_records": 10_000.0,
        "rejection_ledgers": 2_000.0,
        "canonical_documents": 1_000.0,
    }
    case = budget["scenarios"]["target"]
    assert case["steady_state_bytes"] == 13_000_000
    assert case["peak_bytes"] == 13_200_000 and budget["slice_transient_bytes"] == 200_000
    assert case["fits_footprint_cap"] is True and case["fits_free_space_keeping_reserve"] is True
    assert case["steady_state_bytes_if_raw_also_published_to_store"] == 23_000_000
    assert budget["rows_at_footprint_cap"] == 1000
    tight = bulk.disk_budget(totals, {"target": 1000}, 10, 13_000_000, 50_000_000, 1000, 13_000_000)
    assert tight["scenarios"]["target"]["fits_footprint_cap"] is False
    assert tight["scenarios"]["target"]["fits_free_space_keeping_reserve"] is False
    assert "no deletion" in budget["raw_retention"]


# ----------------------------------------------------------------- planning


def test_layout_record_accounts_every_projected_leaf_of_a_real_footer(tmp_path: Path) -> None:
    taxonomy = {"free_decimal_correspondence": {"primary": {"code": "510"}}}
    rows = [
        {
            "text": f"authored row {index}",
            "id": index,
            "pid": "p",
            "metadata": {"url": "authored"},
            "quality_signals": {"fasttext": {"english": 0.9}},
            "eai_taxonomy": taxonomy,
            "unprojected": "never read",
        }
        for index in range(10)
    ]
    path = tmp_path / "authored.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path, row_group_size=4)
    footer = discover_layout_local(
        path, name="data/authored.parquet", max_parser_bytes=32 * MIB, max_decompression_ratio=15.0
    )
    fields = ("text", "eai_taxonomy", "quality_signals", "id", "pid", "metadata")
    record = bulk.layout_record(footer, fields, 15.0)
    assert record["rows"] == 10
    assert [(g["start_row"], g["rows"]) for g in record["groups"]] == [(0, 4), (4, 4), (8, 2)]
    assert all(g["projected_leaves"] == 6 and g["refusal"] is None for g in record["groups"])
    assert all(g["buffered_reads"] == 6 for g in record["groups"])
    everything = sum(column.compressed for column in footer.groups[0].columns)
    assert 0 < record["groups"][0]["projected_compressed_bytes"] < everything
    with pytest.raises(bulk.BulkError, match="projection refused"):
        bulk.layout_record(footer, ("text", "absent_field"), 15.0)


def test_slices_cover_every_row_exactly_once_within_bounded_limits() -> None:
    files = ["data/a.parquet", "data/b.parquet"]
    layouts = {files[0]: layout(files[0], [5, 5, 3]), files[1]: layout(files[1], [5, 4])}
    slices = bulk.plan_slices(files, layouts, LIMITS, 1)
    assert [entry["files"] for entry in slices] == [files, files, [files[0]]]
    assert [entry["rows"] for entry in slices] == [10, 9, 3]
    assert slices[1]["row_ranges"] == {files[0]: [5, 10], files[1]: [5, 9]}
    assert slices[2]["row_ranges"] == {files[0]: [10, 13]}
    seen: set[tuple[str, int]] = set()
    for entry in slices:
        for name, (start, stop) in entry["row_ranges"].items():
            rows = {(name, row) for row in range(start, stop)}
            assert not rows & seen
            seen |= rows
    assert len(seen) == 13 + 9
    limits = AcquisitionLimits.model_validate(slices[1]["limits"])
    assert limits.max_records == 9 and limits.max_scanned_records == 27
    assert limits.max_transferred_bytes == 3 * (2 * MIB) + MIB
    assert limits.max_output_disk_bytes == MIB and limits.max_temp_disk_bytes == 2 * MIB
    assert limits.max_requests == int(3 * 1.25 * (12 + 16))
    assert limits.overall_deadline_seconds == 3600 and limits.max_workers == 1
    assert slices[1]["window_scan_rows"] == 5
    assert bulk.plan_slices(files, layouts, LIMITS, 2)[0]["limits"]["max_workers"] == 2
    refused = dict(layouts)
    refused[files[1]] = layout(files[1], [5, 4], refusal="ratio bound exceeded")
    with pytest.raises(bulk.BulkError, match="refused by the window reader"):
        bulk.plan_slices(files, refused, LIMITS, 1)
    with pytest.raises(bulk.BulkError, match="exactly the distinct batch files"):
        bulk.plan_slices(files, {files[0]: layouts[files[0]]}, LIMITS, 1)
    with pytest.raises(bulk.BulkError, match="workers"):
        bulk.plan_slices(files, layouts, LIMITS, 17)


def test_batch_membership_is_a_pure_disjoint_inventory_prefix(world: World) -> None:
    campaign = world.campaign()
    order = [entry["file"] for entry in campaign.inventory["files"]]
    batches = [campaign.members(index) for index in range(4)]
    assert batches == [order[0:2], order[2:4], order[4:6], order[6:8]]
    assert batches == [world.campaign().members(index) for index in range(4)]
    assert len({name for batch in batches for name in batch}) == 8
    assert order != sorted(order)  # hash order, never provider or name order
    with pytest.raises(bulk.BulkError, match="outside the frozen inventory"):
        campaign.members(4)


def test_campaign_refuses_any_drift_in_its_frozen_inputs(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert world.run("show", "--batch", "0") == 0
    altered = copy.deepcopy(world.config)
    altered["batch"]["files"] = 4
    world.path.write_text(json.dumps(altered), encoding="utf-8")
    assert world.run("show", "--batch", "0") == 1
    world.path.write_text(json.dumps(world.config), encoding="utf-8")
    inventory_path = world.repo / "inventory.json"
    original = inventory_path.read_text(encoding="utf-8")
    inventory = json.loads(original)
    inventory["files"].reverse()
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    assert world.run("show", "--batch", "0") == 1
    inventory_path.write_text(original, encoding="utf-8")
    (world.repo / "quotas.yaml").write_bytes((world.repo / "quotas.yaml").read_bytes() + b"\n")
    assert world.run("show", "--batch", "0") == 1
    shutil.copy(REPO / world.tool.QUOTAS, world.repo / "quotas.yaml")
    assert world.run("show", "--batch", "0") == 0
    monkeypatch.setattr(world.tool.sealer, "code_identity", lambda: {"changed": "0" * 64})
    assert world.run("show", "--batch", "0") == 1


def test_campaign_needs_an_explicit_data_root(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XLM_DATA_ROOT", raising=False)
    assert world.tool.main(["--campaign", str(world.path), "show", "--batch", "0"]) == 1


def test_slice_plans_are_production_plans_that_need_authorization(world: World) -> None:
    record = world.prepare(0, [5, 5])
    directory = world.batch_dir(0)
    dry = load_acquisition_plan(directory / "s00.dry.plan.json")
    assert dry.plan_hash == record["slices"][0]["plan_hash"]
    assert dry.revision == selector.SOURCE_REVISION and dry.view_id == SCIENCE
    assert plan_requires_production_admission(dry) and dry.authorization is None
    assert dry.parquet_window is not None and dry.parquet_window.policy_version == 2
    assert dry.parquet_window.max_window_scan_rows == 5
    assert sorted(dry.projected_fields or []) == sorted(
        ["text", "eai_taxonomy", "quality_signals", "id", "pid", "metadata"]
    )
    with pytest.raises(AuthorizationRequiredError):
        validate_plan_authorization(dry, catalog_source_approved=True)
    assert not (directory / "s00.plan.json").exists()
    assert world.authorize(0, "f" * 64) == 1
    assert not (directory / "s00.plan.json").exists()
    assert world.authorize(0, record["authorization_digest"]) == 0
    assert world.authorize(0, record["authorization_digest"]) == 0
    plan = load_acquisition_plan(directory / "s00.plan.json")
    assert plan.plan_hash == dry.plan_hash
    validate_plan_authorization(plan, catalog_source_approved=True)
    with pytest.raises(AuthorizationRequiredError):  # still needs stored source admission
        validate_plan_authorization(plan, catalog_source_approved=False)


def test_planning_is_idempotent_and_refuses_a_different_replan(world: World) -> None:
    record = world.prepare(0, [5, 5])
    before = (world.batch_dir(0) / "batch.json").read_bytes()
    assert world.run("plan", "--batch", "0") == 0
    assert (world.batch_dir(0) / "batch.json").read_bytes() == before
    assert world.run("plan", "--batch", "0", "--workers", "2") == 1
    assert (world.batch_dir(0) / "batch.json").read_bytes() == before
    assert len(record["slices"]) == 2 and record["rows"] == 20
    assert record["membership_digest"] == world.tool.canonical.digest(record["files"])
    assert world.run("plan", "--batch", "1") == 1  # no footers read for batch 1 yet


# --------------------------------------------------------------- accounting


def test_campaign_runs_batch_by_batch_to_a_deterministic_stop(world: World) -> None:
    assert world.run("gate", "--batch", "0") == 0
    assert world.run("gate", "--batch", "1") == 1
    record = world.prepare(0, [5, 5])
    assert world.run("seal-slice", "--batch", "0", "--slice", "0") == 1
    assert world.authorize(0, record["authorization_digest"]) == 0
    world.fetch(0, 0)
    assert world.run("seal-slice", "--batch", "0", "--slice", "0") == 1  # not adapted yet
    assert world.adapt(0, 0) == [0, 0, 0]
    assert world.run("seal-slice", "--batch", "0", "--slice", "0") == 0
    ledger = world.root / "plans/ew-bulk/ledger/b0000.s00.json"
    sealed = ledger.read_bytes()
    assert world.run("seal-slice", "--batch", "0", "--slice", "0") == 0
    assert ledger.read_bytes() == sealed
    entry = json.loads(sealed)
    assert entry["rows"] == 10 and entry["malformed_rows"] == 0
    assert {view: entry["views"][view]["documents"] for view in entry["views"]} == {
        SCIENCE: 2,
        PRACTICAL: 2,
        PROSE: 2,
    }
    assert entry["receipt"]["files"][0]["locally_computed_sha256"] == entry["raw_sha256"]
    assert entry["receipt"]["plan_hash"] == entry["plan_hash"] == record["slices"][0]["plan_hash"]

    # A real calibration measurement may exist next to the campaign: never counted.
    foreign = world.root / "calib/essential-web-production/calibration"
    foreign.mkdir(parents=True)
    (foreign / "measurement.json").write_text(json.dumps({"input_rows": 16384}), encoding="utf-8")

    assert world.run("account") == 0
    state = json.loads((world.root / "plans/ew-bulk/cumulative.json").read_bytes())
    assert state["complete_batches"] == 0 and state["counted"]["rows"] == 0
    assert state["sealed_including_incomplete_batch"]["rows"] == 10
    assert world.run("gate", "--batch", "1") == 1

    world.fetch(0, 1)
    assert world.adapt(0, 1) == [0, 0, 0]
    assert world.run("seal-slice", "--batch", "0", "--slice", "1") == 0
    assert world.run("account") == 0
    first = json.loads((world.root / "plans/ew-bulk/cumulative.json").read_bytes())
    assert first["complete_batches"] == 1 and first["counted"]["rows"] == 20
    assert first["counted"]["views"][SCIENCE]["canonical_bytes"] == science_bytes_per_batch()
    decision = world.status()
    assert decision["target_reached"] is False
    assert decision["views"][SCIENCE]["status"] == "TOP_UP"
    assert (
        decision["views"][PRACTICAL]["status"]
        == decision["views"][PROSE]["status"]
        == ("SUFFICIENT")
    )
    assert world.run("account") == 0
    assert json.loads((world.root / "plans/ew-bulk/cumulative.json").read_bytes()) == first
    assert world.run("gate", "--batch", "0") == 4

    world.complete(1, [5, 5])
    assert world.run("account") == 3
    final = json.loads((world.root / "plans/ew-bulk/cumulative.json").read_bytes())
    assert final["complete_batches"] == 2 and final["counted"]["rows"] == 40
    assert final["counted"]["views"][SCIENCE]["canonical_bytes"] == 2 * science_bytes_per_batch()
    assert final["counted"]["views"][SCIENCE]["estimated_tokens"] == science_bytes_per_batch() / 2
    decision = world.status()
    assert decision["target_reached"] is True and decision["training_permitted"] is False
    assert decision["exact_token_sufficiency_known"] is False
    assert decision["c05_receipt_required_before_training"] is True
    report = json.loads((world.root / "plans/ew-bulk/sufficiency.json").read_bytes())["sources"]
    assert {view: report[view]["status"] for view in report} == {
        view: entry["status"] for view, entry in decision["views"].items()
    }

    # Stop is deterministic: the next batch does not run without an explicit top-up reason.
    assert world.run("gate", "--batch", "2") == 3
    assert not (world.batch_dir(2) / "top-up.json").exists()
    assert world.run("gate", "--batch", "2", "--top-up-reason", "exact count deficient") == 0
    assert json.loads((world.batch_dir(2) / "top-up.json").read_bytes())["reason"] == (
        "exact count deficient"
    )
    assert world.run("gate", "--batch", "3", "--top-up-reason", "x") == 1  # beyond the ceiling

    # No source row was acquired twice and no file belongs to two batches.
    entries = [
        json.loads(path.read_bytes())
        for path in sorted((world.root / "plans/ew-bulk/ledger").glob("*.json"))
    ]
    rows = [
        (name, row)
        for item in entries
        for name, (start, stop) in item["row_ranges"].items()
        for row in range(start, stop)
    ]
    assert len(rows) == len(set(rows)) == 40
    assert not set(entries[0]["files"]) & set(entries[2]["files"])


def test_a_forged_or_foreign_ledger_entry_stops_the_accounting(world: World) -> None:
    world.complete(0, [5, 5])
    assert world.run("account") == 0
    ledger = world.root / "plans/ew-bulk/ledger"
    entry = json.loads((ledger / "b0000.s00.json").read_bytes())
    shutil.copy(ledger / "b0000.s00.json", ledger / "b0000.s09.json")
    assert world.run("account") == 1  # the same slice sealed twice
    (ledger / "b0000.s09.json").unlink()
    entry["views"][SCIENCE]["canonical_bytes"] += 10**9
    (ledger / "b0000.s00.json").write_text(json.dumps(entry), encoding="utf-8")
    assert world.run("account") == 1  # altered entry no longer matches its digest
    assert world.run("gate", "--batch", "1") == 1


def test_source_drift_between_slices_of_one_file_is_refused(world: World) -> None:
    record = world.prepare(0, [5, 5])
    assert world.authorize(0, record["authorization_digest"]) == 0
    for index in (0, 1):
        world.fetch(0, index)
        assert world.adapt(0, index) == [0, 0, 0]
    assert world.run("seal-slice", "--batch", "0", "--slice", "0") == 0
    entry = world.tool.current_slices(world.campaign(), 0)[1]
    state = json.loads(entry["journal_path"].read_bytes())
    name = record["files"][0]
    state["source_validators"][name] = {"etag": '"changed"', "length": 999}
    entry["journal_path"].write_text(json.dumps(state), encoding="utf-8")
    assert world.run("seal-slice", "--batch", "0", "--slice", "1") == 1
    assert not (world.root / "plans/ew-bulk/ledger/b0000.s01.json").exists()


def test_a_tampered_raw_file_cannot_be_sealed(world: World) -> None:
    record = world.prepare(0, [5])
    assert world.authorize(0, record["authorization_digest"]) == 0
    world.fetch(0, 0)
    assert world.adapt(0, 0) == [0, 0, 0]
    raw = world.tool.current_slices(world.campaign(), 0)[0]["raw_dir"] / "selected_records.jsonl"
    raw.write_bytes(raw.read_bytes() + b"{}\n")
    assert world.run("seal-slice", "--batch", "0", "--slice", "0") == 1


@pytest.mark.parametrize("bad,passes", [(frozenset({1}), True), (frozenset({1, 2, 3}), False)])
def test_malformed_rows_follow_the_frozen_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: Any,
    bad: frozenset[int],
    passes: bool,
) -> None:
    world = make_world(tmp_path, monkeypatch, tool, {SCIENCE: 1, PRACTICAL: 1, PROSE: 1})
    record = world.prepare(0, [100])  # one slice: 2 files x 100 rows
    assert world.authorize(0, record["authorization_digest"]) == 0
    world.fetch(0, 0, bad)
    codes = world.adapt(0, 0)
    assert (codes == [0, 0, 0]) is passes
    assert world.run("seal-slice", "--batch", "0", "--slice", "0") == (0 if passes else 1)
    if passes:
        entry = json.loads((world.root / "plans/ew-bulk/ledger/b0000.s00.json").read_bytes())
        assert entry["malformed_rows"] == 1  # one isolated bad row in 200: quarantined
        assert world.run("account") == 3
        state = json.loads((world.root / "plans/ew-bulk/cumulative.json").read_bytes())
        assert state["counted"]["malformed_rate"] == 0.005
    else:
        assert not (world.root / "plans/ew-bulk/ledger").exists()


def test_renewal_is_only_for_an_unfinished_slice_past_its_deadline(world: World) -> None:
    record = world.prepare(0, [5, 5])
    assert world.authorize(0, record["authorization_digest"]) == 0
    assert world.run("renew", "--batch", "0", "--slice", "0") == 1  # never started
    world.fetch(0, 0)
    entry = world.tool.current_slices(world.campaign(), 0)[0]
    assert world.run("renew", "--batch", "0", "--slice", "0", "--now", "9e12") == 1  # completed
    state = json.loads(entry["journal_path"].read_bytes())
    state["status"] = "INTERRUPTED"
    state["accounting"]["deadline_at"] = 1000.0
    entry["journal_path"].write_text(json.dumps(state), encoding="utf-8")
    assert world.run("renew", "--batch", "0", "--slice", "0", "--now", "999") == 1
    assert world.run("renew", "--batch", "0", "--slice", "0", "--now", "1001") == 0
    renewed = world.tool.current_slices(world.campaign(), 0)[0]
    assert renewed["attempt"] == 2 and renewed["name"] == "s00-a2"
    assert renewed["plan_hash"] != entry["plan_hash"]
    assert renewed["row_ranges"] == entry["row_ranges"] and renewed["limits"] == entry["limits"]
    assert renewed["raw_dir"] != entry["raw_dir"] and entry["journal_path"].is_file()
    assert renewed["authorization_digest"] != record["authorization_digest"]
    assert world.run("state", "--batch", "0") == 0
    status = json.loads((world.batch_dir(0) / "state.json").read_bytes())["slices"]
    assert [(s["name"], s["authorized"], s["fetched"]) for s in status] == [
        ("s00-a2", False, False),
        ("s01", True, False),
    ]
    assert world.authorize(0, renewed["authorization_digest"]) == 0
    plan = load_acquisition_plan(world.batch_dir(0) / "s00-a2.plan.json")
    assert plan.attempt == 2 and plan.plan_hash == renewed["plan_hash"]
    assert (
        plan.compute_selection_hash()
        == load_acquisition_plan(world.batch_dir(0) / "s00.plan.json").compute_selection_hash()
    )


def slice_entry(batch: int, index: int, ranges: dict[str, list[int]]) -> dict[str, Any]:
    rows = sum(stop - start for start, stop in ranges.values())
    return {
        "batch": batch,
        "slice": index,
        "attempt": 1,
        "plan_hash": f"{batch}{index}" * 32,
        "row_ranges": ranges,
        "rows": rows,
        "malformed_rows": 0,
        "transferred_bytes": 100 * rows,
        "decompressed_bytes": 200 * rows,
        "requests": 7,
        "raw_bytes": 900 * rows,
        "rejections_file_bytes": 160 * rows,
        "documents_file_bytes": 80 * rows,
        "elapsed_seconds": 1.5,
        "views": {
            SCIENCE: {"documents": 1, "canonical_bytes": 40},
            PRACTICAL: {"documents": 2, "canonical_bytes": 100},
            PROSE: {"documents": 3, "canonical_bytes": 400},
        },
    }


def planned(entries: list[dict[str, Any]], files: list[str]) -> dict[str, Any]:
    keys = ("slice", "attempt", "plan_hash", "row_ranges", "rows")
    return {"files": files, "slices": [{key: entry[key] for key in keys} for entry in entries]}


def test_cumulative_yield_counts_complete_batches_and_refuses_duplicates() -> None:
    first = [slice_entry(0, 0, {"a": [0, 5], "b": [0, 5]}), slice_entry(0, 1, {"a": [5, 8]})]
    second = [slice_entry(1, 0, {"c": [0, 4]})]
    batches = {0: planned(first, ["a", "b"]), 1: planned(second, ["c"])}
    state = bulk.cumulative(batches, first + second)
    assert state == bulk.cumulative(copy.deepcopy(batches), list(reversed(first + second)))
    assert state["complete_batches"] == 2 and state["complete_files"] == 3
    assert state["counted"]["rows"] == 17 and state["counted"]["slices"] == 3
    assert state["counted"]["views"][PROSE] == {
        "documents": 9,
        "canonical_bytes": 1200,
        "estimated_tokens": 300.0,
    }
    assert state["counted"]["footprint_bytes"] == 17 * (900 + 160 + 80)
    partial = bulk.cumulative(batches, first[:1])
    assert partial["complete_batches"] == 0 and partial["counted"]["rows"] == 0
    assert partial["sealed_including_incomplete_batch"]["rows"] == 10
    with pytest.raises(bulk.BulkError, match="sealed twice"):
        bulk.cumulative(batches, first + first[:1])
    overlap = copy.deepcopy(first)
    overlap[1]["row_ranges"] = {"a": [4, 8]}
    overlap[1]["rows"] = 4
    with pytest.raises(bulk.BulkError, match="acquired twice"):
        bulk.cumulative({0: planned(overlap, ["a", "b"])}, overlap)
    foreign = copy.deepcopy(first)
    foreign[0]["plan_hash"] = "f" * 64
    with pytest.raises(bulk.BulkError, match="planned identity"):
        bulk.cumulative(batches, foreign)
    with pytest.raises(bulk.BulkError, match="contiguous prefix"):
        bulk.cumulative(batches, first[:1] + second)
    third = [slice_entry(1, 0, {"c": [0, 4]}), slice_entry(1, 1, {"c": [4, 6]})]
    with pytest.raises(bulk.BulkError, match="earlier batch completed"):
        bulk.cumulative({0: batches[0], 1: planned(third, ["c"])}, first[:1] + third[:1])
    with pytest.raises(bulk.BulkError, match="no planned batch"):
        bulk.cumulative({0: batches[0]}, first + second)
    with pytest.raises(bulk.BulkError, match="two batches"):
        bulk.cumulative({0: batches[0], 1: planned(second, ["a"])}, first)


def targets(science: int, practical: int, prose: int) -> dict[str, Any]:
    return {
        view: {"required_canonical_bytes": need}
        for view, need in ((SCIENCE, science), (PRACTICAL, practical), (PROSE, prose))
    }


def test_stop_needs_every_component_and_never_claims_exact_tokens() -> None:
    entries = [slice_entry(0, 0, {"a": [0, 5]})]
    state = bulk.cumulative({0: planned(entries, ["a"])}, entries)
    short = bulk.stop_decision(state, targets(41, 100, 400))
    assert short["target_reached"] is False
    assert short["views"][SCIENCE]["deficit_canonical_bytes"] == 1
    assert short["views"][PRACTICAL]["status"] == "SUFFICIENT"
    done = bulk.stop_decision(state, targets(40, 100, 400))
    assert done["target_reached"] is True and done == bulk.stop_decision(
        state, targets(40, 100, 400)
    )
    assert done["exact_token_sufficiency_known"] is False and done["training_permitted"] is False
    assert bulk.stop_decision(state, targets(40, 100, 401))["target_reached"] is False
    assert bulk.stop_decision(state, targets(40, 101, 400))["target_reached"] is False


def test_gate_enforces_order_ceiling_reserve_and_footprint(world: World) -> None:
    entries = [slice_entry(0, 0, {"a": [0, 5]})]
    state = bulk.cumulative({0: planned(entries, ["a"])}, entries)
    open_decision = bulk.stop_decision(state, targets(10**9, 1, 1))
    config = world.config
    run = bulk.gate(config, state, open_decision, 1, 10 * MIB, MIB)
    assert run == {"decision": "RUN", "reasons": []}
    assert bulk.gate(config, state, open_decision, 0, 10 * MIB, MIB)["decision"] == "COMPLETE"
    for batch, free, cap, reason in (
        (2, 10 * MIB, MIB, "cannot run before batch 1"),
        (1, 2 * MIB - 1, MIB, "below the reserve"),
        (1, 4096 * MIB, 1024 * MIB, "footprint cap"),
    ):
        refused = bulk.gate(config, state, open_decision, batch, free, cap)
        assert refused["decision"] == "REFUSE" and reason in " ".join(refused["reasons"])
    beyond = bulk.gate(config, state, open_decision, 3, 10 * MIB, MIB)
    assert "execution ceiling" in " ".join(beyond["reasons"])
    reached = bulk.stop_decision(state, targets(1, 1, 1))
    assert bulk.gate(config, state, reached, 1, 10 * MIB, MIB)["decision"] == "STOP_TARGET_REACHED"
    top_up = bulk.gate(config, state, reached, 1, 10 * MIB, MIB, "exact count deficient")
    assert top_up == {"decision": "RUN", "reasons": ["exact count deficient"]}
    assert world.run("gate", "--batch", "0", "--free-bytes", "1") == 1


def test_row_conservation_rejects_overlapping_or_lost_rows() -> None:
    def view(documents: int, other: int, rejected: int) -> dict[str, Any]:
        return {
            "documents": documents,
            "rejection_counts_by_code": {
                "EssentialWebSelectorOtherComponentError": other,
                "EssentialWebSelectorRejectedError": rejected,
                "EssentialWebMalformedRowError": 1,
            },
        }

    views = {SCIENCE: view(1, 5, 3), PRACTICAL: view(2, 4, 3), PROSE: view(3, 3, 3)}
    assert bulk.check_conservation(10, views) == 1
    with pytest.raises(bulk.BulkError, match="conserve"):
        bulk.check_conservation(11, views)
    views[PROSE] = view(3, 2, 4)
    with pytest.raises(bulk.BulkError, match="another view"):
        bulk.check_conservation(10, views)


def test_c05_obligation_stays_open_and_fail_closed(world: World) -> None:
    c05 = world.config["c05"]
    assert c05["obligation"] == essential_contamination_mitigation().model_dump()
    assert c05["obligation"]["version"] == "c04-benchmark-risk-v2"
    assert c05["obligation"]["mechanism"] == "xlm.data.exclusion"
    assert c05["status"] == "NOT RUN"
    assert c05["required_before"] == ["tokenizer_fit", "training", "official_benchmark_claims"]
    with pytest.raises(ReceiptValidationError, match="bound C05 receipt"):
        verify_benchmark_claim(None, None, {})
    broken = copy.deepcopy(world.config)
    broken["c05"]["status"] = "PASSED"
    with pytest.raises(bulk.BulkError, match="altered"):
        bulk.check_campaign(broken)


def test_committed_campaign_matches_its_frozen_inputs(tool: Any) -> None:
    """The real frozen campaign: read-only checks of committed evidence, no data root use."""
    evidence = REPO / tool.BULK_DIR
    config = json.loads((evidence / "bulk-campaign.json").read_bytes())
    bulk.check_campaign(config)
    campaign = tool.load_campaign(evidence / "bulk-campaign.json", Path("unused-data-root"))
    assert campaign.inventory["file_count"] == 23200
    assert config["inventory"]["digest"] == (
        "4bbd5517e760971405d9aed56bba5b84877a6325d10f5a10a05b50f5a8d763d8"
    )
    assert config["binding"]["revision"] == selector.SOURCE_REVISION
    assert config["binding"]["selector"] == selector.selector_identity()
    quotas = yaml.safe_load((REPO / tool.QUOTAS).read_bytes())
    for view, target in config["stop"]["targets"].items():
        assert target["final_exact_token_quota"] == quotas["final_quotas"][view]
        assert (
            target["first_pass_estimated_token_target"]
            == (quotas["first_pass_headroom_quotas"][view])
        )
        assert target["required_canonical_bytes"] == 4 * target["first_pass_estimated_token_target"]
    size = config["batch"]["files"]
    first, second = campaign.members(0), campaign.members(1)
    assert len(first) == len(second) == size and not set(first) & set(second)
    dry = json.loads((evidence / "dry-run.json").read_bytes())
    assert dry["batch_0"]["files"] == first and dry["batch_1"]["files"] == second
    assert dry["campaign_digest"] == config["digest"] and dry["campaign_rows_counted"] == 0
    seal = json.loads((REPO / tool.SEAL_DIR / "calibration-seal.json").read_bytes())
    assert config["calibration_seal_digest"] == seal["seal_digest"]
    capacity = json.loads((evidence / "science-capacity-model.json").read_bytes())
    science = seal["totals"]["retained"][SCIENCE]["canonical_bytes"]
    for case in capacity["cases"].values():
        need = -(-660_000_000 * case["assumed_bytes_per_token"] * 16384 // science)
        assert case["required_input_rows"] == need


def test_operator_driver_parses_and_requires_the_configured_root() -> None:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell unavailable")
    script = REPO / "scripts/operator_essential_web_bulk.ps1"
    parsed = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-Command",
            "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{script}', [ref]$null, [ref]$e); $e.Count",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert parsed.returncode == 0 and parsed.stdout.strip() == "0", parsed.stderr
    environment = {k: v for k, v in os.environ.items() if k not in ("XLM_DATA_ROOT", "XLM_HOME")}
    refused = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Batch",
            "0",
            "-Stage",
            "Show",
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert refused.returncode != 0 and "operator_storage.ps1 first" in refused.stderr
    text = script.read_text(encoding="utf-8")
    assert "X:\\XLM" not in text and "Remove-Item" not in text and "git push" not in text
    assert text.count("'data', 'fetch'") == 1 and text.count("'data', 'adapt'") == 1
