"""Authored metadata/loopback tests; no live source or benchmark material."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from test_source_run import World
from test_source_run import loopback_only as loopback_only
from test_source_run import served as served
from test_source_run import world as world
from xlm.data.acquisition import source_run
from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion import inputs
from xlm.data.exclusion.preparation import benchmark_requirements, main, preparation_audit


def test_source_inventory_reproduces_seal_without_reading_corpus(world: World) -> None:
    world.plan(100)
    world.run()
    seal = source_run.first_pass_seal(world.roots)
    source, files = inputs.source_inputs(world.roots.data_root, world.roots.scratch_root, "ultrax")
    assert source["seal_digest"] == seal["digest"]
    assert files[0]["documents"] == seal["units"][0]["documents"]
    assert files[0]["documents_sha256"] == seal["units"][0]["documents_sha256"]
    before = (world.roots.plans / "first-pass-seal.json").read_bytes()
    assert inputs.source_inputs(world.roots.data_root, world.roots.scratch_root, "ultrax") == (
        source,
        files,
    )
    assert (world.roots.plans / "first-pass-seal.json").read_bytes() == before


@pytest.mark.parametrize("drift", ["seal", "receipt", "size", "missing"])
def test_source_inventory_refuses_drift(world: World, drift: str) -> None:
    world.plan(100)
    world.run()
    seal = source_run.first_pass_seal(world.roots)
    unit = seal["units"][0]
    directory = world.roots.unit_dir(unit["sequence"], unit["rank"])
    if drift == "seal":
        path = world.roots.plans / "first-pass-seal.json"
        value = json.loads(path.read_bytes())
        value["source"]["revision"] = "changed"
        path.write_text(json.dumps(value), encoding="utf-8")
    elif drift == "receipt":
        path = directory / "receipt.json"
        value = json.loads(path.read_bytes())
        value["documents_sha256"] = "0" * 64
        value["digest"] = canonical.self_digest(value)
        path.write_text(json.dumps(value), encoding="utf-8")
    elif drift == "size":
        with (directory / "documents.jsonl").open("ab") as stream:
            stream.write(b"\n")
    else:
        (directory / "documents.jsonl").unlink()
    with pytest.raises((ValueError, RuntimeError, OSError)):
        inputs.source_inputs(world.roots.data_root, world.roots.scratch_root, "ultrax")


@pytest.mark.parametrize("raw", ['{"x": 1, "x": 2}', '{"x": NaN}', "[]"])
def test_strict_metadata(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "metadata.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        inputs.read_metadata(path, digested=False)


def test_metadata_limit_and_path_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "metadata.json"
    path.write_bytes(b" " * 101)
    monkeypatch.setattr(inputs, "MAX_METADATA_BYTES", 100)
    with pytest.raises(inputs.InputError, match="limit"):
        inputs.read_metadata(path)
    with pytest.raises(inputs.InputError, match="escapes"):
        inputs.contained(tmp_path, "../outside")


def authored_sources(key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    mapping = {
        "ew-fast": ["essential_science", "essential_practical", "essential_prose"],
        "ultrax": ["ultrax_ultrafineweb"],
        "finepdfs": ["finepdfs_en"],
        "synth": ["synth_en_explanations"],
        "wiki_rewrite": ["nemotron_wiki_rewrite"],
        "finewiki": ["finewiki_en"],
        "ifm_general": ["ifm_behaviors_general_planning"],
        "ifm_planning": ["ifm_behaviors_general_planning"],
        "common_pile": ["common_pile_prose"],
        "simple_stories": ["simple_stories"],
    }
    return {"source_key": key}, [
        {
            "component": c,
            "path": f"{key}/{c}",
            "documents": 1,
            "canonical_bytes": 10,
            "file_bytes": 20,
        }
        for c in mapping[key]
    ]


@pytest.fixture
def authored_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(inputs, "essential_inputs", lambda root: authored_sources("ew-fast"))
    monkeypatch.setattr(inputs, "source_inputs", lambda root, scratch, key: authored_sources(key))
    return inputs.build_input_manifest(tmp_path / "data", tmp_path / "scratch")


def test_global_component_coverage_and_no_optional_source(
    authored_manifest: dict[str, Any],
) -> None:
    assert len(authored_manifest["components"]) == 11
    assert len(authored_manifest["files"]) == 12
    assert authored_manifest["components"]["ifm_behaviors_general_planning"]["documents"] == 2
    assert "txt360" not in {s["source_key"] for s in authored_manifest["sources"]}
    assert authored_manifest["digest"] == canonical.self_digest(authored_manifest)
    assert authored_manifest["training_permitted"] is False


def test_omitted_component_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inputs, "essential_inputs", lambda root: authored_sources("ew-fast"))
    monkeypatch.setattr(
        inputs,
        "source_inputs",
        lambda root, scratch, key: (
            authored_sources(key) if key != "common_pile" else ({"source_key": key}, [])
        ),
    )
    with pytest.raises(inputs.InputError, match="eleven"):
        inputs.build_input_manifest(tmp_path / "data", tmp_path / "scratch")


def test_stale_manifest_is_fatal(authored_manifest: dict[str, Any], tmp_path: Path) -> None:
    authored_manifest["training_permitted"] = True
    with pytest.raises(inputs.InputError, match="digest"):
        inputs.verify_input_manifest(authored_manifest, tmp_path / "data", tmp_path / "scratch")


def test_no_executable_plan_without_benchmark_material(authored_manifest: dict[str, Any]) -> None:
    pins = Path(__file__).resolve().parents[1] / "manifests/eval_dataset_pins.yaml"
    audit = preparation_audit(authored_manifest, pins)
    assert audit["status"] == "BLOCKED"
    assert audit["executable"] is False and audit["plan_digest"] is None
    assert all(
        t["material_sha256"] is None for t in audit["benchmark_requirements"]["tasks"].values()
    )


@pytest.mark.parametrize("mutation", ["revision", "repository", "missing"])
def test_benchmark_pin_drift_refused(tmp_path: Path, mutation: str) -> None:
    source = Path(__file__).resolve().parents[1] / "manifests/eval_dataset_pins.yaml"
    value = json.loads(source.read_bytes())
    if mutation == "missing":
        del value["piqa"]
    else:
        value["piqa"][mutation] = "latest" if mutation == "revision" else "unreviewed/repo"
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        benchmark_requirements(path)


def test_cli_never_writes_to_source_store(tmp_path: Path) -> None:
    root = tmp_path / "data"
    assert (
        main(
            [
                "inventory",
                "--data-root",
                str(root),
                "--scratch-root",
                str(tmp_path / "scratch"),
                "--manifest",
                str(root / "manifest.json"),
            ]
        )
        == 1
    )
    assert not root.exists()
