"""Exact planned row accounting and unchanged-parent blob proof; no training."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from xlm.artifacts.manifest import identity_digest
from xlm.data.sampling.update_payload import (
    MAX_CHAIN_BYTES,
    MAX_CHAIN_ROW_BYTES,
    MAX_CHAIN_ROWS,
    receipt_history_digest,
)
from xlm.training.checkpoint import MAX_SCIENCE_STATE_BYTES

PARENT = "d7942ba7cfa1c15f8c9eefe8770c58b655208d84"


def blob(ref: str, name: str) -> bytes:
    return subprocess.run(["git", "show", f"{ref}:{name}"], check=True, capture_output=True).stdout


def main(output: Path) -> None:
    files = [
        "src/xlm/comparison/" + n + ".py" for n in ("promotion", "tracks", "bootstrap", "recipes")
    ]
    files += [
        "src/xlm/cli/compare_cmd.py",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "recipes/experiments/draft_science_v1_pilot_32m.yaml",
    ]
    compatibility = []
    for name in files:
        parent, head, local = blob(PARENT, name), blob("HEAD", name), Path(name).read_bytes()
        assert parent == head == local.replace(b"\r\n", b"\n")
        compatibility.append(
            {
                "path": name,
                "parent_blob_sha256": hashlib.sha256(parent).hexdigest(),
                "head_blob_identical": True,
                "worktree_sha256": hashlib.sha256(local).hexdigest(),
                "worktree_crlf_pairs": local.count(b"\r\n"),
                "normalized_identical": True,
            }
        )
    campaigns = []
    for budget in (1_000_000_000, 3_000_000_000, 6_000_000_000):
        steps = (budget + 65535) // 65536
        chain_bytes = lr_bytes = 2
        max_chain_row = max_lr_row = 0
        for step in range(1, steps + 1):
            before = (step - 1) * 65536
            valid = min(65536, budget - before)
            chain = [step, before, valid, "f" * 64, "f" * 64]
            # Two stock AdamW groups; conservatively reserve 24 bytes per LR float.
            # Serialized finite nonnegative binary64 JSON is no longer than this.
            lr_prefix = json.dumps([step, before, valid, before + valid])[:-1]
            lr_length_bound = len(lr_prefix) + len(", [") + 24 * 2 + len(", ") + len("]]")
            size = len(json.dumps(chain))
            chain_bytes += size + (2 if step > 1 else 0)
            lr_bytes += lr_length_bound + (2 if step > 1 else 0)
            max_chain_row = max(max_chain_row, size)
            max_lr_row = max(max_lr_row, lr_length_bound)
        envelope = 4096
        ancillary_allowance = 8 * 1024**2
        upper = chain_bytes + lr_bytes + envelope + ancillary_allowance
        assert upper < 64 * 1024**2
        campaigns.append(
            {
                "budget_targets": budget,
                "updates": steps,
                "full_updates": budget // 65536,
                "final_partial": budget % 65536,
                "chain_rows_serialized_bytes": chain_bytes,
                "lr_rows_bytes_upper": lr_bytes,
                "max_chain_row_bytes": max_chain_row,
                "max_lr_row_bytes_upper": max_lr_row,
                "header_allowance": envelope,
                "ancillary_allowance": ancillary_allowance,
                "science_bytes_upper_with_allowance": upper,
                "64MiB_remaining_after_rows_and_header": 64 * 1024**2
                - chain_bytes
                - lr_bytes
                - envelope,
            }
        )
    worst_lr = [MAX_CHAIN_ROWS, 10**15 - 1, 2**31 - 1, 10**15 - 1, [1.2345678901234567e-200] * 2]
    internal = (
        MAX_CHAIN_ROWS * (MAX_CHAIN_ROW_BYTES + 2 + len(json.dumps(worst_lr)) + 2)
        + 8 * 1024**2
        + 4096
    )
    assert internal < MAX_SCIENCE_STATE_BYTES
    parity_values = [
        {"rows": [[1, 0, 16, "\u00e9", "a" * 64]], "staged": None},
        [],
        [[0, 65536], [65536, 5]],
    ]
    for value in parity_values:
        assert receipt_history_digest(value) == identity_digest(value)
    report = {
        "scope": "arithmetic upper bounds, not invented run receipts or measured campaign storage",
        "campaigns": campaigns,
        "max_chain_rows": MAX_CHAIN_ROWS,
        "max_chain_row_bytes": MAX_CHAIN_ROW_BYTES,
        "max_chain_bytes": MAX_CHAIN_BYTES,
        "400k_science_bytes_with_assumed_numeric_domain_and_8MiB_ancillary": internal,
        "science_save_limit": MAX_SCIENCE_STATE_BYTES,
        "qualification": (
            "No fixed total follows from row count alone: additional ledgers and LR group "
            "count vary. 8 MiB is an explicit allowance, not an enforced ancillary limit. "
            "Full science save remains capped at 128 MiB; evidence at 64 MiB."
        ),
        "canonical_history_digest_parity": True,
        "compatibility": compatibility,
    }
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
