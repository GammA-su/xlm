"""Acceptance tests for P09 streaming, shard fan-in, retention and invalidation.

These cover the prompt-09 criteria that concern pipeline scale and lifecycle rather
than the behaviour of any single transform: bounded-memory processing over many
fixture shards, version/cache invalidation, quarantine retention and access
controls, and measurable yield with concrete rejection examples.
"""

from __future__ import annotations

import gc
import json
import weakref
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from xlm.core.contracts import CanonicalDocument
from xlm.data.canonical_io import CanonicalDatasetReader, CanonicalDatasetWriter
from xlm.data.cleaning.length_noise import LengthConfig, LengthFilter
from xlm.data.cleaning.normalization import CanonicalNormalizationTransform
from xlm.data.cleaning.pipeline import CleaningPipeline, create_pipeline_preset
from xlm.data.cleaning.quarantine import QuarantineManager, QuarantinePolicy
from xlm.data.normalization import compute_sha256

FIXTURE_SHARDS = Path(__file__).resolve().parents[1] / "fixtures" / "cleaning" / "shards"
EXPECTED_SHARD_COUNT = 6
EXPECTED_DOC_COUNT = 60


def make_doc(doc_id: str, text: str, shard: int = 0, row: int = 0) -> CanonicalDocument:
    b = text.encode("utf-8")
    return CanonicalDocument(
        doc_id=doc_id,
        source_id="fixture_cleaning",
        source_revision="rev_fixture_1",
        source_file=f"shard_{shard:02d}.jsonl",
        source_row=row,
        raw_hash=compute_sha256(b),
        clean_hash=compute_sha256(b),
        text=text,
        utf8_byte_count=len(b),
        language="en",
        language_confidence=1.0,
        document_kind="prose",
        source_metadata={},
        parent_ids=[],
        license_reference="cc-by-4.0",
        transform_log=[],
        quality_reasons=[],
        cluster_ids={},
        split="train",
    )


def test_fixture_corpus_is_present_and_multi_shard() -> None:
    """The bounded-memory acceptance evidence needs a genuinely multi-shard corpus."""
    shards = sorted(FIXTURE_SHARDS.glob("*.jsonl"))
    assert len(shards) == EXPECTED_SHARD_COUNT, f"expected {EXPECTED_SHARD_COUNT} shards"
    total = sum(1 for _ in CanonicalDatasetReader.read_shards(FIXTURE_SHARDS))
    assert total == EXPECTED_DOC_COUNT


def test_read_shards_streams_every_shard_not_just_the_first() -> None:
    """Directory fan-in must cover all shards; reading only the first loses data silently."""
    doc_ids = [d.doc_id for d in CanonicalDatasetReader.read_shards(FIXTURE_SHARDS)]
    seen_shards = {doc_id.split("_")[0] for doc_id in doc_ids}
    assert seen_shards == {f"s{i:02d}" for i in range(EXPECTED_SHARD_COUNT)}
    assert len(doc_ids) == len(set(doc_ids)), "shard fan-in produced duplicate doc_ids"
    # Stable sorted order, so replaying the same directory is deterministic.
    assert doc_ids == sorted(doc_ids)


def test_pipeline_does_not_retain_emitted_documents_mid_stream() -> None:
    """Emitted documents must be released *while* the stream is still running.

    Peak memory is what matters, so the check has to happen mid-stream. Measuring
    after exhaustion would not catch an accumulator: CPython's list iterator drops
    its backing list once exhausted, so a fully materialized run looks clean at the
    end while still having held the whole corpus at its peak.
    """
    pipeline = create_pipeline_preset("educational_prose")
    accepted_iter, summary = pipeline.run_stream(CanonicalDatasetReader.read_shards(FIXTURE_SHARDS))

    probe_count = 20
    early_window = 10
    refs: list[weakref.ReferenceType[CanonicalDocument]] = []
    for doc in accepted_iter:
        refs.append(weakref.ref(doc))
        del doc  # drop the only caller-side reference immediately
        if len(refs) >= probe_count:
            break

    assert len(refs) == probe_count, "fixture must emit enough documents to probe"
    assert summary.completed is False, "stream should still be open at the probe point"

    gc.collect()
    early_alive = [r for r in refs[:early_window] if r() is not None]
    assert not early_alive, (
        f"{len(early_alive)} of the first {early_window} emitted documents were still "
        "alive while the stream was open; the pipeline is accumulating its output"
    )


def test_pipeline_is_lazy_and_does_not_drain_its_input_upfront() -> None:
    """Producing the first output must not require reading the whole corpus."""
    reads = 0

    def counting_source() -> Iterator[CanonicalDocument]:
        nonlocal reads
        for doc in CanonicalDatasetReader.read_shards(FIXTURE_SHARDS):
            reads += 1
            yield doc

    pipeline = create_pipeline_preset("educational_prose")
    accepted_iter, _summary = pipeline.run_stream(counting_source())

    assert reads == 0, "run_stream consumed input before the iterator was advanced"
    next(accepted_iter)
    assert 0 < reads < EXPECTED_DOC_COUNT, (
        f"first output required reading {reads} of {EXPECTED_DOC_COUNT} documents; "
        "the pipeline is not lazy"
    )


def test_summary_is_not_final_until_iterator_is_consumed() -> None:
    """A partially consumed run must not masquerade as a finished one."""
    pipeline = create_pipeline_preset("educational_prose")
    accepted_iter, summary = pipeline.run_stream(CanonicalDatasetReader.read_shards(FIXTURE_SHARDS))
    assert summary.completed is False
    assert summary.total_input_docs == 0

    next(accepted_iter)
    assert summary.completed is False, "summary marked complete mid-stream"

    for _ in accepted_iter:
        pass
    assert summary.completed is True
    assert summary.total_input_docs == EXPECTED_DOC_COUNT


def test_measured_yield_and_rejection_reasons_on_fixture_corpus() -> None:
    """Record concrete, measurable yield and rejection reasons from the fixture corpus."""
    pipeline = create_pipeline_preset("educational_prose")
    accepted_iter, summary = pipeline.run_stream(CanonicalDatasetReader.read_shards(FIXTURE_SHARDS))
    accepted = list(accepted_iter)

    assert summary.total_input_docs == EXPECTED_DOC_COUNT
    assert summary.total_output_docs == len(accepted)
    assert summary.total_rejected_docs > 0, "fixture must exercise rejection paths"
    assert 0.0 < summary.document_yield_ratio < 1.0
    assert 0.0 < summary.byte_yield_ratio < 1.0

    # The fixture plants one repetition-loop and one secret-bearing document per shard.
    assert summary.total_rejected_docs == 2 * EXPECTED_SHARD_COUNT

    # Documents that must survive: math notation, code indentation, short dense prose.
    texts = [d.text for d in accepted]
    assert any("n^2 > 9" in t and "a<b<c" in t for t in texts), "math notation was stripped"
    assert any("    total = 0" in t for t in texts), "code indentation was lost"
    # Below the 100-byte prose floor, but rescued by the educational-density exemption.
    short_dense = [t for t in texts if t.startswith("Definition: entropy")]
    assert short_dense, "valid short high-density educational text was discarded"
    assert len(short_dense[0].encode("utf-8")) < 100


def test_filter_config_change_invalidates_pipeline_and_artifact_identity() -> None:
    """A changed filter threshold must invalidate downstream artifact identity.

    Contract C01 and ARCHITECTURE: a behavioral change must produce a different
    logical artifact key, so a cached clean pool cannot be reused across it.
    """
    baseline = CleaningPipeline(
        [CanonicalNormalizationTransform(), LengthFilter(LengthConfig(min_bytes=50, min_words=5))],
        domain_preset="educational_prose",
    )
    changed_threshold = CleaningPipeline(
        [CanonicalNormalizationTransform(), LengthFilter(LengthConfig(min_bytes=400, min_words=5))],
        domain_preset="educational_prose",
    )
    changed_preset = CleaningPipeline(
        [CanonicalNormalizationTransform(), LengthFilter(LengthConfig(min_bytes=50, min_words=5))],
        domain_preset="code",
    )

    base_hash = baseline.compute_pipeline_hash()
    assert base_hash != changed_threshold.compute_pipeline_hash(), (
        "changing a filter threshold did not change the pipeline hash"
    )
    assert base_hash != changed_preset.compute_pipeline_hash(), (
        "changing the domain preset did not change the pipeline hash"
    )

    # The same definition rebuilt from scratch must be stable across instances.
    rebuilt = CleaningPipeline(
        [CanonicalNormalizationTransform(), LengthFilter(LengthConfig(min_bytes=50, min_words=5))],
        domain_preset="educational_prose",
    )
    assert rebuilt.compute_pipeline_hash() == base_hash

    # The changed threshold must also change observable behaviour, not just the hash.
    # Sized to clear the shared word floor so only the byte threshold differs.
    doc = make_doc(
        "cache_probe",
        "A moderately short paragraph about numerical optimization and the way "
        "step sizes interact with curvature during training.",
    )
    assert baseline.transforms[1].apply(doc).action.value == "ACCEPT"
    assert changed_threshold.transforms[1].apply(doc).action.value == "REJECT"


def test_quarantine_retention_purges_expired_records(tmp_path: Path) -> None:
    """The retention window is configurable and actually enforced."""
    q_dir = tmp_path / "quarantine"
    mgr = QuarantineManager(q_dir, policy=QuarantinePolicy(retention_days=7))

    for i in range(3):
        mgr.record_rejection(
            doc=make_doc(f"old_{i}", "Low quality filler text used for retention testing."),
            reasons=["length_below_minimum"],
            stage_name="length_filter",
        )

    q_file = q_dir / "quarantine.jsonl"
    entries = [json.loads(line) for line in q_file.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 3
    assert all("recorded_at" in e for e in entries)

    # Nothing is expired yet.
    assert mgr.purge_expired() == 0

    # Evaluated 30 days later, every record is beyond the 7-day window.
    future = datetime.now(UTC) + timedelta(days=30)
    assert mgr.purge_expired(now=future) == 3
    assert q_file.read_text(encoding="utf-8").strip() == ""
    assert mgr.recorded_count == 0


def test_quarantine_retention_disabled_keeps_records(tmp_path: Path) -> None:
    """retention_days=None means indefinite retention, not accidental deletion."""
    mgr = QuarantineManager(tmp_path / "q", policy=QuarantinePolicy(retention_days=None))
    mgr.record_rejection(
        doc=make_doc("keep_me", "Filler text retained because retention is disabled."),
        reasons=["length_below_minimum"],
        stage_name="length_filter",
    )
    assert mgr.purge_expired(now=datetime.now(UTC) + timedelta(days=3650)) == 0
    assert mgr.recorded_count == 1


def test_quarantine_access_control_suppresses_all_previews(tmp_path: Path) -> None:
    """store_previews=False withholds content from every quarantined record."""
    q_dir = tmp_path / "q"
    mgr = QuarantineManager(q_dir, policy=QuarantinePolicy(store_previews=False))
    distinctive = "Distinctive rejected sentence that must not be readable in quarantine."
    mgr.record_rejection(
        doc=make_doc("no_preview", distinctive),
        reasons=["length_below_minimum"],
        stage_name="length_filter",
    )
    content = (q_dir / "quarantine.jsonl").read_text(encoding="utf-8")
    assert distinctive not in content
    assert json.loads(content.strip())["sanitized_preview"] is None


def test_streaming_parquet_round_trip_is_lossless(tmp_path: Path) -> None:
    """Bounded row-group writes must preserve every C02 field exactly."""
    pytest.importorskip("pyarrow")
    docs = [
        make_doc(f"rt_{i}", f"Round trip document number {i} with UTF-8: cafe 数学 rocket.")
        for i in range(25)
    ]
    writer = CanonicalDatasetWriter(tmp_path)
    path = writer.write_parquet_stream(iter(docs), filename="rt.parquet", row_group_size=4)

    restored = list(CanonicalDatasetReader.read_parquet(path, batch_size=3))
    assert len(restored) == len(docs)
    for original, loaded in zip(docs, restored, strict=True):
        assert loaded.to_dict() == original.to_dict()
