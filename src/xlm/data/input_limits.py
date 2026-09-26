"""Byte limits on frozen training inputs (P34/P35; values unchanged, named for review).

These bound the full-hash and index-scan work every resolution performs over
the bound token shards. They are engineering bounds, not scientific limits.
Raising them is a deliberate, separately reviewed change; the pilot planner
only *reports* observed sizes against them before any hashing.
"""

from __future__ import annotations

#: Running total of one shard directory's five input files.
MAX_FROZEN_SHARD_INPUT_BYTES = 2 * 1024**3
#: Sum of the four payload files over every mixture component.
MAX_AGGREGATE_FROZEN_INPUT_BYTES = 2 * 1024**3
#: Any JSON file inside a shard directory.
MAX_SHARD_JSON_BYTES = 8 * 1024**2
#: Files counted by the per-shard running total, in the order they are checked.
SHARD_INPUT_FILES = (
    "shard_manifest.json",
    "manifest.json",
    "tokens.bin",
    "offsets.jsonl",
    "shard_counters.json",
)
#: Files counted by the aggregate mixture total.
AGGREGATE_INPUT_FILES = (
    "tokens.bin",
    "offsets.jsonl",
    "shard_manifest.json",
    "shard_counters.json",
)
