# P32 final performance closeout

## 1. Candidate, scope and environment

Starting HEAD: `8b1476655241528d69d2797bc681f6aeb7ee12f5`, the correctness-certified
P32 heavy-crash candidate. Initial branch, HEAD, short status and worktree list
were checked before changes; the worktree was clean on
`perf/p32-happy-path-closeout`. All writes stayed in
`G:\Project\xlm-p32-perf-closeout`. No external network, live acquisition,
dependency installation, research training, push or merge occurred. HTTP
fixtures were authored localhost data, explicitly part of this audit.

Windows 11 build 26200, NTFS on G: (the existing SATA SSD), CPython 3.12.13,
NumPy 2.5.3, PyArrow 25.0.1, tokenizers 0.23.2, Torch 2.14.0+cpu,
pytest 9.1.1 and filelock 4.0.0. The existing environment was copied locally;
its editable path was rebound to this worktree. `pyproject.toml`, `uv.lock`
and `.python-version` are unchanged and their hashes are recorded. Every run
used the offline, locked, no-sync uv wrapper, private caches/home/temp,
native thread counts of one and `TOKENIZERS_PARALLELISM=false`.

## 2–3. Profile first and actual regression

The frozen P31 methodology was reconstructed from local commit `1edacbd` with
the already-reviewed execution API adaptations. Before product edits, three
unprofiled repetitions at each of 1/8/16 workers and a separate profile at
each count ran on 16 deterministic 64 MiB opaque files (1 GiB per point),
ordinary 64 KiB reads. The after series uses the same fixture and methodology.
Every observation is retained in [TABLES](../evidence/P32-PERF-CLOSEOUT/TABLES.md)
and machine-readable evidence; no timing assertion or retry-until-fast exists.

Baseline profile, summed inclusive thread seconds:

| Component | w1 | w8 | w16 |
|---|---:|---:|---:|
| Download/read | 0.908 | 2.461 | 2.748 |
| Payload write | 0.522 | 1.472 | 1.651 |
| Incremental SHA | 0.599 | 1.373 | 1.594 |
| Payload fsync | 2.492 | 3.103 | 2.708 |
| Publication intent including admission/wait | 0.291 | 6.019 | 16.915 |
| Hard-link publication including retained fsync | 0.033 | 0.035 | 0.033 |
| Payload re-verification (32 calls) | 1.854 | 1.914 | 1.874 |
| Journal reads | 2.094 | 2.187 | 2.187 |
| Journal writes including fsync/control checks | 2.018 | 2.710 | 2.750 |
| Owned replacement inventory | 1.630 | 2.127 | 2.132 |
| Control-byte inventory | 2.610 | 3.353 | 3.361 |
| Settlement and file completion | 2.202 | 2.405 | 2.231 |
| Final/initial status writes combined | 0.037 | 0.038 | 0.034 |
| FileLock acquisition wait | 0.263 | 0.340 | 0.335 |
| Journal RLock wait | 0.002 | 4.892 | 5.013 |
| Journal RLock hold | 9.849 | 12.066 | 11.972 |

The two publication hashes reread **2 GiB of logical payload per 1 GiB
acquired**, while holding the journal lock. They are real redundant CPU/read
work, but not the entire regression: replacement and control inventory also
occupy substantial time. Journal authority reads and all those scans remain.
The after profile records **zero** publication re-verification calls; w8/w16
settlement falls to 0.582/0.524 summed seconds. These are diagnostic profiles,
not throughput trials. Python profiling materially increases overhead; timings
overlap and must not be added. Profile scope includes construction, while the
frozen throughput timer covers `fetcher.run`; lifetime journal counters include
constructor work. Cached logical reads are not necessarily physical SSD reads.

## 4–6. Success evidence, recovery and safety proof

`WrittenPayload` is ephemeral, process-local evidence, never a journal field
or metadata cache. Whole-file streaming captures the actual open writer's
device, file ID, length and modification timestamp with `fstat` after the last
successful checkpoint fsync. Its SHA comes from the incremental digest of
the response bytes plus any revalidated resume prefix. The writer context
then closes. Whole-file independent expected-digest validation remains.

Selected staging and ordered merge capture the same evidence after their
existing flush/fsync and before close, using the digest computed by the
streaming writer. Publication checks that the captured writer is closed and
that digest, byte count and file identity agree with the private payload.
All three production publication call sites supply this evidence. Record
inspection opens readers only; the merge's source chunks are separate files.

Intent and output reservation still persist together. The local copy of that
exact intent is compared with freshly read journal bytes in the completion
transaction, including plan, destination, partial path, digest, size, file ID,
reservation token, record count and ETag. Only an uninterrupted admission with
a present private payload and absent destination may use cheap structural
verification. The exclusive `os.link` operation cannot overwrite a destination;
its result must have the intended device/file ID/size. The old atomic helper
still fsyncs and closes its own handle before linking, then retires the private
name. Output settlement and completed status still share one atomic journal
replacement. No writer remains open at publication.

Restart has no process-local evidence and **always uses full size/identity/SHA
verification**. Existing or ambiguous links also use full SHA verification.
Direct publication calls without writer evidence retain the conservative path.
No persisted flag enables a fast restart. Private `_fresh_intent` is supplied
only after `publish_output` validates the closed writer; it is never rebuilt
from a journal. Journal bytes under FileLock remain authoritative.

On this actual Windows/NTFS environment, two whole/selected publication tests
observe equal source/destination device, file ID and size immediately after
the real `os.link`, link count two, and then retirement of the private name
with destination link count one. They forbid any full payload hash during
fresh publication. Additional tests reject wrong digest, size, replaced file
identity, an open writer and altered durable intent. Six interrupted fresh
publications prove that restart hashes surviving bytes and rejects same-size
content corruption. Existing foreign-output/path/intent/reservation regressions
remain unchanged. The dedicated publication suite is 33/33, including 13 new
tests. Broader exactness and process-death evidence below exercises the actual
whole, selected and parallel callers.

The preserved threat contract covers process crash, stale journal bytes,
partial publication, competing cooperating XLM processes, foreign destinations,
wrong hashes/sizes/identities, path escape and malformed intent. The plan
execution lock excludes cooperating payload writers; the journal FileLock
protects shared accounting. An administrator or noncooperating process changing
bytes behind those locks after verification is not part of this contract.
Path equality, size alone, restored stat metadata and uncomputed checksum
metadata are not accepted as ownership or journal authority.

## 7–8. Orphan inventory and fsync

No scan optimization was retained. Independently constructed managers may
encounter a cooperating writer that died after their admission; scanning only
on open would leave its replacement bytes undiscovered/unaccounted. The current
locked, bounded validation and retirement therefore runs on every transaction.
No metadata-only journal cache was introduced. The restored-stat journal
counterexample passes in the related regression selection.

All durability calls remain. Each unprofiled 1 GiB acquisition executes 521
fsyncs in the measured `run` scope: 208 data, 312 journal and one diagnostic;
the lifecycle profile includes three additional constructor journal fsyncs.
Baseline separate profile sums for data/journal/control at w1/w8/w16 are
2.492/0.221/0.0005, 3.103/0.515/0.0006 and 2.708/0.453/0.0005 seconds.
The after w1 profile itself stalls: 22.387 data + 5.362 journal seconds.
After w8/w16 profile data/journal sums are 2.658/0.364 and 2.740/0.465 seconds;
diagnostic fsync remains below 0.001 seconds in both. These summed thread
durations can exceed wall time.

The entire third after throughput repetition has severe stalls: summed fsync
34.553 / 167.426 / 181.023 seconds at w1/w8/w16. None is removed or rerun.
The previously retained 166.42-second observation remains historical evidence.
Storage/OS latency is not eliminated or attributed to a specific hardware
cause here; before/after order is a limitation of these local observations.

## 9–13. Change, exactness, recovery, concurrency and throughput

Product-only change with its direct tests:
`ce2bb32f9487eac560ae9086a6c783d995f3614c`
(`perf(acquisition): avoid redundant success-path payload verification`).
Benchmark/report/evidence changes are a separate following commit.

All **eight exact successful acquisitions match** the certified starting
candidate: opaque, JSONL, gzip and Parquet whole files; JSONL, gzip, exact
Parquet and projected Parquet selections. Ordered bytes/SHA, identities,
receipts, requests, transfer/decompression/scan/output totals, full consumed
maps, occupancy and reservations agree. The frozen comparison normalizes only
the declared receipt timestamp; it does not remove accounting fields.

All required process-death matrices pass, with each death exit 73 and restart
exit 0: **44/44 early, 44/44 mature, 20/20 selected, 4/4 parallel publication**.
No replacement `.tmp` survives. Existing parallel selected-source staging
remains conservatively charged: 58,460 / 59,300 / 61,800 / 61,800 bytes,
exactly the prior contract; it is not silently released for speed.

Focused acquisition/recovery/bounds: **177 passed, one privilege skip**,
124.12 pytest seconds. Related selected/streaming/review regressions:
**52 passed**, 35.58 seconds. Shared-budget tests pass at 1/2/4/8/16 independent
threads and spawned processes, with exact totals and no remaining reservations.
The capability skip is real Windows symlink creation (WinError 1314), not a pass.

| Workers | Before MB/s, repetitions 1/2/3 | After MB/s, repetitions 1/2/3 | Median gain |
|---:|---|---|---:|
| 1 | 88.703 / 90.054 / 89.515 | 98.859 / 97.461 / 24.972 | 8.9% |
| 8 | 124.393 / 124.678 / 123.116 | 141.769 / 136.938 / 29.268 | 10.1% |
| 16 | 123.767 / 119.465 / 120.338 | 141.497 / 135.678 / 28.436 | 12.7% |

These medians describe a modest happy-path improvement, not an unconditional
wall-time win: the total three-run elapsed time is worse after the change
because of the retained storage stalls.
Three-run aggregate throughput (total bytes / total timed seconds) is
89.420 / 124.059 / 121.162 MB/s before and 49.652 / 61.826 / 60.479 MB/s
after at w1/w8/w16, making that limitation explicit. One-worker median CPU falls from
7.578 to 6.625 seconds (12.6%). The redundant 2 GiB rehash and its locked CPU
work are eliminated, while all correctness/durability costs remain. The prior
pre-recovery references, 143.6 MB/s at w1 and 227.3 MB/s at w8, are **not
restored**. No 1 GB/s claim or live-source throughput claim is made.

## 14. Frozen 100k pipeline

One run, after acquisition exactness/recovery and focused checks were green:
**126.884635 seconds; exact comparator PASS, exit 0** against the latest
certified Opus-review artifact tree (109.833620 seconds). That tree was copied
read-only into this worktree; it was not regenerated. Clean=6, dedup=8, token=8;
ordinary 64 KiB acquisition reads; existing local tokenizer, no training.
Source gzip SHA remains
`29b2405959c69d1388d54e2aa2c6c6120bfc8d4f46e1b84a1759972992d2d8aa`.
Exact payloads, manifests, cleaning/dedup decisions, tokens, offsets, metrics
and loader targets agree using the unmodified frozen comparator. Its existing
timestamp/duration exclusions remain the only exclusions. The longer downstream
cleaning/dedup/tokenization times are not attributed to this acquisition change.
Sampled peak tree RSS was 2,351,169,536 bytes; sampled peak artifact footprint
2,478,252,600 bytes under the frozen 3,221,225,472-byte cap; final stage footprint
1,735,291,284 bytes. Sampling can miss shorter peaks. Whole-file throughput
trial peak RSS was 147,034,112 bytes before and 147,083,264 bytes after, under
the frozen 4 GiB / 900-second per-point watchdog. No GPU timings were measured.

## 15–17. Final acceptance and completion

Final six-leg acceptance ran **once and PASSED: 1,800 passed / two capability
skips / zero failed**, 1,802 distinct selected nodes. Every one of the starting
candidate's 1,789 selected nodes is preserved; the only additions are the 13
new publication regressions. No missing outcomes, overlapping selections,
worker restarts, retries, weakened assertions or new xfails. Tier A is the
first leg of this gate, not an additional full Tier-A invocation.

| Leg | Passed | Skipped | Failed | Exit | Runner seconds |
|---|---:|---:|---:|---:|---:|
| Tier A, 16 worksteal | 1,652 | 2 | 0 | 0 | 109.235 |
| Core, 4 loadgroup | 68 | 0 | 0 | 0 | 431.047 |
| Exclusive, serial | 8 | 0 | 0 | 0 | 32.188 |
| Heavy, 4 loadgroup | 7 | 0 | 0 | 0 | 385.250 |
| Scale, serial | 8 | 0 | 0 | 0 | 106.969 |
| Optional, 4 loadgroup | 57 | 0 | 0 | 0 | 205.235 |

Total runner time: 1,269.924 seconds (21.17 minutes). Peak sampled tree RSS:
7,174,897,664 bytes in Tier A, below the 24 GiB per-leg monitor ceiling.
The two existing skips remain missing Windows symlink privilege and missing
`cl` for CPU Inductor; neither is a pass or an installation request. The
native-timeout repair remains intact; heavy workflows complete without a native
worker crash. All nine changed Python modules pass Ruff, format and strict mypy
checks. Product/test source and locked dependencies stayed unchanged throughout
the final gate. Subsequent changes are documentation/evidence only.

**P32 remains correctness-complete for the declared offline scope and eligible
for release integration on that basis.** This does not certify the unavailable
capabilities, live datasets, CUDA or power-loss durability. The ignored artifact
tree held 31,512,211,297 bytes at evidence collection, including every performance
trial, both pipeline trees and acceptance fixtures; the separately copied
environment is approximately 1.008 GiB. Compact evidence, exact commands,
resource observations, all node/phase outcomes and log hashes are preserved in
[the evidence directory](../evidence/P32-PERF-CLOSEOUT/COMMANDS.md).

## 18–19. Integration and further work

On top of corrected review `26c1238`, retain the full P32 chain:
`3864646`, `522cffa`, `7321bd6`, `fa4ff50`, `95ee6b3`, `8b14766`, then
`ce2bb32` and this performance certification commit. The correctness products
are the first three; `95ee6b3` is the required native-timeout diagnostic repair.
Keep the evidence/tooling commits with the integration record.

If integrating from the earlier release base, first take exactly one equivalent
Opus set (`8d6ec9e 477cdbf 80264ca 43618e1 63b723b 8143bed` locally, or the
original equivalents documented by the independent review), then all four
review corrections `3f393e7 de92ad9 1282b51 f981464`. Preserve review evidence
`c765eec 26c1238`. Never take the unsafe original journal cache without its
correction, and do not add P31's larger-read experiment. This is a recommendation
only; no integration, merge or push was performed.

Further acquisition micro-optimization is not justified before the planned
approximately 4 GB corpus and CUDA work. Safe removal of redundant verification
recovers only part of the loss. Remaining locked control inventory and storage
durability serve the certified contract. Storage long-tail investigation could
be useful if it recurs operationally, but changing durability/recovery semantics
to chase a headline rate would not be a suitable preprocessing closeout.

Next command after final certification: `git show --stat ce2bb32`, then review
this report and its evidence together with the complete P32 integration chain.
Any integration remains a separate, explicitly authorized action.

## Requirement ledger

| Requirement | Status | Evidence / boundary |
|---|---|---|
| Closed-writer success evidence and structural link proof | IMPLEMENTED / VERIFIED | Actual Windows links; 13 new regressions, 33 total publication tests. |
| Full recovery SHA and atomic intent/settlement | VERIFIED | 44/44/20/4 real process-death matrices. |
| Exact successful bytes/receipts/accounting | VERIFIED | Eight complete comparisons against starting candidate. |
| Orphan/control accounting, journal byte authority | VERIFIED | Existing focused regressions and restored-stat counterexample; implementation unchanged. |
| Shared-budget concurrency | VERIFIED | Threads and processes at 1/2/4/8/16; selected concurrency regressions. |
| Repeated G: performance | VERIFIED | Every before/after and profile observation retained, including slower after batch and fsync stalls. |
| Frozen 100k pipeline | VERIFIED | One run; exact comparator PASS. |
| One final six-leg acceptance | VERIFIED | 1,800 passed / two existing capability skips / zero failed; all 1,789 baseline nodes preserved plus 13 new tests. |
| Real symlink privilege / Inductor compiler capability | BLOCKED | Existing unavailable capabilities, never counted as passes. |
| Live-source, CUDA, power-loss durability, hosted CI | NOT RUN | Authored offline fixtures and process death do not certify these. |
| Dependency changes, further scan optimization, research training | OUT OF SCOPE | None performed. |
| Push / merge | NOT RUN | Explicitly prohibited. |
