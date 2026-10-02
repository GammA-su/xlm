"""Bounded whole-shard tokenization, with optional exact C07 single-shard assembly.

Each process owns a fixed lane of complete canonical shards and initializes its
tokenizer once. Only paths, limits and small manifests cross process queues.
There is no process-global tokenizer or mutable experiment state.
"""

from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import os
import re
import tempfile
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from queue import Empty
from typing import Any

import psutil

from xlm.core.contracts import CanonicalDocument, TokenShardManifest
from xlm.data.datasets.shards import ShardEntry, load_manifest, verify_manifest
from xlm.data.exclusion.gates import MembershipGate
from xlm.data.exclusion.transport import open_gate
from xlm.data.tokens import TokenShardReader, TokenShardWriter, _token_stage, _write_synced
from xlm.tokenizers.bpe import ByteLevelBPETokenizer


def _rebase_offset_line(line: bytes, token_base: int, byte_base: int) -> bytes:
    """Shift three integer fields in our own canonical JSON; preserve span bytes.

    This private path only consumes verified TokenShardWriter output. JSON strings
    escape quotes, so field spellings inside IDs cannot match these key patterns.
    Refuse schema drift rather than silently leaving a stream-relative field local.
    """

    def shift(match: re.Match[bytes]) -> bytes:
        key = match[1]
        base = token_base if key.startswith(b'"token_start"') else byte_base
        return key + str(int(match[2]) + base).encode("ascii")

    result, count = re.subn(rb'("(?:token_start|byte_start|byte_end)": )([0-9]+)', shift, line)
    if count != 3:
        raise ValueError("unexpected token offset serialization during assembly")
    return result


@dataclass(frozen=True)
class TokenizationLimits:
    max_documents: int = 100_000
    max_input_bytes: int = 256 * 1024**2
    max_output_bytes: int = 2 * 1024**3
    max_record_bytes: int = 8 * 1024**2
    max_seconds: float = 900
    max_rss_bytes: int = 3 * 1024**3

    def validate(self) -> None:
        if any(value <= 0 for value in asdict(self).values()) or not math.isfinite(
            self.max_seconds
        ):
            raise ValueError("all tokenization limits must be positive")


def _file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _documents(
    path: Path,
    entry: ShardEntry,
    source_id: str,
    limit: int,
    deadline: float,
    max_rss_bytes: int,
    selected: bool = False,
) -> Iterator[CanonicalDocument]:
    digest = hashlib.sha256()
    count = size = 0
    process = psutil.Process()
    sampled = 0.0
    with path.open("rb") as stream:
        while line := stream.readline(limit + 1):
            if len(line) > limit or time.monotonic() > deadline:
                raise ValueError("tokenization record or time limit exceeded")
            if time.monotonic() - sampled >= 0.1:
                if process.memory_info().rss > max_rss_bytes:
                    raise ValueError("tokenization RSS limit exceeded")
                sampled = time.monotonic()
            size += len(line)
            count += 1
            if size > entry.byte_count or count > entry.doc_count:
                raise ValueError("canonical shard exceeded declared bounds")
            digest.update(line)
            doc = CanonicalDocument(**json.loads(line))
            # A selected logical component spans canonical sources; the selection
            # gate then requires each record to be selected for exactly this component.
            if doc.source_id != source_id and not selected:
                raise ValueError("tokenization requires shards from one declared source")
            yield doc
    if size != entry.byte_count or count != entry.doc_count or digest.hexdigest() != entry.sha256:
        raise ValueError("canonical shard changed during tokenization")


def _lane(
    input_dir: Path,
    output_dir: Path,
    tokenizer_dir: Path,
    entries: list[tuple[ShardEntry, int]],
    source_id: str,
    pool_hash: str,
    limits: TokenizationLimits,
    tokenizer_hashes: tuple[str, str],
    batch_size: int,
    c05_proof: Path | None = None,
    c05_selection: Path | None = None,
) -> dict[str, Any]:
    start, cpu = time.perf_counter(), time.process_time()
    deadline = time.monotonic() + limits.max_seconds
    files = (tokenizer_dir / "tokenizer.json", tokenizer_dir / "tokenizer_manifest.json")
    if tuple(map(_file_hash, files)) != tokenizer_hashes:
        raise ValueError("tokenizer changed before worker initialization")
    tokenizer = ByteLevelBPETokenizer.load(tokenizer_dir)
    if tuple(map(_file_hash, files)) != tokenizer_hashes:
        raise ValueError("tokenizer changed during worker initialization")
    init = time.perf_counter() - start
    manifests = []
    for entry, allowance in entries:
        shard_id = f"shard-{entry.ordinal:05d}"
        with open_gate(c05_proof, allow_authored=c05_selection is not None) as gate:
            selection = None
            if c05_selection is not None:
                from xlm.data.exclusion.selection import SelectionGate

                if gate is None:
                    raise ValueError("selected-membership tokenization requires a C05 proof")
                selection = SelectionGate(gate, c05_selection)
            manifest = TokenShardWriter(
                output_dir / shard_id,
                shard_id,
                source_id,
                tokenizer,
                pool_hash,
                max_output_bytes=allowance,
                batch_size=batch_size,
                c05_gate=gate,
                selection=selection,
            ).write_documents(
                _documents(
                    input_dir / entry.path,
                    entry,
                    source_id,
                    limits.max_record_bytes,
                    deadline,
                    limits.max_rss_bytes,
                    selection is not None,
                ),
                True,
            )
        manifests.append((entry.ordinal, manifest.to_dict()))
    return {
        "init_seconds": init,
        "wall_seconds": time.perf_counter() - start,
        "cpu_seconds": time.process_time() - cpu,
        "manifests": manifests,
    }


def _process_lane(queue: Any, ordinal: int, args: tuple[Any, ...]) -> None:
    """Exactly one lane/model initialization per explicitly owned worker process."""
    try:
        queue.put((ordinal, _lane(*args), None))
    except BaseException as exc:
        # Parser/record errors can contain rejected source values. Do not return
        # those values in parent diagnostics or process-queue telemetry.
        queue.put((ordinal, None, type(exc).__name__))


def _run_lanes(
    arguments: list[tuple[Any, ...]], max_seconds: float, max_rss_bytes: int
) -> list[dict[str, Any]]:
    context = multiprocessing.get_context("spawn")
    queue = context.Queue(maxsize=len(arguments))
    processes = [
        context.Process(target=_process_lane, args=(queue, i, args))
        for i, args in enumerate(arguments)
    ]
    deadline = time.monotonic() + max_seconds
    results: dict[int, dict[str, Any]] = {}
    started = []
    parent = psutil.Process()
    try:
        for process in processes:
            process.start()
            started.append(process)
        while len(results) < len(processes):
            rss = parent.memory_info().rss
            for process in started:
                if process.pid is not None and process.exitcode is None:
                    try:
                        rss += psutil.Process(process.pid).memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
            if rss > max_rss_bytes:
                raise ValueError("tokenization process pool RSS limit exceeded")
            if time.monotonic() >= deadline:
                raise TimeoutError("tokenization process pool exceeded wall-time limit")
            try:
                ordinal, result, error = queue.get(
                    timeout=min(0.25, max(0.001, deadline - time.monotonic()))
                )
            except Empty:
                if all(p.exitcode is not None for p in started):
                    raise RuntimeError(
                        "tokenization worker group returned incomplete results"
                    ) from None
                continue
            if error is not None:
                raise ValueError(f"tokenization worker {ordinal} failed: {error}")
            if ordinal in results or not 0 <= ordinal < len(processes):
                raise RuntimeError("invalid tokenization worker group result")
            results[ordinal] = result
        for process in started:
            process.join(timeout=max(0.001, deadline - time.monotonic()))
            if process.exitcode != 0:
                raise RuntimeError("tokenization worker did not exit successfully")
        return [results[i] for i in range(len(processes))]
    finally:
        for process in started:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            process.close()
        queue.close()
        queue.join_thread()


def tokenize_shards(
    input_dir: Path,
    tokenizer_dir: Path,
    output_dir: Path,
    *,
    source_id: str,
    pool_hash: str,
    workers: int = 1,
    limits: TokenizationLimits | None = None,
    batch_size: int = 1,
    c05_proof: Path | None = None,
    c05_selection: Path | None = None,
) -> dict[str, Any]:
    """Tokenize a verified P27A canonical dataset; publish an ordered index last.

    Workers do not affect child shard identities or the deterministic index.
    Output allowance is divided by input byte share; a shard exceeding its share
    fails explicitly. No silent fallback, partial index, or unbounded task queue.
    Performance telemetry is returned separately from the artifact identity.
    """
    limits = limits or TokenizationLimits()
    limits.validate()
    if not 1 <= workers <= 16:
        raise ValueError("workers must be 1..16")
    if not 1 <= batch_size <= 512:
        raise ValueError("batch_size must be 1..512")
    manifest = load_manifest(input_dir / "dataset-manifest.json")
    if (
        manifest.total_documents > limits.max_documents
        or manifest.total_bytes > limits.max_input_bytes
    ):
        raise ValueError("canonical input exceeds tokenization limits")
    if not manifest.shards or len(manifest.shards) > 10000:
        raise ValueError("tokenization requires 1..10000 nonempty shards")
    verify_manifest(input_dir, manifest)
    files = (tokenizer_dir / "tokenizer.json", tokenizer_dir / "tokenizer_manifest.json")
    if any(p.stat().st_size > 64 * 1024**2 for p in files):
        raise ValueError("tokenizer file exceeds 64 MiB bound")
    hashes = (_file_hash(files[0]), _file_hash(files[1]))
    reserve = 65536 + 2048 * len(manifest.shards)
    available = limits.max_output_bytes - reserve
    if available < manifest.total_documents:
        raise ValueError("insufficient tokenization output allowance")
    entries = [
        (e, available * e.byte_count // max(1, manifest.total_bytes)) for e in manifest.shards
    ]
    output_dir.mkdir(parents=True, exist_ok=False)
    workers = min(workers, len(entries))
    lanes = [entries[i::workers] for i in range(workers)]
    if workers == 1:
        results = [
            _lane(
                input_dir,
                output_dir,
                tokenizer_dir,
                lanes[0],
                source_id,
                pool_hash,
                limits,
                hashes,
                batch_size,
                c05_proof,
                c05_selection,
            )
        ]
    else:
        arguments = [
            (
                input_dir,
                output_dir,
                tokenizer_dir,
                lane,
                source_id,
                pool_hash,
                limits,
                hashes,
                batch_size,
                c05_proof,
                c05_selection,
            )
            for lane in lanes
        ]
        results = _run_lanes(arguments, limits.max_seconds, limits.max_rss_bytes)
    ordered = sorted(item for result in results for item in result["manifests"])
    if [i for i, _ in ordered] != [e.ordinal for e in manifest.shards]:
        raise ValueError("tokenization worker results are incomplete")
    with open_gate(c05_proof, allow_authored=c05_selection is not None) as gate:
        if gate is not None:
            gate.db.execute("DELETE FROM seen")
            for i, _ in ordered:
                gate.verify_token_shard(
                    output_dir / f"shard-{i:05d}",
                    reset_seen=False,
                    rehearsal=c05_selection is not None,
                )
    index = {
        "schema_version": 1,
        "input_sha256": manifest.aggregate_sha256,
        "tokenizer_file_hashes": hashes,
        "source_id": source_id,
        "pool_hash": pool_hash,
        "shards": [{"path": f"shard-{i:05d}", "manifest": m} for i, m in ordered],
    }
    encoded = json.dumps(index, indent=2, ensure_ascii=False)
    if len(encoded.encode("utf-8")) * 2 > reserve:
        raise ValueError("tokenization index exceeds reserved allowance")
    temporary = output_dir / "token-dataset.tmp"
    _write_synced(temporary, encoded)
    os.replace(temporary, output_dir / "token-dataset.json")
    from xlm.data.tokens import _sync_directory

    _sync_directory(output_dir)
    _sync_directory(output_dir.parent)
    return {"index": index, "workers": results}


def _assemble(
    parts: Path,
    manifests: list[dict[str, Any]],
    output_dir: Path,
    shard_id: str,
    allowance: int,
    c05_gate: MembershipGate | None = None,
    rehearsal: bool = False,
) -> TokenShardManifest:
    """Concatenate bounded shards in order, shifting only stream-relative offsets."""
    readers = [TokenShardReader(parts / item["path"]) for item in manifests]
    if c05_gate is not None:
        c05_gate.db.execute("DELETE FROM seen")
        for reader in readers:
            c05_gate.verify_token_shard(reader.directory, reset_seen=False, rehearsal=rehearsal)
    first = readers[0].manifest
    totals = dict.fromkeys(
        (
            "num_tokens",
            "num_documents",
            "valid_targets",
            "eos_tokens",
            "content_tokens",
            "canonical_bytes",
            "covered_bytes",
        ),
        0,
    )
    binary_hash, index_hash = hashlib.sha256(), hashlib.sha256()
    used = 0
    with _token_stage(output_dir) as stage:
        with (
            (stage / "tokens.bin").open("wb") as binary,
            (stage / "offsets.jsonl").open("wb") as index,
        ):
            for reader in readers:
                current = reader.manifest
                if (
                    current.source_id,
                    current.token_dtype,
                    current.tokenizer_hash,
                    current.pool_hash,
                ) != (first.source_id, first.token_dtype, first.tokenizer_hash, first.pool_hash):
                    raise ValueError("incompatible token shard assembly")
                reader.verify_integrity()
                with (reader.directory / "tokens.bin").open("rb") as stream:
                    while block := stream.read(1024**2):
                        used += len(block)
                        if used + 8192 > allowance:
                            raise ValueError("assembled shard output limit exceeded")
                        binary.write(block)
                        binary_hash.update(block)
                with (reader.directory / "offsets.jsonl").open("rb") as offsets:
                    while raw := offsets.readline(8 * 1024**2 + 1):
                        if len(raw) > 8 * 1024**2:
                            raise ValueError("token offset entry exceeds loader's 8 MiB limit")
                        line = _rebase_offset_line(
                            raw, totals["num_tokens"], totals["canonical_bytes"]
                        )
                        used += len(line)
                        if used + 8192 > allowance:
                            raise ValueError("assembled shard output limit exceeded")
                        index.write(line)
                        index_hash.update(line)
                counters = reader.counters
                for key in totals:
                    totals[key] += counters[key]
            for output in (binary, index):
                output.flush()
                os.fsync(output.fileno())
        coverage = (
            totals["covered_bytes"] / totals["canonical_bytes"]
            if totals["canonical_bytes"]
            else 1.0
        )
        result = replace(
            first,
            shard_id=shard_id,
            num_tokens=totals["num_tokens"],
            num_documents=totals["num_documents"],
            checksum_sha256=binary_hash.hexdigest(),
            offsets_checksum_sha256=index_hash.hexdigest(),
            byte_coverage_ratio=min(1.0, max(0.0, coverage)),
        )
        counter_text = json.dumps(
            {
                "shard_id": shard_id,
                "source_id": first.source_id,
                **totals,
                "token_dtype": first.token_dtype,
            },
            indent=2,
            sort_keys=True,
        )
        manifest_text = json.dumps(result.to_dict(), indent=2)
        if (
            sum(len(t.replace("\n", os.linesep).encode()) for t in (counter_text, manifest_text))
            > 8192
        ):
            raise ValueError("assembled metadata exceeds reserve")
        _write_synced(stage / "shard_counters.json", counter_text)
        if c05_gate is not None:
            proof = c05_gate.seal_token_shard(result.to_dict(), json.loads(counter_text))
            _write_synced(stage / "c05-attestation.json", json.dumps(proof, sort_keys=True))
        _write_synced(stage / "shard_manifest.json", manifest_text)
    return result


def tokenize_to_single_shard(
    input_dir: Path,
    tokenizer_dir: Path,
    output_dir: Path,
    *,
    source_id: str,
    shard_id: str,
    pool_hash: str,
    workers: int = 1,
    limits: TokenizationLimits | None = None,
    batch_size: int = 1,
    c05_proof: Path | None = None,
    c05_selection: Path | None = None,
) -> tuple[TokenShardManifest, dict[str, Any]]:
    """Parallel preparation with the exact existing single-source loader artifact.

    Scratch partials and final output each receive half the supplied disk budget.
    Assembly preserves cross-shard carry by producing one continuous token stream.
    Private scratch is removed on success or failure; published output is immutable.
    """
    limits = limits or TokenizationLimits()
    limits.validate()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".token-workers-", dir=output_dir.parent) as temp:
        parts = Path(temp) / "parts"
        result = tokenize_shards(
            input_dir,
            tokenizer_dir,
            parts,
            source_id=source_id,
            pool_hash=pool_hash,
            workers=workers,
            limits=replace(limits, max_output_bytes=limits.max_output_bytes // 2),
            batch_size=batch_size,
            c05_proof=c05_proof,
            c05_selection=c05_selection,
        )
        start = time.perf_counter()
        with open_gate(c05_proof, allow_authored=c05_selection is not None) as gate:
            manifest = _assemble(
                parts,
                result["index"]["shards"],
                output_dir,
                shard_id,
                limits.max_output_bytes // 2,
                c05_gate=gate,
                rehearsal=c05_selection is not None,
            )
        return manifest, {
            "workers": result["workers"],
            "assembly_seconds": time.perf_counter() - start,
        }


def main() -> None:
    """Explicit bounded local preparation; never runs training or downloads data."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--tokenizer", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--shard-id", required=True)
    parser.add_argument("--pool-hash", required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--c05-proof", type=Path)
    parser.add_argument("--c05-selection", type=Path)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--max-documents", type=int, default=100000)
    parser.add_argument("--max-input-bytes", type=int, default=256 * 1024**2)
    parser.add_argument("--max-output-bytes", type=int, default=2 * 1024**3)
    parser.add_argument("--max-record-bytes", type=int, default=8 * 1024**2)
    parser.add_argument("--max-seconds", type=float, default=900)
    parser.add_argument("--max-rss-bytes", type=int, default=3 * 1024**3)
    args = parser.parse_args()
    manifest, timing = tokenize_to_single_shard(
        args.input,
        args.tokenizer,
        args.output,
        source_id=args.source_id,
        shard_id=args.shard_id,
        pool_hash=args.pool_hash,
        workers=args.workers,
        c05_proof=args.c05_proof,
        c05_selection=args.c05_selection,
        batch_size=args.batch_size,
        limits=TokenizationLimits(
            args.max_documents,
            args.max_input_bytes,
            args.max_output_bytes,
            args.max_record_bytes,
            args.max_seconds,
            args.max_rss_bytes,
        ),
    )
    print(json.dumps({"manifest": manifest.to_dict(), "timing": timing}, indent=2))


if __name__ == "__main__":
    main()
