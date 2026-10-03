"""Bounded BPE child for the C06 fast fit (``python -m xlm.data.exclusion.fitfast_child``).

Runs only :meth:`ByteLevelBPETokenizer.train_from_spool` on a spool the parent
already screened through C05, under the parent's explicit environment
(``TOKENIZERS_PARALLELISM=true``, ``RAYON_NUM_THREADS``). It writes the tokenizer
into the parent's staging directory; the parent verifies it, writes the C05
binding and publishes. A killed child leaves only staging, which is removed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from xlm.tokenizers.bpe import ByteLevelBPETokenizer

    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        return 2
    job = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
    tokenizer = ByteLevelBPETokenizer.train_from_spool(
        Path(job["spool"]),
        int(job["target_vocab_size"]),
        str(job["training_input_hash"]),
        documents=int(job["documents"]),
        spool_bytes=int(job["spool_bytes"]),
        is_production_baseline=bool(job["production"]),
    )
    tokenizer.save(Path(job["output"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
