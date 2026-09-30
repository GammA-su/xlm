# Essential-Web fast bulk plan — 2026-09-30

**READY FOR HIGH-THROUGHPUT ESSENTIAL-WEB BENCHMARK**

Fast campaign `d7b1a503055d72e7082e74b1c58eaa9c1c52ed5282ec2f12df4876979cf45822`
supersedes the range-reader campaign `644be917…0ce9`, which is kept unmodified
as history and fetched nothing. **No bulk fetch ran and no live request was made.**
The first live step is a small benchmark, run by the operator. Batch 0 cannot
start before it passes.

The design, measurements and proofs are in
[ESSENTIAL-WEB-FAST-TRANSPORT.md](ESSENTIAL-WEB-FAST-TRANSPORT.md). Exact
commands and exit statuses are in
[COMMANDS.md](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/COMMANDS.md).

## What is unchanged

Read from the historical campaign and compared on every load; any difference
refuses.

| Invariant | Value |
|---|---|
| Source and revision | `EssentialAI/essential-web-v1.0` at the pinned revision |
| Inventory and order | 23,200 paths, digest `4bbd5517…63d8`, seed 20260930 |
| Batch membership | batch *n* = inventory files [32*n*, 32*n*+32) |
| Batch 0 membership digest | `c8e886c0…8e5`, equal to the historical dry run |
| Selector | frozen B-normal; policy and freeze digests unchanged |
| Adapters | the four adapter files, same hashes as the calibration seal |
| Stop targets | 2.64 / 2.64 / 1.32 GB canonical bytes (660M / 660M / 330M estimated tokens) |
| Quotas, ceiling | Mix-01 6B quotas; 33 batches |
| Malformed policy | the frozen per-pass rule |
| C04 / C05 | `suspect_with_mitigation`; C05 NOT RUN, required before tokenizer fit and training |

Membership through all 33 batches equals the historical campaign's
([dry-run.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/dry-run.json)).

## What changed

| | Historical | Fast |
|---|---|---|
| Transport | projected ranges, 946 requests per file | one stream, 2 requests per file |
| Raw artifact | `selected_records.jsonl`, 589.7 GB | source Parquet, 203.6 GB |
| Ledgers | plain JSONL, 103.5 GB | zstd, about 8.2 GB |
| Restart unit | one row-group slice of 32 files | one file |
| Prepare | reads 32 footers over the network | offline |
| Durable total | 749.9 GB | 268.4 GB |
| Batch time | 3.71 h modeled | 3 to 11 min modeled, by network rate |

One consequence for the malformed rule: a pass is now one whole file instead of
one row-group slice. The rule itself is unchanged.

## Batch size

**32 files, unchanged.** The restart unit is now a single file, so batch size no
longer sets restart granularity. It sets the authorization step and the stop
overshoot. The rule is the largest size whose batch stays within 15 minutes at a
200 Mbit/s planning floor and whose overshoot stays within 5% of the science
target ([batch-policy.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/batch-policy.json)):

| Files | Transfer | Local processing | Batch at 200 Mbit/s | Overshoot | Batches |
|---:|---:|---:|---:|---:|---:|
| 8 | 2.09 GB | 43 s | 1.4 min | 1.0% | 98 |
| 16 | 4.18 GB | 86 s | 2.8 min | 2.1% | 49 |
| **32** | **8.35 GB** | **172 s** | **5.6 min** | **4.1%** | **25** |
| 64 | 16.71 GB | 343 s | 11.1 min | 8.2% | 13 |

64 fails the overshoot limit. Keeping 32 also keeps every frozen batch
membership digest. The 200 Mbit/s floor is a planning assumption, not a measured
rate. Expected batches to the science target: 25; ceiling 33.

## One batch

1. **Prepare** (offline): membership, a whole-file plan through `xlm data plan`,
   ceilings, and the authorization digest. No footer or layout read.
2. **Run** (network): gate, authorize, then per file: stream to scratch, verify,
   copy to `G:`, adapt locally, seal a receipt, release scratch. Then the
   cumulative yield and the stop decision.

The digest binds the campaign, batch, membership, plan hash and these ceilings:

| Limit, per batch | Value |
|---|---|
| Per-file size | 512 MiB |
| Transfer | 24 GiB (expected 8.35 GB) |
| Requests | 512 (expected 64) |
| Durable output | 32 GiB |
| Scratch | 64 GiB cap |
| Rows | 8,000,000 (expected 2.64 M) |
| Deadline | 4 h per run; 1,800 s per file |
| Workers | up to 16 streams; default 8 streams, 12 processes |

Restart: a repeated Run skips sealed units, resumes a partial download from its
last checkpoint, and processes an already retained source file without a new
transfer. A unit is published with its receipt in one rename, so there is no
half-sealed state. A file that fails (oversize, drift, hash mismatch, malformed
limit) stops the batch; later batches cannot start.

The gate refuses when the benchmark has not passed, an earlier batch is
incomplete, the ceiling is reached, `G:` would fall under its 64 GiB reserve,
the 400 GiB campaign cap would be exceeded, or the historical campaign has any
sealed slice.

## Benchmark

[benchmark-plan.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/benchmark-plan.json),
digest `5f865608bb75a6272200c73d4a912d58182b7d991c65fff6ab5ed866aa308115`.

| Phase | Files | What it measures |
|---|---|---|
| 1 stream | batch 0, rank 0 | single-stream rate |
| 4 streams | batch 0, ranks 1–4 | aggregate rate |
| 8 streams | batch 0, ranks 5–12 | aggregate rate |
| Local processing | the same 13 files, 12 processes | rows/s on real files |
| Durable copy | one file to `G:`, then removed | copy rate |
| Parity | one calibration file, 207 MB | real bytes against the seal |

Each file is downloaded once: the three tiers use disjoint files. Expected
transfer is about 3.6 GB; the hard cap is 7.0 GiB. Each phase reports bytes,
wall time, MB/s, Mbit/s, requests, requests per second, retries, machine CPU,
received network bytes, written disk bytes and peak memory.

Nothing is retained. Files and outputs live under `C:\XLM-scratch\ew-fast\benchmark`
and are removed at the end; only the report under `G:\XLM\plans\ew-fast\benchmark`
is kept. The benchmark writes no receipt, so it is never campaign progress, and
batch 0 later transfers those 13 files again.

The parity file is rank 13,757 of the inventory, beyond the 1,056-file ceiling.
Its first 2,048 rows must reproduce the sealed calibration raw hash and all six
canonical hashes. The benchmark fails if parity differs, if a strong ETag is a
digest that differs from the content, or if a file is not transferred.

## Operator commands

From the repository root, in a shell that allows scripts:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass
Set-Location -LiteralPath 'F:\Project\xlm-data-ultrax'
. .\scripts\operator_storage.ps1
```

Live benchmark (about 3.6 GB; retains nothing):

```powershell
.\scripts\operator_essential_web_fast.ps1 -Stage Benchmark -Authorize 5f865608bb75a6272200c73d4a912d58182b7d991c65fff6ab5ed866aa308115
```

After it prints `benchmark PASS`, batch 0. Prepare is offline:

```powershell
.\scripts\operator_essential_web_fast.ps1 -Batch 0 -Stage Prepare
```

Review the printed ceilings and `AUTHORIZATION DIGEST`, then:

```powershell
.\scripts\operator_essential_web_fast.ps1 -Batch 0 -Stage Run -Authorize <digest printed by Prepare>
```

Later batches, one at a time, while the run ends with `CONTINUE`:

```powershell
.\scripts\operator_essential_web_fast.ps1 -Batch 1 -Stage Prepare
.\scripts\operator_essential_web_fast.ps1 -Batch 1 -Stage Run -Authorize <digest>
```

Resume an interrupted batch (no digest needed again), and status at any time:

```powershell
.\scripts\operator_essential_web_fast.ps1 -Batch 1 -Stage Run
.\scripts\operator_essential_web_fast.ps1 -Batch 1 -Stage Status
```

`-Workers` and `-ProcessWorkers` override the defaults within the authorized
ceiling. The run ends with `STOP` when the targets are met; a later top-up batch
needs `-TopUpReason '<reason>'`. Each run writes
`G:\XLM\plans\ew-fast\bNNNN\performance-NN.json` with the measured rates.

The historical driver `operator_essential_web_bulk.ps1` now refuses its network
stages. Its offline `Show` and `Status` still work.

## After the stop

Unchanged: freeze the canonical pool → C05 receipt → train and freeze the
tokenizer → exact count → top up deficient components from the next inventory
batches → refreeze and rescreen. `training_permitted` stays false.

## Readiness

| Check | Value |
|---|---|
| science_identical_to_historical_campaign | true |
| selector_frozen, source_revision_ok | true |
| membership_deterministic, equal to historical | true |
| transport_code_matches_freeze | true |
| real_row_replay_identical | true |
| prepare_needs_network | false |
| offline_tests_pass | true |
| first_batch_gate | REFUSE until the benchmark passes |
| benchmark_passed | false (first operator step) |
| bulk_fetch_run | false |
| c05_receipt_present | false (required before training, not before acquisition) |

## Remaining blockers

None for the benchmark. For batch 0: the benchmark must pass. If it shows that
the endpoint's strong ETag is a digest that does not match the content, or that
parity differs, the campaign stays closed and needs review. Known gap, unchanged:
the C05 matcher is not yet sized or wired for the Mix-01 pool freeze; that blocks
training, not acquisition.

**READY FOR HIGH-THROUGHPUT ESSENTIAL-WEB BENCHMARK**

Next: `.\scripts\operator_essential_web_fast.ps1 -Stage Benchmark -Authorize 5f865608bb75a6272200c73d4a912d58182b7d991c65fff6ab5ed866aa308115`
