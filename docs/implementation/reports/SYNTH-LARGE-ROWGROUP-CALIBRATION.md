# SYNTH large-row-group calibration: scan-bounded window decode

Date 2026-09-27. Branch `data/mix01-ultrax-6b`. Starting HEAD `60a14e4`
(clean tree). Offline only. No network, real fetch, SYNTH payload,
production admission, tokenizer, training, pilot or push happened. Every
byte in the tests and benchmarks is authored and served from local
memory or local files.

**Verdict: READY FOR SYNTH CALIBRATION RETRY.** The retry's first live
step reads only the footer (`sample-blocks`). It refuses before any
payload moves if the real per-column sizes break a bound (§23, risk 1).

## 1. Observed real layout (USER footer reconnaissance)

`PleIAs/SYNTH` @ `0d6813a2966662c39f22f0b9af28a0c1c9f7a437`, view
`default`, adapter `synth_en`. The shards `synth_001` to `synth_011`
share one layout: a single row group of about 155k rows, 14 columns,
about 472–474 MB compressed and 796–800 MB uncompressed.

| Shard | Rows | total_byte_size | Compressed (all columns) | Source |
|---|---:|---:|---:|---|
| synth_001 | 154,674 | 794,617,223 | 471,529,953 | driver refusal log `X:\XLM\calib\synth_en_explanations\logs\sample-blocks-20260927-143322532.log` (read-only) |
| synth_002 | 155,736 | 799,640,471 | 473,875,189 | USER footer reconnaissance |

The previous `sample-blocks` run stopped with: `no usable row groups
within bounds … compared total_byte_size=794617223 against
max_parser_bytes=33554432`.

## 2. Root cause

It is not a driver bug. Whole-row-group sampling compares the
**logical uncompressed size of the whole row group, all 14 columns**
(`total_byte_size`) against `max_parser_bytes` (32 MiB). That happens
twice: in `sampling._refusal_for_group` and again in
`records.check_row_group`. A SYNTH shard is one ~800 MB row group, so no
row group is ever eligible. Raising the bound would permit decoding
155k rows (more than `max_scanned_records` = 100k) and transferring up
to ~470 MB (more than the 256 MiB pilot transfer bound) to keep 1,000
rows.

## 3. Current-path audit (before this change)

`sample-blocks` → `data plan --row-ranges` → `fetch`:

1. `sample-blocks` reads each footer through bounded ranges or a local
   file. It builds `RowGroupSpec`s holding only aggregate sizes and marks
   a group unusable when `total_byte_size > max_parser_bytes` or any
   column (projected or not) exceeds the 15× ratio. It then chooses
   whole aligned row groups, so `row_ranges` are always whole groups.
2. `plan` binds `row_ranges`, `projected_fields` and `limits` into the
   behavioral hash. The pilot gate (`plan_requires_production_admission`)
   checks only `max_transferred_bytes`, `max_records`,
   `max_output_disk_bytes` and `is_pilot`.
3. `fetch` → `acquire_selection` → `_parquet_selection_projected`:
   - calls `check_row_group`, the same whole-group `total_byte_size`
     check;
   - computes the spans of the selected column chunks, splits merged
     spans at `max_parser_bytes`, and prefetches them;
   - opens `ParquetFile(buffer_size=0)`. PyArrow then reads **each whole
     column chunk in one read**. A chunk larger than 32 MiB therefore
     can never be served even if the group check passed: the split spans
     do not cover the read, and the fallback `fetch_range` refuses it;
   - `list(iter_batches(...))` decodes the **entire projected row
     group**;
   - charges the compressed chunk sum as "decompressed";
   - charges every decoded row as scanned.

What `max_parser_bytes` protects today is a mix of four things:

- (a) Thrift footer string and container limits;
- (b) the size of each HTTP range (`fetch_range`);
- (c) a whole-row-group "decode work" proxy (logical size of all
  columns, projection ignored);
- (d) the size of split coalesced spans.

(c) is the misleading one: it charges unprojected columns and uses an
uncompressed logical size for a transfer-shaped bound.

## 4. SYNTH selected-column physical sizes

- **Real per-column sizes for the 11 projected fields are NOT KNOWN
  offline.** The reconnaissance reported only aggregates, and no SYNTH
  shard or per-column footer dump exists on disk. They are not guessed
  here.
- **Hard upper bounds from the aggregates:** selected compressed ≤
  471,529,953 B and selected uncompressed ≤ 794,617,223 B for
  synth_001.
- **Largest individual chunk, and whether any page or range exceeds
  32 MiB:** also unknown for the real files. Under window decode a large
  chunk is no longer a problem, because chunks are streamed in 4 MiB
  ranges (§7). A single *page* larger than 32 MiB is still refused
  (tested).
- **Where the real numbers will come from:** the retry's
  `rows.evidence.json` records them per column: `selected_columns`,
  `selected_compressed_bytes`, `selected_uncompressed_bytes`,
  `largest_selected_chunk` and `unselected_compressed_bytes`. It is
  built from the footer only, before any payload is fetched.
- **Authored real-scale shapes (§19) for reference:**
  - typical split: projected 228 MB compressed / 374 MB uncompressed,
    largest chunk 124.7 MB;
  - worst split: projected 475 MB / 779 MB, largest chunk 372 MB.

## 5. Page-level feasibility (design option A)

**Not available through a safe public API.**

- PyArrow 25.0.1 (locked) exposes only `has_offset_index` and
  `has_column_index`. It does not expose page-location contents, and it
  has no row-range read. `iter_batches` takes only row groups.
- True page seeking would need a hand-written Thrift `OffsetIndex`
  parser plus a synthesized mini-Parquet file built from page slices.
  That is a fragile custom parser with a large attack surface.
- Files written through PyArrow default to `write_page_index=False`, so
  SYNTH may not carry a page index at all.

What *is* available and safe, verified empirically:

- `ParquetFile(buffer_size=N, pre_buffer=False)` reads each column
  chunk **sequentially** in N-byte ranges.
- A lazy `iter_batches` that is stopped early reads **only the prefix**
  of each projected chunk.
- Unprojected chunks are never read.
- Probe result: 3,072 rows from a 155k-row group read 1.7 MB with a
  1 MiB buffer, versus 17.3 MB with `buffer_size=0`.

## 6. Chosen design: scan-bounded streaming window (`parquet_window` v1)

This is option A′: sub-row-group windows without page seeking. It stays
inside the normal selected-record path (`AcquisitionPlan` →
`BoundedFetcher` → journal → verifier → adapter → publication). No
separate calibration path (option C) was needed.

**Sampling: `xlm data sample-blocks --mode window`**

- Takes `--adapter-spec` or `--project-fields`, plus
  `--window-max-scan-rows`, `--window-buffer-bytes` and
  `--window-batch-rows`.
- Produces at most one window per file, inside one row group
  (deterministic, §14).
- Row-group eligibility is projection-aware and judged on the
  **worst admissible window**, so it does not depend on the chosen
  start.

**Plan: `xlm data plan --parquet-window-scan-rows R`**

- Also takes `--parquet-window-buffer-bytes B` and
  `--parquet-window-batch-rows b`.
- Sets `AcquisitionPlan.parquet_window` (`ParquetWindowDecode`).

**Fetch: `_parquet_selection_window`**

- Reads the footer through exact bounded ranges.
- Requires the window to lie in one row group (otherwise it refuses).
- Runs `check_window_group` (§7).
- Streams projected chunks through a B-byte buffered stream, with lazy
  batches of b rows, and stops after the batch that reaches the window
  stop.
- Charges every decoded row as scanned and every decoded batch's
  `nbytes` as decompressed.
- Records and locators are **byte-identical** to the legacy projected
  decode of the same rows (tested).

**Rejected alternatives:**

- **Raising `max_parser_bytes`:** forbidden, and it conflates risks.
- **B, whole projected group decode:** scans 155k rows (more than
  `max_scanned_records` = 100k) and could transfer up to ~470 MB (more
  than 256 MiB). That is production-scale physical work carried under a
  pilot label.
- **A, page index:** §5.
- **C, a separate calibration path:** unnecessary, because the normal
  path now carries the window semantics and disclosures.

## 7. Safety bounds (exact)

| Risk | Bound | Where |
|---|---|---|
| Footer/Thrift allocation | `thrift_*_size_limit = max_parser_bytes` (32 MiB) | unchanged |
| Single HTTP range | `fetch_range` refuses > `max_parser_bytes`; stream buffer `stream_buffer_bytes` ≤ `max_parser_bytes` (plan validation) | unchanged + plan |
| Transfer | `max_transferred_bytes` 256 MiB (durable budget) | unchanged |
| Requests | `max_requests` 100 | unchanged |
| Decoded bytes | Arrow batch `nbytes` for **all** decoded batches, against `max_decompressed_bytes` 512 MiB | new charge |
| Decompression ratio | per **projected** column when its total uncompressed size > `ratio_exempt_bytes` (16 MiB), plus projected aggregate; unprojected never read | new shared rule `ParquetWindowDecode.ratio_refusal` |
| Scanned rows per window | `expected_scan_rows = min(N, ceil(stop/b)·b)` ≤ `max_window_scan_rows` (refused before any column byte) | `check_window_group` |
| Cumulative scanned rows | `max_scanned_records` 100k (durable lease); plan requires `max_window_scan_rows` ≤ it | unchanged + plan |
| Memory | one b-row batch plus ≤ one B buffer per projected column plus the current page | measured §19 |
| No whole-file fallback | ranges only; a non-206 or ignored Range is refused; window crossing a group boundary is refused | unchanged + new |

The SYNTH driver policy is B = 4,194,304, R = 16,384, b = 256, with
`ratio_exempt_bytes` at its default of 16 MiB. All other limits stay at
the pilot defaults; the driver has no limit parameters.

**Why the ratio rule was refined.** The legacy per-column rule refused
harmless columns in the authored benchmark. Sequential `synth_id`-style
values compress 18× under zstd, yet total only 3 MB. A column whose
entire decoded size is ≤ 16 MiB cannot threaten a 512 MiB budget. Any
column above that is still held to the 15× ratio, and so is the
projected aggregate, which catches a bomb spread across exempt columns
(tested). Sampling and fetch call the same function, so they cannot
disagree.

## 8. Parser-bound interpretation

The window path separates the four roles of `max_parser_bytes` (§3):

- (a) Thrift footer limits: unchanged.
- (b) Per-range size: unchanged, and now also the ceiling for
  `stream_buffer_bytes`.
- (c) Whole-group logical size: **replaced** by projection-aware
  checks: the scan-row bound, the projected ratio rule, and decoded-byte
  charging.
- (d) Span splitting: not used, because streaming needs no coalescing.
  Plan validation refuses `range_coalesce_bytes` together with a window.

`check_row_group` and every legacy path are **unchanged** for plans
without `parquet_window`. Changing them would silently change the
acceptance semantics of existing plan hashes.

## 9. Scanned-row accounting

- Every row Arrow decodes counts as scanned, including rows before the
  window start and the tail of the last batch. The journal
  (`records_scanned`), the perf counters and the evidence field
  `expected_scan_rows` all agree (tested).
- The evidence also records `expected_skipped_decoded_rows`.
- **Prediction for the real retry:** `synth_001.parquet` has 154,674 rows
  and a start domain of 16,384. The deterministic start is 8,737, so the
  window is rows **[8737, 9737)** of row group 0.
- That window decodes **9,984 rows (6.45%)** to keep 1,000. It is not a
  1k physical scan, and not a 155k one.

## 10. Transfer and decompression accounting

- **Transfer** is exact response-body bytes through the existing budget.
  It covers the header, the footer (Arrow's speculative 64 KiB tail read
  can overlap the last chunk's bytes; that is metadata), each projected
  chunk's dictionary page, and the data pages up to the window stop plus
  at most one buffer.
- **Decompression** is charged as decoded Arrow bytes.
- **Worst-case estimate for synth_001, even if all 471.5 MB were
  projected:**
  - ~30.4 MB compressed prefix and ~51.3 MB decoded, assuming uniform
    row size;
  - plus at most 11 × 4 MiB of buffer slack and ≤ 1 MiB per dictionary
    page;
  - far below 256 MiB transfer, 512 MiB decoded and 100 requests.
- The estimates in the evidence are labeled as estimates. The runtime
  budgets are the hard bounds.
- **Calibration yield honesty.** A window run transfers prefixes for
  every decoded row, about 10× the retained rows' share.
  - `mix01_inventory.py measure` records, for window plans only,
    `raw_transferred_bytes`, `records_scanned`, `transfer_basis =
    calibration_window_retained_share` and a CALIBRATION-ONLY note.
  - It sizes with `ceil(raw × retained / scanned)`. That is
    conservative: buffer and dictionary overhead only inflate the raw
    figure.
  - `record` carries the disclosure into `calibration.json` and refuses
    a measurement whose `transferred_bytes` is not that share.
  - Combining entries that carry the window basis is refused.
  - Legacy measurements are byte-identical.
- The perf sidecar for window runs carries `measurement_class:
  calibration_window` and a CALIBRATION-ONLY note, and
  `compare_perf_docs` refuses to rank window runs against other runs.

## 11. Plan identity

- `AcquisitionPlan.parquet_window: ParquetWindowDecode | None = None`.
  Its fields are `policy_version` (1), `stream_buffer_bytes`,
  `max_window_scan_rows`, `batch_rows` and `ratio_exempt_bytes`.
- It is bound into the **behavioral hash only when set**, so it binds
  execution identity and authorization.
- It is **excluded from the selection hash**, because records are
  byte-identical to the legacy decode of the same rows. Verification and
  adaptation therefore accept the same locators.
- `schema_version` stays 2.
- Legacy hashes are pinned in a test. Three plans computed at `60a14e4`
  (whole-file, projected, coalesced attempt 3) produce identical
  behavioral and selection hashes.
- Sampling mode `window` is a new mode. `rowgroup` and `contiguous`
  reports are key-identical to before, so the rowgroup identity is not
  reused.
- **Plan validation** refuses a window plan when:
  - there is no projection;
  - the mode is whole-file;
  - coalescing is set;
  - any selected file is not Parquet;
  - the buffer exceeds `max_parser_bytes`;
  - `max_window_scan_rows` exceeds `max_scanned_records`;
  - `batch_rows` exceeds `max_window_scan_rows`;
  - `ratio_exempt_bytes` exceeds `max_decompressed_bytes`;
  - any range is longer than the scan bound.

## 12. Production-gate impact

- For window plans only, `plan_requires_production_admission`
  additionally requires all of the following:
  - `max_decompressed_bytes` ≤ 512 MiB;
  - `max_scanned_records` ≤ 100k;
  - `max_requests` ≤ 100;
  - `max_window_scan_rows` ≤ 100k;
  - `stream_buffer_bytes` ≤ 8 MiB;
  - `ratio_exempt_bytes` ≤ 16 MiB.
- A small retained count cannot carry larger physical work onto the
  pilot path.
- `data plan` now derives `is_pilot` from that same gate. The result is
  identical for legacy plans.
- **Legacy plans keep the original three-limit gate.** Their blind spot
  remains; see §23.

## 13. Adapter projection audit (`synth_en`, 11 columns; nothing removed)

| Column | Role |
|---|---|
| `query_seed_text`, `query`, `synthetic_answer` | required: rendered training text |
| `language` | required: filtering (keeps `en`, rejects others) |
| `synth_id` | required: provenance (`source_metadata.synth_id`) |
| `seed_license` | required: license provenance |
| `exercise`, `model`, `query_seed_url`, `additional_seed_url`, `words` | optional metadata, preserved and type-checked when present |

The adapter reads all 11 columns. Removing any of them would change
canonical metadata, so the contract is unchanged.
`synthetic_reasoning` and the two other unprojected columns are never
requested (tested at the byte-range level).

## 14. Deterministic window policy (window-v1)

For a row group of N rows, window size K = min(block_records, remaining
target) and scan bound R with batch b:

- **Start domain D:** D = N if N ≤ R, else ⌊R/b⌋·b.
- **Start:** `start = sha256("seed|source|view|revision|file|row_group|window-v1|start") mod (D − min(K,D) + 1)`.
- **Window:** `[start, start + min(K, D))`.
- **File order and group choice:** versioned SHA-256 choices as well.

The evidence (`rows.evidence.json`) records:

- `window_policy` (all fields and the construction);
- `row_group`, `group_rows`, `start_domain_rows`;
- `start_in_group`/`stop_in_group`, `start_row`/`stop_row`,
  `selected_count`;
- `expected_scan_rows`;
- per-column selected sizes, `largest_selected_chunk` and the
  physical-transfer ceiling;
- estimates for the chosen window **and** `eligibility_worst_case`;
- a clustered/nonuniform bias warning;
- the start-restriction warning ("first 16384 of N rows").

A test recomputes the start independently with `hashlib`.

## 15. Resume semantics

This is unchanged machinery:

- An interrupted attempt leaves its spent transfer charged in the
  journal. The rerun makes a fresh private attempt from row 0 of the
  group and produces **byte-identical** output (tested with a fault after
  six requests).
- A completed output is a cache hit with zero requests.
- Attempt renewal (`attempt=2`) gives a new execution identity and
  byte-identical selection bytes.
- Driver adoption gained agreement checks for `--sample-mode`,
  `--adapter-spec` (projection) and all three window values on
  `sample-blocks` and `plan`. A legacy request never adopts a window plan,
  and a window request never adopts a legacy one. Nothing is deleted or
  overwritten.

## 16. Normal-source non-regression

- **Unchanged code:** `check_row_group`, `_parquet_selection_exact`,
  `_parquet_selection_projected` and the rowgroup/contiguous planner.
- **Unchanged identities:** the pinned legacy hashes; the legacy
  sampling report has no new keys; legacy perf sidecars have no new
  keys; legacy measurements are byte-identical.
- **Driver:** the SimpleStories argv for sample-blocks, plan and both
  adoption calls is asserted **exactly** equal to the `60a14e4` shape.
  Only the SYNTH unit declares a `Window` policy.
- **UltraX-shaped groups:** several 1,000-row groups sample and fetch in
  window mode, and their bytes equal the legacy projected fetch of the
  same rows.

## 17. Tests (exact commands, all offline, Windows 11, Python 3.12.13, PyArrow 25.0.1)

- **New:**
  - `tests/test_parquet_window_sampling.py`: 32 tests over authored
    fixtures, including a 155,000-row single-row-group SYNTH-shaped
    shard;
  - `tests/test_operator_driver_window.py`: 2 tests (PowerShell argv
    capture, executes nothing);
  - `tests/test_calibration_adopt.py`: +2 window adoption tests.
- **Coverage:**
  - legacy refusal reproduced;
  - deterministic window;
  - projected vs unprojected (byte-range level);
  - oversized projection;
  - projected and unprojected ratio bombs;
  - range bound (a 1 MiB page against a 512 KiB bound);
  - scan bound;
  - cumulative scanned bound;
  - transfer bound;
  - final partial window;
  - group-boundary crossing;
  - resume/retry/attempt identity;
  - perf disclosure;
  - CLI sample → plan;
  - the full chain sample → plan → fetch → verify → `data adapt synth_en`
    → measure → record.

```text
uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_parquet_window_sampling.py tests/test_operator_driver_window.py -n 0 -q
  -> 34 passed (exit 0)
uv run --offline --locked --extra cpu --extra eval python -m pytest <19 related files> -n 8 --dist=worksteal --max-worker-restart=0 -m "not serial and not serial_core and not serial_heavy and not serial_exclusive" -q
  -> 297 passed (exit 0)
uv run --offline --locked --extra cpu --extra eval python -m pytest <same 19 files> -n 0 -m "serial or serial_core or serial_heavy or serial_exclusive" -q
  -> 4 passed (exit 0)
ruff check / ruff format --check (src, scripts, tests) -> clean
mypy (7 changed src modules) -> 1 pre-existing unused-ignore at data_cmd.py numpy import (present at HEAD)
```

The 19 related files are:

- the window, rowgroup and driver tests;
- `acquisition_{plan,fetcher,bounds,verifier,leases,performance}`;
- `selected_record_{concurrency,encoding}`;
- `calibration_adopt`, `production_ingest`, `sampling_trace`,
  `opus_review_accounting`, `p26_streaming`, `mix01_inventory`.

The full offline acceptance suite was **NOT RUN**; this was focused
testing, not a full-suite pass.

## 18. Mutations and adversaries (14/14 killed)

Each mutation was applied temporarily, the window suite was run, and the
source was restored byte-for-byte:

- ignoring projection in sampling safety, and in fetch safety;
- exempting every column; dropping the aggregate ratio check;
- undercounting scanned rows (retained only);
- whole-chunk reads (`buffer_size=0`);
- a silently allowed large range;
- a nondeterministic start;
- a seed dropped from the start;
- evidence claiming a retained-sized scan;
- no scan-bound check;
- the window left out of the behavioral hash;
- a gate that ignores the window;
- measure recording the raw window transfer.

## 19. Local resource measurement (authored, real-scale, no network)

Command:

```text
uv run --offline --locked --extra cpu --extra eval python scripts/bench_parquet_window.py run --workdir <scratch> --profile {typical|worst}
```

Setup:

- A 155,736-row single-group shard built from seeded random letters with
  zstd: 487 MB on disk, 797 MB `total_byte_size`, 14 columns.
- Fetched with the driver policy through the real `BoundedFetcher` in a
  fresh child process.
- Peak RSS is the Windows peak working set of that process; its baseline
  after imports is about 63 MB.

| Profile / window | Rows scanned | Retained | Transferred | Requests | Decoded charged | Peak RSS | Wall |
|---|---:|---:|---:|---:|---:|---:|---:|
| typical, sampled [13310,14310) | 14,336 | 1,000 | 29.9 MB | 16 | 35.2 MB | 127.5 MB | 1.7 s |
| typical, worst admissible [15384,16384) | 16,384 | 1,000 | 34.1 MB | 17 | 40.3 MB | 127.4 MB | 1.8 s |
| worst, sampled [1996,2996) | 3,072 | 1,000 | 21.5 MB | 14 | 15.4 MB | 133.7 MB | 1.6 s |
| worst, worst admissible [15384,16384) | 16,384 | 1,000 | 59.2 MB | 23 | 82.8 MB | 138.9 MB | 2.3 s |

Selected-column bytes by profile:

- typical: 228.3 MB compressed / 373.9 MB uncompressed, largest chunk
  124.7 MB;
- worst: 475.4 MB / 778.5 MB, largest chunk 372.0 MB.

The estimates held against every run:

- typical: sampled window 38.2 MB and 22 requests; worst-case domain
  41.2 MB and 23 requests;
- worst: sampled window 26.6 MB and 20 requests; worst-case domain
  67.2 MB and 29 requests.

The numbers are local and loopback-free, and wall time excludes real
network latency. They are not production-fetch throughput.

## 20. Files changed

- `src/xlm/data/acquisition/plan.py`: `ParquetWindowDecode`, plan field,
  validation, hash binding, pilot constants, gate.
- `src/xlm/data/acquisition/records.py`: `check_window_group`.
- `src/xlm/data/acquisition/selection.py`: `_parquet_selection_window`
  and dispatch.
- `src/xlm/data/acquisition/sampling.py`: `ColumnChunkSpec`,
  `ChosenWindow`, the window planner, evidence.
- `src/xlm/data/acquisition/fetcher.py` and `perf.py`: calibration-window
  sidecar disclosure and ranking refusal.
- `src/xlm/cli/data_cmd.py`: `sample-blocks --mode window` options,
  `plan --parquet-window-*`, gate-derived `is_pilot`.
- `scripts/calibration_adopt.py`: mode, window and projection agreement
  checks.
- `scripts/mix01_inventory.py`: window transfer basis in measure and
  record.
- `scripts/operator_calibrate_remaining.ps1`: SYNTH unit `Window` policy
  only.
- `scripts/bench_parquet_window.py`: new.
- Tests: `tests/test_parquet_window_sampling.py`,
  `tests/test_operator_driver_window.py` and
  `tests/files/calibrate_driver_argv.ps1` (new);
  `tests/test_calibration_adopt.py` (+2).
- Docs: this report, `STATUS.md`, `ROWGROUP-SAMPLING.md` §6 and
  `MIX01-CALIBRATION-REMAINING.md`.

## 21. Commit

The commit follows this report on `data/mix01-ultrax-6b`. It is not
pushed.

## 22. Exact USER retry command

`X:\XLM\calib\synth_en_explanations` holds only logs; no rows, plan or
fetch exist. Step 1 is a footer-only review point:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit synth_en_explanations -Stage SampleBlocks
```

Review `X:\XLM\calib\synth_en_explanations\rows.evidence.json`. Expect:

- rows `[8737, 9737)` of `synth_001.parquet`;
- `expected_scan_rows` 9,984;
- the real per-column projected sizes;
- `eligibility_worst_case` within the pilot bounds.

Then run the remaining stages; completed ones are adopted:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit synth_en_explanations -Stage All
```

## 23. Remaining risks

1. Real per-column sizes and ratios are unknown until step 1. A projected
   column larger than 16 MiB decoded with a ratio above 15×, or pilot
   estimates exceeded, make `sample-blocks` refuse with numbers and move
   no payload.
2. The byte and request estimates assume uniform row size. Skewed rows
   near the start of the group could transfer more than estimated. The
   runtime budgets are hard and fail closed; no partial output is ever
   published.
3. Dictionary pages are read whole. A writer with a dictionary or data
   page larger than 32 MiB is refused, correctly but blockingly.
4. Arrow allocates each page at its header-declared size before the
   decoded batch is charged. This is pre-existing on every path and is
   bounded by the chunk metadata that the ratio rule checks.
5. The sample is clustered: one 1,000-row window from the first 10.6% of
   one shard. The bias is disclosed in the evidence. The calibration
   yield is a single-window estimate.
6. The calibration yield uses the retained-row share (uniform bytes per
   decoded row), disclosed in `calibration.json`.
7. **Production SYNTH acquisition is still unsolved.** Legacy
   whole-group paths still refuse 155k-row groups, and a plan holds one
   range per file. Bulk SYNTH needs its own reviewed design. The window
   path is not authorized for production (gate §12).
8. The legacy production gate still ignores decompression, scan and
   request limits for non-window plans. It was left unchanged to avoid
   silently re-classifying existing plans.
9. Lazy buffered reads are PyArrow-behavior-dependent (verified on the
   locked 25.0.1). Tests assert prefix-only transfer and no unprojected
   bytes, so an upgrade that changes this fails loudly.

**Verdict: READY FOR SYNTH CALIBRATION RETRY.**
