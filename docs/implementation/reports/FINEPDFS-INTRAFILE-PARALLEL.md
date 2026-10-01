# FinePDFs intra-file row-group parallel processing

Date: 2026-10-01. Branch `data/mix01-ultrax-6b`, base `818c663`. Offline only:
no network, no source acquisition, no production plan, no C05, tokenizer,
training or push. The frozen FinePDFs transport policy was not modified.

**Result: FINEPDFS PARALLEL PROCESSING READY FOR PRODUCTION PLANNING.**
Row-group parallel processing reproduces the serial output byte for byte, and
4 workers per file process the owned 2.77 GB shard 3.45× faster than serial.
Three concurrent files at 4 workers each get 2.06× the aggregate throughput of
today's one-process-per-file behavior. The next production plan for
`finepdfs_edu/eng_Latn` binds this concurrency. No other view changes.

## 1. The serial bottleneck

- `essential_web_local.run_pipeline` gives **one process per file**: its pool
  size is `process_workers`. `plan_limits` sets that to `min(files, 12)` for
  `whole_file_local`; `run_plan` caps it at `process_workers_max` (16).
- Inside a file, `source_parquet.selected_payloads` walks row groups
  **serially** with `iter_batches(row_groups=[g], use_threads=False)`. Each
  process sets `pa.set_cpu_count(1)`.
- `source_local.adapt_source_file` then adapts each record, writes
  `documents.jsonl` through one `StreamingJsonlWriter`, appends rejections to
  one in-memory ledger in row order, and hashes the selected-record stream with
  one SHA-256.
- File-wide limits are cumulative: decoded bytes, canonical bytes, ledger
  bytes, and the per-line `OutputBudget` charge. The per-line charge calls
  `disk_usage` and `stat` for every document.
- FinePDFs needs about 3 large files, so file-level parallelism alone leaves
  most of the 16 logical CPUs idle. The serial reference here took 123.97 s
  (median of 3) at 0.99 effective cores.

## 2. Architecture

`src/xlm/data/acquisition/source_rowgroups.py` (new) and `source_local.py`
implement the design:

```
verified local Parquet (one file, opened read-only by every worker)
  -> group_tasks(): one task per row group overlapping the row range, in file order
  -> RowGroupPool: spawn-context ProcessPoolExecutor(workers)
       at most workers + lookahead row groups outstanding; the coordinator
       always waits on the head-of-line group
  -> adapt_row_group() in a worker: same projection, decode (512-row batches),
     located_record serialization, record bounds, adapter call and
     document/rejection serialization as the serial loop. It writes no file and
     returns ordered events plus the bytes it produced.
  -> _replay() in the coordinator, strictly in row-group order:
       per batch:    cumulative decoded-byte bound
       per row:      ledger bound / canonical ceiling / output-budget arithmetic
       per group:    one block write to the same writer; payloads go to a single
                     ordered hashing thread
```

- **Entry points.** The parallel path runs only when the limits carry
  `row_group_parallel`, the adapter is declared row-independent, and more than
  one row group overlaps the range. Otherwise `adapt_source_file` runs the
  unchanged legacy loop.
- **Adapter eligibility.** `ROW_INDEPENDENT_ADAPTERS` lists every Mix-01
  adapter except the two Essential-Web adapters. A test checks with an AST scan
  that no listed `adapt` assigns attributes on `self`/`cls` or declares global
  state.
- **Refusals.** An ineligible adapter with a parallel configuration fails
  before any output exists. It never falls back silently.
- **Source identity.** Workers compare size and `mtime_ns` against what the
  coordinator saw, plus each row group's row count. The coordinator re-checks
  the file after the merge.

## 3. Determinism

- **Ordering.** Output order never depends on completion order. The pool
  yields results only in task order, and the test
  `test_completion_order_never_changes_output` makes later groups finish first.
- **Same values everywhere.** Each worker builds the same locator, the same
  `input_line` (`row_index - start + 1`, which equals the serial running count
  because every row is consumed), and the same serializations.
- **Same first error.** File-wide checks run at the same row or batch as in
  the serial loop, with the same exception type and message. A worker's
  record-level error is re-raised after its preceding rows are replayed.
  Pathological records in two groups raise the earlier group's error even
  when the later group finishes first.
- **Summary.** `adaptation_summary.json` stays deterministic: no timings or
  paths. Execution-shape fields (`row_group_workers`, `row_group_pool`,
  timings) appear only in the unit result. They never enter the documents,
  ledger or summary.
- **Budget charging.** Each group's documents are written as one block.
  Before that, the budget's byte arithmetic is checked per line through
  `OutputBudget.charge` itself, so the same error is raised. The physical
  free-space reserve is checked once per block instead of once per line. That
  check is environmental and never deterministic, in either path.
- **Unchanged files.** No change to `source_parquet.py`,
  `essential_web_local.py`, `mix01_adapters.py` or `columns.py`. They keep
  their Essential-Web compatibility hashes (`identity-check.json`).

## 4. Resource and memory contract

`RowGroupParallel` (version 1; frozen, data-only) is bound into the plan:

| field | meaning |
|---|---|
| `workers` | row-group processes serving one file (2..16) |
| `lookahead` | extra outstanding groups (reorder window = `workers + lookahead`) |
| `processing_slots` | global budget: `max(1, process_workers) × (workers + 1) ≤ slots`, because the coordinator holds a slot |
| `memory_bytes` | sampled process-tree resident ceiling per file (coordinator + workers) |

**Where it is enforced**

- `plan_limits`: `process_workers = min(files, 12, slots // (workers + 1))`.
- `run_plan`: operator overrides are checked. `build_benchmark` checks the
  same budget.
- `RowGroupPool.sample()`: tree RSS is sampled every 0.25 s and after every
  group. Exceeding the ceiling raises `ProcessingMemoryError`. This is a
  sampled bound, not a kernel limit.

**Unchanged limits.** Decoded, record, parser, canonical, ledger, output-budget
and row limits keep their per-file meaning and values. Workers check only
group-local values.

**Scratch.** Workers write nothing, so scratch growth is unchanged. Every run
ends with 1,977,629,785 staging bytes: documents, ledger and summary.

**Measured on the 221-group shard** (largest group result 93,751,885 B):

- Per-worker peak RSS: ≤ 702 MB.
- Reorder buffer: ≤ 456 MB at W=8.
- Process-tree peak per file: 0.62 GB serial, 1.04 GB at W=2, 1.46 GB at W=4,
  2.49 GB at W=8.
- Three files at W=4 together: ≤ 4.24 GB.

**FinePDFs binding.** `workers=4, lookahead=4, processing_slots=15,
memory_bytes=6 GiB`. The composed worst case for this file is about 3.6 GB:
4 × 0.70 GB workers, plus 8 × 94 MB of reorder buffer, plus the coordinator.
6 GiB leaves headroom for larger row groups in other files. Three files use at
most 18 GiB on a 72 GB machine.

## 5. New plan limits

Yes, they are opt-in. `source_plan.SOURCE_ROW_GROUP_PARALLEL` is a data table
following the `SOURCE_RECORD_BYTES` pattern, with one entry per view and an
evidence basis.

- For `("finepdfs_edu", "eng_Latn")` it adds `row_group_parallel` and
  `row_group_parallel_basis` to the plan's `limits`. They are part of the plan
  digest.
- Views without an entry produce exactly the same plans as before. The stored
  UltraX p01 plan and its seal tests pass unchanged.
- `prepare_units` forwards the key to workers.
- `scripts/mix01_source.py plan` prints an `intra-file` review line.

## 6–11. Benchmark (owned shard, offline)

**Setup**

- Source: `C:\XLM-scratch\finepdfs\bench-b2\f00000.parquet.part`. It was
  re-hashed in this session: SHA-256 `4eeb58bc…a38d`, 2,771,021,138 B.
- Every unit is a fresh child process calling the production
  `process_source_unit` with the authorized b3 limits, read-only from
  `G:\XLM\plans\finepdfs\benchmarks\b3`.
- The lookahead equals the worker count.
- The OS file cache is warm: the full-file identity hash reads every byte
  before the first run. All results are warm-cache numbers.
- Runs were interleaved 1, 2, 4, 8 within each repeat, for 3 repeats.

**Background load.** Another application (`partitionwizard.exe`) kept about 1
logical CPU busy. The machine sat at 15–18% CPU before the runs. Timings
include that load.

**Spread.** The range across 3 repeats is at most ±1.1%. Precision is given to
0.1 s. Throughput is computed from the per-unit `process_seconds`.

| workers | process s (median; min–max) | rows/s | speedup | efficiency | effective cores | process-tree peak RSS (max) |
|---|---|---|---|---|---|---|
| 1 (serial) | 124.0; 122.7–124.1 | 1,778 | 1.00 | 1.00 | 0.99 | 0.62 GB |
| 2 | 63.5; 63.4–63.6 | 3,469 | 1.95 | 0.98 | 2.05 | 1.04 GB |
| 4 | 35.9; 35.9–36.2 | 6,136 | 3.45 | 0.86 | 3.90 | 1.46 GB |
| 8 | 25.0; 24.8–25.1 | 8,816 | 4.96 | 0.62 | 7.10 | 2.49 GB |

**CPU cost**

- Total CPU per file grows with W: 123.0 s serial, then 125.8, 137.8 and
  172.7 s at W=2, 4 and 8. The cause is SMT and memory-bandwidth contention
  plus the coordinator's own work.
- Coordinator CPU: 13.4 s at W=2, 14.8 s at W=4, 18.2 s at W=8.
- Most of the coordinator's work is receiving 7.6 GB of results over the pipe
  and SHA-256 of the 5.6 GB selected-record stream. Keeping that stream's
  exact digest requires the bytes in order.

**Concurrent files** (3 units of the same shard at once, 2 repeats):

| layout | slots | group wall s | aggregate rows/s | per-unit s | group peak RSS |
|---|---|---|---|---|---|
| 3 × W=1 (today) | 3 | 136.4, 136.8 | 4,848 / 4,835 | 132.8–134.2 | 1.73 GB |
| 3 × W=4 (proposed) | 15 | 67.5, 65.5 | 9,798 / 10,102 | 62.1–64.9 | 4.24 GB |

At 3 × W=4 the machine CPU averaged 92% and aggregate throughput was 2.06× the
current behavior.

**Early-code probes.** Probes run before the coordinator optimizations are
excluded from the evidence. Those optimizations were child-discovery
throttling, block writes and the ordered hashing thread. With the old code,
W=8 reached only 32 s: the coordinator was the bottleneck, and 7.6 s of it was
`psutil.children()`.

## 12. Output identity gate

Every one of the 24 timed runs reproduces the first serial run's complete
output record: 12 single-file and 12 concurrent units, at 1, 2, 4 and 8
workers. `summary.json` records `identity_gate.all_identical = true`.

- Rows 220,407; documents 144,125; rejected 76,282
  (`RecordRejectedError`: 76,282); canonical bytes 1,784,575,330. These are
  identical to the historical b3 receipts.
- `documents.jsonl` SHA-256
  `f6d9bd018d72854813ed32308c7666d3ae59550781be2bec41deda1b587bc0bd`,
  1,973,974,950 B.
- Rejection ledger (uncompressed) SHA-256 `c43da012…35b1`. Stored `.zst`
  `43d6b612…2475`.
- `adaptation_summary.json` SHA-256 `3ba6a395…3c42`.
- Selected-record stream SHA-256 `8d40d3ba…c639`, 5,597,450,610 B. Decoded
  bytes 5,358,525,316. Largest record 24,829,428 B.

No metadata was nondeterministic. Timings and pool statistics appear only in
the unit result and the benchmark logs.

## 13–14. Recommended production concurrency

**Bound for FinePDFs: 4 workers per file, lookahead 4, 15 slots, giving
3 files × (4 + 1).**

- **4 workers is the knee.** It runs at 86% efficiency. Going to 8 workers
  saves another 11 s per file at 62% efficiency and nearly doubles memory.
- **Concurrent files.** With FinePDFs' ~3 files the planner derives
  `process_workers = min(3, 15 // 5) = 3`. All files run at once on 15 of 16
  logical CPUs; the remaining one is for the run process and download threads.
- **No unbounded knobs.** An operator may lower `process_workers` but never
  raise the product past the slots. Many small files would gain little from
  intra-file workers: file-level processes already fill the CPUs, and the
  budget would cap them at 3. Such views simply get no table entry.

## 15. Frozen transport-policy identity

Unchanged. `identity_check.py` re-verified against the current code:

- Policy digest `f3a524116f38ac9b49e55d5a33e81ecbc503074f21af9f3fe5263b02a6e7fc73`
  still verifies; selected mode is `whole_file_local`.
- Sizing digest `4709fb8460c1cca6bc54624ce9e8ad3069fea7434c8a98c9c2eb856f835b2a58`
  still verifies.
- `range_selected` remains `non_comparable`. No policy file was touched.

## 16. Plan identity impact

| identity | changed? |
|---|---|
| transport policy / sizing measurement | no |
| adapter (`mix01_adapters.py`, `columns.py`) | no (hashes verified) |
| Essential-Web compatibility chain (`source_parquet.py`, `essential_web_local.py`) | no (hashes verified) |
| `AcquisitionPlan` hash (`AcquisitionLimits`) | no (row-group config is not an acquisition limit) |
| FinePDFs production **plan record digest** | yes: `limits` gains `row_group_parallel` and its basis. No such plan exists yet. |
| other views' plans (incl. stored UltraX p01) | no |
| historical b1–b3 benchmark records/receipts | no (read only) |
| source executable code | `source_local.py`, `source_plan.py`, `source_run.py`, `source_benchmark.py` changed. None of them is hash-bound. |

## 17. Tests and static checks

All commands run with `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1` and
`TOKENIZERS_PARALLELISM=false`, on Windows 11 Pro 10.0.26200, Python 3.12.13,
pyarrow 25.0.1, psutil 7.2.2, Ryzen 7 5700X3D (8C/16T) and 72 GB RAM.

**New `tests/test_source_rowgroups.py`** (21 cases, authored synthetic Parquet
with real spawn workers) covers:

- deterministic, clipped partition;
- byte identity at (2,0), (3,2) and (4,1) workers/lookahead;
- completion order reversed;
- partial row range;
- one-group and absent-config legacy path;
- pathological records in two groups;
- decoded, canonical, ledger and output breaches identical to serial;
- memory ceiling refusal with worker cleanup;
- worker crash without summary, followed by an exact restart;
- unportable worker error;
- source change;
- ineligible adapter refused before output;
- adapter statelessness scan;
- global slot budget;
- opt-in plan binding;
- payload hash equal to the certified reader.

**`tests/test_source_run.py`** gains two cases:

- a parallel plan run with 2 file processes × 2 row-group workers seals units
  identical to serial, verifies, and refuses `process_workers=3`;
- a worker crash publishes nothing, and a rerun seals serial-identical units.

**`tests/test_source_record_bound.py`** now expects the two FinePDFs row-group
keys. Its FinePDFs benchmark run exercises the parallel path.

**Commands and results**

- `uv run --offline --locked --extra cpu --extra eval python -m pytest <19 related files> -n 16 --dist=worksteal --max-worker-restart=0`
  passed 324/324 (exit 0) after the final code. The 19 files are the source,
  Mix-01 and Essential-Web fast/recovery/windows/campaign suites. The serial
  complement (`-n 0 -m "serial or serial_core or serial_heavy or
  serial_exclusive"`) collected no tests (exit 5): none of these files has a
  serial marker.
- `ruff check` and `ruff format --check` pass on all 12 changed or new Python
  files, including the evidence scripts.
- `mypy --strict` reports no issues in 8 files: 5 source modules and 3 test
  files. Compiled mypy ran this session.
- `mypy --strict scripts/mix01_source.py` reports 13 errors. All of them exist
  in HEAD's copy too (`import-untyped` when the script is checked alone, plus
  line 458). None are in the new lines.
- `git diff --check` exits 0, and the new files have no trailing whitespace.

**Not run:** the full offline acceptance suite (it is reserved for the release
gate), any network or live test, and CUDA tests (not relevant).

## Requirement ledger

| requirement | status |
|---|---|
| trace current parallelism | IMPLEMENTED (section 1) |
| generic row-group partition / ordered merge, no shared JSONL appends, no source copies | IMPLEMENTED, VERIFIED (tests, real shard) |
| byte identity, counts, order, codes, locators | VERIFIED (24/24 real runs; tests) |
| failure identity / no partial publication / restart | VERIFIED (tests) |
| explicit identity-bound limits + global budget | IMPLEMENTED, VERIFIED (tests) |
| measured RSS / speed matrix 1/2/4/8 | VERIFIED (warm cache, background load labeled) |
| cold-cache timing | NOT RUN (warm cache only; labeled) |
| hard (kernel-enforced) memory limit | OUT OF SCOPE (sampled ceiling documented) |
| production plan creation / acquisition / network | OUT OF SCOPE (not done) |
| full acceptance suite | NOT RUN (release gate only) |

**Open performance limitations**

- At W ≥ 8 the coordinator's pipe receive and SHA-256 work (about 18 CPU-s)
  limits further scaling. Shared-memory transfer could lift that limit but was
  not needed for the chosen W=4.
- The memory ceiling is sampled every 0.25 s, not enforced by the kernel.

## Artifacts

`docs/implementation/evidence/FINEPDFS-INTRAFILE-PARALLEL/`:

- `bench.py`: offline harness.
- `summarize.py`.
- `identity_check.py`.
- `runs-matrix.jsonl`, `runs-concurrent.jsonl`: one record per run, hashes and
  counts only, no corpus text.
- `summary.json`.
- `identity-check.json`.
- `matrix.log`, `concurrent.log`.

Scratch outputs were deleted after hashing.

## Next step

Generate (do not authorize) the FinePDFs production plan for operator review.
It now binds `row_group_parallel`. The inventory lister milestone noted in the
whole-file policy report must exist first:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py plan --source-key finepdfs --data-root G:\XLM
```

Review its `intra-file` line and digest, then stop before authorization.
