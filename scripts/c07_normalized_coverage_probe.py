"""Bounded, content-free, read-only diagnostics of the immutable C07-v2 shards."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import psutil

from xlm.data.input_policy import (
    PINNED_TABLE_SHA,
    TrainingInputPolicy,
    admit_sources,
    validate_headers,
)
from xlm.data.input_validation import validate_training_index
from xlm.data.tokens import TokenShardReader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("reproduce", "validate", "snapshot"))
    args = parser.parse_args()
    root = Path("G:/XLM/shards/mix01-policy-v2")
    sources = {p.name: p for p in root.iterdir() if p.is_dir()}
    policy = TrainingInputPolicy()
    artifact_bytes = admit_sources(sources, policy)
    validate_headers(sources, policy, PINNED_TABLE_SHA)
    if len(sources) != 11:
        raise ValueError("expected exactly 11 production components")
    began = time.monotonic()
    process = psutil.Process()

    def check() -> None:
        if time.monotonic() - began > 1800 or process.memory_info().rss > 16 * 1024**3:
            raise RuntimeError("diagnostic time or RSS ceiling exceeded")

    if args.mode == "snapshot":
        paths = sorted(p for source in sources.values() for p in source.iterdir())
        paths += sorted(Path("G:/XLM/freeze/mix01-policy-v2").iterdir())
        for path in paths:
            stat = path.stat()
            item: dict[str, object] = {
                "path": str(path),
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
            if stat.st_size <= 8 * 1024**2:
                with path.open("rb") as stream:
                    item["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
            print(json.dumps(item, sort_keys=True), flush=True)
        return
    names = ["common_pile_prose"] if args.mode == "reproduce" else sorted(sources)
    for name in names:
        reader = TokenShardReader(sources[name])
        if args.mode == "reproduce":
            with (reader.directory / "tokens.bin").open("rb") as payload:
                for ordinal, record in enumerate(reader.iter_document_offsets()):
                    reader.check_read_window(record["token_count"])
                    ids = np.frombuffer(payload.read(record["token_count"] * 2), dtype="<u2")
                    try:
                        reader.validate_v2_ids(record, ids)
                    except ValueError as exc:
                        lengths = reader.token_byte_table()[ids]
                        covered = int(lengths.sum())
                        diagnostic = {
                            k: record[k]
                            for k in (
                                "token_count",
                                "byte_count",
                                "covered_bytes",
                                "bos_positions",
                                "eos_positions",
                            )
                        }
                        diagnostic.update(
                            record_ordinal_zero_based=ordinal,
                            first_token_id=int(ids[0]),
                            last_token_id=int(ids[-1]),
                            first_token_byte_length=int(lengths[0]),
                            last_token_byte_length=int(lengths[-1]),
                            reconstructed_covered_sum=covered,
                            byte_count_minus_covered_bytes=record["byte_count"] - covered,
                            error=str(exc),
                        )
                        print(json.dumps(diagnostic, sort_keys=True), flush=True)
                        if not (
                            str(exc) == "v2 structural byte spans differ from v1 framing"
                            and record["eos_positions"] == [len(ids) - 1]
                            and lengths[-1] == 0
                            and covered == record["covered_bytes"] < record["byte_count"]
                        ):
                            raise RuntimeError("hypothesis contradicted") from None
                        return
                    if ordinal % 4096 == 0:
                        check()
            raise RuntimeError("no failing record found")
        start = time.monotonic()
        validate_training_index(reader, check)
        print(
            json.dumps(
                {
                    "component": name,
                    "verified": True,
                    "documents": reader.manifest.num_documents,
                    "token_ids": reader.manifest.num_tokens,
                    "valid_targets": reader.counters["valid_targets"],
                    "seconds": time.monotonic() - start,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    memory = process.memory_info()
    print(
        json.dumps(
            {
                "seconds": time.monotonic() - began,
                "artifact_bytes": artifact_bytes,
                "peak_rss_bytes": getattr(memory, "peak_wset", memory.rss),
                "scratch_payload_bytes": 0,
                "components": len(names),
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
