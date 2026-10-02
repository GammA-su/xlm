"""Whole-file .jsonl.gz production transport end to end on a loopback endpoint (offline).

The same runner, planner and receipts as Parquet sources, with a Common
Pile-shaped multi-component inventory: full run, verification and no redo,
interruption and exact byte resume, local-complete reuse, local processing
retry, corrupt payload fail-closed, byte/request/scratch/durable ceilings and
the format guards of the planner.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from mix01_source_fixtures import REVISION as FIXTURE_REVISION
from test_source_plan import models
from test_source_run import Served, loopback_only, served  # noqa: F401  (fixtures)
from xlm.data.acquisition import source_formats as formats
from xlm.data.acquisition import source_local
from xlm.data.acquisition import source_parquet as sp
from xlm.data.acquisition import source_plan as planner
from xlm.data.acquisition import source_run as runner
from xlm.data.acquisition import transport_policy as tp
from xlm.data.acquisition.source_dashboard import (
    FRESH_DOWNLOAD,
    LOCAL_COMPLETE_REUSE,
    LOCAL_PROCESSING_RETRY,
    RESUMABLE_PARTIAL,
    SEALED_SKIP,
)

REPOSITORY = "common-pile/comma_v0.1_training_dataset"
#: The loopback handler resolves every file to the authored fixture revision.
REVISION = FIXTURE_REVISION
PIN = {
    "source_id": "common_pile",
    "view_id": "common_pile_prose",
    "component_id": "common_pile_prose",
    "provider": "huggingface",
    "repository": REPOSITORY,
    "revision": REVISION,
    "adapter_id": "common_pile",
}
COMPONENTS = (
    "libretexts",
    "news",
    "oercommons",
    "pressbooks",
    "project_gutenberg",
    "public_domain_review",
)
ROWS_PER_FILE = 120


def component_rows(component: str, chunk: int) -> list[dict[str, Any]]:
    rows = []
    for r in range(ROWS_PER_FILE):
        if r % 40 == 13:
            rows.append({"text": "   "})  # recorded CommonPileEmptyTextError
            continue
        body = " ".join(
            hashlib.sha256(f"{component}|{chunk}|{r}|{w}".encode()).hexdigest()[:8]
            for w in range(30 + r % 17)
        )
        rows.append({"text": f"Authored {component} prose {chunk}.{r}: {body}"})
    return rows


def gz_file(rows: list[dict[str, Any]]) -> bytes:
    return gzip.compress(("\n".join(json.dumps(r) for r in rows) + "\n").encode(), mtime=0)


def inventory_of(names: list[str], sizes: dict[str, int] | None = None) -> dict[str, Any]:
    seed = 20260918
    entries = sorted(
        (
            {
                "file": name,
                "size_bytes": None if sizes is None else sizes[name],
                "order_key": hashlib.sha256(
                    f"{seed}|{REPOSITORY}|{REVISION}|{name}".encode()
                ).hexdigest(),
            }
            for name in names
        ),
        key=lambda e: (e["order_key"], e["file"]),
    )
    value: dict[str, Any] = {
        "inventory_version": 1,
        "source_id": "common_pile",
        "repository": REPOSITORY,
        "revision": REVISION,
        "seed": seed,
        "selection": "SHA-256(seed|repository|revision|file) ascending, filename tiebreak",
        "files": entries,
        "file_count": len(entries),
    }
    value["inventory_digest"] = planner.inventory_digest(value)
    return value


def admitted(plan: Any) -> None:
    assert plan.source_id == "common_pile" and plan.revision == REVISION


def local_models() -> dict[tp.TransportMode, tp.ThroughputModel]:
    return {mode: model for mode, model in models().items() if mode in planner.JSONL_GZ_MODES}


@dataclass
class World:
    roots: runner.Roots
    served: Served
    base: str
    inventory: dict[str, Any]
    layout: tp.SourceLayout
    policy: dict[str, Any]
    stream: io.StringIO

    def url(self, name: str) -> str:
        return f"{self.base}/resolve/{name}"

    def plan(self, files: int) -> dict[str, Any]:
        per_file = self.layout.rows_per_file * self.layout.canonical_bytes_per_row
        tokens = int((files - 0.5) * per_file / 1.15 / 4)
        quotas = {"first_pass_headroom_quotas": {"common_pile_prose": tokens}}
        estimate = {
            "assumptions": {"bytes_per_token_base": 4.0, "safety_margin": 1.15},
            "sources": {
                "common_pile_prose": {
                    "status": "ESTIMATED",
                    "first_pass_usable_token_target": tokens,
                    "required_canonical_bytes_base": tokens * 4.0,
                }
            },
        }
        record = planner.build_plan(
            source_key="common_pile",
            pin=PIN,
            requirement=planner.requirement_from(
                "common_pile_prose",
                quotas,
                estimate,
                quotas_sha256="1" * 64,
                estimate_sha256="2" * 64,
            ),
            inventory=self.inventory,
            inventory_sha256="3" * 64,
            layout=self.layout,
            calibration={"component_calibration": "0" * 64},
            policy=self.policy,
            admission={"probe_fingerprint": "f" * 64, "bridge_receipt_digest": "b" * 64},
            benchmark_reserved=0,
        )
        runner.store_plan(self.roots, record)
        runner.authorize(self.roots, int(record["sequence"]), record["digest"], "tester", admitted)
        return record

    def run(self, sequence: int = 1, **kwargs: Any) -> dict[str, Any]:
        options: dict[str, Any] = {"process_workers": 0, "download_workers": 2}
        options.update(kwargs)
        return runner.run_plan(
            self.roots, sequence, admitted=admitted, stream=self.stream, url_for=self.url, **options
        )


@pytest.fixture
def world(tmp_path: Path, served: tuple[Served, str], monkeypatch: pytest.MonkeyPatch) -> World:  # noqa: F811
    state, base = served
    monkeypatch.setattr(sp, "READ_BYTES", 256)
    monkeypatch.setattr(sp, "CHECKPOINT_BYTES", 1024)
    names = [f"{c}/{c}.chunk.{i:02d}.jsonl.gz" for c in COMPONENTS for i in range(1)]
    canonical = rows = 0
    for name in names:
        component, chunk = name.split("/", 1)[0], 0
        content = component_rows(component, chunk)
        state.files[name] = gz_file(content)
        canonical += sum(len(r["text"].encode()) for r in content if r["text"].strip())
        rows += len(content)
    biggest = max(len(p) for p in state.files.values())
    layout = tp.SourceLayout(
        source_id="common_pile",
        file_bytes=biggest,
        group_rows=ROWS_PER_FILE,
        group_bytes=biggest,
        projected_group_bytes=biggest,
        range_requests_per_group=1.0,
        metadata_requests_per_file=1,
        canonical_bytes_per_row=canonical / rows,
        range_record_bytes_per_row=canonical / rows,
        metadata_bytes_per_file=0,
        source_files=None,
        evidence={"component_calibration": "0" * 64},
        rows_per_file_measured=ROWS_PER_FILE,
    )
    report = tp.evaluate(
        layout, tp.Requirement("common_pile", 10_000, 1.15), tp.Ceilings(), local_models()
    )
    assert report["selected_mode"] in {m.value for m in planner.JSONL_GZ_MODES}
    return World(
        roots=runner.Roots(tmp_path / "data", tmp_path / "scratch", "common_pile"),
        served=state,
        base=base,
        inventory=inventory_of(names),
        layout=layout,
        policy=tp.freeze(report, basis="modeled", inputs={"model": "authored"}),
        stream=io.StringIO(),
    )


def test_full_run_seals_verifies_and_never_redoes(world: World) -> None:
    record = world.plan(3)
    files = [e["file"] for e in record["selection"]["files"]]
    assert len(files) == 3
    report = world.run()
    assert report["outcome"]["status"] == "completed"
    assert report["processing"]["rows"] == 3 * ROWS_PER_FILE
    assert report["processing"]["rejected"] == 3 * 3
    resume = runner.resume_state(world.roots, record)
    assert resume["sealed"] == 3
    for receipt in resume["receipts"]:
        assert receipt["raw"]["representation"] == "verified_source_jsonl_gz"
        assert receipt["raw"]["contract_id"] == "mix01-source-raw-artifact-jsonl-gz-v1"
        assert receipt["row_groups"] == 0
        assert receipt["rejection_counts_by_code"] == {"CommonPileEmptyTextError": 3}
        raw = world.roots.data_root / receipt["raw"]["path"]
        sidecar = json.loads(sp.identity_path(raw).read_text("utf-8"))
        assert sidecar["kind"] == "verified_source_jsonl_gz"
        assert formats.load_durable(raw, receipt["file"]) == sidecar
        assert receipt["identity"]["expected_sha256_source"] == "x-linked-etag"
    assert runner.verify_plan(world.roots, record, content=True)["units_verified"] == 3
    assert not list(world.roots.scratch().rglob("*.part"))
    hits = {name: world.served.hits(name) for name in files}
    again = world.run()
    assert again["transfer"]["files"] == 0
    assert {name: world.served.hits(name) for name in files} == hits
    restart = runner.classify(world.roots, record, runner.resume_state(world.roots, record), "p01")
    assert restart["counts"][SEALED_SKIP] == 3
    assert "Authored" not in world.stream.getvalue()


def test_units_are_deterministic_across_runs_and_workers(world: World, tmp_path: Path) -> None:
    record = world.plan(3)
    world.run(download_workers=3, process_workers=2)
    for receipt in runner.resume_state(world.roots, record)["receipts"]:
        again = source_local.adapt_source_file(
            world.roots.data_root / receipt["raw"]["path"],
            tmp_path / "again" / receipt["file"].replace("/", "_"),
            source_file=receipt["file"],
            source_id=PIN["source_id"],
            view_id=PIN["view_id"],
            adapter_id=PIN["adapter_id"],
            repository=PIN["repository"],
            revision=PIN["revision"],
            plan_id=record["acquisition_plan"]["plan_id"],
            plan_hash=record["acquisition_plan"]["plan_hash"],
            selection_hash=record["acquisition_plan"]["selection_hash"],
            identity=receipt["identity"],
            limits={k: record["limits"][k] for k in runner.PROCESS_LIMIT_KEYS},
        )
        assert again["documents_sha256"] == receipt["documents_sha256"]
        assert again["selected_records_sha256"] == receipt["selected_records"]["sha256"]


def test_interruption_preserves_partials_and_resumes_exactly(world: World) -> None:
    record = world.plan(3)
    victim = record["selection"]["files"][0]["file"]
    world.served.drops[victim] = [1300] * 8
    with pytest.raises(sp.SourceTransferError, match="attempts exhausted"):
        world.run(download_workers=1)
    restart = runner.classify(world.roots, record, runner.resume_state(world.roots, record), "p01")
    assert restart["counts"][RESUMABLE_PARTIAL] == 1
    assert restart["counts"][FRESH_DOWNLOAD] == 2
    assert world.roots.scratch("p01", "f00000.jsonl.gz.part").is_file()
    world.served.drops.clear()
    world.run(download_workers=1)
    ranges = [r for kind, name, r in world.served.requests if kind == "object" and name == victim]
    assert ranges[-1] is not None and ranges[-1].startswith("bytes=")
    assert runner.resume_state(world.roots, record)["sealed"] == 3


def test_complete_scratch_download_is_reused(world: World) -> None:
    record = world.plan(2)
    name = record["selection"]["files"][0]["file"]
    sp.download_source(
        world.url(name),
        world.roots.scratch("p01", "f00000.jsonl.gz.part"),
        world.roots.scratch("p01", "f00000.state.json"),
        name=name,
        limits=runner.transfer_limits(record),
        revision=REVISION,
    )
    restart = runner.classify(world.roots, record, runner.resume_state(world.roots, record), "p01")
    assert restart["units"][LOCAL_COMPLETE_REUSE] == ["f00000"]
    hits = world.served.hits(name)
    world.run()
    assert world.served.hits(name) == hits
    assert runner.resume_state(world.roots, record)["sealed"] == 2


def test_corrupt_payload_fails_closed_publishes_nothing_and_keeps_the_download(
    world: World,
) -> None:
    record = world.plan(2)
    victim = record["selection"]["files"][1]["file"]
    payload = bytearray(world.served.files[victim])
    payload[len(payload) // 2] ^= 0xFF  # the repository digest is of these bytes
    world.served.files[victim] = bytes(payload)
    with pytest.raises(source_local.SourceAdaptError, match="corrupt gzip"):
        world.run(download_workers=1)
    resume = runner.resume_state(world.roots, record)
    assert [r["file"] for r in resume["receipts"]] == [record["selection"]["files"][0]["file"]]
    restart = runner.classify(world.roots, record, resume, "p01")
    assert restart["units"][LOCAL_PROCESSING_RETRY] == ["f00001"]
    hits = world.served.hits(victim)
    with pytest.raises(source_local.SourceAdaptError):
        world.run(download_workers=1, offline=True)
    assert world.served.hits(victim) == hits  # retried locally, never re-downloaded


def test_transfer_ceilings_refuse(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    record = world.plan(2)
    victim = record["selection"]["files"][0]["file"]
    # A remote file larger than the plan's per-file bound is refused before its body.
    stored = gzip.compress(
        random.Random(0).randbytes(int(record["limits"]["max_file_bytes"])),
        compresslevel=0,
        mtime=0,
    )
    world.served.files[victim] = world.served.files[victim] + stored
    with pytest.raises(sp.SourceTransferError, match="outside the per-file bound"):
        world.run(download_workers=1)
    assert runner.resume_state(world.roots, record)["sealed"] == 0


def test_request_ceiling_refuses(world: World) -> None:
    record = world.plan(1)
    victim = record["selection"]["files"][0]["file"]
    world.served.drops[victim] = [700] * 100
    with pytest.raises(sp.SourceTransferError):
        world.run(download_workers=1)
    hits = world.served.hits(victim)
    assert hits <= int(record["limits"]["max_requests_per_file"])
    assert runner.resume_state(world.roots, record)["sealed"] == 0


def test_durable_and_scratch_caps_refuse_before_transfer(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.plan(2)
    monkeypatch.setattr(runner, "free_bytes", lambda path: 10)
    with pytest.raises(runner.RunError, match="durable volume"):
        world.run()
    assert world.served.requests == []


def test_planner_refuses_mixed_formats_and_non_whole_file_modes(world: World) -> None:
    mixed = inventory_of(["news/news.chunk.00.jsonl.gz", "news/news.chunk.01.parquet"])
    with pytest.raises(planner.PlanError, match="mixes file formats"):
        planner.check_format_modes(
            [e["file"] for e in mixed["files"]], tp.TransportMode.WHOLE_FILE_LOCAL, PIN
        )
    for mode in (tp.TransportMode.ROW_GROUP_LOCAL, tp.TransportMode.RANGE_SELECTED):
        with pytest.raises(planner.PlanError, match="cannot take transport mode"):
            planner.check_format_modes(["news/news.chunk.00.jsonl.gz"], mode, PIN)
    with pytest.raises(planner.PlanError, match="neither .parquet nor .jsonl.gz"):
        planner.check_format_modes(
            ["news/news.chunk.00.jsonl"], tp.TransportMode.WHOLE_FILE_LOCAL, PIN
        )


def test_reservation_containment_ignores_the_windows_extended_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xlm.data.acquisition import source_reservations

    answers = {
        "plain": "C:\\scratch\\common_pile\\p01\\f00001.state.json.tmp",
        "extended": "\\\\?\\C:\\scratch\\common_pile\\p01\\f00001.state.json.tmp",
        "unc": "\\\\?\\UNC\\server\\share\\scratch\\x.part",
    }
    for kind, text in answers.items():
        monkeypatch.setattr(Path, "resolve", lambda self, strict=False, value=text: Path(value))
        result = str(source_reservations.plain_resolve(Path("ignored")))
        assert not result.startswith("\\\\?\\"), kind
        assert result.endswith("x.part" if kind == "unc" else "f00001.state.json.tmp")
    assert result.startswith("\\\\server\\share")


def test_sufficiency_top_up_stays_in_the_frozen_inventory(world: World) -> None:
    first = world.plan(2)
    world.run()
    status = runner.sufficiency(world.roots)
    assert status["status"] in ("TOP_UP", "SUFFICIENT")
    planned = {e["file"] for e in first["selection"]["files"]}
    assert planned <= {e["file"] for e in world.inventory["files"]}
