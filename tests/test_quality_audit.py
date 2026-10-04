"""Phase-A quality audit end to end on authored fixtures (no real corpus, no network).

Covers: content-free artifacts, PROPOSAL_ONLY candidates, worker-count byte identity,
chunking invariance, resume and changed-file/binding refusal, integrity refusals,
report re-derivation, the C05 kept overlay from an authored proof, and the
operator-only review materialization.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from quality_fixtures import CANARIES, PROSE, TEXTS, build_corpus, document, standard_layout
from xlm.data.evidence_v2 import canonical
from xlm.data.quality import runner, scan
from xlm.data.quality.cli import main
from xlm.data.quality.report import ARTIFACTS
from xlm.data.quality.review import ReviewError, read_review_rows
from xlm.data.quality.runner import (
    Limits,
    ReviewLimits,
    materialize_from_audit,
    run_audit,
    verify_report,
)
from xlm.data.quality.scan import RECEIPT_FILE, QualityError

REPO = Path(__file__).resolve().parents[1]


def limits(workers: int = 1, **changes: Any) -> Limits:
    values: dict[str, Any] = {
        "workers": workers,
        "max_rss_bytes": 8 * 1024**3,
        "free_reserve_bytes": 0,
        "max_output_bytes": 256 * 1024**2,
        "line_ceiling": 1024**2,
        "deadline_seconds": 600.0,
    }
    values.update(changes)
    return Limits(**values)


def audit(manifest: Path, output: Path, workers: int = 1, **kwargs: Any) -> dict[str, Any]:
    return run_audit(manifest, output, limits=limits(workers), progress_interval=None, **kwargs)


def artifacts(output: Path) -> dict[str, bytes]:
    return {name: (output / name).read_bytes() for name in ARTIFACTS}


def corpus_hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((root / "data").rglob("*.jsonl"))
    }


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    return build_corpus(tmp_path / "corpus", standard_layout())


# -- artifacts ---------------------------------------------------------------------------


def test_audit_is_complete_content_free_and_read_only(corpus: Path, tmp_path: Path) -> None:
    before = corpus_hashes(corpus.parent)
    output = tmp_path / "out"
    result = audit(corpus, output)
    assert result["complete"] and result["files_scanned"] == 4
    assert corpus_hashes(corpus.parent) == before
    receipt = json.loads((output / RECEIPT_FILE).read_bytes())
    assert receipt["status"] == "COMPLETE"
    assert receipt["corpus_modified"] is False and receipt["actions_executed"] == []
    assert receipt["result_digest"] == result["result_digest"]
    payloads = list(artifacts(output).values())
    payloads += [zlib.decompress(p.read_bytes()) for p in (output / "units").iterdir()]
    for payload in payloads:
        for canary in CANARIES:
            assert canary.encode("utf-8") not in payload, canary
    summary = json.loads((output / "quality-audit.json").read_bytes())
    total = summary["global"]["all"]
    manifest = json.loads(corpus.read_bytes())
    assert total["documents"] == sum(f["documents"] for f in manifest["files"])
    assert total["canonical_bytes"] == sum(f["canonical_bytes"] for f in manifest["files"])
    assert total["size_buckets"]["empty"]["docs"] == 1
    assert total["flags"]["markup_full_html"]["docs"] >= 1
    assert set(total["metrics"]["max_char_run"]["quantiles_bin_lo_hi"]) == {
        "p50",
        "p75",
        "p90",
        "p95",
        "p99",
        "p99.9",
    }
    assert total["metrics"]["max_char_run"]["max"] == 300


def test_candidate_policies_are_proposal_only(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    for band in ("conservative", "moderate", "aggressive"):
        raw = (output / f"candidate-policy-{band}.yaml").read_text(encoding="utf-8")
        assert raw.startswith("# PROPOSAL_ONLY - NOT EXECUTABLE")
        assert "&id" not in raw and "*id" not in raw
        policy = yaml.safe_load(raw)
        assert policy["status"] == "PROPOSAL_ONLY" and policy["executable"] is False
        assert policy["actions_executed_in_phase_a"] == []
        assert "stale" in policy["staleness"]
        for component in policy["components"].values():
            for rule in component["rules"]:
                assert rule["action"] is None
                assert set(rule["action_options"]) <= {"KEEP", "DROP", "TRANSFORM"}
                impact = rule["estimated_impact"]
                assert {"docs", "bytes", "docs_pct", "bytes_pct"} <= set(impact)


def test_review_manifest_holds_locators_roles_and_no_text(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    rows = read_review_rows(output / "review-manifest.jsonl")
    assert rows
    roles = {role for row in rows for role in row["roles"]}
    assert {"strong_positive", "control"} <= roles
    for row in rows:
        assert {"path", "row", "offset", "doc_id_sha256", "row_sha256", "detector", "value"} <= set(
            row
        )
        assert "doc_id" not in row
        assert "text" not in row and "excerpt" not in row
    keys = [(r["component"], r["detector"], r["coarse_bin"]) for r in rows]
    for key in set(keys):
        assert keys.count(key) <= 4


def test_language_evidence_distinguishes_inherited_from_row_level(
    corpus: Path, tmp_path: Path
) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    scopes = json.loads((output / "quality-language.json").read_bytes())["scopes"]
    pdf = scopes["component:finepdfs_en"]["all"]
    stories = scopes["component:simple_stories"]["all"]
    assert pdf["row_level_evidence_fraction"]["upstream_full_doc_lid"] == 1.0
    labels = pdf["labels"]["upstream_full_doc_lid"]["values"]
    assert set(labels) == {"eng_Latn"} and labels["eng_Latn"]["docs"] == 40
    assert stories["assessment"].startswith("inherited only")
    assert stories["language_confidence_constant_1_0"] is True


# -- determinism ---------------------------------------------------------------------------


def test_worker_counts_produce_byte_identical_artifacts(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scan, "CHUNK_BYTES", 4096)  # many chunks per file
    outputs = {}
    digests = set()
    for workers in (1, 2, 4, 8):
        output = tmp_path / f"w{workers}"
        digests.add(audit(corpus, output, workers=workers)["result_digest"])
        outputs[workers] = artifacts(output)
    assert len(digests) == 1
    assert outputs[1] == outputs[2] == outputs[4] == outputs[8]


def test_chunking_never_changes_measurements(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit(corpus, tmp_path / "large")
    monkeypatch.setattr(scan, "CHUNK_BYTES", 1024)
    audit(corpus, tmp_path / "small")
    for name in ("quality-by-component.json", "quality-histograms.json"):
        large = json.loads((tmp_path / "large" / name).read_bytes())
        small = json.loads((tmp_path / "small" / name).read_bytes())
        assert large["scopes"] == small["scopes"]
    assert (tmp_path / "large" / "review-manifest.jsonl").read_bytes() == (
        tmp_path / "small" / "review-manifest.jsonl"
    ).read_bytes()


# -- resume / refusal ------------------------------------------------------------------------


class Interrupt(Exception):
    pass


def interrupted(corpus: Path, output: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    real = scan.commit_unit

    def flaky(*args: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise Interrupt
        real(*args, **kwargs)

    monkeypatch.setattr(runner, "commit_unit", flaky)
    with pytest.raises(Interrupt):
        audit(corpus, output)
    monkeypatch.setattr(runner, "commit_unit", real)
    assert not (output / RECEIPT_FILE).exists()
    assert len(list((output / "units").glob("*.unit.zz"))) == 1
    for name in ARTIFACTS:
        assert not (output / name).exists()


def test_resume_reuses_committed_units_and_equals_a_clean_run(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resumed = tmp_path / "resumed"
    interrupted(corpus, resumed, monkeypatch)
    result = audit(corpus, resumed)
    assert (result["files_resumed"], result["files_scanned"]) == (1, 3)
    clean = tmp_path / "clean"
    audit(corpus, clean)
    assert artifacts(resumed) == artifacts(clean)


def test_resume_rehashes_committed_files_and_ignores_mtime(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    interrupted(corpus, output, monkeypatch)
    manifest = json.loads(corpus.read_bytes())
    first = corpus.parent / "data" / manifest["files"][0]["path"]
    stat = first.stat()
    # A touched mtime with identical bytes is not a change (mtime is never trusted) ...
    os.utime(first, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    resumed = tmp_path / "copy"
    shutil.copytree(output, resumed)
    assert audit(corpus, resumed)["files_resumed"] == 1
    # ... while the same size with one changed byte and a restored mtime refuses.
    raw = bytearray(first.read_bytes())
    raw[-3] ^= 1
    first.write_bytes(bytes(raw))
    os.utime(first, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(QualityError, match="frozen manifest"):
        audit(corpus, output)
    assert not (output / RECEIPT_FILE).exists()


def test_resume_refuses_a_changed_detector_policy(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    interrupted(corpus, output, monkeypatch)
    monkeypatch.setattr(scan, "policy_identity", lambda: "0" * 64)
    with pytest.raises(QualityError, match="different audit binding"):
        audit(corpus, output)


def test_completed_audit_refuses_rerun_and_report_rederives(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    first = audit(corpus, output)
    with pytest.raises(QualityError, match="already complete"):
        audit(corpus, output)
    verified = verify_report(corpus, output)
    assert verified == {
        "verified": True,
        "result_digest": first["result_digest"],
        "artifacts": len(ARTIFACTS),
        "sources_rehashed": True,
    }
    target = output / "quality-summary.md"
    target.write_bytes(target.read_bytes() + b"\nedited\n")
    with pytest.raises(QualityError, match="differs"):
        verify_report(corpus, output)


def test_incomplete_audit_is_never_a_report(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    interrupted(corpus, output, monkeypatch)
    with pytest.raises(QualityError, match="incomplete"):
        verify_report(corpus, output)


def _rewrite_manifest(manifest_path: Path, change: Any) -> None:
    manifest = json.loads(manifest_path.read_bytes())
    change(manifest)
    manifest.pop("digest")
    manifest["digest"] = canonical.self_digest(manifest)
    canonical.write_canonical_json(manifest_path, manifest)


def test_source_bytes_differing_from_manifest_refuse(corpus: Path, tmp_path: Path) -> None:
    manifest = json.loads(corpus.read_bytes())
    target = corpus.parent / "data" / manifest["files"][2]["path"]
    raw = bytearray(target.read_bytes())
    raw[-5] = ord("Z") if raw[-5] != ord("Z") else ord("Y")
    target.write_bytes(bytes(raw))
    output = tmp_path / "out"
    with pytest.raises(QualityError, match="SHA-256"):
        audit(corpus, output)
    assert not (output / RECEIPT_FILE).exists()
    assert not scan.unit_path(output, 2).exists()


def test_declared_byte_count_mismatch_refuses(tmp_path: Path) -> None:
    bad = document("bad-1", PROSE, 1)
    bad["utf8_byte_count"] += 1
    manifest = build_corpus(tmp_path / "c", {"finepdfs_en/eng_Latn/a": [bad]})
    _rewrite_manifest(
        manifest, lambda m: m["files"][0].update(canonical_bytes=bad["utf8_byte_count"])
    )
    with pytest.raises(QualityError, match="utf8_byte_count"):
        audit(manifest, tmp_path / "out")


def test_manifest_row_count_mismatch_refuses(corpus: Path, tmp_path: Path) -> None:
    _rewrite_manifest(
        corpus, lambda m: m["files"][3].update(documents=m["files"][3]["documents"] + 1)
    )
    with pytest.raises(QualityError, match="rows|row count"):
        audit(corpus, tmp_path / "out")


def test_tampered_manifest_digest_refuses(corpus: Path, tmp_path: Path) -> None:
    manifest = json.loads(corpus.read_bytes())
    manifest["files"][0]["documents"] += 1
    corpus.write_bytes(canonical.canonical_bytes(manifest))
    with pytest.raises(QualityError, match="input manifest refused"):
        audit(corpus, tmp_path / "out")


def test_row_above_the_document_ceiling_refuses(corpus: Path, tmp_path: Path) -> None:
    with pytest.raises(QualityError, match="document ceiling"):
        run_audit(corpus, tmp_path / "out", limits=limits(line_ceiling=512), progress_interval=None)


def test_malformed_row_refuses(tmp_path: Path) -> None:
    manifest = build_corpus(tmp_path / "c", {"finepdfs_en/eng_Latn/a": [document("x", PROSE, 1)]})
    path = tmp_path / "c" / "data" / json.loads(manifest.read_bytes())["files"][0]["path"]
    raw = path.read_bytes() + b"{not json\n"
    path.write_bytes(raw)
    _rewrite_manifest(
        manifest,
        lambda m: m["files"][0].update(
            documents=2,
            file_bytes=len(raw),
            documents_sha256=hashlib.sha256(raw).hexdigest(),
        ),
    )
    with pytest.raises(QualityError, match="malformed canonical JSONL row"):
        audit(manifest, tmp_path / "out")


def test_output_overlapping_corpus_files_refuses(corpus: Path) -> None:
    data = corpus.parent / "data"
    first = data / json.loads(corpus.read_bytes())["files"][0]["path"]
    with pytest.raises(QualityError, match="overlaps a corpus file directory"):
        audit(corpus, first.parent / "audit")
    with pytest.raises(QualityError, match="overlaps a corpus file directory"):
        audit(corpus, data / "canonical")
    with pytest.raises(QualityError, match="contains the corpus data root"):
        audit(corpus, corpus.parent)


def test_output_beside_corpus_files_under_the_data_root_is_allowed(
    corpus: Path,
) -> None:
    # Operator layout: G:/XLM is the data root and G:/XLM/quality holds the audit.
    output = corpus.parent / "data" / "quality" / "audit"
    assert audit(corpus, output)["complete"] is True


def test_output_byte_ceiling_refuses(corpus: Path, tmp_path: Path) -> None:
    with pytest.raises(QualityError, match="output byte ceiling"):
        run_audit(
            corpus,
            tmp_path / "out",
            limits=limits(max_output_bytes=20_000),
            progress_interval=None,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"workers": 3},
        {"max_rss_bytes": 17 * 1024**3},
        {"line_ceiling": 0},
        {"deadline_seconds": -1.0},
    ],
)
def test_limits_outside_their_bounds_refuse(corpus: Path, tmp_path: Path, change: Any) -> None:
    with pytest.raises(QualityError):
        run_audit(corpus, tmp_path / "out", limits=limits(**change), progress_interval=None)


# -- C05 overlay -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def c05_flow(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    from scripts.c05_authored_pilot import KEY
    from scripts.c05_synthetic_flow import KEY_ENV, decide_and_plan, prepare, run_c05

    environment = pytest.MonkeyPatch()
    environment.setenv(KEY_ENV, KEY)
    root = tmp_path_factory.mktemp("quality-c05") / "root"
    paths = prepare(root)
    result = run_c05(paths, decide_and_plan(paths))
    yield {
        "manifest": root / "manifest.json",
        "proof": Path(result["proof"]),
        "completion": result["completion"]["payload"],
        "key_env": KEY_ENV,
        "key": KEY,
    }
    environment.undo()


def test_overlay_splits_all_into_kept_and_removed(
    c05_flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(c05_flow["key_env"], c05_flow["key"])
    output = tmp_path / "out"
    audit(c05_flow["manifest"], output, proof=c05_flow["proof"], allow_authored_proof=True)
    summary = json.loads((output / "quality-audit.json").read_bytes())
    completion = c05_flow["completion"]
    counts = {k: v["documents"] for k, v in summary["global"].items()}
    assert counts == {
        "all": completion["documents"],
        "c05_kept": completion["kept"],
        "c05_removed": completion["excluded"] + completion["duplicates"],
    }
    assert summary["overlay"]["kept"] == completion["kept"]
    assert summary["overlay"]["mode"] == "authored"
    kept_flags = {r["kept"] for r in read_review_rows(output / "review-manifest.jsonl")}
    assert kept_flags <= {True, False}
    # Same corpus with 2 workers: identical bytes.
    again = tmp_path / "again"
    audit(
        c05_flow["manifest"], again, workers=2, proof=c05_flow["proof"], allow_authored_proof=True
    )
    assert artifacts(output) == artifacts(again)


def test_overlay_refuses_authored_proof_without_explicit_rehearsal_flag(
    c05_flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.quality.overlay import OverlayError

    monkeypatch.setenv(c05_flow["key_env"], c05_flow["key"])
    with pytest.raises(OverlayError, match="development evidence"):
        audit(c05_flow["manifest"], tmp_path / "out", proof=c05_flow["proof"])


def test_overlay_refuses_changed_membership(
    c05_flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.quality.overlay import OverlayError

    monkeypatch.setenv(c05_flow["key_env"], c05_flow["key"])
    spec = json.loads(c05_flow["proof"].read_bytes())
    membership = Path(spec["completion"]) / "membership.jsonl"
    original = membership.read_bytes()
    mutated = bytearray(original)
    mutated[10] = ord("0") if mutated[10] != ord("0") else ord("1")
    try:
        membership.write_bytes(bytes(mutated))
        with pytest.raises(OverlayError, match="membership changed"):
            audit(
                c05_flow["manifest"],
                tmp_path / "out",
                proof=c05_flow["proof"],
                allow_authored_proof=True,
            )
    finally:
        membership.write_bytes(original)


def test_overlay_refuses_a_different_manifest(
    c05_flow: dict[str, Any], corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xlm.data.quality.overlay import OverlayError

    monkeypatch.setenv(c05_flow["key_env"], c05_flow["key"])
    with pytest.raises(OverlayError, match="different input manifest"):
        audit(corpus, tmp_path / "out", proof=c05_flow["proof"], allow_authored_proof=True)


# -- operator review materialization -----------------------------------------------------------


def _complete(corpus: Path, tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    output = tmp_path / "out"
    audit(corpus, output)
    receipt = json.loads((output / RECEIPT_FILE).read_bytes())
    return output, receipt


def materialize(output: Path, destination: Path, **kwargs: Any) -> dict[str, Any]:
    limits = ReviewLimits(max_documents=kwargs.pop("max_documents", 10), max_chars=10_000)
    return materialize_from_audit(output, destination, limits=limits, **kwargs)


def test_materialize_review_writes_escaped_local_text(corpus: Path, tmp_path: Path) -> None:
    output, _ = _complete(corpus, tmp_path)
    destination = tmp_path / "review"
    result = materialize(
        output, destination, detectors=["html_tag_char_ratio"], roles=["strong_positive"]
    )
    assert result["materialized"] >= 1
    page = (destination / "review.html").read_text(encoding="utf-8")
    assert "<script>var" not in page and "&lt;script&gt;" in page
    records = [
        json.loads(x) for x in (destination / "review.jsonl").read_text("utf-8").splitlines()
    ]
    assert any("Buy now" in r["excerpt"] for r in records)
    assert all(isinstance(r["doc_id"], str) and r["doc_id"] for r in records)
    assert (destination / "README.txt").exists()


def _tamper_review(output: Path, change: Any) -> None:
    path = output / "review-manifest.jsonl"
    rows = read_review_rows(path)
    change(rows)
    path.write_bytes(b"".join(json.dumps(r, sort_keys=True).encode() + b"\n" for r in rows))


def test_materialize_review_destination_refusals(corpus: Path, tmp_path: Path) -> None:
    output, _ = _complete(corpus, tmp_path)
    with pytest.raises(ReviewError, match="inside the repository"):
        materialize(output, REPO / "quality-review-should-not-exist")
    with pytest.raises(ReviewError, match="overlaps"):
        materialize(output, corpus.parent / "data" / "review")
    with pytest.raises(ReviewError, match="overlaps"):
        materialize(output, output / "review")
    existing = tmp_path / "exists"
    existing.mkdir()
    with pytest.raises(ReviewError, match="already exists"):
        materialize(output, existing)
    assert not (REPO / "quality-review-should-not-exist").exists()


@pytest.mark.parametrize(
    "change",
    [
        lambda rows: rows[0].update(offset=rows[0]["offset"] + 1),
        lambda rows: rows[0].update(row=rows[0]["row"] + 1),
        lambda rows: rows[0].update(doc_id_sha256="0" * 64),
        lambda rows: rows[0].update(path="../outside.jsonl"),
        lambda rows: rows[0].update(note="smuggled free text"),
        lambda rows: rows.pop(),
    ],
)
def test_materialize_refuses_any_review_manifest_change(
    corpus: Path, tmp_path: Path, change: Any
) -> None:
    output, _ = _complete(corpus, tmp_path)
    _tamper_review(output, change)
    with pytest.raises((ReviewError, QualityError), match="differs"):
        materialize(output, tmp_path / "review")
    assert not (tmp_path / "review").exists()


def test_materialize_rehashes_selected_sources(corpus: Path, tmp_path: Path) -> None:
    output, _ = _complete(corpus, tmp_path)
    rows = read_review_rows(output / "review-manifest.jsonl")
    source = corpus.parent / "data" / rows[0]["path"]
    stat = source.stat()
    raw = bytearray(source.read_bytes())
    raw[-3] ^= 1  # same size, then restore the mtime: mtime is never trusted
    source.write_bytes(bytes(raw))
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(QualityError, match="frozen manifest"):
        materialize(output, tmp_path / "review", components=[rows[0]["component"]])
    assert not (tmp_path / "review").exists()


def test_materialize_refuses_unverifiable_receipts(corpus: Path, tmp_path: Path) -> None:
    output, receipt = _complete(corpus, tmp_path)
    path = output / RECEIPT_FILE
    for change in ("status", "digest", "envelope"):
        body = json.loads(path.read_bytes())
        if change == "status":
            body["status"] = "INCOMPLETE"
        elif change == "digest":
            body["result_digest"] = "0" * 64
        else:
            del body["envelope"]["workers"]
        path.write_bytes(canonical.canonical_bytes(body))
        with pytest.raises(QualityError, match="receipt invalid"):
            materialize(output, tmp_path / f"review-{change}")
    path.write_bytes(canonical.canonical_bytes(receipt))
    assert materialize(output, tmp_path / "review-ok", max_documents=2)["materialized"] == 2


def test_cli_audit_report_and_operator_confirmation(
    corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out"
    assert (
        main(
            [
                "audit",
                "--manifest",
                str(corpus),
                "--output",
                str(output),
                "--workers",
                "1",
                "--no-progress",
                "--free-reserve-gib",
                "0",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["complete"] is True
    assert main(["report", "--manifest", str(corpus), "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True
    destination = tmp_path / "review"
    args = ["materialize-review", "--output", str(output), "--destination", str(destination)]
    assert main(args) == 1
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["refused"] and "--operator-confirm" in refusal["error"]
    assert not destination.exists()
    assert main([*args, "--operator-confirm", "--roles", "control", "--max-documents", "3"]) == 0
    assert json.loads(capsys.readouterr().out)["materialized"] == 3


def test_module_entry_point_spawns_workers(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "xlm.data.quality",
            "audit",
            "--manifest",
            str(corpus),
            "--output",
            str(output),
            "--workers",
            "2",
            "--no-progress",
            "--free-reserve-gib",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert completed.returncode == 0, completed.stdout[-2000:]
    assert json.loads(completed.stdout)["complete"] is True
    assert TEXTS["html_page"][:20] not in completed.stderr
