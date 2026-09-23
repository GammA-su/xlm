"""P27B deterministic cleaning throughput: offline fixtures only.

Structural assertions are deterministic; wall times and RSS are reported,
never threshold-gated (except one generous peak-RSS bound). ``recorded_at``
quarantine timestamps and per-stage/elapsed timings are inherently
run-dependent and are excluded from cross-run comparisons everywhere.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from xlm.cli.data_cmd import app as data_app
from xlm.core.contracts import CanonicalDocument
from xlm.data.canonical_io import CanonicalDatasetReader
from xlm.data.cleaning.base import CleaningBudgetExhaustedError
from xlm.data.cleaning.features import TextFeatures, compute_char_stats
from xlm.data.cleaning.pii import _NON_EMAIL_HINT_RE, SECRET_PATTERNS
from xlm.data.cleaning.pipeline import create_pipeline_preset
from xlm.data.cleaning.quarantine import QuarantineManager, QuarantinePolicy
from xlm.data.cleaning.sharded import (
    merge_unit_results,
    plan_clean_units,
    run_sharded_clean,
)
from xlm.data.datasets.shards import ShardedJsonlWriter, load_manifest, verify_manifest
from xlm.data.normalization import compute_sha256

WORDS = (
    "the study of neural computation involves learning representations from data "
    "through optimization of differentiable objectives with careful regularization "
    "and validation across diverse benchmarks for robust generalization in practice".split()
)
GERMAN = (
    "die Untersuchung neuronaler Netze erfordert sorgfaeltige Optimierung und "
    "Validierung der Modelle auf verschiedenen Datensaetzen zur Verallgemeinerung".split()
)


def _prose(rng: random.Random, n_words: int, vocab: list[str] | None = None) -> str:
    vocab = vocab or WORDS
    return " ".join(rng.choice(vocab) for _ in range(n_words))


_CONTENT_WORDS = (
    "river mountain forest ocean valley prairie canyon glacier meadow harbor island "
    "garden bridge tower castle village market bakery school library museum theater "
    "garden farmer baker teacher doctor writer painter singer dancer actor author "
    "captain pilot sailor soldier farmer merchant trader banker lawyer judge mayor "
    "apple bread cheese honey lemon mango olive pepper salt sugar wheat bread "
    "table chair window door roof floor wall clock lamp mirror carpet curtain "
    "pencil paper book letter envelope stamp photo album radio clock phone bell "
    "horse dog cat bird fish sheep goat cow pig chicken duck goose turkey rabbit "
    "oak pine maple birch willow cedar elm ash beech chestnut walnut hazel "
    "spring summer autumn winter morning evening night dawn dusk noon midnight "
    "happy bright gentle quiet brave calm eager fair glad keen lively merry nice "
    "proud quick ready sharp short simple soft steep still sweet swift tender "
    "thick thin vivid warm wide wise young early late rapid slow steep steady "
    "run walk jump climb swim fly crawl slide glide march wander roam drift "
    "sing dance paint write draw build craft carve weave sew knit bake cook "
    "plant harvest gather store carry fetch bring take give share lend borrow "
    "open close lift drop push pull turn twist fold stack arrange clean wash "
    "clear cloudy rainy sunny windy snowy foggy stormy mild cold hot cool "
    "red blue green yellow orange purple pink brown black white gray golden silver".split()
)

_STOP_WORDS = [
    "the",
    "and",
    "of",
    "to",
    "in",
    "is",
    "that",
    "for",
    "with",
    "from",
    "this",
    "have",
    "were",
    "they",
    "their",
    "which",
    "have",
    "has",
]


def _distinct_prose(rng: random.Random, n_words: int) -> str:
    """High-entropy English filler: distinct content words plus forced stop words.

    Random small-vocabulary filler trips the repetition 5-gram filter, so
    documents that must reach later stages (language, PII) use a ~300-word
    pool with a stop word woven in every eighth position (keeps the English
    heuristic satisfied while 5-gram repetition stays near zero).
    """
    words = []
    for i in range(n_words):
        if i % 8 == 7:
            words.append(rng.choice(_STOP_WORDS))
        else:
            words.append(rng.choice(_CONTENT_WORDS))
    return " ".join(words)


def _doc_dict(doc_id: str, text: str, row: int, kind: str = "prose") -> dict:
    blob = text.encode("utf-8")
    return {
        "doc_id": doc_id,
        "source_id": "fixture_throughput",
        "source_revision": "rev0",
        "source_file": "f.jsonl",
        "source_row": row,
        "raw_hash": compute_sha256(blob),
        "clean_hash": compute_sha256(blob),
        "text": text,
        "utf8_byte_count": len(blob),
        "language": "en",
        "language_confidence": 1.0,
        "document_kind": kind,
        "source_metadata": {},
        "parent_ids": [],
        "license_reference": "cc-by-4.0",
        "transform_log": [],
        "quality_reasons": [],
        "cluster_ids": {},
        "split": "train",
    }


def gen_mixed(n: int, seed: int) -> list[str]:
    """Fourteen deterministic cleaning categories, JSON-serialized canonical lines."""
    rng = random.Random(seed)
    lines: list[str] = []
    for i in range(n):
        cat = i % 14
        if cat == 0:
            text = _prose(rng, 300)
        elif cat == 1:
            text = (
                "# Heading\n\n---\n\n" + _prose(rng, 200) + "\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
            )
        elif cat == 2:
            text = (" repeated filler sentence about data processing methods" * 40)[:6000]
        elif cat == 3:
            text = _prose(rng, 50) + "\n" + "z" * 400 + "\n" + _prose(rng, 50)
        elif cat == 4:
            text = _prose(rng, 250, GERMAN)
        elif cat == 5:
            text = "@#$% " * 300 + _prose(rng, 40)
        elif cat == 6:
            text = "tiny doc"
        elif cat == 7:
            text = (
                "<html><body><p>"
                + _prose(rng, 150)
                + "</p><script>var x = 1;</script></body></html>"
            )
        elif cat == 8:
            text = (
                _distinct_prose(rng, 100)
                + " contact bob@real-domain.com for info "
                + _distinct_prose(rng, 100)
            )
        elif cat == 9:
            text = (
                _distinct_prose(rng, 100)
                + " contact user@example.com for info "
                + _distinct_prose(rng, 100)
            )
        elif cat == 10:
            text = _distinct_prose(rng, 100) + " ssn 123-45-6789 here " + _distinct_prose(rng, 100)
        elif cat == 11:
            text = "placeholder@example.com plus 123-45-6789 " + _distinct_prose(rng, 100)
        elif cat == 12:
            text = "# Wiki article\n\n" + _prose(rng, 500) + "\n\n## Section\n\n" + _prose(rng, 400)
        else:
            text = _prose(rng, 350)
        lines.append(json.dumps(_doc_dict(f"doc-{i:06d}", text, i), ensure_ascii=False))
    return lines


def gen_clean_prose(n: int, seed: int, n_words: int = 300) -> list[str]:
    rng = random.Random(seed)
    return [
        json.dumps(_doc_dict(f"clean-{i:06d}", _prose(rng, n_words), i), ensure_ascii=False)
        for i in range(n)
    ]


def make_doc(doc_id: str, text: str) -> CanonicalDocument:
    return CanonicalDocument(**_doc_dict(doc_id, text, 0))


def _reference_run(
    tmp_path: Path, lines: list[str], name: str, max_docs: int | None = None
) -> tuple[list[dict], list[dict], object]:
    from xlm.data.cleaning.pipeline import PipelineExecutionSummary

    selected = tmp_path / f"{name}.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    q_dir = tmp_path / f"{name}_q"
    mgr = QuarantineManager(q_dir)
    pipeline = create_pipeline_preset("educational_prose")
    accepted_iter, summary = pipeline.run_stream(
        CanonicalDatasetReader.read_jsonl(selected),
        quarantine_mgr=mgr,
        max_docs=max_docs,
    )
    docs = [doc.to_dict() for doc in accepted_iter]
    quarantine_path = q_dir / "quarantine.jsonl"
    quarantine = (
        [
            json.loads(line)
            for line in quarantine_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if quarantine_path.is_file()
        else []
    )
    assert isinstance(summary, PipelineExecutionSummary)
    return docs, quarantine, summary


def _scrub_quarantine(entries: list[dict]) -> list[dict]:
    scrubbed = []
    for entry in entries:
        copy = dict(entry)
        copy.pop("recorded_at", None)
        scrubbed.append(copy)
    return scrubbed


def _scrub_summary(summary_dict: dict) -> dict:
    copy = dict(summary_dict)
    copy.pop("elapsed_seconds", None)
    copy["stage_metrics"] = [
        {key: value for key, value in stage.items() if key != "duration_ms"}
        for stage in copy["stage_metrics"]
    ]
    return copy


def _engine_run(
    tmp_path: Path,
    selected: Path,
    name: str,
    *,
    workers: int = 1,
    output_shard_bytes: int | None = None,
    quarantine_shard_bytes: int | None = None,
    max_docs: int | None = None,
    input_shard_bytes: int = 1024 * 1024,
) -> tuple[Path, Path, object, dict]:
    out = tmp_path / f"{name}_out"
    qdir = tmp_path / f"{name}_qeng"
    summary, _, assembled, throughput, _ = run_sharded_clean(
        input_path=selected,
        output_dir=out,
        preset="educational_prose",
        workers=workers,
        input_shard_bytes=input_shard_bytes,
        output_shard_bytes=output_shard_bytes,
        quarantine_dir=qdir,
        quarantine_shard_bytes=quarantine_shard_bytes,
        quarantine_policy=QuarantinePolicy(),
        max_docs=max_docs,
        max_input_bytes=2 * 1024**3,
    )
    return out, qdir, summary, throughput


def _read_accepted(out: Path) -> list[dict]:
    manifest_path = out / "clean-manifest.json"
    files = []
    if manifest_path.is_file():
        manifest = load_manifest(manifest_path)
        assert verify_manifest(out, manifest) is manifest
        files = [out / entry.path for entry in sorted(manifest.shards, key=lambda e: e.ordinal)]
    else:
        files = [out / "documents.jsonl"]
    docs = []
    for path in files:
        docs.extend(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return docs


def _read_quarantine(qdir: Path) -> list[dict]:
    manifest_path = qdir / "quarantine-manifest.json"
    files = []
    if manifest_path.is_file():
        manifest = load_manifest(manifest_path)
        assert verify_manifest(qdir, manifest) is manifest
        files = [qdir / entry.path for entry in sorted(manifest.shards, key=lambda e: e.ordinal)]
    else:
        files = [qdir / "quarantine.jsonl"]
    entries = []
    for path in files:
        entries.extend(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return entries


# P27B-G exactness: cached features vs direct computation.


def test_feature_cache_matches_direct_all_stages() -> None:
    pipeline = create_pipeline_preset("educational_prose")
    edge_texts = [
        "",
        "   \n\t  ",
        "---\n| a | b |\n|---|---|\n| 1 | 2 |\n---\n",
        "<html><body><p>Prose with markup and 0 < x < 1 math.</p></body></html>",
        "Contact bob@real-domain.com or user@example.com today for details. " * 10,
        "SSN 123-45-6789 plus placeholder@example.com in one document. " * 8,
        " ".join(GERMAN[:60]),
        "@#$%^&* symbols " * 60,
        "tiny",
        "Das Wetter ist schoen und die Validierung erfordert Sorgfalt. " * 20,
        "line with\rcarriage\r\nreturns\n\nand \u00a0 nbsp",
        "caf\u00e9 na\u00efve \u00b2 squared \u2167 numeral",
    ]
    for position, text in enumerate(edge_texts):
        doc = make_doc(f"edge-{position}", text)
        features = TextFeatures()
        for transform in pipeline.transforms:
            direct = transform.apply(doc)
            cached = transform.apply(doc, features)
            assert cached.action == direct.action, (transform.transform_id, text[:40])
            assert cached.reasons == direct.reasons, transform.transform_id
            assert cached.metrics.to_dict() == direct.metrics.to_dict(), transform.transform_id
            if cached.document is None:
                assert direct.document is None
            else:
                assert direct.document is not None
                assert cached.document.to_dict() == direct.document.to_dict()
            if cached.document is not None:
                doc = cached.document
        features.release()


def test_char_stats_match_reference_counts() -> None:
    samples = [
        "",
        "Hello, World! 123",
        "caf\u00e9 na\u00efve \u00b2 \u2167 \u00a0 \t\n\r\x00\x01\x7f\ufffd",
        "ABC def GHI jkl \u00c4\u00d6\u00dc \u03b1\u03b2\u03b3 \u4e2d\u6587",
        "a" * 5000 + "1" * 5000 + "@" * 5000 + " " * 5000,
    ]
    for text in samples:
        stats = compute_char_stats(text)
        assert stats.alpha == sum(1 for c in text if c.isalpha())
        assert stats.latin == sum(1 for c in text if c.isascii() and c.isalpha())
        assert stats.alnum == sum(1 for c in text if c.isalnum())
        non_ws = [c for c in text if not c.isspace()]
        assert stats.non_ws == len(non_ws)
        assert stats.symbols == sum(1 for c in non_ws if not c.isalnum())
        assert stats.digits == sum(1 for c in non_ws if c.isdigit())


def test_pii_hint_gate_matches_detailed_presence() -> None:
    secrets = [
        "hf_abcdefghijklmnopqrstuvwxyz01234567",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "Bearer abcdefghijklmnopqrstuvwx",
        "-----BEGIN RSA PRIVATE KEY-----",
        "CANARY_SECRET_probe_1",
        "user@real-domain.com",
        "123-45-6789",
        "user@example.com",
        "akiaiosfodnn7example",
        "BEARER ABCDEFGHIJKLMNOPQRSTUVWX",
        "hf_short",
        "12-34-5678",
        "trailing hf_abcdefghijklmnopqrstuvwxyz01234567 embedded",
    ]
    templates = [
        "clean prose without any marker at all. ",
        "marker here: {} end. ",
        "{} at the start. ",
        "two markers: {} and {}. ",
    ]
    checked = 0
    for template in templates:
        dual = "{} and {}" in template
        for first in secrets:
            if dual:
                texts = [template.format(first, second) for second in secrets[::3]]
            else:
                texts = [template.format(first)]
            for text in texts:
                expected = any(
                    name != "email_address" and pattern.search(text)
                    for name, pattern in SECRET_PATTERNS
                )
                assert (_NON_EMAIL_HINT_RE.search(text) is not None) == expected, text
                checked += 1
    assert checked > 100


# P27B-K mandated PII cases + P27B secret quarantine semantics.


def test_pii_mandated_cases(tmp_path: Path) -> None:
    from xlm.data.cleaning.pii import PiiSecretFilter

    filt = PiiSecretFilter()
    rng = random.Random(3)
    _ = _distinct_prose(rng, 120)

    exempt = [
        f"{_distinct_prose(rng, 60)} write to user@example.com please {_distinct_prose(rng, 60)}",
        f"{_distinct_prose(rng, 60)} admin@sub.example.org handles it {_distinct_prose(rng, 60)}",
        f"{_distinct_prose(rng, 60)} a@b.example and x@mail.example.net here "
        f"{_distinct_prose(rng, 60)}",
    ]
    for text in exempt:
        result = filt.apply(make_doc("exempt", text))
        assert result.action.value == "ACCEPT", text[-60:]
        assert not any(r.startswith("detected_secret:email") for r in result.reasons)

    detected = [
        f"{_distinct_prose(rng, 60)} write to user@real-domain.com please "
        f"{_distinct_prose(rng, 60)}",
        f"{_distinct_prose(rng, 60)} somebody@notreallyexample.com {_distinct_prose(rng, 60)}",
        f"{_distinct_prose(rng, 60)} somebody@my-example.com here {_distinct_prose(rng, 60)}",
        f"{_distinct_prose(rng, 60)} somebody@example.org.attacker.com here "
        f"{_distinct_prose(rng, 60)}",
    ]
    for text in detected:
        result = filt.apply(make_doc("detected", text))
        assert result.action.value == "REJECT", text[-60:]
        assert "detected_secret:email_address" in result.reasons

    combo = (
        f"{_distinct_prose(rng, 60)} placeholder@example.com plus 123-45-6789 "
        f"{_distinct_prose(rng, 60)}"
    )
    result = filt.apply(make_doc("combo", combo))
    assert result.action.value == "REJECT"
    assert "detected_secret:ssn" in result.reasons
    assert "detected_secret:email_address" not in result.reasons


def test_secret_quarantine_omits_sensitive_content(tmp_path: Path) -> None:
    rng = random.Random(5)
    token = "hf_abcdefghijklmnopqrstuvwxyz01234567"
    key = "AKIAIOSFODNN7EXAMPLE"
    lines = [
        json.dumps(
            _doc_dict(
                "q-sec-1", f"{_distinct_prose(rng, 80)} token {token} {_distinct_prose(rng, 80)}", 0
            )
        ),
        json.dumps(
            _doc_dict(
                "q-sec-2", f"{_distinct_prose(rng, 80)} key {key} {_distinct_prose(rng, 80)}", 1
            )
        ),
        json.dumps(
            _doc_dict(
                "q-sec-3",
                f"{_distinct_prose(rng, 80)} ssn 123-45-6789 {_distinct_prose(rng, 80)}",
                2,
            )
        ),
        json.dumps(_doc_dict("q-ok-1", "tiny", 3)),
    ]
    _, quarantine, _ = _reference_run(tmp_path, lines, "secrets")
    assert len(quarantine) == 4
    blob = "\n".join(json.dumps(entry) for entry in quarantine)
    assert token not in blob
    assert key not in blob
    assert "123-45-6789" not in blob
    secret_entries = [e for e in quarantine if e["is_secret_omitted"]]
    assert len(secret_entries) == 3
    assert all(e["sanitized_preview"] is None for e in secret_entries)
    plain = [e for e in quarantine if not e["is_secret_omitted"]]
    assert len(plain) == 1 and plain[0]["sanitized_preview"] is not None


def test_markdown_structural_lines_survive(tmp_path: Path) -> None:
    text = (
        "# Report\n\n---\n\nIntro prose about data processing and validation. "
        + _prose(random.Random(9), 120)
        + "\n\n| col one | col two |\n|---|---|\n| a | b |\n| c | d |\n\n---\n\n"
        + _prose(random.Random(10), 120)
        + "\n\n------------\n\n"
        + _prose(random.Random(11), 120)
    )
    lines = [json.dumps(_doc_dict("md-1", text, 0))]
    docs, _, _ = _reference_run(tmp_path, lines, "markdown")
    assert len(docs) == 1
    assert "---" in docs[0]["text"]
    assert "|---|---|" in docs[0]["text"]


# P27B unit planning and metrics merge.


def test_plan_units_cover_input_exactly_once(tmp_path: Path) -> None:
    lines = gen_mixed(300, 21)
    selected = tmp_path / "input.jsonl"
    selected.write_text(
        "\n".join(lines) + "\n\n   \n" + "\n".join(lines[:50]) + "\n", encoding="utf-8"
    )
    units, info = plan_clean_units(selected, input_shard_bytes=32 * 1024, max_docs=None)
    assert info["kind"] == "single_file"
    assert info["total_documents"] == 350
    assert sum(u.doc_count for u in units) == 350
    assert [u.index for u in units] == list(range(len(units)))
    # Ranges tile the file without gaps or overlaps.
    assert units[0].start == 0
    assert units[-1].end == selected.stat().st_size
    for first, second in zip(units, units[1:], strict=False):
        assert first.end == second.start
    # Every line belongs to exactly one unit.
    from xlm.data.cleaning.sharded import _iter_unit_lines

    seen = []
    for unit in units:
        for _, raw in _iter_unit_lines(unit):
            seen.append(raw)
    assert len(seen) == 350


def test_plan_units_max_docs_allocation(tmp_path: Path) -> None:
    lines = gen_mixed(100, 22)
    selected = tmp_path / "input.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    units, _ = plan_clean_units(selected, input_shard_bytes=8 * 1024, max_docs=30)
    assert sum(u.max_docs for u in units if u.max_docs) == 30
    # Allocations fill units in order.
    allocations = [u.max_docs or 0 for u in units]
    nonzero = [a for a in allocations if a > 0]
    assert nonzero
    first_partial = next(i for i, u in enumerate(units) if (u.max_docs or 0) < u.doc_count)
    assert all((u.max_docs or 0) == 0 for u in units[first_partial + 1 :])


def test_plan_manifest_input_validates(tmp_path: Path) -> None:
    lines = gen_mixed(60, 23)
    manifest_dir = tmp_path / "shards"
    writer = ShardedJsonlWriter(manifest_dir, dataset_id="in", target_shard_bytes=8 * 1024)
    for index, line in enumerate(lines):
        writer.write_line(line, f"doc-{index:06d}")
    manifest = writer.finish()
    units, info = plan_clean_units(manifest_dir, input_shard_bytes=8 * 1024, max_docs=None)
    assert info["kind"] == "manifest"
    assert len(units) == len(manifest.shards)
    assert sum(u.doc_count for u in units) == 60
    (manifest_dir / manifest.shards[0].path).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="byte count|SHA-256"):
        plan_clean_units(manifest_dir, input_shard_bytes=8 * 1024, max_docs=None)


def test_merge_preserves_global_reason_order() -> None:
    pipeline = create_pipeline_preset("educational_prose")
    first = {
        "index": 0,
        "stage_metrics": [
            {
                "stage_name": t.transform_id,
                "transform_id": t.transform_id,
                "transform_version": t.version,
                "input_docs": 1,
                "output_docs": 0,
                "input_bytes": 10,
                "output_bytes": 0,
                "rejected_docs": 1,
                "duration_ms": 0.5,
            }
            for t in pipeline.transforms
        ],
        "reason_counts": {"zzz_first": 2, "aaa_second": 1},
        "total_input_docs": 1,
        "total_output_docs": 0,
        "total_input_bytes": 10,
        "total_output_bytes": 0,
        "total_rejected_docs": 1,
        "is_partial_sample": False,
        "completed": True,
        "timing": {
            "parse_seconds": 0.0,
            "serialize_seconds": 0.0,
            "write_seconds": 0.0,
            "quarantine_seconds": 0.0,
            "fsync_seconds": 0.0,
            "wall_seconds": 0.0,
        },
    }
    second = {**first, "index": 1, "reason_counts": {"aaa_second": 3, "mmm_third": 1}}
    summary, _ = merge_unit_results([second, first], pipeline, None)
    assert list(summary.reason_counts) == ["zzz_first", "aaa_second", "mmm_third"]
    assert summary.reason_counts == {"zzz_first": 2, "aaa_second": 4, "mmm_third": 1}
    assert summary.declared_max_docs is None


# P27B-S equivalence: engine vs reference, workers, input shapes, shard sizes.


def test_engine_matches_reference_legacy(tmp_path: Path) -> None:
    lines = gen_mixed(400, 31)
    ref_docs, ref_q, ref_summary = _reference_run(tmp_path, lines, "ref400")
    selected = tmp_path / "ref400.jsonl"
    out, qdir, summary, _ = _engine_run(tmp_path, selected, "eng400")
    assert _read_accepted(out) == ref_docs
    assert _scrub_quarantine(_read_quarantine(qdir)) == _scrub_quarantine(ref_q)
    assert _scrub_summary(summary.to_dict()) == _scrub_summary(ref_summary.to_dict())
    assert (out / "cleaning_summary.json").is_file()
    assert (out / "cleaning_throughput.json").is_file()


def test_workers_1_2_4_agree(tmp_path: Path) -> None:
    lines = gen_mixed(600, 32)
    selected = tmp_path / "w.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    runs = []
    for workers in (1, 2, 4):
        out, qdir, summary, _ = _engine_run(tmp_path, selected, f"w{workers}", workers=workers)
        runs.append(
            (
                _read_accepted(out),
                _scrub_quarantine(_read_quarantine(qdir)),
                _scrub_summary(summary.to_dict()),
            )
        )
    assert runs[0] == runs[1] == runs[2]


def test_sharded_output_equivalence(tmp_path: Path) -> None:
    lines = gen_mixed(400, 33)
    selected = tmp_path / "s.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    legacy_out, legacy_q, legacy_summary, _ = _engine_run(tmp_path, selected, "leg")
    shard_out, shard_q, shard_summary, _ = _engine_run(
        tmp_path, selected, "sh", output_shard_bytes=32 * 1024, quarantine_shard_bytes=16 * 1024
    )
    assert _read_accepted(shard_out) == _read_accepted(legacy_out)
    assert _scrub_quarantine(_read_quarantine(shard_q)) == _scrub_quarantine(
        _read_quarantine(legacy_q)
    )
    assert _scrub_summary(shard_summary.to_dict()) == _scrub_summary(legacy_summary.to_dict())


def test_manifest_input_matches_single_file(tmp_path: Path) -> None:
    lines = gen_mixed(200, 34)
    selected = tmp_path / "m.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_dir = tmp_path / "in_shards"
    writer = ShardedJsonlWriter(manifest_dir, dataset_id="in", target_shard_bytes=16 * 1024)
    for index, line in enumerate(lines):
        writer.write_line(line, f"doc-{index:06d}")
    in_manifest = writer.finish()
    assert verify_manifest(manifest_dir, in_manifest) is in_manifest
    single_out, _, single_summary, _ = _engine_run(
        tmp_path, selected, "msingle", output_shard_bytes=16 * 1024
    )
    mani_out, _, mani_summary, _ = _engine_run(
        tmp_path, manifest_dir, "mmani", output_shard_bytes=16 * 1024
    )
    single_manifest = load_manifest(single_out / "clean-manifest.json")
    mani_manifest = load_manifest(mani_out / "clean-manifest.json")
    assert [e.to_dict() for e in mani_manifest.shards] == [
        e.to_dict() for e in single_manifest.shards
    ]
    assert mani_manifest.aggregate_sha256 == single_manifest.aggregate_sha256
    assert _read_accepted(mani_out) == _read_accepted(single_out)
    assert _scrub_summary(mani_summary.to_dict()) == _scrub_summary(single_summary.to_dict())


def test_output_shard_sizes_preserve_stream(tmp_path: Path) -> None:
    lines = gen_mixed(300, 35)
    selected = tmp_path / "z.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    streams = []
    for target_kb, tag in ((8, "a"), (64, "b"), (512, "c")):
        out, _, _, _ = _engine_run(
            tmp_path, selected, f"z{tag}", output_shard_bytes=target_kb * 1024
        )
        streams.append(_read_accepted(out))
    assert streams[0] == streams[1] == streams[2]


def test_assembly_spanning_copy_blocks(tmp_path: Path) -> None:
    # Unit temps larger than the 1 MiB assembly copy block force line
    # framing across block boundaries; every record must survive exactly once.
    lines = gen_clean_prose(500, 47, n_words=700)
    selected = tmp_path / "blocks.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert selected.stat().st_size > 2 * 1024 * 1024
    ref_docs, _, _ = _reference_run(tmp_path, lines, "blocksref")
    out, _, _, _ = _engine_run(
        tmp_path, selected, "blocks", workers=2, output_shard_bytes=256 * 1024
    )
    assert _read_accepted(out) == ref_docs


def test_max_docs_matches_reference(tmp_path: Path) -> None:
    lines = gen_mixed(200, 36)
    ref_docs, ref_q, ref_summary = _reference_run(tmp_path, lines, "cap", max_docs=50)
    assert ref_summary.is_partial_sample is True
    selected = tmp_path / "cap.jsonl"
    out, qdir, summary, _ = _engine_run(
        tmp_path, selected, "capeng", max_docs=50, input_shard_bytes=8 * 1024
    )
    assert _read_accepted(out) == ref_docs
    assert _scrub_quarantine(_read_quarantine(qdir)) == _scrub_quarantine(ref_q)
    assert summary.is_partial_sample is True
    assert _scrub_summary(summary.to_dict()) == _scrub_summary(ref_summary.to_dict())
    assert summary.declared_max_docs == 50


def test_empty_and_blank_input(tmp_path: Path) -> None:
    selected = tmp_path / "empty.jsonl"
    selected.write_text("", encoding="utf-8")
    out, qdir, summary, _ = _engine_run(tmp_path, selected, "empty")
    assert summary.total_input_docs == 0 and summary.completed is True
    assert _read_accepted(out) == []
    blank = tmp_path / "blank.jsonl"
    blank.write_text("\n   \n\t\n", encoding="utf-8")
    out2, _, summary2, _ = _engine_run(tmp_path, blank, "blank")
    assert summary2.total_input_docs == 0
    assert _read_accepted(out2) == []


def test_malformed_json_error_matches_reference(tmp_path: Path) -> None:
    lines = gen_mixed(20, 37)
    lines.insert(7, "{not valid json")
    selected = tmp_path / "bad.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 8"):
        _reference_run(tmp_path, lines, "badref")
    out = tmp_path / "badeng_out"
    with pytest.raises(ValueError, match="line 8"):
        run_sharded_clean(
            input_path=selected,
            output_dir=out,
            preset="educational_prose",
            workers=1,
            input_shard_bytes=64 * 1024,
            output_shard_bytes=None,
            quarantine_dir=tmp_path / "badeng_q",
            quarantine_shard_bytes=None,
            quarantine_policy=QuarantinePolicy(),
            max_docs=None,
            max_input_bytes=2 * 1024**3,
        )


def test_quarantine_caps_match_reference(tmp_path: Path) -> None:
    lines = gen_mixed(200, 38)
    policy = QuarantinePolicy(max_records=25, max_quarantine_bytes=1 * 1024 * 1024)
    selected = tmp_path / "caps.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mgr = QuarantineManager(tmp_path / "caps_ref_q", policy=policy)
    pipeline = create_pipeline_preset("educational_prose")
    accepted_iter, _ = pipeline.run_stream(
        CanonicalDatasetReader.read_jsonl(selected), quarantine_mgr=mgr
    )
    list(accepted_iter)
    ref_entries = [
        json.loads(line)
        for line in (tmp_path / "caps_ref_q" / "quarantine.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    out = tmp_path / "caps_out"
    run_sharded_clean(
        input_path=selected,
        output_dir=out,
        preset="educational_prose",
        workers=2,
        input_shard_bytes=16 * 1024,
        output_shard_bytes=None,
        quarantine_dir=tmp_path / "caps_q",
        quarantine_shard_bytes=None,
        quarantine_policy=policy,
        max_docs=None,
        max_input_bytes=2 * 1024**3,
    )
    eng_entries = _read_quarantine(tmp_path / "caps_q")
    assert len(eng_entries) == 25
    assert _scrub_quarantine(eng_entries) == _scrub_quarantine(ref_entries)


@pytest.mark.serial
def test_quarantine_ceiling_aborts_without_publication(tmp_path: Path) -> None:
    lines = gen_mixed(200, 39)
    policy = QuarantinePolicy(max_records=10_000, max_quarantine_bytes=1024)
    selected = tmp_path / "ceil.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mgr = QuarantineManager(tmp_path / "ceil_ref_q", policy=policy)
    pipeline = create_pipeline_preset("educational_prose")
    ref_iter, _ = pipeline.run_stream(
        CanonicalDatasetReader.read_jsonl(selected), quarantine_mgr=mgr
    )
    with pytest.raises(CleaningBudgetExhaustedError):
        list(ref_iter)
    out = tmp_path / "ceil_out"
    with pytest.raises(CleaningBudgetExhaustedError):
        run_sharded_clean(
            input_path=selected,
            output_dir=out,
            preset="educational_prose",
            workers=2,
            input_shard_bytes=16 * 1024,
            output_shard_bytes=16 * 1024,
            quarantine_dir=tmp_path / "ceil_q",
            quarantine_shard_bytes=None,
            quarantine_policy=policy,
            max_docs=None,
            max_input_bytes=2 * 1024**3,
        )
    assert not (out / "clean-manifest.json").exists()
    assert not (out / "cleaning_summary.json").exists()
    assert list(out.glob(".staging-*")) == []


@pytest.mark.serial
def test_worker_failure_aborts_without_publication(tmp_path: Path) -> None:
    lines = gen_mixed(100, 40)
    lines[60] = "{poisoned"
    selected = tmp_path / "poison.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = tmp_path / "poison_out"
    with pytest.raises(ValueError, match="line 61"):
        run_sharded_clean(
            input_path=selected,
            output_dir=out,
            preset="educational_prose",
            workers=2,
            input_shard_bytes=8 * 1024,
            output_shard_bytes=8 * 1024,
            quarantine_dir=tmp_path / "poison_q",
            quarantine_shard_bytes=None,
            quarantine_policy=QuarantinePolicy(),
            max_docs=None,
            max_input_bytes=2 * 1024**3,
        )
    assert not (out / "clean-manifest.json").exists()
    assert not (out / "cleaning_summary.json").exists()
    assert list(out.glob(".staging-*")) == []


# CLI surface.


def test_cli_clean_legacy_and_sharded(tmp_path: Path) -> None:
    lines = gen_mixed(120, 41)
    selected = tmp_path / "cli.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    runner = CliRunner()
    legacy = runner.invoke(
        data_app,
        ["clean", "--input", str(selected), "--output-dir", str(tmp_path / "cli_leg")],
    )
    assert legacy.exit_code == 0, legacy.output
    assert (tmp_path / "cli_leg" / "documents.jsonl").is_file()
    assert (tmp_path / "cli_leg" / "documents.parquet").is_file()
    assert "Clean throughput" in legacy.output
    sharded = runner.invoke(
        data_app,
        [
            "clean",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "cli_sh"),
            "--workers",
            "2",
            "--output-shard-bytes",
            "16384",
        ],
    )
    assert sharded.exit_code == 0, sharded.output
    assert (tmp_path / "cli_sh" / "clean-manifest.json").is_file()
    assert _read_accepted(tmp_path / "cli_sh") == _read_accepted(tmp_path / "cli_leg")


def test_cli_clean_manifest_input_and_errors(tmp_path: Path) -> None:
    lines = gen_mixed(80, 42)
    selected = tmp_path / "e.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_dir = tmp_path / "e_shards"
    writer = ShardedJsonlWriter(manifest_dir, dataset_id="in", target_shard_bytes=8192)
    for index, line in enumerate(lines):
        writer.write_line(line, f"doc-{index:06d}")
    writer.finish()
    runner = CliRunner()
    result = runner.invoke(
        data_app,
        [
            "clean",
            "--input",
            str(manifest_dir),
            "--output-dir",
            str(tmp_path / "e_out"),
            "--workers",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    single = runner.invoke(
        data_app,
        ["clean", "--input", str(selected), "--output-dir", str(tmp_path / "e_single")],
    )
    assert single.exit_code == 0, single.output
    assert _read_accepted(tmp_path / "e_out") == _read_accepted(tmp_path / "e_single")
    bad_preset = runner.invoke(
        data_app,
        [
            "clean",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "x"),
            "--preset",
            "nope",
        ],
    )
    assert bad_preset.exit_code == 1
    tiny_budget = runner.invoke(
        data_app,
        [
            "clean",
            "--input",
            str(selected),
            "--output-dir",
            str(tmp_path / "y"),
            "--max-input-bytes",
            "10",
        ],
    )
    assert tiny_budget.exit_code == 1
    assert "exceeds limit" in tiny_budget.output


# Benchmarks (slow): throughput tables are printed, never asserted on time.


def _benchmark_table(tag: str, rows: list[tuple]) -> None:
    print(
        f"{tag}: " + ", ".join(f"w{w}/{d}docs/{wall:.1f}s/{d / wall:.0f}dps" for w, d, wall in rows)
    )


@pytest.mark.slow
def test_benchmark_10k_mixed_workers(tmp_path: Path) -> None:
    import psutil

    lines = gen_mixed(10_000, 43)
    selected = tmp_path / "bench10k.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = []
    for workers in (1, 2, 4, 8):
        started = time.monotonic()
        out, _, summary, _ = _engine_run(tmp_path, selected, f"b10k_{workers}", workers=workers)
        wall = time.monotonic() - started
        assert summary.total_input_docs == 10_000
        assert _read_accepted(out)
        rows.append((workers, 10_000, wall))
    _benchmark_table("10k-mixed", rows)
    assert psutil.Process().memory_info().rss > 0


@pytest.mark.slow
def test_benchmark_100k_mixed_endpoints(tmp_path: Path) -> None:
    lines = gen_mixed(100_000, 46)
    selected = tmp_path / "bench100k.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    input_mib = selected.stat().st_size / (1024**2)
    rows = []
    for workers in (1, 8):
        started = time.monotonic()
        out, _, summary, throughput = _engine_run(
            tmp_path, selected, f"b100k_{workers}", workers=workers
        )
        wall = time.monotonic() - started
        assert summary.total_input_docs == 100_000
        print(
            f"100k-mixed w{workers}: in={input_mib:.0f}MiB wall={wall:.1f}s "
            f"docs/s={100_000 / wall:.0f} out_shards={throughput['output_shards']}"
        )
        rows.append((workers, 100_000, wall))
    _benchmark_table("100k-mixed", rows)


@pytest.mark.slow
def test_benchmark_128mib_sharded_workers(tmp_path: Path) -> None:
    import psutil

    lines = gen_clean_prose(4000, 44, n_words=4100)
    selected = tmp_path / "bench128.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    input_mib = selected.stat().st_size / (1024**2)
    assert 120 < input_mib < 160, f"input {input_mib:.1f} MiB outside 128 MiB band"
    rows = []
    for workers in (1, 2, 4, 8):
        process = psutil.Process()
        rss_before = process.memory_info().rss
        started = time.monotonic()
        out, _, summary, throughput = _engine_run(
            tmp_path,
            selected,
            f"b128_{workers}",
            workers=workers,
            output_shard_bytes=32 * 1024**2,
            input_shard_bytes=32 * 1024**2,
        )
        wall = time.monotonic() - started
        rss_growth = (process.memory_info().rss - rss_before) / (1024**2)
        assert summary.total_input_docs == 4000
        print(
            f"128MiB w{workers}: wall={wall:.1f}s docs/s={4000 / wall:.0f} "
            f"rss_growth={rss_growth:.1f}MiB shards={throughput['output_shards']}"
        )
        rows.append((workers, 4000, wall))
    _benchmark_table("128MiB", rows)


@pytest.mark.slow
def test_memory_bound_heavy_docs(tmp_path: Path) -> None:
    import psutil

    rng = random.Random(45)
    lines = [
        json.dumps(_doc_dict(f"heavy-{i:04d}", _prose(rng, 8000) + " tail text here", i))
        for i in range(200)
    ]
    selected = tmp_path / "heavy.jsonl"
    selected.write_text("\n".join(lines) + "\n", encoding="utf-8")
    process = psutil.Process()
    baseline = process.memory_info().rss
    out, _, summary, _ = _engine_run(tmp_path, selected, "heavy", workers=2)
    assert summary.total_input_docs == 200
    growth_mib = (process.memory_info().rss - baseline) / (1024**2)
    print(f"heavy-docs: rss_growth={growth_mib:.1f}MiB for 200x50KB docs, 2 workers")
    assert growth_mib < 2048
