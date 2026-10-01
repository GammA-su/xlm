"""Essential-Web first-pass pool seal: completeness, content binding, immutability.

The end-to-end tests run an authored fast campaign on the loopback endpoint to
its first-pass stop and seal it; they prove the seal logic, not live data.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

import test_essential_web_bulk as historical_tests
from test_essential_web_fast import (
    PRACTICAL,
    PROSE,
    SCIENCE,
    World,
    make_fast_world,
)
from test_essential_web_fast import loopback_only as loopback_only
from test_essential_web_fast import served as served
from xlm.data.evidence_v2 import canonical
from xlm.data.sources import essential_web_pool_seal as seal_lib

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "docs/implementation/evidence/ESSENTIAL-WEB-FIRST-PASS-SEAL/first-pass-seal.json"
VIEWS = (SCIENCE, PRACTICAL, PROSE)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, served: Any) -> World:  # noqa: F811
    required = {SCIENCE: 2 * historical_tests.science_bytes_per_batch() - 1, PRACTICAL: 1, PROSE: 1}
    return make_fast_world(tmp_path, monkeypatch, served, required)


@pytest.fixture
def sealer(world: World, monkeypatch: pytest.MonkeyPatch) -> Any:
    module = importlib.import_module("essential_web_pool_seal")
    admissions = {
        view: {"admission_decision": {"artifact": f"admission_{view}", "sha256": "a" * 64}}
        for view in VIEWS
    }
    monkeypatch.setattr(module, "admission_bindings", lambda campaign: admissions)
    return module


def run_first_pass(world: World) -> None:
    world.pass_benchmark()
    world.authorize(0)
    assert world.run("run", "--batch", "0", "--workers", "1") == 0
    world.authorize(1)
    assert world.run("run", "--batch", "1", "--workers", "1") == 3  # targets reached


def seal_main(world: World, sealer: Any, *argv: str) -> int:
    return int(sealer.main(["--campaign", str(world.path), "--data-root", str(world.root), *argv]))


def saved_seal(world: World) -> dict[str, Any]:
    path = world.root / "plans/ew-fast" / seal_lib.SEAL_FILENAME
    value: dict[str, Any] = json.loads(path.read_bytes())
    return value


def test_seal_binds_the_complete_first_pass_and_is_reproducible(
    world: World, sealer: Any, tmp_path: Path
) -> None:
    run_first_pass(world)
    store = sorted(p for p in world.root.rglob("*") if p.is_file())
    before = {p: p.read_bytes() for p in store}
    copy = tmp_path / "evidence" / "first-pass-seal.json"
    assert seal_main(world, sealer, "build", "--copy", str(copy)) == 0
    seal = saved_seal(world)
    seal_lib.check_seal(seal)
    assert json.loads(copy.read_bytes()) == seal
    # The only store change is the new seal; nothing sealed earlier moved or changed.
    after = sorted(p for p in world.root.rglob("*") if p.is_file())
    assert set(after) - set(store) == {world.root / "plans/ew-fast" / seal_lib.SEAL_FILENAME}
    assert all(p.read_bytes() == before[p] for p in store)
    receipts = world.receipts()
    assert [u["receipt_digest"] for u in seal["units"]] == [
        receipts[f"f{rank:05d}"]["digest"] for rank in range(4)
    ]
    assert seal["campaign"]["digest"] == world.config["digest"]
    assert [b["index"] for b in seal["batches"]] == [0, 1]
    assert seal["totals"]["files"] == 4 and seal["totals"]["rows"] == 40
    cumulative = json.loads((world.root / "plans/ew-fast/cumulative.json").read_bytes())
    for view in VIEWS:
        assert (
            seal["membership"][view]["canonical_bytes"]
            == (cumulative["counted"]["views"][view]["canonical_bytes"])
        )
        assert seal["sufficiency"][view]["status"] == "SUFFICIENT"
    assert seal["stage"] == "first_pass_canonical_availability"
    assert seal["exposure_policy"]["oversupply_preserved"] is True
    assert seal["exposure_policy"]["mixture_weights_changed"] is False
    assert seal["pending"]["training_permitted"] is False
    assert seal["pending"]["c05"].startswith("NOT RUN")
    assert seal["amendments"]["recovery"] is None  # the authored campaign has none
    # Deterministic: a rebuild is a no-op and verification reproduces it exactly.
    raw = (world.root / "plans/ew-fast" / seal_lib.SEAL_FILENAME).read_bytes()
    assert seal_main(world, sealer, "build") == 0
    assert (world.root / "plans/ew-fast" / seal_lib.SEAL_FILENAME).read_bytes() == raw
    assert seal_main(world, sealer, "verify") == 0
    assert seal_main(world, sealer, "verify", "--seal", str(copy)) == 0


def test_an_incomplete_or_insufficient_pass_is_never_sealed(
    world: World, sealer: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    world.pass_benchmark()
    world.authorize(0)
    assert world.run("run", "--batch", "0", "--workers", "1") == 0
    seal = world.root / "plans/ew-fast" / seal_lib.SEAL_FILENAME
    capsys.readouterr()
    assert seal_main(world, sealer, "build") == 1
    assert "first-pass target is not met" in capsys.readouterr().err
    world.prepare(1)
    assert seal_main(world, sealer, "build") == 1
    assert "planned but was never authorized" in capsys.readouterr().err
    world.authorize(1)
    assert seal_main(world, sealer, "build") == 1
    assert "not every planned batch is complete" in capsys.readouterr().err
    assert not seal.exists()


@pytest.mark.parametrize("damage", ["documents", "ledger", "raw", "stray", "sidecar"])
def test_changed_or_extra_bytes_refuse_build_and_verify(
    world: World, sealer: Any, damage: str
) -> None:
    run_first_pass(world)
    assert seal_main(world, sealer, "build") == 0
    unit = world.root / "canonical/ew-fast/b0001/f00003"
    receipt = world.receipts()["f00003"]
    raw = world.root / receipt["raw"]["path"]
    if damage == "documents":
        target = unit / SCIENCE / "documents.jsonl"
        data = bytearray(target.read_bytes())
        data[-2] ^= 0x01  # same size, different bytes
        target.write_bytes(bytes(data))
    elif damage == "ledger":
        target = unit / PROSE / "adaptation_rejections.jsonl.zst"
        target.write_bytes(target.read_bytes()[:-1] + b"\x00")
    elif damage == "raw":
        data = bytearray(raw.read_bytes())
        data[10] ^= 0x01
        raw.write_bytes(bytes(data))
    elif damage == "stray":
        (unit / PRACTICAL / "notes.txt").write_text("x", encoding="utf-8")
    else:
        sidecar = next(raw.parent.glob(raw.name + "*.json"))
        record = json.loads(sidecar.read_bytes())
        record["etag"] = '"changed"'
        sidecar.write_text(json.dumps(record), encoding="utf-8")
    assert seal_main(world, sealer, "verify") == 1
    assert seal_main(world, sealer, "verify", "--skip-content") == (
        1 if damage in ("stray", "sidecar") else 0
    )


def test_an_altered_saved_seal_is_refused(world: World, sealer: Any, tmp_path: Path) -> None:
    run_first_pass(world)
    assert seal_main(world, sealer, "build") == 0
    seal = saved_seal(world)
    forged = json.loads(json.dumps(seal))
    forged["sufficiency"][SCIENCE]["status"] = "TOP_UP"
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(forged), encoding="utf-8")
    assert seal_main(world, sealer, "verify", "--seal", str(path)) == 1
    redigested = {k: v for k, v in forged.items() if k != "digest"}
    redigested["digest"] = canonical.digest(redigested)
    path.write_text(json.dumps(redigested), encoding="utf-8")
    assert seal_main(world, sealer, "verify", "--seal", str(path)) == 1  # store disagrees
    # A different record at the seal path is never overwritten.
    target = world.root / "plans/ew-fast" / seal_lib.SEAL_FILENAME
    target.write_text(json.dumps(redigested), encoding="utf-8")
    assert seal_main(world, sealer, "build") == 1
    assert json.loads(target.read_bytes()) == redigested


def unit(rank: int, science: int = 10) -> dict[str, Any]:
    return {
        "inventory_rank": rank,
        "batch": rank // 2,
        "file": f"f{rank}.parquet",
        "digest": f"{rank:064d}",
        "raw": {"bytes": 100, "sha256": "b" * 64},
        "rows": 5,
        "malformed_rows": 0,
        "views": {
            view: {"documents": 1, "canonical_bytes": science, "documents_sha256": f"{rank:064x}"}
            for view in VIEWS
        },
    }


def test_membership_is_ordered_and_binds_each_document_file() -> None:
    units = [seal_lib.unit_entry(unit(rank)) for rank in range(3)]
    first = seal_lib.membership(units, SCIENCE)
    assert first == seal_lib.membership(units, SCIENCE)
    assert first["digest"] != seal_lib.membership(list(reversed(units)), SCIENCE)["digest"]
    changed = json.loads(json.dumps(units))
    changed[1]["views"][SCIENCE]["documents_sha256"] = "f" * 64
    assert seal_lib.membership(changed, SCIENCE)["digest"] != first["digest"]
    assert seal_lib.membership(changed, PROSE) == seal_lib.membership(units, PROSE)


def test_build_refuses_gaps_and_missing_admissions() -> None:
    config: dict[str, Any] = {"batch": {"files": 2}}
    batches = [{"index": 0}]
    with pytest.raises(seal_lib.SealError, match="contiguous inventory prefix"):
        seal_lib.build_seal(
            config=config,
            receipts=[unit(0), unit(2)],
            batches=batches,
            state={},
            amendments={},
            admissions={},
        )
    with pytest.raises(seal_lib.SealError, match="not every planned batch"):
        seal_lib.build_seal(
            config=config,
            receipts=[unit(0), unit(1)],
            batches=[{"index": 0}, {"index": 1}],
            state={},
            amendments={},
            admissions={},
        )


@pytest.mark.skipif(not COMMITTED.is_file(), reason="committed first-pass seal is absent")
def test_committed_first_pass_seal_is_intact_and_complete() -> None:
    seal = json.loads(COMMITTED.read_bytes())
    seal_lib.check_seal(seal)
    campaign = json.loads(
        (
            REPO / "docs/implementation/evidence/ESSENTIAL-WEB-FAST-TRANSPORT/campaign.json"
        ).read_bytes()
    )
    assert seal["campaign"]["digest"] == campaign["digest"]
    assert seal["source"]["revision"] == "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
    assert len(seal["batches"]) == 18 and seal["totals"]["files"] == 576
    assert seal["totals"]["rows"] == 47_979_123
    expected = {SCIENCE: 2_692_218_118, PRACTICAL: 6_962_804_542, PROSE: 20_071_278_364}
    for view, canonical_bytes in expected.items():
        assert seal["membership"][view]["canonical_bytes"] == canonical_bytes
        assert seal["sufficiency"][view]["status"] == "SUFFICIENT"
    assert seal["pending"]["training_permitted"] is False
    assert seal["exposure_policy"]["oversupply_preserved"] is True
