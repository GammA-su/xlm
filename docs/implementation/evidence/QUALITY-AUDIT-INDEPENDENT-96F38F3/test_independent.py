"""Independent acceptance probes. Authored data only; no C05 execution/network."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "tests"))
from quality_fixtures import build_corpus, document, standard_layout
from test_quality_audit import audit, limits, interrupted, artifacts, _rewrite_manifest
from xlm.data.evidence_v2 import canonical
from xlm.data.quality import runner, scan, report, detectors
from xlm.data.quality.cli import main
from xlm.data.quality.review import read_review_rows
from xlm.data.quality.policy import METRIC_INDEX, METRICS, FLAGS


@pytest.fixture
def corpus(tmp_path):
    return build_corpus(tmp_path / "corpus", standard_layout())


def source(manifest, index=0):
    body = json.loads(manifest.read_bytes())
    return Path(body["data_root"]) / body["files"][index]["path"]


def flip(path):
    stat = path.stat()
    raw = path.read_bytes()
    # Preserve valid JSON, canonical byte length, and doc_id.
    rows = raw.splitlines(keepends=True)
    row = json.loads(rows[0])
    text = row["text"]
    if text:
        changed = ("Z" if text[0] != "Z" else "Y") + text[1:]
        row["text"] = changed
        rows[0] = canonical.canonical_bytes(row) + b"\n"
    else:
        # An irrelevant metadata byte still changes frozen file identity.
        rows[0] = rows[0].replace(b"train", b"traim", 1)
    replacement = b"".join(rows)
    assert replacement != raw and len(replacement) == len(raw)
    path.write_bytes(replacement)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_mtime_ns == stat.st_mtime_ns


def test_resume_rehashes_current_source(corpus, tmp_path, monkeypatch):
    out = tmp_path / "out"
    interrupted(corpus, out, monkeypatch)
    flip(source(corpus))
    with pytest.raises(scan.QualityError):
        audit(corpus, out)


def test_report_refuses_same_stat_mutation(corpus, tmp_path):
    out = tmp_path / "out"
    audit(corpus, out)
    flip(source(corpus))
    with pytest.raises(scan.QualityError):
        runner.verify_report(corpus, out)


def test_source_mutation_after_hash_before_commit(corpus, tmp_path, monkeypatch):
    real = runner.process_chunk
    def change(task):
        result = real(task)
        if task.ordinal == 0 and task.last:
            flip(source(corpus))
        return result
    monkeypatch.setattr(runner, "process_chunk", change)
    out = tmp_path / "out"
    with pytest.raises(scan.QualityError):
        audit(corpus, out)
    assert not scan.unit_path(out, 0).exists()


@pytest.mark.parametrize("mutation", ["truncate", "extend", "insert", "delete", "same_size"])
def test_fresh_scan_mutations_refuse(corpus, tmp_path, mutation):
    path = source(corpus)
    raw = path.read_bytes()
    if mutation == "same_size":
        flip(path)
    else:
        line = raw.splitlines(keepends=True)[0]
        changed = {"truncate": raw[:-1], "extend": raw+b" ", "insert": line+raw,
                   "delete": raw[len(line):]}[mutation]
        path.write_bytes(changed)
    out = tmp_path / "out"
    with pytest.raises(scan.QualityError):
        audit(corpus, out)
    assert not scan.unit_path(out, 0).exists()
    assert not (out / scan.RECEIPT_FILE).exists()


@pytest.mark.parametrize("stage", ["mid_file", "between_files", "aggregation", "receipt"])
def test_interruptions_are_incomplete(corpus, tmp_path, monkeypatch, stage):
    class Stop(Exception):
        pass
    out = tmp_path / "out"
    if stage == "between_files":
        interrupted(corpus, out, monkeypatch)
    else:
        def stop(*args, **kwargs):
            raise Stop()
        if stage == "mid_file":
            monkeypatch.setattr(runner, "process_chunk", stop)
        elif stage == "aggregation":
            monkeypatch.setattr(runner, "build_artifacts", stop)
        else:
            real = canonical.write_atomic
            def receipt_stop(path, payload):
                if path.name == scan.RECEIPT_FILE:
                    path.with_suffix(".json.tmp").write_bytes(b"partial")
                    raise Stop()
                return real(path, payload)
            monkeypatch.setattr(canonical, "write_atomic", receipt_stop)
        with pytest.raises(Stop):
            audit(corpus, out)
    assert not (out / scan.RECEIPT_FILE).exists()
    with pytest.raises(scan.QualityError, match="incomplete"):
        runner.verify_report(corpus, out)


@pytest.mark.parametrize("change", ["code", "policy", "manifest", "workers", "output", "rss", "deadline"])
def test_resume_binding_changes(corpus, tmp_path, monkeypatch, change):
    out = tmp_path / "out"
    interrupted(corpus, out, monkeypatch)
    changes = {}
    if change == "code":
        ident = runner.implementation()
        monkeypatch.setattr(runner, "implementation", lambda: {**ident, "code_identity": "a"*64})
    elif change == "policy":
        monkeypatch.setattr(scan, "policy_identity", lambda: "b"*64)
    elif change == "manifest":
        _rewrite_manifest(corpus, lambda m: m.update(audit_note="changed"))
    elif change == "workers":
        changes["workers"] = 2
    elif change == "output":
        changes["max_output_bytes"] = 128*1024**2
    elif change == "rss":
        changes["max_rss_bytes"] = 7*1024**3
    else:
        changes["deadline_seconds"] = 500
    if change in {"code", "policy", "manifest"}:
        with pytest.raises(scan.QualityError, match="binding"):
            runner.run_audit(corpus, out, limits=limits(**changes), progress_interval=None)
    else:
        result = runner.run_audit(corpus, out, limits=limits(**changes), progress_interval=None)
        assert result["files_resumed"] == 1


def test_deadline_covers_aggregation(corpus, tmp_path, monkeypatch):
    real = runner.build_artifacts
    def slow(*args, **kwargs):
        time.sleep(1.5)
        return real(*args, **kwargs)
    monkeypatch.setattr(runner, "build_artifacts", slow)
    out = tmp_path / "out"
    with pytest.raises(scan.QualityError):
        runner.run_audit(corpus, out, limits=limits(deadline_seconds=1), progress_interval=None)
    assert not (out / scan.RECEIPT_FILE).exists()


def test_one_worker_deadline_checked_after_last_task(tmp_path, monkeypatch):
    corpus = build_corpus(tmp_path / "c", {"a/b/c": [document("x", "tiny prose", 1)]})
    real = runner.process_chunk
    def slow(task):
        time.sleep(0.8)
        return real(task)
    monkeypatch.setattr(runner, "process_chunk", slow)
    with pytest.raises(scan.QualityError):
        runner.run_audit(corpus, tmp_path / "out", limits=limits(deadline_seconds=0.4), progress_interval=None)


def test_binding_write_respects_output_cap(corpus, tmp_path):
    out = tmp_path / "out"
    with pytest.raises(scan.QualityError):
        runner.run_audit(corpus, out, limits=limits(max_output_bytes=1), progress_interval=None)
    assert sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) <= 1


def test_insufficient_space_refuses(corpus, tmp_path):
    with pytest.raises(scan.QualityError, match="free space"):
        runner.run_audit(corpus, tmp_path / "out", limits=limits(free_reserve_bytes=10**18), progress_interval=None)


def test_input_manifest_cannot_be_overwritten(corpus, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    moved = out / "quality-audit.json"
    moved.write_bytes(corpus.read_bytes())
    before = moved.read_bytes()
    try:
        audit(moved, out)
    except scan.QualityError:
        pass
    assert moved.read_bytes() == before


def junction(link, target):
    result = subprocess.run(["powershell", "-NoProfile", "-Command",
        f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' | Out-Null"],
        capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_output_root_junction_refuses(corpus, tmp_path):
    alias = tmp_path / "alias"
    junction(alias, source(corpus).parent)
    with pytest.raises(scan.QualityError, match="overlap"):
        audit(corpus, alias / "nested")


def test_nested_units_junction_cannot_delete_source(corpus, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    sentinel = source(corpus).parent / "retained.unit.zz.tmp"
    sentinel.write_bytes(b"AUTHORED RETAINED SOURCE MATERIAL")
    junction(out / "units", source(corpus).parent)
    try:
        audit(corpus, out)
    except scan.QualityError:
        pass
    assert sentinel.exists(), "cleanup followed units junction into source directory"


def test_materialize_checks_review_manifest_hash(corpus, tmp_path):
    out = tmp_path / "out"
    audit(corpus, out)
    path = out / "review-manifest.jsonl"
    rows = read_review_rows(path)
    rows[0]["detector"] = "FORGED-DETECTOR"
    path.write_text(json.dumps(rows[0])+"\n", encoding="utf-8")
    code = main(["materialize-review", "--output", str(out), "--destination", str(tmp_path/"review"), "--operator-confirm"])
    assert code == 1


def test_materialize_rehashes_source(tmp_path):
    corpus = build_corpus(tmp_path / "c", {"a/b/c": [document("x", "Original authored prose remains safe.", 1)]})
    out = tmp_path / "out"
    audit(corpus, out)
    flip(source(corpus))
    code = main(["materialize-review", "--output", str(out), "--destination", str(tmp_path/"review"), "--operator-confirm"])
    assert code == 1


def test_forged_receipt_cannot_read_unaudited_file(tmp_path):
    root = tmp_path / "unrelated"
    root.mkdir()
    target = root / "secret.jsonl"
    target.write_text(json.dumps({"doc_id": "x", "text": "AUTHORED-PRIVATE-CANARY"})+"\n", encoding="utf-8")
    st = target.stat()
    out = tmp_path / "out"
    out.mkdir()
    (out / scan.RECEIPT_FILE).write_text(json.dumps({"status":"COMPLETE", "binding":{"data_root":str(root), "line_ceiling":4096},
        "source_files":[{"path":"secret.jsonl", "size":st.st_size, "mtime_ns":st.st_mtime_ns}]}), encoding="utf-8")
    (out / "review-manifest.jsonl").write_text(json.dumps({"path":"secret.jsonl", "offset":0, "doc_id":"x"})+"\n", encoding="utf-8")
    assert main(["materialize-review", "--output", str(out), "--destination", str(tmp_path/"review"), "--operator-confirm"]) == 1


def test_source_metadata_is_not_exported_as_snippets(tmp_path):
    canary = "AUTHORED-SENSITIVE-PROSE-SECRET-92847"
    doc = document("x", "Ordinary authored prose.", 1, metadata={"language_provenance":canary})
    corpus = build_corpus(tmp_path / "c", {"a/b/c": [doc]})
    out = tmp_path / "out"
    audit(corpus, out)
    assert canary.encode() not in (out / "quality-language.json").read_bytes()


def test_candidate_saturated_ratio_matches_advertised_cut(tmp_path):
    corpus = build_corpus(tmp_path / "c", {"a/b/c": [document("x", "z"*1024, 1)]})
    out = tmp_path / "out"
    audit(corpus, out)
    import yaml
    policy = yaml.safe_load((out / "candidate-policy-moderate.yaml").read_bytes())
    rule = next(r for r in policy["components"]["a"]["rules"] if r["detector"]=="repeated_char_ratio")
    actual = 1 if 1.0 >= rule["cut"] else 0
    assert rule["estimated_impact"]["docs"] == actual, rule


def test_receipt_resource_envelope_is_recorded(corpus, tmp_path):
    out = tmp_path / "out"
    audit(corpus, out)
    receipt = json.loads((out / scan.RECEIPT_FILE).read_bytes())
    for name in ("max_rss_bytes", "free_reserve_bytes", "max_output_bytes", "deadline_seconds"):
        assert name in json.dumps(receipt), name


def test_report_refuses_noncomplete_receipt(corpus, tmp_path):
    out = tmp_path / "out"
    audit(corpus, out)
    path = out / scan.RECEIPT_FILE
    body = json.loads(path.read_bytes())
    body["status"] = "INCOMPLETE"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(scan.QualityError):
        runner.verify_report(corpus, out)


def test_conservation_all_scopes(corpus, tmp_path):
    out = tmp_path / "out"
    audit(corpus, out)
    summary = json.loads((out/"quality-audit.json").read_bytes())["global"]["all"]
    scopes = json.loads((out/"quality-by-component.json").read_bytes())["scopes"]
    for prefix in ("component:", "allocation:", "source:"):
        selected = [v["all"] for k,v in scopes.items() if k.startswith(prefix)]
        assert sum(v["documents"] for v in selected) == summary["documents"]
        assert sum(v["canonical_bytes"] for v in selected) == summary["canonical_bytes"]
    for population in [summary]+[v["all"] for v in scopes.values()]:
        docs, size = population["documents"], population["canonical_bytes"]
        for name in ("flags", "size_buckets", "classes", "bool_intersections"):
            for impact in population[name].values():
                assert 0 <= impact["docs"] <= docs and 0 <= impact["bytes"] <= size
                assert impact["docs_pct"] == round(100*impact["docs"]/docs,6)
                assert impact["bytes_pct"] == round(100*impact["bytes"]/size,6)
        for metric in population["metrics"].values():
            assert metric["applicable"]+metric["not_applicable"] == docs


def test_reverse_unit_order_preserves_review(corpus, tmp_path):
    out = tmp_path / "out"
    audit(corpus, out)
    binding = json.loads((out/scan.BINDING_FILE).read_bytes())
    manifest = scan.load_manifest(corpus)
    units = list(runner.stream_units(out, manifest, binding["digest"], []))
    first, _ = report.build_artifacts(binding, units)
    second, _ = report.build_artifacts(binding, reversed(units))
    assert first == second


def test_language_many_scores_remain_bounded_and_mergeable(tmp_path):
    rows = [document(str(i), "A sufficiently ordinary authored sentence.", i+1,
        metadata={"fasttext_english":i/300}) for i in range(301)]
    corpus = build_corpus(tmp_path / "c", {"a/b/c": rows})
    audit(corpus, tmp_path/"out")


def test_policy_xml_code_not_strong_html():
    text = '```xml\n<!DOCTYPE note>\n<note><to>Ada</to></note>\n```'
    result = detectors.analyze(text, len(text.encode()))
    assert "markup_full_html" not in [FLAGS[i] for i in result.flags]


def delayed_result(value):
    time.sleep((4-value)*0.15)
    return value, time.monotonic()


def memory_worker(value):
    allocated = bytearray(value)
    time.sleep(5)
    return len(allocated)


def test_forced_reverse_completion_is_consumed_in_order():
    with scan.OrderedPool(4) as pool:
        results = list(pool.map(delayed_result, [0, 1, 2, 3]))
    assert [r[0] for r in results] == [0, 1, 2, 3]
    assert results[-1][1] < results[0][1]


def test_blocked_workers_deadline_is_bounded():
    from xlm.data.exclusion.supervisor import Supervisor, Deadline
    from xlm.data.exclusion.policy import C05Error
    start = time.monotonic()
    with pytest.raises(C05Error):
        with Supervisor(Deadline(0.4, start), 2*1024**3, interval=0.1) as supervisor:
            with scan.OrderedPool(2, supervisor) as pool:
                list(pool.map(time.sleep, [10, 10]))
    assert time.monotonic()-start < 6


def test_worker_crash_refuses():
    from concurrent.futures.process import BrokenProcessPool
    with pytest.raises(BrokenProcessPool):
        with scan.OrderedPool(2) as pool:
            list(pool.map(os._exit, [7]))


def test_child_rss_included():
    import psutil
    from xlm.data.exclusion.supervisor import Supervisor, Deadline
    from xlm.data.exclusion.policy import C05Error
    ceiling = psutil.Process().memory_info().rss + 96*1024**2
    with pytest.raises(C05Error, match="RSS"):
        with Supervisor(Deadline(8, time.monotonic()), ceiling, interval=0.05) as supervisor:
            with scan.OrderedPool(2, supervisor) as pool:
                list(pool.map(memory_worker, [64*1024**2, 64*1024**2]))
    assert supervisor.peak_rss > ceiling


def test_midstream_source_change_refuses(corpus, tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "CHUNK_BYTES", 4096)
    real = runner.process_chunk
    changed = False
    def mutate(task):
        nonlocal changed
        result = real(task)
        if not changed:
            path = source(corpus)
            raw = path.read_bytes()
            path.write_bytes(raw[:-10]+bytes([raw[-10]^1])+raw[-9:])
            changed = True
        return result
    monkeypatch.setattr(runner, "process_chunk", mutate)
    out = tmp_path / "out"
    with pytest.raises(scan.QualityError):
        audit(corpus, out)
    assert not scan.unit_path(out, 0).exists()


def test_public_overlay_consumption_only(tmp_path, monkeypatch):
    # Reuse only public authored material left by the interrupted baseline fixture.
    # Never invoke its producer, matcher, private ledger or group arrays.
    root = Path("F:/qa-tmp-96f38f3/quality-c050/root")
    monkeypatch.setenv("XLM_AUTHORED_C05_KEY", "authored-pilot-only-not-a-protected-trust-root")
    manifest, proof = root/"manifest.json", root/"proof.json"
    out = tmp_path/"overlay"
    audit(manifest, out, proof=proof, allow_authored_proof=True)
    audit(manifest, tmp_path/"plain")
    over = json.loads((out/"quality-audit.json").read_bytes())["global"]
    plain = json.loads((tmp_path/"plain"/"quality-audit.json").read_bytes())["global"]["all"]
    assert over["all"] == plain
    for key in ("documents", "canonical_bytes", "jsonl_line_bytes"):
        assert over["all"][key] == over["c05_kept"][key]+over["c05_removed"][key]
    from xlm.data.quality.overlay import load_overlay, OverlayError
    body = json.loads(manifest.read_bytes())
    kwargs = dict(manifest_digest=body["digest"], documents={f["path"]:f["documents"] for f in body["files"]}, allow_authored=True, consumes=[])
    overlay = load_overlay(proof, **kwargs)
    assert sum(int(b.sum()) for b in overlay.kept.values()) == over["c05_kept"]["documents"]
    kwargs["manifest_digest"] = "0"*64
    with pytest.raises(OverlayError, match="different input manifest"):
        load_overlay(proof, **kwargs)
    spec = json.loads(proof.read_bytes())
    badproof = tmp_path/"proof.json"
    spec["completion_digest"] = "0"*64
    badproof.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(OverlayError, match="completion changed"):
        load_overlay(badproof, **kwargs)


def test_review_traversal_refuses(corpus, tmp_path):
    out = tmp_path / "out"
    audit(corpus, out)
    rows = read_review_rows(out/"review-manifest.jsonl")
    rows[0]["path"] = "../../outside.jsonl"
    (out/"review-manifest.jsonl").write_text(json.dumps(rows[0])+"\n", encoding="utf-8")
    assert main(["materialize-review", "--output", str(out), "--destination", str(tmp_path/"review"), "--operator-confirm"]) == 1


def test_huge_review_manifest_refuses(tmp_path):
    path = tmp_path / "review.jsonl"
    with path.open("wb") as stream:
        stream.truncate(256*1024**2+1)
    from xlm.data.quality.review import ReviewError
    with pytest.raises(ReviewError, match="size bound"):
        read_review_rows(path)


@pytest.mark.parametrize("kind", ["duplicate_key", "nonfinite"])
def test_noncanonical_json_refuses(tmp_path, kind):
    corpus = build_corpus(tmp_path/"c", {"a/b/c":[document("x", "authored", 1)]})
    path = source(corpus)
    raw = path.read_bytes()
    addition = b'"text":"discarded duplicate",' if kind == "duplicate_key" else b'"unexpected":NaN,'
    raw = b"{"+addition+raw[1:]
    path.write_bytes(raw)
    _rewrite_manifest(corpus, lambda m:m["files"][0].update(file_bytes=len(raw),documents_sha256=hashlib.sha256(raw).hexdigest()))
    with pytest.raises(scan.QualityError):
        audit(corpus,tmp_path/"out")


def test_signed_overlay_row_identity_mismatch_refuses(tmp_path, monkeypatch):
    from xlm.data.exclusion.artifacts import signed
    from xlm.data.quality.overlay import OverlayError
    root = Path("F:/qa-tmp-96f38f3/quality-c050/root")
    key = b"authored-pilot-only-not-a-protected-trust-root"
    monkeypatch.setenv("XLM_AUTHORED_C05_KEY", key.decode())
    spec = json.loads((root/"proof.json").read_bytes())
    public = Path(spec["completion"])
    envelope = json.loads((public/"completion.json").read_bytes())
    rows = [json.loads(line) for line in (public/"membership.jsonl").read_bytes().splitlines()]
    rows[0]["doc_id"] = "!WRONG-IDENTITY"  # remains strictly before every other id
    raw = b"".join(canonical.canonical_bytes(r)+b"\n" for r in rows)
    copied = tmp_path/"public"
    copied.mkdir()
    (copied/"membership.jsonl").write_bytes(raw)
    body = envelope["payload"]
    body.update(membership_bytes=len(raw), membership_sha256=hashlib.sha256(raw).hexdigest())
    resigned = signed(body, body["issuer"], key)
    (copied/"completion.json").write_bytes(canonical.canonical_bytes(resigned))
    spec.update(completion=str(copied), completion_digest=resigned["digest"])
    proof = tmp_path/"proof.json"
    proof.write_bytes(canonical.canonical_bytes(spec))
    with pytest.raises((OverlayError, scan.QualityError)):
        audit(root/"manifest.json",tmp_path/"out",proof=proof,allow_authored_proof=True)


def test_boilerplate_lines_counts_lines():
    text = "Privacy Policy and Terms of Service. Subscribe to our newsletter."
    result = detectors.analyze(text,len(text.encode()))
    assert result.values[METRIC_INDEX["boilerplate_lines"]] <= 1
