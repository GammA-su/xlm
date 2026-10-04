"""Hardening probes for independent-audit findings I01-I14 (authored fixtures only).

Each test pins the exact refusal MECHANISM (message) for one exploit, so that a probe
cannot pass merely because an unrelated check happened to fire first. No network,
no real corpus, no real C05 (the overlay probes reuse the authored synthetic C05
fixture from ``test_quality_audit``), no tokenizer, no training.
"""

# ruff: noqa: F811  (pytest fixture imported from test_quality_audit)

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import time
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from quality_fixtures import PROSE, TEXTS, build_corpus, document, standard_layout
from test_quality_audit import (  # noqa: F401 (fixture)
    _rewrite_manifest,
    artifacts,
    audit,
    c05_flow,
    interrupted,
    limits,
)
from xlm.data.evidence_v2 import canonical
from xlm.data.quality import detectors, runner, scan
from xlm.data.quality.aggregate import LanguageStats, Population, bin_matrix
from xlm.data.quality.detectors import analyze
from xlm.data.quality.overlay import IDENTITY, OverlayError, doc_digest
from xlm.data.quality.policy import FLAGS, METRIC_INDEX, METRICS, ratio_bin
from xlm.data.quality.report import ARTIFACTS
from xlm.data.quality.review import ReviewError, read_review_rows
from xlm.data.quality.runner import ReviewLimits, materialize_from_audit, run_audit, verify_report
from xlm.data.quality.scan import RECEIPT_FILE, ChunkTask, QualityError, process_chunk


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    return build_corpus(tmp_path / "corpus", standard_layout())


def source(manifest: Path, index: int = 0) -> Path:
    body = json.loads(manifest.read_bytes())
    return Path(str(body["data_root"])) / str(body["files"][index]["path"])


def same_size_change(path: Path) -> None:
    """One flipped byte inside the last row's text; size kept, mtime restored."""
    stat = path.stat()
    raw = bytearray(path.read_bytes())
    position = raw.rfind(b'"text":"') + 9
    raw[position] = ord("Z") if raw[position] != ord("Z") else ord("Y")
    path.write_bytes(bytes(raw))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_mtime_ns == stat.st_mtime_ns


def junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' | Out-Null",
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("junction creation unavailable on this host")


def every_output_byte(output: Path) -> list[bytes]:
    payloads = []
    for path in output.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            payloads.append(raw)
            if path.name.endswith(".unit.zz"):
                payloads.append(zlib.decompress(raw))
    return payloads


# -- I01 source identity --------------------------------------------------------------------


def test_i01_fresh_scan_rehashes_after_measurement(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = scan.process_chunk

    def mutate_during_last_measurement(task: ChunkTask) -> Any:
        result = real(task)
        if task.ordinal == 0 and task.last:
            same_size_change(source(corpus))
        return result

    monkeypatch.setattr(runner, "process_chunk", mutate_during_last_measurement)
    output = tmp_path / "out"
    with pytest.raises(QualityError, match="frozen manifest SHA-256/size/rows"):
        audit(corpus, output)
    assert not scan.unit_path(output, 0).exists()
    assert not (output / RECEIPT_FILE).exists()


def test_i01_report_rehashes_every_source(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    same_size_change(source(corpus, 3))
    with pytest.raises(QualityError, match="frozen manifest SHA-256/size/rows"):
        verify_report(corpus, output)


def test_i01_mtime_is_not_part_of_any_identity(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    receipt = (output / RECEIPT_FILE).read_bytes()
    assert b"mtime_ns" not in receipt
    for path in (output / "units").iterdir():
        assert b"mtime_ns" not in zlib.decompress(path.read_bytes())
    target = source(corpus)
    stat = target.stat()
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 9_000_000_000))
    assert verify_report(corpus, output)["verified"] is True


# -- I02 output / input / junction safety ----------------------------------------------------


def test_i02_inputs_inside_output_refuse_without_writing(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    moved = output / "quality-audit.json"
    moved.write_bytes(corpus.read_bytes())
    before = moved.read_bytes()
    with pytest.raises(QualityError, match="audit input"):
        audit(moved, output)
    assert moved.read_bytes() == before
    assert sorted(p.name for p in output.iterdir()) == ["quality-audit.json"]


def test_i02_foreign_output_content_is_refused_and_kept(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    interrupted(corpus, output, monkeypatch)
    foreign = output / "operator-notes.txt"
    foreign.write_bytes(b"keep me")
    with pytest.raises(QualityError, match="does not own"):
        audit(corpus, output)
    assert foreign.read_bytes() == b"keep me"
    foreign.unlink()
    stray = output / "units" / "notes.unit.zz.tmp.bak"
    stray.write_bytes(b"keep me too")
    with pytest.raises(QualityError, match="does not own"):
        audit(corpus, output)
    assert stray.exists()


def test_i02_owned_units_junction_is_refused_before_cleanup(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    interrupted(corpus, output, monkeypatch)
    target = tmp_path / "elsewhere"
    target.mkdir()
    sentinel = target / "f00001.unit.zz.tmp"  # a job-owned staging NAME, outside the job
    sentinel.write_bytes(b"AUTHORED RETAINED MATERIAL")
    shutil.rmtree(output / "units")
    junction(output / "units", target)
    with pytest.raises(QualityError, match="link/junction"):
        audit(corpus, output)
    assert sentinel.read_bytes() == b"AUTHORED RETAINED MATERIAL"


def test_i02_owned_staging_is_the_only_cleanup(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    interrupted(corpus, output, monkeypatch)
    staging = output / "units" / "f00003.unit.zz.tmp"
    staging.write_bytes(b"partial unit from a crash")
    assert audit(corpus, output)["files_resumed"] == 1
    assert not staging.exists()


def test_i02_aliased_review_destination_refuses(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    real = tmp_path / "real"
    real.mkdir()
    junction(tmp_path / "alias", real)
    with pytest.raises(ReviewError, match="alias"):
        materialize_from_audit(output, tmp_path / "alias" / "review", limits=ReviewLimits())
    assert list(real.iterdir()) == []


# -- I03 materialization trust -------------------------------------------------------------


def test_i03_forged_but_well_formed_receipt_refuses(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    path = output / RECEIPT_FILE
    body = json.loads(path.read_bytes())
    body["source_files"][0]["documents_sha256"] = "0" * 64
    body["digest"] = canonical.self_digest(body)  # internally consistent forgery
    path.write_bytes(canonical.canonical_bytes(body))
    with pytest.raises(QualityError, match="source file identities differ"):
        materialize_from_audit(output, tmp_path / "review", limits=ReviewLimits())
    assert not (tmp_path / "review").exists()


def test_i03_review_text_is_streamed_under_an_output_ceiling(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    with pytest.raises(ReviewError, match="--max-output-mib"):
        materialize_from_audit(
            output,
            tmp_path / "review",
            limits=ReviewLimits(max_documents=50, max_output_bytes=4096),
        )


# -- I04 whole-command supervision ---------------------------------------------------------


def test_i04_deadline_starts_at_dispatch(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    with pytest.raises(QualityError, match="deadline"):
        run_audit(
            corpus,
            output,
            limits=limits(deadline_seconds=5.0),
            progress_interval=None,
            started=time.monotonic() - 10.0,
        )
    assert not output.exists()


def test_i04_publication_gate_withholds_complete(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    monkeypatch.setattr(runner, "PUBLICATION_MARGIN", 10_000.0)
    with pytest.raises(QualityError, match="deadline"):
        audit(corpus, output)
    assert not (output / RECEIPT_FILE).exists()
    monkeypatch.setattr(runner, "PUBLICATION_MARGIN", 0.5)
    result = audit(corpus, output)
    assert result["files_resumed"] == 4 and (output / RECEIPT_FILE).exists()


def test_i04_process_tree_rss_ceiling_refuses(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import psutil

    real = scan.process_chunk

    def hog(task: ChunkTask) -> Any:
        ballast = bytearray(384 * 1024**2)
        ballast[::4096] = b"x" * len(ballast[::4096])
        time.sleep(1.5)
        del ballast
        return real(task)

    monkeypatch.setattr(runner, "process_chunk", hog)
    ceiling = psutil.Process().memory_info().rss + 128 * 1024**2
    output = tmp_path / "out"
    with pytest.raises(QualityError, match="RSS"):
        run_audit(corpus, output, limits=limits(max_rss_bytes=ceiling), progress_interval=None)
    assert not (output / RECEIPT_FILE).exists()


def test_i04_report_is_supervised(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    with pytest.raises(QualityError, match="deadline"):
        verify_report(corpus, output, deadline_seconds=0.5, started=time.monotonic() - 1.0)


# -- I05 numeric language histograms -------------------------------------------------------


def test_i05_1001_distinct_numeric_values_merge_in_any_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = []
    for i in range(1001):
        row = document(f"n{i}", f"Plain authored sentence number {i}.", i + 1)
        row["language_confidence"] = i / 1000
        row["source_metadata"] = {"fasttext_english": (1000 - i) / 1000}
        rows.append(row)
    corpus = build_corpus(tmp_path / "c", {"a/b/c": rows})
    monkeypatch.setattr(scan, "CHUNK_BYTES", 8192)
    outputs = [artifacts_of(corpus, tmp_path / f"w{w}", w) for w in (1, 4)]
    assert outputs[0] == outputs[1]
    scopes = json.loads(outputs[0]["quality-language.json"])["scopes"]
    confidence = scopes["global"]["all"]["language_confidence"]
    assert len(confidence["bins_nonzero"]) == 1001 and confidence["in_range"] == 1001
    stats = []
    for order in (range(1001), reversed(range(1001))):
        total = LanguageStats()
        for i in order:
            single = LanguageStats()
            single.add(rows[i], 10)
            total.merge(single)
        stats.append(total.to_json())
    assert stats[0] == stats[1]


def artifacts_of(corpus: Path, output: Path, workers: int) -> dict[str, bytes]:
    audit(corpus, output, workers=workers)
    return artifacts(output)


# -- I06 linear runs -----------------------------------------------------------------------


def quadratic_reference(text: str) -> dict[str, int | float | None]:
    """The pre-hardening per-code-point regex probe (correctness oracle only)."""
    import re

    best = {"space": 0, "punct": 0, "alnum": 0, "other": 0}
    covered = 0
    counts = detectors.char_counts(text)
    for ch, n in counts.items():
        if n < 8 or ch * 8 not in text:
            continue
        kind = detectors._run_class(detectors.char_mask(ch))
        for match in re.finditer(re.escape(ch) + "{8,}", text):
            length = match.end() - match.start()
            covered += length
            best[kind] = max(best[kind], length)
    return {
        "max_char_run": max(best.values()),
        "max_space_run": best["space"],
        "max_punct_run": best["punct"],
        "max_alnum_run": best["alnum"],
        "max_other_run": best["other"],
        "repeated_char_ratio": covered / len(text) if text else None,
    }


def test_i06_linear_runs_equal_the_reference_exactly() -> None:
    rng = random.Random(11)
    alphabet = "ab -=!\n\té日\U0001f600\u200d" + "".join(chr(0x4E00 + i) for i in range(40))
    samples = list(TEXTS.values())
    for _ in range(300):
        parts = []
        for _ in range(rng.randint(1, 30)):
            parts.append(rng.choice(alphabet) * rng.choice([1, 2, 7, 8, 9, 15, 16, 300]))
        samples.append("".join(parts))
    for text in samples:
        values = analyze(text, len(text.encode("utf-8", "surrogatepass"))).values
        expected = quadratic_reference(text)
        for name, value in expected.items():
            assert values[METRIC_INDEX[name]] == value, name


@pytest.mark.serial_exclusive
def test_i06_run_detector_scales_linearly() -> None:
    timings = {}
    for distinct in (1000, 2000, 4000, 8000):
        text = "".join(chr(0x4E00 + i) * 8 for i in range(distinct))
        codes = detectors.code_points(text)
        best = float("inf")
        for _ in range(3):
            values: list[Any] = [None] * len(METRICS)
            started = time.perf_counter()
            detectors._runs(text, codes, values)
            best = min(best, time.perf_counter() - started)
        timings[distinct] = best
    # Linear: 8x the input costs about 8x (quadratic was ~64x: 0.07 s -> 4.6 s).
    assert timings[8000] / max(timings[1000], 1e-6) < 24, timings
    assert timings[8000] < 0.5, timings


def test_i06_caches_are_bounded() -> None:
    assert detectors.CLASS_CACHE_LIMIT == 65_536
    for code in range(0x20000, 0x20000 + 70_000):
        detectors.char_mask(chr(code))
    assert len(detectors._CLASS_CACHE) <= detectors.CLASS_CACHE_LIMIT


# -- I07 privacy canaries ------------------------------------------------------------------

CANARIES = {
    "text": "CANARY-TEXT-SECRET-55c1",
    "doc_id": "CANARY-DOCID-SECRET-91b2",
    "language": "CANARY-LANGUAGE-SECRET",
    "label": "CANARY-LABEL-SECRET",
    "provenance": "CANARY-PROVENANCE-SECRET prose that must never be exported",
    "kind": "CANARY-KIND-SECRET",
    "key": "canary_language_secret_key_d41f",
    "value": "CANARY-METADATA-VALUE-SECRET",
    "score": "CANARY-SCORE-SECRET",
}


def test_i07_no_source_string_reaches_any_output(tmp_path: Path) -> None:
    row = document(
        CANARIES["doc_id"],
        f"{PROSE} {CANARIES['text']}",
        1,
        metadata={
            "language_provenance": CANARIES["provenance"],
            "upstream_full_doc_lid": CANARIES["label"],
            "fasttext_english": CANARIES["score"],
            CANARIES["key"]: CANARIES["value"],
            "free_note": CANARIES["value"],
        },
    )
    row["language"] = CANARIES["language"]
    row["document_kind"] = CANARIES["kind"]
    corpus = build_corpus(tmp_path / "c", {"a/b/c": [row, document("ok", PROSE, 2)]})
    output = tmp_path / "out"
    audit(corpus, output)
    verify_report(corpus, output)
    for payload in every_output_byte(output):
        for name, canary in CANARIES.items():
            assert canary.encode() not in payload, name
            assert canary.lower().encode() not in payload, name
    language = json.loads((output / "quality-language.json").read_bytes())
    scope = language["scopes"]["global"]["all"]
    assert "<unrecognized>" in scope["language_field"]["values"]
    assert "<unrecognized>" in scope["provenance"]["values"]
    assert scope["documents_with_unrecognized_language_like_keys"] == 1
    assert scope["scores"]["fasttext_english"]["non_numeric"] == 1


def test_i07_adapter_provenance_maps_to_categories(tmp_path: Path) -> None:
    import ast

    literals = []
    adapters = Path(__file__).resolve().parents[1] / "src/xlm/data/adapters"
    for path in adapters.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value == "language_provenance":
                        assert isinstance(value, ast.Constant)
                        literals.append(value.value)
    from xlm.data.quality.aggregate import provenance_category

    assert literals and all(provenance_category(v) != "<unrecognized>" for v in literals)


# -- I08 HTML / XML / code examples --------------------------------------------------------


def flags_of(text: str) -> set[str]:
    return {FLAGS[f] for f in analyze(text, len(text.encode())).flags}


def test_i08_markup_semantics() -> None:
    fenced = "Example:\n```html\n<!DOCTYPE html><html><body>x</body></html>\n```\nDone."
    assert "markup_full_html" not in flags_of(fenced)
    assert "markup_fenced_example" in flags_of(fenced)
    xml = "<!DOCTYPE note>\n<note><to>Ada</to></note>\n"
    assert {"markup_xml", "has_other_doctype"} <= flags_of(xml)
    assert "markup_full_html" not in flags_of(xml)
    html = "<!DOCTYPE html>\n<html><body><p>Hi</p></body></html>"
    assert {"markup_full_html", "has_html_doctype"} <= flags_of(html)
    unfenced_xml_fence = "```xml\n<!DOCTYPE note>\n<note><to>Ada</to></note>\n```"
    assert "markup_full_html" not in flags_of(unfenced_xml_fence)


# -- I09 / I14 exact comparators -----------------------------------------------------------


def test_i09_ratio_bins_match_printed_edges_exactly() -> None:
    for k in range(1001):
        edge = k / 1000
        assert ratio_bin(edge) == k
        if k:
            assert ratio_bin(float(np.nextafter(edge, -1.0))) == k - 1
    probes = [k / 1000 for k in range(1001)] + [29 / 100, 57 / 100, 7 / 10, 1 / 3, 1.0]
    matrix = np.full((len(probes), len(METRICS)), np.nan)
    column = METRIC_INDEX["alpha_ratio"]
    matrix[:, column] = probes
    assert bin_matrix(matrix)[:, column].tolist() == [ratio_bin(p) for p in probes]


def _as_float(value: float | int | None) -> float:
    assert value is not None
    return float(value)


def _satisfies(value: float, comparator: str, cut: float) -> bool:
    return {">=": value >= cut, ">": value > cut, "<": value < cut, "<=": value <= cut}[comparator]


def test_i09_every_candidate_rule_matches_its_impact(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    by_component: dict[str, list[dict[str, Any]]] = {}
    for key, rows in standard_layout().items():
        by_component.setdefault(key.split("/")[0], []).extend(rows)
    checked = 0
    for band in ("conservative", "moderate", "aggressive"):
        policy = yaml.safe_load((output / f"candidate-policy-{band}.yaml").read_bytes())
        for component, entry in policy["components"].items():
            measured = [
                (analyze(r["text"], r["utf8_byte_count"]).values, r["utf8_byte_count"])
                for r in by_component[component]
            ]
            for rule in entry["rules"]:
                if "comparator" not in rule:
                    continue
                n = METRIC_INDEX[rule["detector"]]
                hits = [
                    size
                    for values, size in measured
                    if values[n] is not None
                    and _satisfies(_as_float(values[n]), rule["comparator"], rule["cut"])
                ]
                assert rule["estimated_impact"]["docs"] == len(hits), rule
                assert rule["estimated_impact"]["bytes"] == sum(hits), rule
                checked += 1
    assert checked > 50


def test_i09_saturated_ratio_cut_is_exactly_one(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path / "c", {"a/b/c": [document("x", "z" * 1024, 1)]})
    output = tmp_path / "out"
    audit(corpus, output)
    policy = yaml.safe_load((output / "candidate-policy-moderate.yaml").read_bytes())
    rule = next(
        r for r in policy["components"]["a"]["rules"] if r["detector"] == "repeated_char_ratio"
    )
    assert (rule["comparator"], rule["cut"], rule["estimated_impact"]["docs"]) == (">=", 1.0, 1)


def test_i14_ocr_presence_boundary_is_inclusive(tmp_path: Path) -> None:
    exact = "x\n" + "\n".join(["two words"] * 999)  # 1 single-char line of 1000
    below = "x\n" + "\n".join(["two words"] * 1000)  # 1 of 1001 -> 0.000999...
    assert analyze(exact, len(exact)).values[METRIC_INDEX["single_char_line_ratio"]] == 0.001
    corpus = build_corpus(
        tmp_path / "c", {"a/b/c": [document("e", exact, 1), document("b", below, 2)]}
    )
    output = tmp_path / "out"
    audit(corpus, output)
    summary = json.loads((output / "quality-audit.json").read_bytes())["global"]["all"]
    presence = summary["presence_ratio_ge_0_001"]["single_char_line_ratio"]
    assert (presence["comparator"], presence["cut"], presence["docs"]) == (">=", 0.001, 1)
    assert "presence_ratio_gt_0_001" not in summary


# -- I10 strict receipt --------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b.update(extra="field"),
        lambda b: b.update(kind="xlm_quality_audit_receipt_v1"),
        lambda b: b.update(schema_version=1),
        lambda b: b.update(phase="SCANNING"),
        lambda b: b.update(status="INCOMPLETE"),
        lambda b: b["envelope"].pop("max_rss_bytes"),
        lambda b: b["envelope"].update(workers=-1),
        lambda b: b.update(actions_executed=["DROP"]),
        lambda b: b["artifacts"].pop("quality-summary.md"),
        lambda b: b.update(producer_envelopes=[]),
    ],
)
def test_i10_receipt_schema_is_strict(corpus: Path, tmp_path: Path, change: Any) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    path = output / RECEIPT_FILE
    body = json.loads(path.read_bytes())
    change(body)
    body["digest"] = canonical.self_digest(body)
    path.write_bytes(canonical.canonical_bytes(body))
    with pytest.raises(QualityError, match="receipt invalid"):
        verify_report(corpus, output)


def test_i10_receipt_self_digest_and_envelope(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    audit(corpus, output, workers=2)
    path = output / RECEIPT_FILE
    body = json.loads(path.read_bytes())
    assert body["envelope"]["workers"] == 2 and body["envelope"]["queue_tasks"] == 4
    assert body["producer_envelopes"] == [body["envelope"]]
    body["execution"]["note"] = "tampered without re-digesting"
    path.write_bytes(canonical.canonical_bytes(body))
    with pytest.raises(QualityError, match="self-digest"):
        verify_report(corpus, output)


# -- I11 overlay identity ------------------------------------------------------------------


def resign(c05: dict[str, Any], tmp_path: Path, change: Any) -> Path:
    from xlm.data.exclusion.artifacts import signed

    spec = json.loads(c05["proof"].read_bytes())
    public = Path(spec["completion"])
    envelope = json.loads((public / "completion.json").read_bytes())
    rows = [json.loads(x) for x in (public / "membership.jsonl").read_bytes().splitlines()]
    change(rows)
    raw = b"".join(canonical.canonical_bytes(r) + b"\n" for r in rows)
    copied = tmp_path / "public"
    copied.mkdir()
    (copied / "membership.jsonl").write_bytes(raw)
    body = envelope["payload"]
    body.update(membership_bytes=len(raw), membership_sha256=hashlib.sha256(raw).hexdigest())
    resigned = signed(body, body["issuer"], c05["key"].encode())
    (copied / "completion.json").write_bytes(canonical.canonical_bytes(resigned))
    spec.update(completion=str(copied), completion_digest=resigned["digest"])
    proof = tmp_path / "proof.json"
    proof.write_bytes(canonical.canonical_bytes(spec))
    return proof


def test_i11_signed_wrong_content_digest_refuses(
    c05_flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(c05_flow["key_env"], c05_flow["key"])
    proof = resign(c05_flow, tmp_path, lambda rows: rows[0].update(content="0" * 64))
    with pytest.raises(QualityError, match="kept-row content differs"):
        audit(c05_flow["manifest"], tmp_path / "out", proof=proof, allow_authored_proof=True)


def test_i11_signed_wrong_doc_id_refuses(
    c05_flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(c05_flow["key_env"], c05_flow["key"])
    proof = resign(c05_flow, tmp_path, lambda rows: rows[0].update(doc_id="!WRONG-IDENTITY"))
    with pytest.raises(QualityError, match="kept-row identity differs"):
        audit(c05_flow["manifest"], tmp_path / "out", proof=proof, allow_authored_proof=True)


def test_i11_signed_wrong_allocation_refuses(
    c05_flow: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(c05_flow["key_env"], c05_flow["key"])
    proof = resign(c05_flow, tmp_path, lambda rows: rows[0].update(view="another-view"))
    with pytest.raises(OverlayError, match="allocation differs"):
        audit(c05_flow["manifest"], tmp_path / "out", proof=proof, allow_authored_proof=True)


def test_i11_kept_identity_checked_in_the_worker() -> None:
    row = document("doc-1", PROSE, 1)
    line = canonical.canonical_bytes(row) + b"\n"
    identity = np.zeros(1, dtype=IDENTITY)
    identity["doc"][0] = np.frombuffer(doc_digest("doc-1"), dtype=np.uint8)
    identity["content"][0] = np.frombuffer(hashlib.sha256(line[:-1]).digest()[:16], np.uint8)
    identity["bytes"][0] = row["utf8_byte_count"]
    task = ChunkTask(0, "p", 1, 0, line, np.ones(1, bool).tobytes(), 4096, True, identity.tobytes())
    assert process_chunk(task).rows == 1
    identity["bytes"][0] += 1
    wrong = ChunkTask(
        0, "p", 1, 0, line, np.ones(1, bool).tobytes(), 4096, True, identity.tobytes()
    )
    with pytest.raises(QualityError, match="kept-row identity"):
        process_chunk(wrong)


# -- I12 boilerplate semantics -------------------------------------------------------------


def test_i12_boilerplate_lines_are_distinct_lines() -> None:
    text = "Privacy Policy and Terms of Service. Subscribe to our newsletter."
    values = analyze(text, len(text)).values
    assert values[METRIC_INDEX["boilerplate_lines"]] == 1
    assert values[METRIC_INDEX["boilerplate_phrase_hits"]] == 4  # subscribe + newsletter
    two = "Privacy Policy\nHome\nOrdinary prose about rivers and valleys."
    values = analyze(two, len(two)).values
    assert values[METRIC_INDEX["boilerplate_lines"]] == 2


# -- I13 strict canonical JSON -------------------------------------------------------------


def _replace(raw: bytes, old: bytes, new: bytes) -> bytes:
    assert old in raw
    return raw.replace(old, new, 1)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda r: b'{"text":"shadow",' + r[1:], "strict canonical JSON"),
        (
            lambda r: _replace(r, b'"language_confidence":1.0', b'"language_confidence":NaN'),
            "strict",
        ),
        (
            lambda r: _replace(r, b'"language_confidence":1.0', b'"language_confidence":Infinity'),
            "strict",
        ),
        (
            lambda r: _replace(r, b'"language_confidence":1.0', b'"language_confidence":-Infinity'),
            "strict",
        ),
        (lambda r: _replace(r, b"Rivers", b"Riv\xffers"), "strict"),
        (lambda r: b'{"unexpected":1,' + r[1:], "schema"),
        (lambda r: _replace(r, b'"split":"train",', b""), "schema"),
        (
            lambda r: _replace(r, b'"utf8_byte_count":', b'"utf8_byte_count":true,"x":'),
            "strict|schema",
        ),
        (lambda r: _replace(r, b'"split":"train"', b'"split":"holdout"'), "schema"),
    ],
)
def test_i13_noncanonical_rows_refuse(tmp_path: Path, mutation: Any, message: str) -> None:
    corpus = build_corpus(tmp_path / "c", {"a/b/c": [document("x", PROSE, 1)]})
    path = source(corpus)
    raw = mutation(path.read_bytes())
    path.write_bytes(raw)
    _rewrite_manifest(
        corpus,
        lambda m: m["files"][0].update(
            file_bytes=len(raw), documents_sha256=hashlib.sha256(raw).hexdigest()
        ),
    )
    output = tmp_path / "out"
    with pytest.raises(QualityError, match=message):
        audit(corpus, output)
    assert not scan.unit_path(output, 0).exists()


def test_i13_oversized_manifest_and_unit_refuse(corpus: Path, tmp_path: Path) -> None:
    big = tmp_path / "big.json"
    with big.open("wb") as stream:
        stream.truncate(scan.MAX_MANIFEST_BYTES + 1)
    with pytest.raises(QualityError, match="size bound"):
        scan.load_manifest(big)
    bomb = zlib.compress(b"0" * (scan.MAX_UNIT_DECODED_BYTES + 1024), 9)
    with pytest.raises(QualityError, match="expands beyond"):
        scan.decode_unit(bomb)


# -- additional: review keyed by manifest identity, no complete without receipt -------------


def test_review_ranks_are_bound_to_the_manifest(corpus: Path, tmp_path: Path) -> None:
    first = tmp_path / "first"
    audit(corpus, first)
    _rewrite_manifest(corpus, lambda m: m.update(note="same files, new manifest identity"))
    second = tmp_path / "second"
    audit(corpus, second)
    ranks = [
        sorted(r["rank"] for r in read_review_rows(out / "review-manifest.jsonl"))
        for out in (first, second)
    ]
    assert ranks[0] != ranks[1]


def test_aggregates_never_claim_completion(corpus: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    audit(corpus, output)
    for name in ARTIFACTS:
        if name.endswith(".json"):
            body = json.loads((output / name).read_bytes())
            assert "status" not in body or body["status"] == "PROPOSAL_ONLY"
            assert "NOT a completion signal" in body["completion"]


def test_population_merge_order_independence_with_language() -> None:
    pops = []
    for n, name in enumerate(sorted(TEXTS)):
        pop = Population()
        text = TEXTS[name]
        row = document(name, text, n + 1, metadata={"fasttext_english": n / 40})
        pop.add_language(row, len(text.encode()))
        pops.append(pop)
    forward, backward = Population(), Population()
    for pop in pops:
        forward.merge(pop)
    for pop in reversed(pops):
        backward.merge(pop)
    assert forward.to_json() == backward.to_json()
