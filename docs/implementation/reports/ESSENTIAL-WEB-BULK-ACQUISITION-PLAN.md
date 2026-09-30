# Essential-Web bulk acquisition plan — 2026-09-30

> **HISTORICAL (2026-09-30).** This campaign was stopped before any bulk fetch and is
> superseded by the [fast bulk plan](ESSENTIAL-WEB-FAST-BULK-PLAN.md). Its driver no
> longer fetches. The text below is preserved as written.

**READY FOR FULL ESSENTIAL-WEB ACQUISITION**

The campaign is frozen, planned and tested offline. **No bulk fetch ran.** The
first live step is the footer read of batch 0, by the operator. Campaign digest
`644be917fc40c124f3682eed0852101e1b66c8862adab73b65513cb2686b0ce9`.

Selector, mixture weights, quotas, calibration membership and source revision
are unchanged. Nothing was pushed. Exact commands, exit statuses and the
fixture/live distinction are in
[COMMANDS.md](../evidence/ESSENTIAL-WEB-BULK-ACQUISITION/COMMANDS.md). The
sealed calibration this plan builds on is in
[ESSENTIAL-WEB-PRODUCTION-CALIBRATION.md](ESSENTIAL-WEB-PRODUCTION-CALIBRATION.md).

Three things the operator should know first:

1. **Expected cost**: about 25 batches of 32 files, 64.4 M rows, 182 GB
   transferred and 90 hours of fetching with one worker. All four are estimates.
2. **Disk is the tight resource.** Retained output is estimated at 750 GB of the
   1 TB volume, 590 GB of it raw records. No existing contract permits deleting
   raw records, so nothing is deleted. See [Raw retention and disk](#raw-retention-and-disk).
3. **One authorization per batch.** Each batch prints a digest that binds its
   exact slice plans; the run needs that digest.

## Bottleneck and scan requirement

Rows each component needs alone for its frozen first-pass target (pooled
calibration yield, 4 bytes per token, linear):

| Component | Final exact | First-pass estimated | Required input rows |
|---|---:|---:|---:|
| essential_science | 600 M | 660 M | **64,424,483** |
| essential_practical | 600 M | 660 M | 22,754,957 |
| essential_prose | 300 M | 330 M | 2,740,950 |

Science controls the scan: 10.244553 estimated tokens per input row. At the
science requirement the expected pools are 1.869 B estimated practical tokens
(2.83 times target) and 7.756 B estimated prose tokens (23.5 times target).

### Token-sizing envelope

The stop condition is fixed in canonical bytes at 4 bytes per token. The 3 and 5
cases show what the same 660 M target would need under the other assumptions.

| Bytes/token | Rows | Science docs | Science bytes | Files | Transfer A (prefix-linear) | Transfer B (full-file) | Time A | Time B |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 3 | 48,318,362 | 297,861 | 1.98 GB | 585 | 324.6 GB | 136.3 GB | 246.8 h | 67.8 h |
| 4 | 64,424,483 | 397,148 | 2.64 GB | 780 | 432.8 GB | 181.7 GB | 329.1 h | 90.4 h |
| 5 | 80,530,604 | 496,435 | 3.30 GB | 975 | 541.0 GB | 227.1 GB | 411.3 h | 113.0 h |

A is the simple linear calibration extrapolation. It is not an acquisition
budget. B is the physical full-file estimate described below. Times are for one
worker.

The 660 M estimate is not exact sufficiency. 2.64 GB of canonical science text
gives at least 600 M exact tokens only if the trained tokenizer averages at most
4.4 bytes per token. At 5 bytes per token the final quota needs 73,209,640 rows;
the campaign reaches that by deterministic top-up, not by raising the target.

## Dispersion and execution contingency

The calibration is eight crawl clusters, not 16,384 independent rows. Science
yield ranges from 5.08 to 18.79 estimated tokens per row across crawls. No
confidence interval is claimed.

Two descriptive checks on the pooled figure:

- Leaving one crawl out moves the requirement to between 60,093,267 and
  73,140,172 rows.
- Weighting crawls by their inventory file counts, instead of equally, gives
  10.448 tokens per row and 63,167,162 rows. This assumes equal rows per file
  in every crawl, which is not verified.

**Quota headroom and execution contingency are separate.**

| | Value | Role |
|---|---|---|
| Quota headroom | 660 M against 600 M (10%) | frozen target; decides when to stop |
| Execution contingency | ceiling of 33 batches (1,056 files) | bounds how far the campaign may run |

The ceiling is the largest leave-one-out requirement (73,140,172 rows) in files
of the smallest observed size (69,622 rows). It is operationally useful: the
campaign is progressive and cannot know the yield in advance, so it needs room
beyond the 25 expected batches without being open-ended. It does not raise the
target; the campaign still stops at the first batch boundary that meets it.
Beyond 33 batches a new reviewed campaign version is required.

## Inventory capacity

Frozen inventory: 23,200 paths in eight crawl strata, digest
`4bbd5517e760971405d9aed56bba5b84877a6325d10f5a10a05b50f5a8d763d8`, recomputed
and matched. Order: SHA-256(seed | repository | revision | file), seed 20260930.

| Crawl | 2014-15 | 2015-32 | 2016-50 | 2018-05 | 2019-09 | 2021-04 | 2021-49 | 2024-26 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Files | 2,771 | 1,919 | 3,131 | 3,428 | 2,576 | 3,314 | 2,894 | 3,167 |

Rows are known for eight files (660,927 rows; 69,622 to 97,828 per file) and
unknown for 23,192. No row count was invented for them.

The central requirement is 780 files at the mean observed size, or 926 at the
smallest: 4.0% of the inventory. The inventory would be insufficient only if
files averaged under 2,777 rows, against 69,622 for the smallest seen. Capacity
is plausibly sufficient by a wide margin and **not proven**.

So the campaign is progressive: scan the next inventory batch, measure
cumulative canonical bytes, continue until the targets are met, stop, and top up
later from the next batches if the exact count is deficient.

## Physical full-file cost

Evidence: the eight real retained footers, all 69 row groups
([footer-layouts.json](../evidence/ESSENTIAL-WEB-BULK-ACQUISITION/footer-layouts.json),
sizes only).

- Row groups hold 10,000 rows; a file has 7 to 10 (the last one partial).
- The adapter projection is 101 physical leaves of 103 columns, 88.4% of file
  bytes. Text is 81.7% of the projected bytes.
- The certified reader (`window-v2`) reads projected chunks in 4 MiB ranges and
  handles one row group per file per plan. The nested taxonomy fields rule out
  the older projected reader.

**Why the prefix cost overstates.** A 2,048-row window already reads every
non-text chunk of its 10,000-row group in full, plus whole 4 MiB text buffers.
The model reproduces this exactly: in all eight files the measured transfer
equals non-text chunks + footer + 65,548 bytes + 2.000257 text buffers. The
residual is 1,076 bytes per file.

| | Transfer per row | 64,424,483 rows |
|---|---:|---:|
| A. Prefix-linear | 6,718.15 B | 432.8 GB |
| B. Full-file | 2,820.08 B | 181.7 GB |
| B, smallest to largest file | 2,611.58 – 2,935.12 B | 168.2 – 189.1 GB |

B is projected compressed chunks plus one footer read per row group. It
excludes retries and restarts, which the slice limits bound separately. It rests
on eight files; each batch is planned from its own footers, so the estimate is
replaced by measurement as the campaign runs.

Time in B is 737,893 requests at 0.274 s, plus body time at 0.463 s per MB, plus
0.61 ms per row. These coefficients come from the prefix windows and are not
measured on full groups.

Full-text-first stays. Nothing here reopens that decision.

## Execution design

No new acquisition framework. The campaign uses `xlm data plan`, `xlm data
fetch`, `xlm data adapt`, the existing verifier class, `mix01_inventory`
(`measure_unit`, `estimate`, `sufficiency`) and the frozen inventory order. New
code only plans batches and keeps the ledger:
`src/xlm/data/sources/essential_web_bulk.py`, `scripts/essential_web_bulk.py`,
`scripts/operator_essential_web_bulk.ps1`.

| Requirement | How |
|---|---|
| Deterministic order | batch *n* is inventory files [32*n*, 32*n*+32) |
| Bounded batches | 32 files; one slice per row-group index; explicit limits per slice |
| Immutable receipts | write-once ledger entry per slice, with the verification receipt |
| Restart | completed fetches, adaptations and sealed slices are skipped |
| No duplicate rows | accounting refuses any overlapping (file, row range) |
| Exact lineage | plan hash, selection hash, raw hash, source validators, document hashes |
| Revision drift | pinned revision in every URL; ETag and length must agree across slices |
| Malformed policy | the frozen adapter policy, unchanged |
| Resource budgets | per-slice limits, free-space reserve, footprint cap, batch ceiling |
| Cumulative yield | recomputed from the ledger after every batch |

One batch:

1. **Show** (offline): membership.
2. **Layout** (network): footers of the 32 files only, about 9 MB. No rows.
3. **Plan** (offline): up to 10 slice plans through `xlm data plan`, their limits,
   and the authorization digest.
4. **Run** (network): gate; record authorization and mint authorized plans; per
   slice fetch, adapt three views, seal; then cumulative yield and stop decision.

Each slice budget covers the fetch plus two complete restarts (factor 3),
because an interrupted selection restarts from zero with its spent budget kept.
A slice whose 24-hour deadline expires unfinished gets a fresh attempt
(`renew`), which needs its own authorization; the old attempt is preserved.

The campaign refuses to load if the adapter or selector code, inventory,
catalog or quota file changes after the freeze.

Calibration and probe rows are never counted. The ledger holds only campaign
slices, and all eight calibration files sit at inventory ranks 2,405 to 19,848,
beyond the 1,056-file ceiling.

### Production limits

| Limit | Value per slice | Basis |
|---|---|---|
| Transfer | 3 × (projected bytes + 1 MiB per file) | footers |
| Decompressed | 3 × 1.5 × projected uncompressed | measured decode ratio 1.238 |
| Requests | 3 × 1.25 × (buffered reads + 8 per file) | footers |
| Records / scanned | slice rows / 3 × slice rows | footers |
| Output / scratch | rows × 27,465 B / twice that | 3 × measured 9,154 B per row |
| Record size | 8 MiB | see below |
| Deadline | 24 hours | covers an overnight pause |
| Retries, timeout, workers | 5, 15 s, 1 | repository defaults; calibrated at 1 |

**Record size is raised from the 1 MiB default.** The largest of 16,384
calibration records is 540,293 bytes. One larger row would fail its whole slice
every time, since the reader has no skip. 8 MiB stays under the unchanged 32 MiB
parser bound. A slice that still hits the bound fails closed and needs review.

## Batch size

| Files | Rows | Transfer | Raw JSONL | Science est. tokens | Time | Slice transfer | Slice time | Batches |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | 0.66 M | 1.86 GB | 6.05 GB | 6.77 M | 0.93 h | 0.24 GB | 6.7 min | 98 |
| 16 | 1.32 M | 3.73 GB | 12.10 GB | 13.54 M | 1.86 h | 0.48 GB | 13.4 min | 49 |
| **32** | **2.64 M** | **7.46 GB** | **24.20 GB** | **27.08 M** | **3.71 h** | **0.96 GB** | **26.7 min** | **25** |
| 64 | 5.29 M | 14.91 GB | 48.40 GB | 54.17 M | 7.42 h | 1.92 GB | 53.5 min | 13 |

All values are estimates from eight footers and eight prefix windows, one worker.

**Chosen: 32 files.** The slice is the restart unit. The rule is the largest
size whose slice stays within 30 minutes and 1 GiB of transfer. 64 fails both.
32 also keeps a batch inside one working session and well under the 256-file
plan limit.

Expected batches to the science target: **25** (24.4 at the mean file size);
23 to 28 across the leave-one-out range; ceiling 33.

Offline processing per slice (verify, three adaptations, seal) is not measured
at this scale. `-Workers 2` is supported by the fetcher and changes no record,
but its speed is unmeasured.

## Practical and prose oversupply

**Option A: keep every accepted document.** This is the existing architecture.

- The mixture exposure plan already gives each source an exact token quota
  (`weight × budget`). A source with more material than its quota is simply not
  exhausted. Quotas do not change.
- Stopping publication of a component partway (option B) has no existing
  mechanism and would make membership depend on scan position.
- B would save little: canonical documents are about 57 GB in total, against
  590 GB of raw records that must be scanned for science either way.

The existing hash-ranked draw (`pools/tokenizer_fit.py`) selects the tokenizer
fit sample. A pool-level subsetter to avoid tokenizing the whole prose
oversupply is a pool-freeze question, not an acquisition one, and is not built
here.

## Raw retention and disk

**Raw records are retained. No deletion is implemented.** `CONTRACTS.md` and the
reports contain no permission to reclaim verified raw payloads, so none was
invented.

One change follows from measured size. `xlm data verify` publishes a second
copy of the raw file into the artifact store. Two copies of 590 GB do not fit
the volume. The campaign therefore verifies with the same verifier class
without publishing, and stores the receipt in the ledger. One raw copy remains.

Measured bytes per input row: raw 9,154.0, rejection ledgers 1,606.3, canonical
documents 880.0.

| Scenario | Rows | Steady state | Peak | Raw | Ledgers | Documents |
|---|---:|---:|---:|---:|---:|---:|
| First-pass target, 4 B/token | 64,424,483 | 749.9 GB | 755.8 GB | 589.7 GB | 103.5 GB | 56.7 GB |
| Leave-one-out maximum | 73,140,172 | 851.4 GB | 857.2 GB | 669.5 GB | 117.5 GB | 64.4 GB |
| Final quota at 5 B/token | 73,209,640 | 852.2 GB | 858.0 GB | 670.2 GB | 117.6 GB | 64.4 GB |

Volume `G:\XLM`: 1,000.2 GB total, 999.1 GB free when measured. Peak adds 5.9 GB
of slice staging.

Guards, checked before every slice: at least 64 GiB free after the slice's
caps, and a campaign footprint cap of 850 GiB (about 77.9 M rows).

**Consequence.** Essential-Web alone would use about three quarters of the
volume, and 87.6% of the scanned rows are admitted by no component. The other
Mix-01 sources and the token shards must fit in what remains. If that is not
enough, the options are a contract amendment permitting raw reclamation after
sealing, or more storage. That is an operator decision. It does not block
starting: the guards stop the campaign cleanly.

## Stop condition

Continue deterministic inventory batches until, over **complete batches**,
cumulative canonical bytes reach all of:

| Component | Required canonical bytes | = first-pass estimated tokens × 4 |
|---|---:|---:|
| essential_science | 2,640,000,000 | 660 M |
| essential_practical | 2,640,000,000 | 660 M |
| essential_prose | 1,320,000,000 | 330 M |

This is the repository's own mechanism: `mix01_inventory estimate` produced the
required bytes, and `mix01_inventory sufficiency` gives the verdict after every
batch. The campaign refuses to continue if its own decision and that verdict
disagree. Overshoot is at most one batch, about 4% of the science target.

After the stop the next batch does not run without an explicit top-up reason.
Then: freeze the canonical pool → C05 receipt → train and freeze the tokenizer →
exact count → top up deficient components from the next inventory batches →
refreeze and rescreen.

## Malformed rows

Calibration: 8 of 16,384 (0.0488%), inside the frozen policy; no pass stopped.
Dominant reason: `unknown_label:k` in 6 of 8 rows, a knowledge-domain label
outside the frozen set. The other is the known `invalid_fdc_syntax`. No new
renderer incompatibility. The threshold is unchanged. In the campaign a pass
above the threshold fails, its slice cannot be sealed, and the batch stops.

## C05

Obligation unchanged: `c04-benchmark-risk-v2`, `suspect_with_mitigation`,
`xlm.data.exclusion`. The campaign definition carries it with status NOT RUN.

C05 does not run before or during acquisition. It runs on the frozen canonical
pool, before tokenizer fitting and gradient training, and again on the final
pool after any top-up, because the receipt must match the final membership. The
stop decision always reports `training_permitted: false`. Official benchmark
claims stay blocked without the protected receipt.

Known gap, unchanged from the admission review: the matcher is not yet sized or
wired for the Mix-01 pool freeze. That blocks training, not acquisition.

## Dry run

Offline, on the real frozen inventory
([dry-run.json](../evidence/ESSENTIAL-WEB-BULK-ACQUISITION/dry-run.json)):

| Proof | Result |
|---|---|
| Batch 0 membership | 32 files, digest `c8e886c0…8e5` |
| Batch 1 disjoint from batch 0 | true (digest `fc4a6bec…b139`) |
| Restart reproduces membership | true |
| All 1,056 files through the ceiling distinct | true |
| Calibration measurement present, campaign rows counted | present, 0 |
| Gate for batch 0 on the real volume | RUN |
| Gate for batch 1 before batch 0 | REFUSE |

Real-footer slice planning, on the eight footer-certified files
([dry-slices](../evidence/ESSENTIAL-WEB-BULK-ACQUISITION/dry-slices/dry-slices.json)):
ten plans through the real `xlm data plan`, 660,927 rows, each a production plan
that the fetch gate refuses without authorization. Slice 0 models 226,939,951
projected bytes, the same figure the readiness review derived independently.
This is a demonstration, not a campaign batch, and was not executed.

The operator driver ran its offline `Show` stage against the real root. Its
`Layout` and `Run` stages were parsed and **not executed**.

## Operator commands

From the repository root, in a shell that allows scripts:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass
Set-Location -LiteralPath 'F:\Project\xlm-data-ultrax'
. .\scripts\operator_storage.ps1
```

First command (batch 0; reads 32 footers, fetches no rows):

```powershell
.\scripts\operator_essential_web_bulk.ps1 -Batch 0 -Stage Prepare
```

Review the printed slices and `AUTHORIZATION DIGEST`, then run the batch:

```powershell
.\scripts\operator_essential_web_bulk.ps1 -Batch 0 -Stage Run -Authorize <digest>
```

Every later batch, one at a time, while the run ends with `CONTINUE`:

```powershell
.\scripts\operator_essential_web_bulk.ps1 -Batch 1 -Stage Prepare
.\scripts\operator_essential_web_bulk.ps1 -Batch 1 -Stage Run -Authorize <digest>
```

Resume an interrupted batch (no digest needed again):

```powershell
.\scripts\operator_essential_web_bulk.ps1 -Batch 1 -Stage Run
```

Status at any time, offline:

```powershell
.\scripts\operator_essential_web_bulk.ps1 -Batch 1 -Stage Status
```

The run ends with `STOP` when the targets are met. A later top-up batch needs
`-TopUpReason '<reason>'`.

Treat batch 0 as the full-file check: compare its measured transfer with the
modeled bytes that `Plan` prints, and read
`xlm data performance --plan <slice plan> --scratch-dir <slice scratch>` for
time and peak memory on full row groups.

## Tests and checks

All offline; authored fixtures unless stated. A mock fetch proves campaign
logic, not live dataset compatibility.

| Check | Result |
|---|---|
| Calibration seal, real artifacts, write then `--verify` | exit 0 / 0 |
| New tests (`test_essential_web_calibration.py`, `test_essential_web_bulk.py`) | 8 + 25 passed |
| Focused selection, 20 modules | 505 passed, 0 failed, 0 skipped, 99.6 s |
| Ruff check, ruff format, strict mypy on the six new files | clean |
| Full and fast repository selections, CUDA | NOT RUN |

The focused selection covers the seal evidence, capacity and cost models,
inventory prefix, batch planner, restart and idempotency, cumulative yield,
stop condition, disk and resource bounds, malformed policy, admission, selector
regressions, acquisition plan, bounds, verifier and window reader, and the C05
receipt gate.

## Requirement ledger

| Requirement | Status |
|---|---|
| Measured capacity model, bottleneck, 3/4/5 envelope | VERIFIED (arithmetic on sealed counts) |
| Crawl dispersion and separate execution contingency | VERIFIED |
| Inventory digest and capacity assessment | VERIFIED digest; capacity NOT PROVEN, progressive policy IMPLEMENTED |
| Physical full-file model | VERIFIED against the eight prefix transfers; full groups NOT RUN |
| Restartable campaign, ledger, stop, gates, renewal | IMPLEMENTED, VERIFIED offline |
| Operator driver | IMPLEMENTED; `Show` VERIFIED on the real root; `Layout`/`Run` NOT RUN |
| Live footer read and bulk fetch | NOT RUN (operator) |
| Raw reclamation | OUT OF SCOPE (no contract permission) |
| C05 receipt | NOT RUN (after pool freeze) |
| Exact token counts | OUT OF SCOPE (tokenizer not frozen) |

Open limitations: full-row-group time and peak memory are unmeasured; the
two-worker speed is unmeasured; offline adaptation time at slice scale is
unmeasured; records above 8 MiB fail their slice; capacity rests on eight files.

## Readiness

| Check | Value |
|---|---|
| selector_frozen | true |
| source_revision_ok | true |
| production_admission_recorded | true |
| calibration_sealed | true |
| current_adapter_reproduces_calibration | true |
| inventory_ready | true |
| campaign_frozen | true |
| membership_deterministic | true |
| first_batch_gate | RUN |
| offline_tests_pass | true |
| batch_0_footers_read | false (first operator step) |
| bulk_fetch_run | false |
| raw_reclamation_permitted_by_contract | false |
| c05_receipt_present | false (required before training, not before acquisition) |

**READY FOR FULL ESSENTIAL-WEB ACQUISITION**

Next: `.\scripts\operator_essential_web_bulk.ps1 -Batch 0 -Stage Prepare`.
