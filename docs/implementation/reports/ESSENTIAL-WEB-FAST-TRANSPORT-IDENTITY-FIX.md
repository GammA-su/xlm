# Essential-Web fast transport: identity fix and live benchmark — 2026-09-30

**READY FOR HIGH-THROUGHPUT ESSENTIAL-WEB BATCH 0**

The live benchmark ran (operator) and proved the transport. It was recorded as
FAIL for one reason, an identity rule that was wrong. The rule is corrected, the
saved report is re-accepted offline, and the campaign is refrozen as
`8e42ba31b0ef9fcbae1079cebf88d2449b88a9b37ec52b68c7a408eb2546bb8c`.
**No network request was made in this task and nothing was downloaded again.**
Transport architecture, concurrency, selector, mixture and quotas are unchanged.

Commands and exit statuses:
[COMMANDS.md](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/COMMANDS.md#identity-fix-and-offline-re-acceptance).

## Cause of the failure

The first rule said: a strong ETag made of 64 hexadecimal characters must equal
the SHA-256 of the file. All 14 benchmark files have such an ETag, and for none
of them is it the SHA-256. The rule inferred a meaning from a shape.

## What the saved evidence shows

From the saved report
([live-benchmark.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/live-benchmark.json)),
for all 14 files:

| Received | Value | Relation to the local SHA-256 |
|---|---|---|
| `ETag` of the storage endpoint | strong, 64 hex | different in 14 of 14 |
| `X-Linked-ETag` of the pinned resolve response | 64 hex | **equal in 14 of 14** |
| `X-Linked-Size` | bytes | equal to the received length in 14 of 14 |
| `X-Repo-Commit` | `ce4eccc7…113d` | the pinned revision in 14 of 14 |
| `X-Xet-Hash` | not recorded | the benchmark did not capture this header |

So the repository did declare a SHA-256 for every file, in `X-Linked-ETag`, and
every transfer matched it. The check that failed compared the content with the
wrong header.

**What the storage ETag is.** Not determined. It is not the SHA-256 of the file.
It is stable: for the parity file it equals the validator sealed in the
calibration a day earlier. The redirect target is the Xet bridge host, so a Xet
file hash is the likely meaning, but no `X-Xet-Hash` was captured to compare it
with, and nothing here claims it. The code now records that header when it is
present.

Local repository metadata holds only 40-hex Git object ids for directory
listings. It has no per-file SHA-256, so the digest declared in the response is
the only independent expected digest available.

## Identity rule

| | Old | New |
|---|---|---|
| Strong ETag | 64 hex had to equal the SHA-256 | opaque validator; resume and drift only |
| Local SHA-256 | computed | computed and recorded, always |
| Independent expected SHA-256 | the plan's, if any | the plan's, else the repository's `X-Linked-ETag`; **must equal** the local SHA-256 |
| No independent digest | — | local SHA-256 kept as the acquired-content digest; recorded as not independently verified |
| `X-Xet-Hash` | ignored | its own field, never used as a SHA-256 |
| Revision, path, length | required | required, unchanged |

Still fail-closed and never retried: a changed ETag, length, repository digest or
resolved revision on a continuation; a plan digest that disagrees with the
repository's. `Range` and `If-Range` with the strong ETag are unchanged.

**No integrity guarantee was weakened.** The removed check never verified
anything on this endpoint: it could only fail. The check that replaces it
compares the received bytes with the digest the repository declares for the
pinned path, which the old code recorded but did not enforce. A corrupted
transfer is now refused; before, it would have been reported as an ETag
mismatch indistinguishable from the false alarm.

One limit, stated plainly: the campaign does not require the repository digest
to be present. If a response carried none, the file would be accepted on its
local SHA-256 alone and its receipt would say `sha256_independently_verified:
false`. All 14 live responses carried it.

## Offline re-acceptance

The benchmark retains nothing, so its files were removed when it finished. They
were not fetched again, and the local SHA-256 values could not be recomputed.
That does not leave a gap: the report holds the SHA-256 computed over the
received bytes during the transfer, and it equals the repository's digest for
every file.

`benchmark-accept` re-applies the corrected rule to the saved report
([live-benchmark-accepted.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/live-benchmark-accepted.json)).
It accepts only if:

- the only recorded failures are the superseded ETag rule (14 of 14 were);
- the report covers exactly the planned files, under the same plan apart from
  the transport code hash;
- every file has a strong ETag, the pinned revision, a matching declared size
  and a declared SHA-256 equal to the local one;
- parity holds;
- the code that turns a file into records is byte-for-byte the code the parity
  phase ran. Only `source_parquet.py` changed; `essential_web_local.py` and
  `columns.py` have the same hashes.

Result: **PASS**. The original report is kept unmodified
(SHA-256 `de372896…ef94`). The measurements are carried over, not altered.

## Measured benchmark

Measured once, live, on 13 batch-0 files plus the parity file. 3.70 GB, 28
requests, 0 retries.

| Streams | Files | Bytes | Wall | MB/s | Mbit/s |
|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 318,869,507 | 14.1 s | 22.7 | 181 |
| 4 | 4 | 1,109,821,205 | 42.6 s | 26.0 | 208 |
| 8 | 8 | 2,060,691,375 | 14.4 s | 142.7 | 1,142 |

| | Measured |
|---|---:|
| Local processing, 12 processes | 1,111,054 rows in 97.8 s = 11,363 rows/s |
| Peak memory, 12 processes | 3.5 GB |
| Durable copy to `G:` | 364 MB/s |
| Parity on real upstream bytes | IDENTICAL (all ten checks) |
| Ledger compression on real files | 1,789 MB → 151 MB (11.8×) |

The 4-stream tier was slower per stream than the other two (one file ran at
6.3 MB/s). One sample per tier cannot separate endpoint variance from
concurrency.

## Modeled batch and campaign

Modeled from the measured rates; not a measurement of a batch
([throughput-model.json](../evidence/ESSENTIAL-WEB-FAST-TRANSPORT/throughput-model.json), `live`).
A batch is about 8.35 GB and 2.64 M rows, estimated from eight known file sizes.

Transfer and processing overlap, so they are not summed. At the 8-stream rate
files arrive about four times faster than they are processed, so processing
sets the pace: it starts when the first file lands and runs at its own rate.

| | Central (8-stream rate) | If the network is as slow as the 4-stream tier |
|---|---:|---:|
| Download | 59 s | 321 s |
| First file lands | 15 s | 40 s |
| Processing | 233 s | 233 s |
| **Batch wall time** | **249 s (4.2 min)** | 410 s (6.8 min) |
| Limited by | local processing | network |
| **25 batches** | **1.73 h** | 2.85 h |

Not included: main-thread sealing of each unit, retries, and the operator's
Prepare and authorization between batches. Against the historical model of
3.71 h per batch and about 90 h in total, the central estimate is 54 times
faster per batch.

## Tests and checks

All offline, loopback server and authored fixtures unless marked real.

| Check | Result |
|---|---|
| `tests/test_essential_web_fast.py` | 33 passed (6 new) |
| Focused selection, 28 modules | 713 passed, 0 failed, 0 skipped, 225 s |
| Real-row replay with the corrected code | exit 0, `IDENTICAL` |
| Ruff check, ruff format, strict mypy on five files | clean |
| Fast and full repository selections, CUDA | NOT RUN |

New tests: a 64-hex ETag is not treated as a SHA-256; an opaque ETag of any
shape is accepted as a validator; `X-Xet-Hash` stays a separate field; the local
SHA-256 is always recorded; the repository or plan digest must match; a changed
ETag or repository digest refuses a continuation; wrong length, path and
revision refuse; a corrupted scratch file is not reused; the saved real report
is re-accepted and the authored variants are refused; parity stays
byte-identical.

## Requirement ledger

| Requirement | Status |
|---|---|
| Headers and metadata inspected offline | VERIFIED |
| Identity rule corrected | IMPLEMENTED, VERIFIED offline |
| Resume with strong ETag | unchanged, VERIFIED offline |
| Existing benchmark re-accepted without download | VERIFIED; files themselves not available |
| Local SHA-256 recomputed from files | NOT RUN (files discarded by design) |
| Measured model updated | VERIFIED (arithmetic on measured rates) |
| A measured whole batch | NOT RUN |
| Meaning of the storage ETag | NOT DETERMINED |
| Bulk fetch | NOT RUN |
| C05 receipt | NOT RUN (after pool freeze) |

Smallest optional future check, not needed for batch 0: batch 0 itself records
`X-Xet-Hash` when the endpoint sends it, which would settle what the ETag is at
no extra transfer.

Next: `.\scripts\operator_essential_web_fast.ps1 -Batch 0 -Stage Prepare`
