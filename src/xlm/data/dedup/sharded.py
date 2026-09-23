"""Deterministic sharded and parallel deduplication execution (P28-B/E/K).

Architecture::

    clean input (single file ranges, verified manifest shards, plain-dir
    files, Parquet) with deterministic global ordinals
        |
    worker processes (or one in-process worker): each streams its units,
    computes the match view once per document, and writes deterministic
    per-unit signature shards (facts JSONL + fixed-width signature binary)
        |
    parent consumes units in global order: exact/band index adds, ordinal
    bookkeeping, then the unchanged pass-2 logic over ordinal-packed
    candidate pairs, verified in chunks
        |
    survivors stream through a second input read into legacy single-file
    or deterministic target-sized shards.

Determinism contract:

- Global document order is always unit order; worker timing never affects
  a byte. Union-find connectivity, cluster IDs, survivor selection, and
  every counter are identical for any worker count or unit layout.
- Candidate pairs are packed ordinal ints sorted numerically. The packed
  order differs textually from lexicographic doc-id order, but union
  connectivity, pair counts, and match-kind sets are order-independent,
  so results are exactly the reference results (proven by AC tests).
- No document crosses a process boundary: workers receive paths, byte
  ranges, and ordinal bases only.
- One worker failure aborts publication: staging removed, no report.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import struct
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xlm.core.contracts import CanonicalDocument
from xlm.data.canonical_io import CanonicalDatasetReader

# Unit planning and byte-range line framing are shared with P27B cleaning:
# the planner is input-shape logic (file ranges, verified manifests), not
# cleaning policy. _iter_unit_lines is reused rather than reimplemented so
# framing bugs cannot diverge between the two engines.
from xlm.data.cleaning.sharded import CleanUnit, _iter_unit_lines, plan_clean_units
from xlm.data.datasets.shards import ShardedJsonlWriter, ShardManifest, verify_manifest
from xlm.data.dedup.clusters import (
    DocumentFacts,
    UnionFind,
    build_clusters,
)
from xlm.data.dedup.engine import DedupConfig, DedupResult, DedupStats
from xlm.data.dedup.index import PartitionedKeyIndex
from xlm.data.dedup.lineage import lineage_key
from xlm.data.dedup.matchview import match_normalize
from xlm.data.dedup.minhash import MinHasher, estimated_jaccard
from xlm.data.normalization import compute_sha256

DEDUP_MANIFEST_FILENAME = "dataset-manifest.json"

_SIG_RECORD_HEADER = struct.Struct("<I")
_COPY_CHUNK_BYTES = 1024 * 1024


@dataclass
class ShardedDedupTelemetry:
    """Phase timings for one sharded dedup run (telemetry only, never identity)."""

    input_scan_seconds: float = 0.0
    normalize_seconds: float = 0.0
    minhash_seconds: float = 0.0
    index_add_seconds: float = 0.0
    exact_seconds: float = 0.0
    candidate_seconds: float = 0.0
    verify_seconds: float = 0.0
    cluster_seconds: float = 0.0
    serialize_seconds: float = 0.0
    wall_seconds: float = 0.0
    peak_rss_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_scan_seconds": self.input_scan_seconds,
            "normalize_seconds": self.normalize_seconds,
            "minhash_seconds": self.minhash_seconds,
            "index_add_seconds": self.index_add_seconds,
            "exact_seconds": self.exact_seconds,
            "candidate_seconds": self.candidate_seconds,
            "verify_seconds": self.verify_seconds,
            "cluster_seconds": self.cluster_seconds,
            "serialize_seconds": self.serialize_seconds,
            "wall_seconds": self.wall_seconds,
            "peak_rss_bytes": self.peak_rss_bytes,
        }


def _sig_record_size(num_permutations: int) -> int:
    return _SIG_RECORD_HEADER.size + 8 * num_permutations


def _iter_unit_documents(unit: CleanUnit) -> Iterator[tuple[int, CanonicalDocument]]:
    """Yield ``(local_position, document)`` for one unit, streaming."""
    if unit.kind == "parquet":
        for position, doc in enumerate(CanonicalDatasetReader.read_parquet(Path(unit.path))):
            yield position, doc
        return
    for position, (_line_no, raw) in enumerate(_iter_unit_lines(unit)):
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in {unit.path}: {exc}") from exc
        try:
            yield position, CanonicalDocument(**data)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid canonical record in {unit.path}: {exc}") from exc


def _process_dedup_unit(
    hasher: MinHasher,
    unit: CleanUnit,
    ordinal_base: int,
    staging_dir: Path,
) -> dict[str, Any]:
    """Compute one unit's signature shard; return local metrics."""
    facts_path = staging_dir / f"unit-{unit.index:05d}.facts.jsonl"
    sigs_path = staging_dir / f"unit-{unit.index:05d}.sigs.bin"
    normalize_seconds = 0.0
    minhash_seconds = 0.0
    doc_count = 0
    with (
        facts_path.open("w", encoding="utf-8") as facts_handle,
        sigs_path.open("wb") as sigs_handle,
    ):
        for position, doc in _iter_unit_documents(unit):
            ordinal = ordinal_base + position
            started = time.monotonic()
            match_view = match_normalize(doc.text)
            exact_key = compute_sha256(match_view)
            tokens = match_view.split(" ") if match_view else []
            normalize_seconds += time.monotonic() - started
            started = time.monotonic()
            signature = hasher.signature_from_tokens(tokens)
            band_keys = hasher.band_keys(signature)
            minhash_seconds += time.monotonic() - started
            rule, lineage = lineage_key(doc)
            facts_handle.write(
                json.dumps(
                    {
                        "ordinal": ordinal,
                        "doc_id": doc.doc_id,
                        "source_id": doc.source_id,
                        "clean_hash": doc.clean_hash,
                        "utf8_byte_count": doc.utf8_byte_count,
                        "lineage_key": lineage,
                        "lineage_rule": rule,
                        "exact_key": exact_key,
                        "band_keys": band_keys,
                    }
                )
                + "\n"
            )
            sigs_handle.write(_SIG_RECORD_HEADER.pack(ordinal))
            sigs_handle.write(struct.pack(f"<{len(signature)}Q", *signature))
            doc_count += 1
    return {
        "index": unit.index,
        "doc_count": doc_count,
        "normalize_seconds": normalize_seconds,
        "minhash_seconds": minhash_seconds,
    }


_WORKER_STATE: tuple[str, MinHasher] | None = None


def _init_dedup_worker(minhash_config: dict[str, Any]) -> None:
    """Pool initializer: build the MinHasher once per worker process."""
    global _WORKER_STATE
    import os

    # One thread per worker: the kernel is memory-bound elementwise work,
    # and per-worker BLAS/thread pools would oversubscribe the machine.
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    from xlm.data.dedup.minhash import MinHashConfig

    config = MinHashConfig(**minhash_config)
    _WORKER_STATE = (config.identity(), MinHasher(config))


def dedup_unit_worker(spec: dict[str, Any]) -> dict[str, Any]:
    """Process one worker's dedup units (module-level so spawn can pickle it)."""
    global _WORKER_STATE
    from xlm.data.cleaning.sharded import CleanUnit as _CleanUnit
    from xlm.data.dedup.minhash import MinHashConfig

    config = MinHashConfig(**spec["minhash_config"])
    if _WORKER_STATE is None or _WORKER_STATE[0] != config.identity():
        _WORKER_STATE = (config.identity(), MinHasher(config))
    _, hasher = _WORKER_STATE
    staging_dir = Path(spec["staging_dir"])
    results = []
    for unit_spec in spec["units"]:
        unit = _CleanUnit(**unit_spec["unit"])
        results.append(_process_dedup_unit(hasher, unit, unit_spec["ordinal_base"], staging_dir))
    return {"units": results}


@dataclass
class AssembledDedup:
    """Filenames published by :func:`run_sharded_dedup`."""

    survivor_files: list[str] = field(default_factory=list)
    manifest_file: str | None = None
    report_file: str = "dedup_report.json"
    throughput_file: str = "dedup_throughput.json"


def _batched_jaccard(
    signatures: dict[int, list[int]], pairs: list[tuple[int, int]], threshold: float
) -> list[bool]:
    """Confirm pairs in chunks (NumPy integer equality when available)."""
    if not pairs:
        return []
    try:
        import numpy as np  # type: ignore[import-not-found]

        perms = len(next(iter(signatures.values())))
        order = sorted(signatures)
        position = {ordinal: i for i, ordinal in enumerate(order)}
        matrix = np.array([signatures[o] for o in order], dtype=np.uint64)
        confirmed: list[bool] = []
        chunk_size = 4096
        for start in range(0, len(pairs), chunk_size):
            chunk = pairs[start : start + chunk_size]
            left = matrix[[position[a] for a, _ in chunk]]
            right = matrix[[position[b] for _, b in chunk]]
            agreements = (left == right).sum(axis=1)
            # Elementwise IEEE division, identical to the scalar reference.
            confirmed.extend(bool(v) for v in (agreements / perms >= threshold).tolist())
        return confirmed
    except ImportError:
        return [estimated_jaccard(signatures[a], signatures[b]) >= threshold for a, b in pairs]


def run_sharded_dedup(
    *,
    input_path: Path,
    output_dir: Path,
    config: DedupConfig,
    workers: int,
    input_shard_bytes: int,
    output_shard_bytes: int | None,
    work_dir: Path,
    max_input_bytes: int,
) -> tuple[
    DedupResult, ShardedDedupTelemetry, AssembledDedup, dict[str, Any], ShardManifest | None
]:
    """Execute deterministic sharded dedup; publish nothing on failure."""
    if workers < 1:
        raise ValueError(f"workers must be positive, got {workers}")
    telemetry = ShardedDedupTelemetry()
    wall_start = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    scan_start = time.monotonic()
    units, input_info = plan_clean_units(
        input_path, input_shard_bytes=input_shard_bytes, max_docs=None
    )
    telemetry.input_scan_seconds = time.monotonic() - scan_start
    if input_info["total_bytes"] > max_input_bytes:
        raise ValueError(
            f"dedup input exceeds limit ({input_info['total_bytes']:,} > "
            f"{max_input_bytes:,} bytes); refusing an unbounded job."
        )

    # Deterministic global ordinals: units in order, docs sequential within.
    ordinal_bases: list[int] = []
    cursor = 0
    for unit in units:
        ordinal_bases.append(cursor)
        cursor += unit.doc_count

    staging_dir = work_dir / f".staging-{uuid.uuid4().hex}"
    staging_dir.mkdir(parents=True, exist_ok=False)
    minhash_payload = config.minhash.model_dump(mode="json")

    def worker_spec(chunk: list[tuple[CleanUnit, int]]) -> dict[str, Any]:
        return {
            "minhash_config": minhash_payload,
            "units": [
                {
                    "unit": {
                        "index": unit.index,
                        "kind": unit.kind,
                        "path": unit.path,
                        "start": unit.start,
                        "end": unit.end,
                        "doc_count": unit.doc_count,
                        "lines_before": unit.lines_before,
                        "max_docs": None,
                    },
                    "ordinal_base": base,
                }
                for unit, base in chunk
            ],
            "staging_dir": str(staging_dir),
        }

    unit_results: list[dict[str, Any]] = []
    try:
        indexed = list(zip(units, ordinal_bases, strict=True))
        if workers == 1 or len(indexed) <= 1:
            worker_out = dedup_unit_worker(worker_spec(indexed))
            for unit_result in worker_out["units"]:
                unit_results.append(unit_result)
        else:
            chunks = [indexed[i::workers] for i in range(workers)]
            chunks = [chunk for chunk in chunks if chunk]
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=workers,
                initializer=_init_dedup_worker,
                initargs=(minhash_payload,),
            ) as pool:
                future_map = {
                    pool.submit(dedup_unit_worker, worker_spec(chunk)): chunk for chunk in chunks
                }
                try:
                    for future in future_map:
                        worker_out = future.result()
                        unit_results.extend(worker_out["units"])
                except BaseException:
                    for future in future_map:
                        future.cancel()
                    raise
        for unit_result in unit_results:
            telemetry.normalize_seconds += unit_result["normalize_seconds"]
            telemetry.minhash_seconds += unit_result["minhash_seconds"]

        stats = DedupStats()
        facts: dict[str, DocumentFacts] = {}
        ordinal_of: dict[str, int] = {}
        id_of_ordinal: dict[int, str] = {}
        match_kinds: dict[str, set[str]] = {}
        lineage_members: dict[str, list[str]] = {}
        union = UnionFind()

        exact_index = PartitionedKeyIndex(work_dir / "exact", config.partition_count)
        band_index = PartitionedKeyIndex(work_dir / "bands", config.partition_count)

        index_start = time.monotonic()
        with exact_index, band_index:
            for unit, _base in indexed:
                facts_path = staging_dir / f"unit-{unit.index:05d}.facts.jsonl"
                with facts_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        record = json.loads(stripped)
                        doc_id = record["doc_id"]
                        stats.documents_seen += 1
                        facts[doc_id] = DocumentFacts(
                            doc_id=doc_id,
                            source_id=record["source_id"],
                            clean_hash=record["clean_hash"],
                            utf8_byte_count=record["utf8_byte_count"],
                            lineage_key=record["lineage_key"],
                            lineage_rule=record["lineage_rule"],
                        )
                        ordinal_of[doc_id] = record["ordinal"]
                        id_of_ordinal[record["ordinal"]] = doc_id
                        lineage_members.setdefault(record["lineage_key"], []).append(doc_id)
                        union.add(doc_id)
                        exact_index.add(record["exact_key"], doc_id)
                        for band_key in record["band_keys"]:
                            band_index.add(band_key, doc_id)
        telemetry.index_add_seconds = time.monotonic() - index_start
        stats.index_entries_written = exact_index.entries_written + band_index.entries_written

        # --- Exact duplicates, one index partition at a time. ---
        exact_start = time.monotonic()
        for _key, doc_ids in exact_index.groups(min_size=2):
            for other in doc_ids[1:]:
                union.union(doc_ids[0], other)
                stats.exact_duplicate_pairs += 1
                match_kinds.setdefault(other, set()).add("exact")
            match_kinds.setdefault(doc_ids[0], set()).add("exact")
        telemetry.exact_seconds = time.monotonic() - exact_start

        # --- Bounded near-duplicate verification over ordinal-packed pairs. ---
        verify_start = time.monotonic()
        candidate_pairs: set[int] = set()
        per_doc_budget: dict[int, int] = {}
        budget = config.max_candidates_per_document
        bucket_count = 0
        if config.enable_near_duplicates:
            candidate_start = time.monotonic()
            for _band_key, doc_ids in band_index.groups(min_size=2):
                bucket_count += 1
                if len(doc_ids) > config.max_bucket_size:
                    stats.oversized_buckets += 1
                    continue
                ordinals = sorted(ordinal_of[doc_id] for doc_id in doc_ids)
                for i, left in enumerate(ordinals):
                    for right in ordinals[i + 1 :]:
                        packed = (left << 32) | right
                        if packed in candidate_pairs:
                            continue
                        if per_doc_budget.get(left, 0) >= budget or (
                            per_doc_budget.get(right, 0) >= budget
                        ):
                            stats.candidate_pairs_skipped_budget += 1
                            continue
                        candidate_pairs.add(packed)
                        per_doc_budget[left] = per_doc_budget.get(left, 0) + 1
                        per_doc_budget[right] = per_doc_budget.get(right, 0) + 1
            telemetry.candidate_seconds = time.monotonic() - candidate_start

            ordered_pairs = sorted(candidate_pairs)
            needed = {pack >> 32 for pack in ordered_pairs} | {
                pack & 0xFFFFFFFF for pack in ordered_pairs
            }
            signatures = _load_signatures_by_ordinal(
                staging_dir, units, needed, config.minhash.num_permutations
            )
            stats.signatures_retained = len(signatures)
            threshold = config.minhash.jaccard_threshold
            pair_list = [(pack >> 32, pack & 0xFFFFFFFF) for pack in ordered_pairs]
            confirmed = _batched_jaccard(signatures, pair_list, threshold)
            for (left_ord, right_ord), is_dup in zip(pair_list, confirmed, strict=True):
                stats.near_candidate_pairs_considered += 1
                if is_dup:
                    left_id = id_of_ordinal[left_ord]
                    right_id = id_of_ordinal[right_ord]
                    union.union(left_id, right_id)
                    stats.near_duplicate_pairs_confirmed += 1
                    match_kinds.setdefault(left_id, set()).add("near")
                    match_kinds.setdefault(right_id, set()).add("near")
        telemetry.verify_seconds = time.monotonic() - verify_start

        cluster_start = time.monotonic()
        clusters = build_clusters(union, facts, match_kinds)
        dropped = sorted({d for c in clusters for d in c.dropped_doc_ids})
        dropped_set = set(dropped)
        survivors = sorted(doc_id for doc_id in facts if doc_id not in dropped_set)
        telemetry.cluster_seconds = time.monotonic() - cluster_start

        result = DedupResult(
            config_identity=config.identity(),
            clusters=clusters,
            lineage_groups={k: sorted(v) for k, v in sorted(lineage_members.items())},
            stats=stats,
            survivor_doc_ids=survivors,
            dropped_doc_ids=dropped,
        )

        # --- Survivors stream through a second input read (no materialization). ---
        serialize_start = time.monotonic()
        survivor_set = dropped_set
        assembled = AssembledDedup()
        if output_shard_bytes is not None:
            writer = ShardedJsonlWriter(
                output_dir,
                dataset_id=f"dedup_{config.identity()[:12]}",
                target_shard_bytes=output_shard_bytes,
                source_artifact={"input_kind": input_info["kind"]},
                producer={"tool": "xlm-data-dedup", "workers": workers},
            )
            for doc in _iter_survivors(input_path, units, survivor_set, result):
                payload = json.dumps(doc.to_dict(), ensure_ascii=False)
                writer.write_line(payload, doc.doc_id)
            manifest = writer.finish()
            verify_manifest(output_dir, manifest)
            assembled.survivor_files = [entry.path for entry in manifest.shards]
            assembled.manifest_file = "dataset-manifest.json"
        else:
            manifest = None
            legacy_tmp = staging_dir / "documents.jsonl.tmp"
            with legacy_tmp.open("wb") as legacy_handle:
                for doc in _iter_survivors(input_path, units, survivor_set, result):
                    line_bytes = (json.dumps(doc.to_dict(), ensure_ascii=False) + "\n").encode()
                    legacy_handle.write(line_bytes)
                legacy_handle.flush()
                os.fsync(legacy_handle.fileno())
            os.replace(legacy_tmp, output_dir / "documents.jsonl")
            assembled.survivor_files = ["documents.jsonl"]
        telemetry.serialize_seconds = time.monotonic() - serialize_start
        telemetry.wall_seconds = time.monotonic() - wall_start
        telemetry.peak_rss_bytes = _peak_rss()

        throughput = {
            "documents": stats.documents_seen,
            "exact_duplicate_pairs": stats.exact_duplicate_pairs,
            "signatures_per_second": (stats.documents_seen / max(1e-9, telemetry.minhash_seconds)),
            "lsh_buckets": bucket_count,
            "candidate_pairs": len(candidate_pairs),
            "verified_pairs": stats.near_candidate_pairs_considered,
            "confirmed_pairs": stats.near_duplicate_pairs_confirmed,
            "clusters": len(clusters),
            "survivors": len(survivors),
            "workers": workers,
            "input_shards": input_info["shards"],
            "input_kind": input_info["kind"],
            "output_shards": len(assembled.survivor_files),
            "wall_seconds": telemetry.wall_seconds,
            "peak_rss_bytes": telemetry.peak_rss_bytes,
            "phases": telemetry.to_dict(),
        }
        report_path = output_dir / assembled.report_file
        report_stage = staging_dir / "dedup_report.json.tmp"
        report_stage.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        os.replace(report_stage, report_path)
        throughput_path = output_dir / assembled.throughput_file
        throughput_stage = staging_dir / "dedup_throughput.json.tmp"
        throughput_stage.write_text(
            json.dumps(throughput, indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(throughput_stage, throughput_path)

        shutil.rmtree(staging_dir, ignore_errors=True)
        return result, telemetry, assembled, throughput, manifest
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def _load_signatures_by_ordinal(
    staging_dir: Path, units: list[CleanUnit], needed: set[int], num_permutations: int
) -> dict[int, list[int]]:
    """Load only needed signatures by seeking to ordinal offsets (no full scan)."""
    record_size = _sig_record_size(num_permutations)
    by_unit: dict[int, list[int]] = {}
    for ordinal in needed:
        by_unit.setdefault(_unit_for_ordinal(units, ordinal), []).append(ordinal)
    signatures: dict[int, list[int]] = {}
    for unit_index, ordinals in by_unit.items():
        path = staging_dir / f"unit-{unit_index:05d}.sigs.bin"
        base = _unit_ordinal_base(units, unit_index)
        with path.open("rb") as handle:
            for ordinal in sorted(ordinals):
                handle.seek((ordinal - base) * record_size)
                record = handle.read(record_size)
                if len(record) != record_size:
                    raise ValueError(f"signature shard {path} is truncated; refusing to read")
                stored = _SIG_RECORD_HEADER.unpack(record[: _SIG_RECORD_HEADER.size])[0]
                if stored != ordinal:
                    raise ValueError(
                        f"signature shard {path} misaligned at ordinal {ordinal}; refusing to read"
                    )
                values = list(
                    struct.unpack_from(
                        f"<{num_permutations}Q", record, _SIG_RECORD_HEADER.size
                    )
                )
                signatures[ordinal] = values
    return signatures


def _unit_ordinal_base(units: list[CleanUnit], index: int) -> int:
    base = 0
    for unit in units:
        if unit.index == index:
            return base
        base += unit.doc_count
    raise ValueError(f"unknown unit index {index}")


def _unit_for_ordinal(units: list[CleanUnit], ordinal: int) -> int:
    base = 0
    for unit in units:
        if base <= ordinal < base + unit.doc_count:
            return unit.index
        base += unit.doc_count
    raise ValueError(f"ordinal {ordinal} outside planned units")


def _iter_survivors(
    input_path: Path,
    units: list[CleanUnit],
    dropped: set[str],
    result: DedupResult,
) -> Iterator[CanonicalDocument]:
    """Stream surviving documents in global input order with cluster annotation."""
    from xlm.data.dedup.engine import iter_surviving_documents

    def documents() -> Iterator[CanonicalDocument]:
        for unit in units:
            for _position, doc in _iter_unit_documents(unit):
                yield doc

    yield from iter_surviving_documents(documents(), result)


def _peak_rss() -> int:
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except Exception:
        return 0
