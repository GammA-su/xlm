"""Bounded BPE child for the C06 fast fit (``python -m xlm.data.exclusion.fitfast_child``).

Runs only the authenticated spool trainer on a spool the parent already screened
through C05, under the parent's explicit environment (``TOKENIZERS_PARALLELISM=true``,
``RAYON_NUM_THREADS``). It hashes exactly the framed bytes it consumes and refuses
before saving unless they equal the parent's authenticated spool (SHA-256, bytes,
frames, payload bytes); it then re-hashes the file as defense in depth. It writes
the tokenizer only into the parent's private staging directory and a small
content-free result file; publication is the parent's alone. A killed child leaves
only owned staging/work paths, which the parent removes.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

RESULT_BYTES = 64 * 1024
HASH_BLOCK = 16 * 1024**2


def _file_sha(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(HASH_BLOCK):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _tree_bytes(directory: Path) -> int:
    return sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())


def run(job: dict[str, Any]) -> dict[str, Any]:
    from xlm.tokenizers.bpe import train_spool

    spool = Path(job["spool"])
    if spool.stat().st_size != int(job["spool_file_bytes"]):
        raise ValueError("authenticated spool size changed before BPE")
    tokenizer, consumed = train_spool(
        spool,
        int(job["target_vocab_size"]),
        str(job["training_input_hash"]),
        documents=int(job["documents"]),
        payload_bytes=int(job["payload_bytes"]),
        is_production_baseline=bool(job["production"]),
        max_frame_bytes=int(job["max_frame_bytes"]),
        expected_sha256=str(job["spool_sha256"]),
        expected_file_bytes=int(job["spool_file_bytes"]),
    )
    post_sha, post_bytes = _file_sha(spool)
    if (post_sha, post_bytes) != (job["spool_sha256"], int(job["spool_file_bytes"])):
        raise ValueError("authenticated spool changed during BPE")
    output = Path(job["output"])
    tokenizer.save(output)
    if _tree_bytes(output) > int(job["tokenizer_bytes_ceiling"]):
        raise ValueError("tokenizer output exceeds its ceiling")
    return {
        "status": "ok",
        "consumed_spool_sha256": consumed.sha256,
        "consumed_spool_bytes": consumed.file_bytes,
        "consumed_frames": consumed.frames,
        "consumed_payload_bytes": consumed.payload_bytes,
        "post_spool_sha256": post_sha,
        "tokenizers_parallelism": os.environ.get("TOKENIZERS_PARALLELISM"),
        "rayon_num_threads": os.environ.get("RAYON_NUM_THREADS"),
    }


def _write_result(path: Path, value: dict[str, Any]) -> None:
    raw = json.dumps(value, sort_keys=True).encode("utf-8")[:RESULT_BYTES]
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        return 2
    job = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
    result = Path(job["result"])
    try:
        value = run(job)
    except Exception as exc:  # noqa: BLE001 - reported content-free to the parent
        _write_result(
            result,
            {"status": "refused", "error_type": type(exc).__name__, "reason": str(exc)[:500]},
        )
        return 3
    _write_result(result, value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
