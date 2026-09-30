# Essential-Web Batch-0 recovery — 2026-09-30

**READY TO RESUME ESSENTIAL-WEB BATCH 0**

Implementation and offline validation are complete. Production remains **31/32
sealed (96.875%)**. The operator must authorize the additive recovery digest in
the command below before the remaining unit can run. No authorization was
recorded on the operator's behalf, and no production unit was sealed here.

No external network, redownload, selector, canonical serialization, mixture,
quota, stop-rule change, fast-transport research, or push. Dependencies remain
the existing Python 3.12.13/uv.lock CPU/eval environment. Authored transport tests
use loopback HTTP; the real-file diagnostic and replay use retained local bytes.

## Authoritative state and diagnosis

Read the frozen campaign, `G:/XLM/plans/ew-fast/b0000/batch.json`, authorized plan,
31 immutable unit receipts, retained source identities, canonical outputs and
compressed rejection ledgers. Old stdout was not the source of state.

| Item | Verified value |
|---|---|
| Campaign | `8e42ba31b0ef9fcbae1079cebf88d2449b88a9b37ec52b68c7a408eb2546bb8c` |
| Total / sealed / incomplete | 32 / 31 / 1 |
| Completion | 96.875% (dashboard rounds to 96.9%) |
| Remaining unit | `f00026` |
| Remaining file | `data/crawl=CC-MAIN-2016-50/train-02164-of-03132.parquet` |
| Retained source size | 205,781,426 bytes |
| Retained SHA-256 | `06dfd85a171f7a1a155fa34493b9f61b3de0ff16b46f0765d29c2631fbf02ad1` |
| Failing row | zero-based 58,327; one-based 58,328 |
| Responsible field/type | `text`, string |
| Text UTF-8 bytes | 11,476,241 |
| JSON-encoded text value bytes, including quotes/escapes | 11,489,800 |
| Entire canonical raw record, including newline | **11,494,172 bytes** |
| Old configured record bound | **8,388,608 bytes (8 MiB)** |
| Selected record with acquisition locator | 11,494,805 bytes |
| B-normal decision | **essential_practical**, component stage, no rejection reason |
| Structural outcome | practical adapter accepts; science/prose return `EssentialWebSelectorOtherComponentError` |
| Exception | `RecordLimitError`: Parquet record byte bound exceeded |

**Cause A: legitimate oversized upstream data.** The complete retained file
matches its identity; its footer and row-group bounds pass. All 69,697 projected
rows were scanned. The certified `encode_record` and whole-Parquet encoder
agree byte for byte for every row. Exactly one row exceeds the old bound, and
11,494,172 is the maximum across the entire file. This is neither corruption
nor a locator-accounting bug. No document text was printed or added to Git.

The smallest demonstrated bound is therefore **11,494,172**, applied only to
this file with this SHA-256. It is not rounded up or made a new global default.
The existing 8,192-byte locator allowance and canonical validation overhead
stay unchanged; the canonical validator now uses the unit's effective bound.
Another oversized record, corrupt source, changed identity, or excessive
malformed count still refuses. The general campaign record bound stays 8 MiB.

## Reuse, recovery identity and authorization

The audit rehashed every sealed source, document file, compressed ledger and
adaptation summary, and decompressed each ledger to verify its uncompressed
hash. `resume-check` independently rehashed the retained artifacts against the
receipts. All **341 recorded artifact sizes and modification times** stayed
unchanged. The 31 receipt digests are pinned in the amendment and cannot be
silently replaced. Verification reads bytes; it does not decode or adapt any
sealed source.

The scheduler now shares one path with the read-only `resume-check` command.
It skips all verified seals and prefers a verified retained source even when
an interrupted scratch checkpoint exists. That checkpoint must match source
path, URL, ETag, length and SHA-256. Its prior transfer charge survives in the
new receipt. Partial-download recovery remains covered by the transport tests.
Unsealed private staging is rebuilt; published artifacts are never replaced.

The changed telemetry code is part of the original transport fingerprint, and
the new bound exceeds the old authorized processing envelope. Pretending the
old authorization covers those changes would be wrong. Instead, the existing
campaign and seals remain immutable, with an **additive Batch-0-only amendment**:

| Binding | Value / change |
|---|---|
| Original campaign | unchanged, `8e42ba31…bb8c` |
| Original batch digest | unchanged, `699eec085a5b2ab3950dde97fd1838bb97ebcf66508d914aeef926877c9d3576` |
| Original acquisition plan hash | unchanged, `fb3ccb390609069bee07eb87aa397a58f8d252a8fab292919825895d59c30397` |
| Original batch authorization | unchanged, `46728328e84b54e16d93878898d4dd8d607ee8d890923d7f72e94c7ab2d0597f` |
| **New recovery authorization digest** | **`51c09f0b15e6b19ebeb9854c6d65ec1994b473467b555723f0924606574c24db`** |

The amendment binds the original campaign/batch, previous and current code
hashes, exact source hash, new processing bound, audit hash and all 31 previous
receipt digests. Changed code refuses until refrozen. Execution requires
`recovery-authorization.json` matching this digest. The remaining receipt will
record the amendment and effective bound alongside its original acquisition
lineage. Original C04 admission, benchmark evidence, selector identity and
batch-boundary stop checks remain enforced. The amendment is not permission
to start later batches; those require a separately reviewed operational freeze.

## Dashboard and ETA

No dependency was added. Parent-side telemetry renders an ANSI TTY frame about
once a second; redirected output gets a durable snapshot every 30 seconds and
at seals. The PowerShell Run/Resume path inherits the terminal. Worker counters
are small atomic metadata files updated at most once per second. Output shows
files, verified/received bytes, MB/s and Mbit/s, worker counts, processing rows,
backlog, elapsed time, ETA, malformed/retry/failure counts, scratch/cap, durable
batch footprint, free disk and active filenames. Unknown totals remain `?`.

Rates use a time-weighted **15-second EWMA**. ETA is the maximum of remaining
download bytes/rate and **all remaining processing rows/rate**, including
backlog and known rows not yet processed. It never sums overlapping stage
times. At least two intervals and recent positive work are required. Unknown
totals or a required stage stalled for 15 seconds show `calculating...`;
completion is zero. Retry traffic contributes to throughput but not unique
file completion. This is an approximate stage estimate: unknown future footer
row counts prevent an ETA, and sealing tail latency is not predicted.

Campaign target displays use the existing canonical-bytes/4 estimated-token
accounting over **sealed receipts, including the incomplete batch**. Only
complete batches still count for the stop decision. Current sealed yield:

| View | Documents | Estimated tokens / target | Progress |
|---|---:|---:|---:|
| Science | 17,486 | 38,984,118 / 660M | 5.9% |
| Practical | 69,985 | 93,320,000 / 660M | 14.1% |
| Prose | 228,755 | 269,406,636.75 / 330M | 81.6% |

Illustrative initial console rendering from the verified state (free space
changes; no throughput is claimed before execution):

```text
Essential-Web Batch 0 RESUME 8e42ba31b0ef
FILES 31/32 96.9% remaining=1
DOWNLOAD 8,147,933,549/8147933549 B 100.0% 0.0 MB/s 0.0 Mbit/s workers=0 retries=0
PROCESS 2,535,118/2604815 rows 97.3% 0 rows/s workers=0 backlog=0
TIME elapsed=00:00:00 ETA~calculating...
SCIENCE   docs=17,486 estimated tokens=38.98M/660M 5.9%
PRACTICAL docs=69,985 estimated tokens=93.32M/660M 14.1%
PROSE     docs=228,755 estimated tokens=269.41M/330M 81.6%
ERRORS malformed=1210 retries=0 failed units=0
DISK scratch=<measured>/68,719,476,736 B durable batch=<measured> B free=<measured> B
ACTIVE download=- process=-
```

`failed units` counts failures in this invocation; the historical incomplete
unit appears under `remaining`. Append-only `events.jsonl` permanently records
resume, sealed, retry, first malformed warning per unit, and failed transitions
with elapsed time and bounded metadata. Refresh frames do not enter that log.

## Offline evidence and resources

[Audit](../evidence/ESSENTIAL-WEB-BATCH0-RECOVERY/audit.json),
[dry resume](../evidence/ESSENTIAL-WEB-BATCH0-RECOVERY/resume-check.json),
[immutability](../evidence/ESSENTIAL-WEB-BATCH0-RECOVERY/immutability.json),
[remaining-file replay](../evidence/ESSENTIAL-WEB-BATCH0-RECOVERY/local-replay.json),
[frozen amendment](../evidence/ESSENTIAL-WEB-BATCH0-RECOVERY/recovery.json), and
[commands/results](../evidence/ESSENTIAL-WEB-BATCH0-RECOVERY/COMMANDS.md).

Dry result: **sealed=31, scheduled=1, already-sealed scheduled=0, network units=0**;
only `f00026`. Recovery authorization remains false until the operator acts.

The real local replay processed only the remaining file into
`C:/XLM-scratch/ew-fast/recovery-proof-20260930`, with socket connections blocked.
All 69,697 rows completed; 269 science, 1,427 practical and 4,571 prose documents;
24 malformed rows quarantined. The canonical validator and byte accounting
reconcile all three outputs. Wall 47.5 s, process CPU 45.796875 s, sampled peak
RSS 358,625,280 bytes, output 63,352,737 bytes. The separate full-row diagnostic
took 38.718 s including the sealed hash audit, sampled peak RSS 231,383,040
bytes. Sampling can miss a brief peak; these are not GPU or campaign benchmarks.
The private replay outputs remain local and are never counted as campaign yield.

## Requirement ledger

| Requirement | Status |
|---|---|
| Authoritative 31/32 state, remaining source and root cause | VERIFIED, real local bytes |
| Exact per-source bounded amendment; unchanged science | IMPLEMENTED, VERIFIED |
| Seals reusable/immutable; exact scheduler skips all 31 | VERIFIED, real hashes + authored execution |
| Dashboard, durable events, EWMA overlap ETA, target progress | IMPLEMENTED, VERIFIED offline |
| Full remaining-file processing and canonical validation | VERIFIED, private offline replay |
| Operator recovery authorization | NOT RUN; execution BLOCKED until exact digest is authorized |
| Production restart and final 32nd seal | NOT RUN |
| Full repository acceptance, CUDA, external live tests | NOT RUN |
| Further batches, full campaign, tokenizer/training/C05 final pool | OUT OF SCOPE |

The requested related regressions pass: **733 passed, zero skipped**, including
20 new recovery cases. Ruff, formatting and strict mypy pass on all ten changed
Python files; the PowerShell driver parses. Exact commands and development
failures are recorded in COMMANDS.md. This is a focused selection, not full
repository acceptance. Dynamic TTY behavior is tested with an authored terminal
stream; an interactive production console has not been run.

## Operator command

From `F:/Project/xlm-data-ultrax`, review the amendment, then run this explicit
authorization plus offline-only resume. The original Batch-0 authorization
remains in place; Prepare and downloads are unnecessary.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command '. .\scripts\operator_storage.ps1; .\scripts\operator_essential_web_fast.ps1 -Batch 0 -Stage Resume -RecoveryAuthorize 51c09f0b15e6b19ebeb9854c6d65ec1994b473467b555723f0924606574c24db'
```

`Resume` passes `run --offline`, which refuses before the pipeline if any
remaining source would need a download. An ordinary repeated invocation reuses
the recorded recovery authorization. The same command after completion exits
without processing any unit. Nothing pushes.
