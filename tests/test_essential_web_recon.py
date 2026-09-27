"""Essential-Web selector reconnaissance helper (offline, authored fixtures only).

Rows are authored in the observed Essential shape (nested ``eai_taxonomy`` /
``quality_signals`` / ``metadata`` structs). Nothing here touches the network:
the tree lister is an in-memory fake and sampling uses local Parquet files.
"""

from __future__ import annotations

import importlib.util
import json
import random
import socket
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from xlm.data.acquisition.plan import ParquetWindowDecode
from xlm.data.acquisition.records import LOCATOR_FIELD
from xlm.data.acquisition.sampling import (
    SamplingRefusal,
    SamplingRequest,
    discover_layout_local,
    plan_sample_blocks,
)

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "essential_web_recon.py"
_SPEC = importlib.util.spec_from_file_location("essential_web_recon", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
recon = importlib.util.module_from_spec(_SPEC)
sys.modules["essential_web_recon"] = recon
_SPEC.loader.exec_module(recon)

REV = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"
CRAWLS = [f"crawl=CC-MAIN-{y}-{w:02d}" for y in range(2013, 2025) for w in (10, 22, 40)]
V2 = ParquetWindowDecode(
    policy_version=2,
    stream_buffer_bytes=64 * 1024,
    max_window_scan_rows=16_384,
    batch_rows=256,
    ratio_exempt_bytes=1024 * 1024,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _lister(order_seed: int = 0, files_per_crawl: int = 5) -> Any:
    rng = random.Random(order_seed)

    def listing(path: str) -> list[dict[str, Any]]:
        if path == "data":
            entries = [{"type": "directory", "path": f"data/{c}"} for c in CRAWLS]
            entries.append({"type": "file", "path": "data/README.md", "size": 1})
        else:
            entries = [
                {
                    "type": "file",
                    "path": f"{path}/{i:05d}.parquet",
                    "size": 1000 + i,
                    "oid": f"o{i}",
                }
                for i in range(files_per_crawl)
            ]
        rng.shuffle(entries)
        return entries

    return listing


def _manifest(**kw: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"revision": REV, "seed": 20260918, "strata": 8}
    values.update(kw)
    lister = values.pop("lister", _lister())
    return recon.build_manifest(lister, **values)


# ------------------------------------------------------------- selection


def test_temporal_stratified_selection_is_deterministic() -> None:
    first = recon.select_crawls(CRAWLS, seed=20260918, revision=REV, strata=6)
    assert first == recon.select_crawls(
        list(reversed(CRAWLS)), seed=20260918, revision=REV, strata=6
    )
    ordered = sorted(CRAWLS, key=recon.crawl_key)
    assert [c["stratum_size"] for c in first] == [6] * 6
    for item in first:
        index = ordered.index(item["crawl"])
        assert ordered.index(item["stratum_first"]) <= index <= ordered.index(item["stratum_last"])
    keys = [recon.crawl_key(c["crawl"]) for c in first]
    assert keys == sorted(keys)


def test_changed_seed_changes_selection() -> None:
    a = _manifest()
    b = _manifest(seed=20260919)
    assert a["files"] != b["files"]
    assert a["digest"] != b["digest"]


def test_pinned_revision_required() -> None:
    for bad in (None, "main", "CE4ECCC7E9604667B6D7F32CB6274B8B41F3113D", REV[:39]):
        with pytest.raises(recon.ReconError, match="pinned revision"):
            recon.require_revision(bad)
    with pytest.raises(recon.ReconError, match="pinned revision"):
        _manifest(revision="main")
    assert recon.main(["discover", "--revision", REV, "--out-dir", "unused"]) == 2


def test_selection_independent_of_provider_order() -> None:
    reference = _manifest(lister=_lister(order_seed=0))
    for order_seed in (1, 2, 3):
        assert _manifest(lister=_lister(order_seed=order_seed)) == reference
    # Not "first file of each crawl": the seeded key spreads over the listing.
    assert {Path(f).name for f in reference["files"]} != {"00000.parquet"}


def test_malformed_crawl_ids_refused() -> None:
    for bad in ("crawl=CC-MAIN-2013", "CC-MAIN-2013-20", "crawl=CC-MAIN-2013-99", "crawl=x"):
        with pytest.raises(recon.ReconError):
            recon.crawl_key(bad)

    def listing(path: str) -> list[dict[str, Any]]:
        return [{"type": "directory", "path": "data/crawl=bogus"}]

    with pytest.raises(recon.ReconError, match="malformed crawl"):
        _manifest(lister=listing)
    with pytest.raises(recon.ReconError, match="duplicate"):
        recon.select_crawls([CRAWLS[0], CRAWLS[0]], seed=1, revision=REV, strata=1)


def test_manifest_digest_reproducible_and_tamper_evident(tmp_path: Path) -> None:
    manifest = _manifest()
    assert manifest["digest"] == recon.manifest_digest(manifest)
    assert _manifest()["digest"] == manifest["digest"]
    assert manifest["revision"] == REV and manifest["seed"] == 20260918
    assert manifest["ignored_top_level_files"] == ["data/README.md"]
    assert "text" not in manifest["projection"]
    path = tmp_path / "discovery.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert recon.load_manifest(path)["digest"] == manifest["digest"]
    manifest["files"][0] = "data/other.parquet"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(recon.ReconError, match="digest"):
        recon.load_manifest(path)


# ------------------------------------------------------------- footer shape

_PRIMARY = pa.struct([("code", pa.string()), ("label", pa.string())])
_CLASSIFIER = pa.struct([("primary", _PRIMARY), ("secondary", _PRIMARY)])
_FDC_LABELS = pa.struct([("level_1", pa.string()), ("level_2", pa.string())])
_FDC = pa.struct([("primary", pa.struct([("code", pa.string()), ("labels", _FDC_LABELS)]))])


def _essential_table(rows: int, *, taxonomy_list: bool = False) -> pa.Table:
    taxonomy_fields = [
        ("free_decimal_correspondence", _FDC),
        ("document_type_v2", _CLASSIFIER),
        ("bloom_knowledge_domain", _CLASSIFIER),
    ]
    if taxonomy_list:
        taxonomy_fields.append(("tags", pa.list_(pa.string())))
    taxonomy_type = pa.struct(taxonomy_fields)
    quality_type = pa.struct(
        [("fasttext", pa.struct([("english", pa.float64()), ("dclm", pa.float64())]))]
    )
    meta_type = pa.struct([("url", pa.string()), ("snapshot_id", pa.string())])
    idx_type = pa.struct([("line_start_idx", pa.list_(pa.int64()))])
    taxonomy = []
    for i in range(rows):
        value: dict[str, Any] = {
            "free_decimal_correspondence": {
                "primary": {"code": f"{(i % 9) + 1}46.9", "labels": {"level_1": "L", "level_2": ""}}
            },
            "document_type_v2": {"primary": {"code": "1", "label": f"D{i % 3}"}, "secondary": None},
            "bloom_knowledge_domain": {
                "primary": {"code": "2", "label": "Conceptual"},
                "secondary": None,
            },
        }
        if taxonomy_list:
            value["tags"] = ["x"]
        taxonomy.append(value)
    return pa.table(
        {
            "id": pa.array(range(rows), pa.int64()),
            "text": pa.array([f"doc {i}" for i in range(rows)]),
            "eai_taxonomy": pa.array(taxonomy, taxonomy_type),
            "quality_signals": pa.array(
                [{"fasttext": {"english": 0.9, "dclm": 0.1}}] * rows, quality_type
            ),
            "pid": pa.array([f"p{i}" for i in range(rows)]),
            "metadata": pa.array(
                [{"url": "u", "snapshot_id": "crawl=CC-MAIN-2013-20"}] * rows, meta_type
            ),
            "line_start_n_end_idx": pa.array([{"line_start_idx": [0, 1]}] * rows, idx_type),
        }
    )


def _sample(path: Path, projection: tuple[str, ...]) -> Any:
    layouts = {
        path.name: discover_layout_local(
            path, name=path.name, max_parser_bytes=32 * 1024 * 1024, max_decompression_ratio=15.0
        )
    }
    request = SamplingRequest(
        source_id="essential_web",
        view_id="selector_recon",
        revision=REV,
        files=(path.name,),
        seed=20260918,
        mode="window",
        block_records=500,
        target_records=500,
        projected_fields=projection,
        window=V2,
    )
    return plan_sample_blocks(layouts, request)


def test_supported_nested_struct_projection(tmp_path: Path) -> None:
    path = tmp_path / "essential.parquet"
    pq.write_table(_essential_table(2000), path, row_group_size=1000)
    projection = recon.recon_projection()
    assert projection == ("eai_taxonomy", "quality_signals", "id", "pid", "metadata")
    result = _sample(path, projection)
    report = result.to_report()
    leaves = set(report["projected_physical_leaves"])
    assert "eai_taxonomy.document_type_v2.primary.label" in leaves
    assert "quality_signals.fasttext.english" in leaves
    assert not any(leaf.startswith(("text", "line_start")) for leaf in leaves)


def test_unsupported_list_in_projection_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "essential_list.parquet"
    pq.write_table(_essential_table(1000, taxonomy_list=True), path, row_group_size=1000)
    with pytest.raises(SamplingRefusal, match="eai_taxonomy"):
        _sample(path, recon.recon_projection())


# ------------------------------------------------------------- analysis


def _record(
    code: Any, doc: str | None, bloom: str, english: Any, crawl_file: str
) -> dict[str, Any]:
    taxonomy: dict[str, Any] = {
        "bloom_knowledge_domain": {"primary": {"code": "2", "label": bloom}},
        "extraction_artifacts": {"primary": {"code": "0", "label": "No Artifacts"}},
    }
    if code is not None:
        taxonomy["free_decimal_correspondence"] = {
            "primary": {"code": code, "labels": {"level_1": "X"}}
        }
    if doc is not None:
        taxonomy["document_type_v2"] = {"primary": {"code": "1", "label": doc}}
    return {
        "id": 1,
        "pid": "p",
        "metadata": {"snapshot_id": "s"},
        "eai_taxonomy": taxonomy,
        "quality_signals": {"fasttext": {"english": english}},
        LOCATOR_FIELD: {"repository": recon.REPOSITORY, "revision": REV, "source_file": crawl_file},
    }


def _rows(manifest: dict[str, Any], tmp_path: Path) -> list[tuple[str, dict[str, Any]]]:
    f0, f1 = manifest["files"][0], manifest["files"][1]
    records = [
        _record("530.1", "Academic", "Conceptual", 0.95, f0),
        _record("512", "Academic", "Procedural", 0.85, f0),
        _record("641.5", "Tutorial", "Procedural", 0.7, f1),
        _record("813", "Blog", "Conceptual", 0.6, f1),
        _record(None, None, "Factual", "bad", f1),
        _record("abc", "Blog", "Conceptual", None, f1),
    ]
    path = tmp_path / "selected_records.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return recon.read_records(path, manifest)


def test_analysis_distributions_crosstabs_accounting(tmp_path: Path) -> None:
    manifest = _manifest()
    rows = _rows(manifest, tmp_path)
    result = recon.analyze(rows, manifest)
    assert result["records"] == 6
    assert sorted(result["records_per_crawl"].values()) == [2, 4]
    fdc_path = "eai_taxonomy.free_decimal_correspondence.primary.code"
    assert result["path_accounting"][fdc_path] == {"__missing__": 1, "ok": 5}
    assert result["path_accounting"]["quality_signals.fasttext.english"] == {"__null__": 1, "ok": 5}
    digits = {d["value"]: d["count"] for d in result["fdc"]["digits_1"]}
    assert digits == {"5": 2, "6": 1, "8": 1, "__missing__": 1, "__malformed__": 1}
    doc = {d["value"]: d["count"] for d in result["classifiers"]["document_type_v2"]["primary"]}
    assert doc == {"Academic": 2, "Blog": 2, "Tutorial": 1, "__missing__": 1}
    tab = result["crosstabs"]["fdc_digit1_x_document_type_v2"]
    assert tab["5"] == {"Academic": 2} and tab["__missing__"] == {"__missing__": 1}
    english = result["scores"]["quality_signals.fasttext.english"]
    assert english["n"] == 4 and english["non_numeric_or_absent"] == 2
    assert english["percentiles"]["p0"] == 0.6 and english["percentiles"]["p50"] == 0.7
    assert english["percentiles"]["p100"] == 0.95
    assert result["quality_observations"]["fasttext_english_at_or_above"]["0.8"] == round(2 / 6, 6)


def test_percentiles_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    out = recon.percentiles(values)
    assert out["p1"] == 1.0 and out["p50"] == 50.0 and out["p99"] == 99.0 and out["p100"] == 100.0
    assert recon.percentiles([])["p50"] is None


def test_candidate_overlap_and_never_approved(tmp_path: Path) -> None:
    manifest = _manifest()
    result = recon.analyze(_rows(manifest, tmp_path), manifest)
    assert result["approved_selectors"] == []
    assert all(c["status"] == recon.CANDIDATE_STATUS for c in result["candidates"].values())
    assert result["candidates"]["science.fdc_5xx"]["count"] == 2
    assert result["candidates"]["practical.bloom_procedural"]["count"] == 2
    pair = next(
        o
        for o in result["candidate_overlaps"]
        if {o["a"], o["b"]} == {"science.fdc_5xx", "practical.bloom_procedural"}
    )
    assert pair["intersection"] == 1 and pair["jaccard"] == round(1 / 3, 6)
    assert result["group_overlap"]["pairwise_intersection"]["science&practical"] == 1
    with pytest.raises(recon.ReconError):
        recon.validate_candidates({"x": {"group": "science", "approved": True, "all": []}})
    custom = {
        "science.q": {
            "group": "science",
            "all": [{"path": "quality_signals.fasttext.english", "gte": 0.8}],
        }
    }
    assert (
        recon.analyze(_rows(manifest, tmp_path), manifest, custom)["candidates"]["science.q"][
            "count"
        ]
        == 2
    )


def test_analysis_needs_no_text_and_refuses_foreign_records(tmp_path: Path) -> None:
    manifest = _manifest()
    rows = _rows(manifest, tmp_path)
    assert all("text" not in record for _, record in rows)
    result = recon.analyze(rows, manifest)
    assert result["text_analyzed"] is False
    bad = _record("5", "A", "Factual", 0.9, "data/crawl=CC-MAIN-2099-01/x.parquet")
    path = tmp_path / "foreign.jsonl"
    path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(recon.ReconError, match="not in manifest"):
        recon.read_records(path, manifest)


def test_analyze_cli_deterministic_outputs(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest_path = tmp_path / "discovery.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _rows(manifest, tmp_path)
    outputs = []
    for run in ("a", "b"):
        args = [
            "analyze",
            "--manifest",
            str(manifest_path),
            "--records",
            str(tmp_path / "selected_records.jsonl"),
            "--output-json",
            str(tmp_path / f"{run}.json"),
            "--output-md",
            str(tmp_path / f"{run}.md"),
        ]
        assert recon.main(args) == 0
        outputs.append(
            ((tmp_path / f"{run}.json").read_bytes(), (tmp_path / f"{run}.md").read_bytes())
        )
    assert outputs[0] == outputs[1]
    assert b"NOT APPROVED" in outputs[0][1]


def test_cli_recon_sample_then_plan_metadata_only(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from xlm.cli.data_cmd import app as data_app

    rel = "data/crawl=CC-MAIN-2013-20/00001.parquet"
    local = tmp_path / "mirror"
    (local / rel).parent.mkdir(parents=True)
    pq.write_table(_essential_table(3000), local / rel, row_group_size=1500)
    catalog = tmp_path / "catalog.json"
    source = {
        "candidate_number": 1,
        "source_id": "essential_web",
        "provider": "https",
        "repository": "http://127.0.0.1:9/unused",
        "revision": REV,
    }
    catalog.write_text(json.dumps({"catalog_id": "authored", "sources": [source]}), "utf-8")
    fields = ",".join(recon.recon_projection())
    window = ["16384", str(4 * 1024 * 1024), "256", "2"]
    common = [
        "--source",
        "essential_web",
        "--view",
        recon.RECON_VIEW,
        "--catalog",
        str(catalog),
        "--files",
        rel,
        "--seed",
        "20260918",
    ]
    rows = tmp_path / "rows.json"
    runner = CliRunner()
    sampled = runner.invoke(
        data_app,
        [
            "sample-blocks",
            *common,
            "--revision",
            REV,
            "--local-dir",
            str(local),
            "--mode",
            "window",
            "--project-fields",
            fields,
            "--block-records",
            "512",
            "--target-records",
            "4096",
            "--max-records",
            "4096",
            "--window-max-scan-rows",
            window[0],
            "--window-buffer-bytes",
            window[1],
            "--window-batch-rows",
            window[2],
            "--window-policy-version",
            window[3],
            "--output",
            str(rows),
            "--report",
            str(tmp_path / "rows.evidence.json"),
        ],
    )
    assert sampled.exit_code == 0, sampled.output
    evidence = json.loads((tmp_path / "rows.evidence.json").read_text(encoding="utf-8"))
    assert evidence["projected_logical_fields"] == list(recon.recon_projection())
    assert not any(p.startswith("text") for p in evidence["projected_physical_leaves"])
    plan_path = tmp_path / "plan.json"
    planned = runner.invoke(
        data_app,
        [
            "plan",
            *common,
            "--mode",
            "selected_records",
            "--row-ranges",
            str(rows),
            "--project-fields",
            fields,
            "--parquet-window-scan-rows",
            window[0],
            "--parquet-window-buffer-bytes",
            window[1],
            "--parquet-window-batch-rows",
            window[2],
            "--parquet-window-policy-version",
            window[3],
            "--max-records",
            "4096",
            "--attempt",
            "1",
            "--pilot-approved",
            "--output",
            str(plan_path),
        ],
    )
    assert planned.exit_code == 0, planned.output
    saved = json.loads(plan_path.read_text(encoding="utf-8"))
    assert saved["parquet_window"]["policy_version"] == 2 and saved["revision"] == REV
    assert saved["projected_fields"] == list(recon.recon_projection())
