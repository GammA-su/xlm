# Essential-Web fast transport — 2026-09-30

**READY FOR HIGH-THROUGHPUT ESSENTIAL-WEB BENCHMARK**

> **Update, same day.** The live benchmark ran. Its only failure was the ETag rule described
> below, which was wrong; the rule is corrected and the saved report is re-accepted offline.
> See [identity fix and live benchmark](ESSENTIAL-WEB-FAST-TRANSPORT-IDENTITY-FIX.md).
> The campaign was refrozen as `8e42ba31…bb8c`. Text below is kept as written except where
> it named the superseded rule or digests.

The range-reader bulk campaign was stopped before any bulk fetch. Its physical
transport and storage are replaced by whole-file streaming, a retained source
Parquet and local processing. **No live request was made here and no bulk data
was fetched.** Source, revision, inventory order, batch membership, B-normal
selector, adapters, quotas, stop targets, malformed policy and the C04/C05
obligations are unchanged and are checked against the historical campaign on
every load.

What is proved offline and what is not:

| Claim | Status |
|---|---|
| Local processing reproduces the sealed calibration byte for byte (16,384 real rows) | VERIFIED offline |
| Local processing equals the certified range reader on authored files | VERIFIED offline |
| Transport rules: resume, drift, bounds, scratch cap | VERIFIED on a loopback server |
| Throughput against the real endpoint | MEASURED live: 142.7 MB/s at 8 streams |
| Parity on real upstream file bytes | VERIFIED live: identical |
| The strong ETag equals the content SHA-256 on the real endpoint | FALSE; the rule was wrong and is removed |

The campaign gate refuses batch 0 until that benchmark passes. Exact commands
and exit statuses are in
[COMMANDS.md](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/COMMANDS.md); the operator
plan is in [ESSENTIAL-WEB-FAST-BULK-PLAN.md](ESSENTIAL-WEB-FAST-BULK-PLAN.md).

## Root cause of 3.7 hours per batch

The historical reader fetches projected column chunks in 4 MiB ranges and re-reads
the footer for every row group. For one 32-file batch the historical model gives:

| Component | Seconds | Share |
|---|---:|---:|
| Request latency: 30,280 requests × 0.274 s | 8,289 | 62% |
| Response bodies: 7.46 GB | 3,453 | 26% |
| Decode and serialization | 1,614 | 12% |
| Total, one worker | 13,356 (3.71 h) | |

The batch is latency-bound, not byte-bound. It needs 946 requests per file, each
a round trip through a redirect, to save 12% of the bytes.

## Why Prepare failed at 60 seconds

Footer discovery read the 32 files one after another under a single transport
budget whose total deadline is 60 s, with no redirect reuse. It failed on
`train-02135-of-03132.parquet`, the 23rd file: about 2.7 s per file. Thirty-two
files need about 87 s, so the step could not succeed at this batch size. The
deadline was not raised. Prepare now makes no request at all.

## Transport comparison

From the eight real retained footers (sizes only) and the sealed calibration
timing ([transport-comparison.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/transport-comparison.json)):

| | A. Projected range reader | B. Whole-file stream |
|---|---:|---:|
| Bytes, eight files | 1,863,864,060 | 2,088,498,277 |
| Bytes per row | 2,820 | 3,160 |
| HTTP requests per file | 946 | 2 |
| Request latency per file at 0.274 s | 259 s | 0.5 s |
| Local read cost | none | 40 core-seconds per file |
| Temporary disk | JSONL staging, twice the slice | the compressed file, 261 MB mean |

Whole over projected: **1.1205** (per file 1.106 to 1.140), a 12.1% byte
overhead for a 473-fold reduction in requests. This is under the 1.35 preference
threshold, which is an operational heuristic and not a scientific rule. **Chosen:
whole-file streaming.**

## Raw artifact

One authoritative raw representation: the unmodified upstream Parquet file.

| Identity | Source |
|---|---|
| Repository, source path | the plan |
| Exact revision | pinned in the request URL; a differing `X-Repo-Commit` is drift |
| Strong ETag, remote length | the response; weak or missing validators are refused |
| SHA-256 of every byte | computed while streaming and again while copying |
| Declared digest against content | the SHA-256 the repository declares must equal the local one; the ETag is an opaque validator |

This is stronger evidence than before. The historical artifact hashed a derived
projection of selected rows and stated that it could not verify a full-file
digest.

`selected_records.jsonl` is no longer written. The selected-record stream is
produced in memory with the certified serialization, and its SHA-256 and size
are recorded in each file's receipt, so it stays reproducible from the retained
file. Nothing is deleted and there is no duplicate raw copy.

**A contract amendment was required, and it is small.** `whole_file` acquisition
of immutable originals already exists in the plan schema and C04 already prefers
selected immutable shards. What did not exist was adaptation from a source
Parquet and a compressed ledger. `CONTRACTS.md` now carries the C04 amendment
`essential-web-raw-artifact-v2`, scoped to this source; the data form is
[raw-artifact-contract.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/raw-artifact-contract.json).
The existing whole-file fetcher could not be reused: it decodes every row one at
a time and refuses row groups above 32 MiB, and the real row groups are 48 MB.

## Transport implementation

`src/xlm/data/acquisition/source_parquet.py`:

- One sequential GET per file through the existing allowlist and redirect checks.
- A data fsync and a state write every 64 MiB. An interrupted stream resumes with
  `Range` and `If-Range` from the last checkpoint whose prefix hash still matches.
  Bytes past a checkpoint are never trusted. Spent transfer stays charged.
- A changed ETag, length or resolved revision is source drift and is not retried.
- Bounds: 512 MiB per file (largest of the eight known files is 316.7 MB),
  16 requests and 5 retries per file, 30 s request timeout, 1,800 s per file,
  and per-batch transfer and request ceilings.

## Scratch

[scratch-policy.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/scratch-policy.json).
The root is configurable: `scratch_root` in `recipes/operator/storage.json`
(`C:\XLM-scratch`), exported as `XLM_SCRATCH_ROOT`, or `--scratch-root`. It must
be disjoint from the durable root `G:\XLM`, which stays where it is.

Flow per file: network → scratch partial → verify → exclusive hashed copy to
`G:\XLM\acq-raw\ew-fast\source\<path>` → local processing from the scratch copy
→ canonical unit and receipt published on `G:` with one rename → scratch copy
removed.

- Hard cap 64 GiB over everything under `<scratch>\ew-fast`, including partial
  downloads and files no running unit owns. A file reserves 512 MiB before its
  first byte and shrinks to its declared length. A file that does not fit waits.
- A 32 GiB free-space reserve on the scratch volume is a second refusal.
- A scratch copy is removed only after its durable copy reproduced the verified
  hash and its canonical unit with receipt is published.
- Retained sources, receipts, canonical units and other owners' files are never
  deleted.

## Concurrency and local processing

[concurrency-policy.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/concurrency-policy.json).
Concurrency is per file only. Download threads stream files while finished files
are processed in separate processes, so file N+1 downloads while file N is
adapted. No output byte depends on worker counts or completion order.

Measured on this machine (Ryzen 7 5700X3D, 8 cores, 16 threads), offline, on
48 files of real calibration rows:

| Processes | Rows/s | Against one process |
|---:|---:|---:|
| 1 | 2,047 | 1.0 |
| 4 | 6,064 | 3.0 |
| 8 | 11,573 | 5.7 |
| 12 | 15,413 | 7.5 |
| 16 | 16,685 | 8.2 |

**Default: 8 download streams and 12 processes**, one thread each. The rule is
the largest measured count that still adds 10% over the previous one, never
above the logical CPUs minus four. Sixteen adds 8% and would leave nothing for
the streams. Up to 16 streams are permitted by the resource contract.

Two earlier, smaller samples gave 13,015 and 10,627 rows/s at 8 processes. The
rate depends on file size and is not a precise constant.

Local processing, `src/xlm/data/sources/essential_web_local.py`: footer safety
checks, the same projected decode as the certified reader, the certified record
serialization, a JSON round trip exactly as `xlm data adapt` performs, then the
three frozen adapters, unchanged. The adapters now take most of the time.

## Byte-identical proof

1. **Real rows.** The calibration never transferred a whole file, so its 16,384
   executed raw rows were written back into Parquet under the real upstream
   schema of each file and processed by the new path. All eight selected-record
   streams hash to the sealed `raw_sha256`. All 24 document files and all 24
   rejection ledgers equal the sealed hashes, also when the same rows are split
   into three row groups.
   ([local-replay.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/local-replay.json))
2. **Authored files against the certified reader.** A test fetches a file with
   the real window reader over HTTP ranges, adapts it with `xlm data adapt`, and
   compares with the new path: selected records, documents and ledgers are
   byte-identical.

The container in (1) was rebuilt locally. The same check on real upstream file
bytes is the parity phase of the live benchmark.

## Ledger storage

The rejection ledger keeps its exact version-1 lines: source coordinates,
rejection code and category, reason, upstream row hash and adapter. It is stored
as one zstd frame at level 9 and identified by the SHA-256 of its uncompressed
bytes, which the summary and receipt record next to the compressed hash. The
codec ships with the locked PyArrow; no dependency was added.

Measured on the 24 real calibration ledgers: 26,318,331 → 2,072,808 bytes,
**12.70×**. The projected ledger footprint falls from 103.5 GB to about 8.2 GB.
The real ledgers are about 40 times longer per file, so the ratio may differ.

## Storage model

[storage-model.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/storage-model.json).
Linear extrapolation of measured bytes per input row, first-pass target at
4 bytes per token (64,424,483 rows, about 780 files):

| | Current | Fast |
|---|---:|---:|
| Raw | 589.7 GB (JSONL) | 203.6 GB (source Parquet) |
| Ledgers | 103.5 GB | 8.2 GB |
| Canonical documents | 56.7 GB | 56.7 GB |
| Manifests and receipts | — | 0.03 GB |
| **Durable total** | **749.9 GB** | **268.4 GB** |

| | Fast design |
|---|---:|
| `G:` steady state | 268.4 GB |
| `G:` peak | 268.4 GB |
| `C:` scratch peak, expected | 8.4 GB |
| `C:` scratch peak, bound by reservations | 17.2 GB (cap 64 GiB) |
| Total campaign footprint | 276.8 GB |

The durable peak equals the steady state because files are written under
temporary names and then linked or renamed. At the leave-one-crawl-out maximum
(73,140,172 rows) the durable total is 304.8 GB. Durable use falls by 64%.
Guards: 64 GiB free reserve and a 400 GiB campaign cap on `G:`.

## Throughput model

[throughput-model.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/throughput-model.json).
One 32-file batch is about 8.35 GB and 2.64 M rows.

| Aggregate network rate | Network time | Batch time | Limited by |
|---:|---:|---:|---|
| 100 Mbit/s | 668 s | 11.1 min | network |
| 200 Mbit/s | 334 s | 5.6 min | network |
| 1 Gbit/s | 67 s | 2.9 min | local processing |
| 4 Gbit/s | 17 s | 2.9 min | local processing |

The endpoint rate is unknown until the benchmark; these rows are scenarios, not
claims. Local processing takes about 172 s per batch at 12 processes. Above
roughly 400 Mbit/s the CPU is the limit, and a faster link no longer helps.
Against 3.71 h, that is 20 times faster at 100 Mbit/s and 78 times at the
processing limit.

## Tests and checks

All offline. Authored fixtures and a loopback server unless marked real.

| Check | Result |
|---|---|
| New module `tests/test_essential_web_fast.py` | 28 passed |
| Focused selection, 28 modules | 708 passed, 0 failed, 0 skipped, 205 s |
| Real-row replay, tool exit | 0, `IDENTICAL` |
| Ruff check, ruff format, strict mypy on the five new files | clean |
| Fast and full repository selections, CUDA | NOT RUN |

The focused selection covers whole-file transport, ETag and hash verification,
resume within and across processes, drift, scratch cap, concurrency, the raw
contract, ledger compression, byte-identical reproduction, membership, stop and
gate conditions, the historical campaign, the fetcher and range transport, the
selector, admission and the C05 receipt gate. A loopback server proves transport
logic, not live endpoint behaviour.

## Requirement ledger

| Requirement | Status |
|---|---|
| Transport comparison from real footers | VERIFIED (arithmetic) |
| Whole-file transport with resume and verification | IMPLEMENTED, VERIFIED offline |
| `verified_source_parquet` raw contract | IMPLEMENTED; amendment recorded |
| No duplicate raw representation | IMPLEMENTED |
| Configurable, capped scratch | IMPLEMENTED, VERIFIED offline |
| File-level download concurrency | IMPLEMENTED, VERIFIED offline; live rates NOT RUN |
| Local processing, byte-identical | VERIFIED on real rows and authored files |
| Parallel local processing | VERIFIED; measured 1 to 16 processes |
| Prepare without network | VERIFIED |
| Compressed ledger | IMPLEMENTED; ratio measured on real ledgers |
| Storage model | VERIFIED (arithmetic); extrapolation |
| Live benchmark and parity | NOT RUN (operator) |
| Bulk fetch | NOT RUN |
| C05 receipt | NOT RUN (after pool freeze) |

Open limitations: the endpoint rate, the ETag convention and parity on upstream
bytes are unmeasured; the durable-volume copy rate is unmeasured; local rates
come from calibration rows repeated to file size; file sizes are known for eight
files, so a file above 512 MiB stops its batch for review; peak memory at 12
processes was 2.6 GB in the offline benchmark and is unmeasured on full files.

Next: the live benchmark command in
[ESSENTIAL-WEB-FAST-BULK-PLAN.md](ESSENTIAL-WEB-FAST-BULK-PLAN.md#operator-commands).
