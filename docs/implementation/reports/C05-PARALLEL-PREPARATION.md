# C05 parallel protected preparation

Date: 2026-10-02. Branch `feat/c05-global-preparation`, starting HEAD
`6863bc08089db19017c969c6e5d52c84aea1155f`. Offline; authored synthetic fixtures
only. No network access, no real benchmark material, no `X:\C05-Protected`, no
real C05 build or scan.

## Bottleneck (HEAD)

`build()` was fully serial. Parquet was decoded one row per `iter_batches`
call. Each row and each pattern `db.execute` ran a full `check()`: process RSS,
`disk_usage` and an `iterdir`/`stat` of the destination. A duplicate test ran a
`SELECT` before each item insert. The 12,000-row authored fixture took 52.3 s,
about 230 rows/s.

## Design

- Worker processes (`multiprocessing` spawn) instead of threads. Render,
  `match_tokens`, informativeness checks and canonical digests are pure Python
  and hold the GIL. Children receive only paths, the content-free `MaterialFile`
  and the matcher policy over the spawn pipe. They write no files.
- One task per Parquet row group, planned from the footer, or per JSONL file.
  The parent validates the footer item count and the decompressed row-group
  ceiling. The worker re-validates the row group and decodes it whole, which is
  bounded by `max_record_bytes`.
- Bounded IPC: batches of at most 32 rows (patterns per row are policy-bounded)
  go through a result queue of `2 x workers` batches.
- One writer: the parent owns SQLite and inserts items, patterns and refs with
  `executemany` per batch. `duplicate_items += batch - inserted` equals items
  minus distinct hashes, which does not depend on order. All other aggregates are
  sums, and the index keeps its canonical `ORDER BY r.hash,r.ref`.
- Global ceilings: `check()` runs per batch and at least every 0.25 s while the
  writer waits. RAM is the parent plus all descendants (`ProcessTree`). Each
  worker's `ready` message, sent before any decoding, forces a rescan of the
  descendant tree, and every known process is re-sampled on every check.
- Integrity: every file has its size and SHA-256 verified before any row is read.
  After its last row, the item count, size and SHA-256 are verified again.
- Failure: a worker error, abnormal exit, ceiling refusal, `KeyboardInterrupt` or
  SQLite error ends the generator. Its `finally` block terminates, joins and if
  needed kills every child, and closes the queues. `PREPARATION-INCOMPLETE` is
  created with the destination and removed only after the receipt and export are
  written.
- Progress: `Progress` writes to stderr, rate-limited, with forced lines at
  milestones. The CLI adds `--progress-interval` (default 1.0) and
  `--no-progress`, both operational only. Ctrl+C returns 130 with a content-free
  refusal on stdout.
- `Resources.workers` changes from `Literal[1]` to `int` with `1 <= workers <= 16`,
  default 1. Default serialization and digests are unchanged.

## Determinism evidence

For workers 1, 2, 4 and 16, the authored 60-row fixture (8 files, 480 rows)
reproduces the pre-parallel HEAD outputs byte for byte. The index SHA-256 is
`8c7ba6bf…59bd44` and the envelope digest is `4a8417dd…cce4c9`. The 12,000-row
fixture also matches HEAD (`f8693929…`, `aaeee959…`). The signature is a
deterministic HMAC, so the envelope bytes are identical too. No digest depends
on `workers`, except that a plan binding a different `Resources` gets a new plan
digest.

## Informational timing (not a CI gate; 16 logical CPUs, authored fixture)

| Fixture | HEAD serial | New w1 | w4 | w8 | w16 |
|---|---|---|---|---|---|
| 480 rows | 1.84 s | 0.48 s | 0.88 s | – | – |
| 12,000 rows | 52.3 s | 14.4 s | 6.1 s | 6.4 s | 7.2 s |

Peak process-tree RSS on the 12,000-row fixture: 123 MiB with w1 and 1,295 MiB
with w16. On this small fixture, above 4 workers the time is dominated by spawn
start-up (about 1 s), the single writer and the serial canonical index phase
(about 2 s). Real-workload speed-up is not measured.

## Tests and checks

| Command (uv `--offline --locked --extra cpu --extra eval`) | Result |
|---|---|
| `pytest tests/test_c05_parallel_preparation.py -n 0` | 21 passed, exit 0 |
| `pytest tests/test_c05_*.py -n 8 --dist=worksteal --max-worker-restart=0` | 187 passed, exit 0 (rerun after formatting) |
| `ruff format --check` / `ruff check` (changed files) | clean |
| `mypy --strict` (4 changed source modules) | no issues |
| `git diff --check` | clean |

Full offline acceptance suite: NOT RUN (focused and related C05 only).

## Requirement ledger

| Requirement | Status |
|---|---|
| Honors `Resources.workers` 1..16, no override | IMPLEMENTED, VERIFIED |
| Index, receipt and envelope identical across workers | VERIFIED (1/2/4/16) |
| Pre- and post-file identity, changed file refused (w1, w2) | VERIFIED |
| Malformed row or worker exception fails the build, children terminated | VERIFIED |
| Global pattern/record ceilings, deadline with live workers, RAM incl. children, disk | VERIFIED |
| Interrupt: nothing published, marker kept | VERIFIED (simulated `KeyboardInterrupt`) |
| Progress on stderr, rate-limited, content-free; stdout single JSON | VERIFIED |
| Detached-volume / separate-principal unchanged | VERIFIED (existing tests) |
| Real Ctrl+C in a Windows console | NOT RUN |
| Real 76-file workload speed-up | NOT RUN (operator) |

## Next

The operator writes a new reviewed `resources.json` with the chosen `workers`,
deletes any interrupted destination, and reruns `build-local` (runbook section
"Parallel protected preparation").
