"""Authored local material, never publisher benchmark payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xlm.data.evidence_v2 import canonical
from xlm.data.exclusion.artifacts import verify_benchmark
from xlm.data.exclusion.policy import C05Error, MatcherPolicy, ProductionPolicy, Resources
from xlm.data.exclusion.protected import MaterialSpec, build, inspect
from xlm.data.exclusion.runner import file_sha, index_patterns
from xlm.data.exclusion.streaming import StreamingMatcher

KEY = b"authored-preparation-key-not-an-operator-key"


def authored_material(root: Path) -> tuple[MaterialSpec, dict[str, Any]]:
    root.mkdir()
    rows = {
        "arc_easy": {
            "question": "Why do copper bridges expand during summer?",
            "choices": {"text": ["yes", "no"]},
        },
        "piqa": {"goal": "Fasten a loose wooden shelf", "sol1": "yes", "sol2": "no"},
        "blimp": {
            "sentence_good": "Those clever owls sing.",
            "sentence_bad": "Those clever owls sings.",
        },
        "hellaswag": {"ctx": "A sailor repairs the sail.", "endings": ["yes", "no"]},
    }
    files = []
    pins = {}
    for task, row in rows.items():
        path = root / (task + ".jsonl")
        path.write_bytes((canonical.canonical_bytes(row) + b"\n") * 2)
        pins[task] = {"repository": "authored/" + task, "revision": "1" * 40}
        files.append(
            {
                "task": task,
                **pins[task],
                "config": "authored-config",
                "split": "authored-split",
                "path": path.name,
                "sha256": file_sha(path),
                "bytes": path.stat().st_size,
                "items": 2,
            }
        )
    spec = MaterialSpec.model_validate(
        {
            "files": files,
            "publisher_inventory_sha256": "2" * 64,
            "all_published_configs_splits_reviewed": True,
            "isolation": {
                "mode": "authored",
                "operator_principal": "fixture-operator",
                "denied_agent_principal": "fixture-agent",
                "attestation_sha256": "3" * 64,
                "access_controls_verified": True,
            },
        }
    )
    return spec, pins


def prepare(spec: MaterialSpec, root: Path, destination: Path) -> dict[str, Any]:
    return build(
        spec,
        root,
        destination,
        policy=MatcherPolicy(),
        resources=Resources(free_bytes=0),
        issuer="fixture",
        key=KEY,
        code_commit="4" * 40,
        code_identity="5" * 64,
        dependency_sha256="6" * 64,
    )


def test_local_preparation_counts_provenance_and_receipt_is_content_free(tmp_path: Path) -> None:
    root = tmp_path / "material"
    spec, pins = authored_material(root)
    assert all(row["present"] and row["size_matches"] for row in inspect(spec, root)["files"])
    envelope = prepare(spec, root, tmp_path / "protected-authored")
    receipt = verify_benchmark(
        envelope, {"fixture": KEY}, pins, ProductionPolicy(), mode="authored"
    )
    assert (receipt.items, receipt.duplicate_items, receipt.items_without_patterns) == (8, 4, 0)
    serialized = json.dumps(envelope)
    assert "copper" not in serialized and "clever" not in serialized and "gold" not in serialized
    index = tmp_path / "protected-authored" / "index.jsonl"
    scan = StreamingMatcher(index_patterns(index, 1024 * 1024), max_patterns=1000, max_nodes=10000)
    assert any(len(refs) >= 2 for refs in scan.provenance.values())
    with pytest.raises(C05Error, match="mode"):
        verify_benchmark(envelope, {"fixture": KEY}, pins, ProductionPolicy())
    with pytest.raises(C05Error, match="write-once"):
        prepare(spec, root, tmp_path / "protected-authored")


@pytest.mark.parametrize("change", ["missing", "size", "hash", "coverage", "count"])
def test_preparation_refuses_incomplete_or_stale_material(tmp_path: Path, change: str) -> None:
    root = tmp_path / "material"
    spec, _ = authored_material(root)
    path = root / spec.files[0].path
    if change == "missing":
        path.unlink()
    elif change == "size":
        path.write_bytes(path.read_bytes() + b"\n")
    elif change == "hash":
        path.write_bytes(path.read_bytes().replace(b"copper", b"silver"))
    elif change == "coverage":
        spec = spec.model_copy(update={"all_published_configs_splits_reviewed": False})
    else:
        first = spec.files[0].model_copy(update={"items": 1})
        spec = spec.model_copy(update={"files": (first, *spec.files[1:])})
    with pytest.raises((C05Error, OSError)):
        prepare(spec, root, tmp_path / "output")
    assert not (tmp_path / "output" / "benchmark-preparation.receipt.json").exists()


def test_protected_mode_requires_other_principal(tmp_path: Path) -> None:
    import getpass

    root = tmp_path / "material"
    spec, _ = authored_material(root)
    isolation = spec.isolation.model_copy(
        update={
            "mode": "protected",
            "operator_principal": getpass.getuser(),
            "denied_agent_principal": getpass.getuser(),
        }
    )
    spec = spec.model_copy(update={"isolation": isolation})
    with pytest.raises(C05Error, match="separate operator"):
        prepare(spec, root, tmp_path / "output")


def test_authored_plan_binds_material_input_policy_limits_and_roots(tmp_path: Path) -> None:
    from test_c05_engine import document, setup_run
    from xlm.data.exclusion.artifacts import make_plan

    initial, _, _ = setup_run(tmp_path / "corpus", [document("a", "Unique authored diary.")])
    root = tmp_path / "material"
    spec, pins = authored_material(root)
    envelope = prepare(spec, root, tmp_path / "prepared")
    manifest = {
        "data_root": initial.data_root,
        "sources": [
            {
                "source_key": "authored",
                "seal_digest": "2" * 64,
                "source": {"source_id": "authored", "revision": "revision"},
            }
        ],
        "files": [f.model_dump(mode="json") for f in initial.files],
    }
    manifest["digest"] = canonical.self_digest(manifest)
    arguments: dict[str, Any] = {
        "sequence": 1,
        "scratch": tmp_path / "scratch",
        "output": tmp_path / "output",
        "code_commit": "4" * 40,
        "code_identity": "5" * 64,
        "dependency_sha256": "6" * 64,
    }
    plan = make_plan(
        manifest,
        envelope,
        {"fixture": KEY},
        pins,
        ProductionPolicy(),
        Resources(free_bytes=0),
        mode="authored",
        **arguments,
    )
    assert plan.benchmark_receipt_digest == envelope["digest"]
    assert plan.files[0].documents_sha256 == initial.files[0].documents_sha256
    assert plan.identity() != plan.model_copy(update={"sequence": 2}).identity()
    with pytest.raises(C05Error, match="overlap"):
        plan.model_copy(update={"output_root": plan.data_root}).identity()
    with pytest.raises(C05Error, match="acceptance incomplete"):
        make_plan(
            manifest,
            envelope,
            {"fixture": KEY},
            pins,
            ProductionPolicy(),
            Resources(free_bytes=0),
            **arguments,
        )


def test_local_parquet_and_missing_material_report(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    root = tmp_path / "material"
    spec, pins = authored_material(root)
    first = spec.files[0]
    original = root / first.path
    rows = [canonical.loads_bytes_strict(line) for line in original.read_bytes().splitlines()]
    parquet = root / "authored.parquet"
    pq.write_table(pa.Table.from_pylist(rows), parquet)
    replacement = first.model_copy(
        update={
            "path": parquet.name,
            "format": "parquet",
            "bytes": parquet.stat().st_size,
            "sha256": file_sha(parquet),
        }
    )
    spec = spec.model_copy(update={"files": (replacement, *spec.files[1:])})
    result = prepare(spec, root, tmp_path / "prepared")
    assert (
        verify_benchmark(result, {"fixture": KEY}, pins, ProductionPolicy(), mode="authored").items
        == 8
    )
    parquet.unlink()
    inspection = inspect(spec, root)
    assert not inspection["complete_local_sizes"]
    assert inspection["missing_material"] == [
        {
            "task": first.task,
            "repository": first.repository,
            "revision": first.revision,
            "config": first.config,
            "split": first.split,
            "path": parquet.name,
            "expected_bytes": replacement.bytes,
        }
    ]
