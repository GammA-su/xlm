# Independent Opus 5.5 product review — 2026-09-24

**NOT APPROVED AS SUBMITTED. Corrected lease batching is useful, but the full
P32 recovery contract is not verified and preprocessing freeze is not approved
by this review.** Three optimization defects were reproduced and corrected;
an inherited early-download recovery defect was also corrected. Publication
recovery and complete crash-time scratch accounting remain open. This is a
review of authored offline fixtures, not live-source certification.

The corrected product is `f981464`. All writes, test roots, copied environment,
scratch, output, journals and new commits are in `G:\Project\xlm-opus55-review`,
branch `review/opus55-product`. No external network, installation, download,
training, push or merge occurred. HTTP means an authored localhost server only.
Other worktrees were read only.

Evidence: [measurements](../evidence/OPUS55-REVIEW/measurements.json),
[complete acquisition tables](../evidence/OPUS55-REVIEW/TABLES.md),
[commands and exits](../evidence/OPUS55-REVIEW/COMMANDS.md), and
[log hashes](../evidence/OPUS55-REVIEW/log-manifest.json).

## 1. Green base and actual starting state

Green base: `9765a00fcbf4466aa460531c4b9cec064f04e979`.
The mandatory branch/HEAD/status/worktree checks found the expected branch and
a clean tree, but HEAD was the green base: the six commits were **not yet on
this branch**. This discrepancy was reported before changing anything. Their
existing local objects formed the exact requested series; they were
cherry-picked in order, without a merge or modifying another worktree.

Diff review, budget analysis and failing correctness reproductions preceded
performance benchmarking. Opus's report was inspected from local commit
`c5fb998`; its published throughput is treated as a claim to compare, not as
evidence of this branch's correctness.

## 2. Commits reviewed, invariants and compatibility

| Original | Review equivalent | Change / reason for speed | Review verdict |
|---|---|---|---|
| `7da15f4` | `8d6ec9e` | Cache own journal bytes using inode/size/mtime; share ancestor validation across a transaction. Avoid reopening on Windows. | **Reject cache as implemented.** Restorable metadata can conceal different bytes. Retain shared path checks and progress helper; correction `3f393e7` always reads under FileLock. |
| `b15059d` | `477cdbf` | Durable capacity leases, geometric whole-file checkpoints, combined settle/progress/reserve transaction, transport lease reads. | Accept batching mechanism with stated bounds and recovery limitations. Budget authority remains the durable journal; read failures hold unknown in-flight allowance. Initial ownership corrected separately. |
| `5bf7bbb` | `80264ca` | Authored loopback, CPU-stage and storage benchmark infrastructure. | Measurement tools, not a product speedup. No benchmark bypass is used for product measurements; normal fsync remains enabled. Original cleanup-capable harness was not invoked on existing outputs. |
| `84a04e5` | `43618e1` | Leases for scan counts, decompression/group charges and selected staging replace per-row transactions. | **Reject original skipped-row accounting.** Corrected callback reserves before each logical scan, including skipped JSONL rows. Group/staging reservations remain before their admitted work. |
| `2201589` | `63b723b` | Reuse canonical record bytes; splice only locator bytes at a sorted-key edge. | **Reject original mutation guard.** Object identity alone allows stale payloads after mutation. Corrected immutable typed snapshot and canonical fallback preserve exact bytes. |
| `87791e2` | `8143bed` | Exact Arrow uint64 limb arithmetic and reduction when NumPy is unavailable. | Exact on randomized/edge cases. Preserve NumPy-first policy; no tokenizer, shingle, permutation, band or threshold identity change. |

The lease commits change transaction frequency, crash over-reservation and
private checkpoint granularity; they do not change successful resource units,
schemas, plan/selection identity, corpus order or final hashes. Locks remain
the per-manager RLock, journal RLock plus cross-process FileLock, and the plan
execution FileLock. A lease is local to its stream; it is not an authority
outside the journal. Under contention, reservation can reject work while
another worker holds unused allowance; it cannot grant past the shared limit.

## 3. Accounting authority and invariant verdict

The useful invariant is **durably consumed/occupied + durably reserved >=
admitted work that can survive a crash**. `reserved` alone is not the total:
settlement intentionally moves used allowance into consumed/occupied counters.
For cumulative resources, successful settlement releases unused allowance and
charges exact known usage. Temporary/output disk use occupancy, not a lifetime
consumed-byte counter.

| Resource | Reservation and durable authority | Work / settlement | Crash behavior and qualification |
|---|---|---|---|
| Transfer body bytes | `CapacityLease` / `exchange`, persisted before response read; whole-file temp and transfer grants are combined | `read_leased` then payload write; fsynced verified-prefix checkpoint settles exact known bytes and renews atomically | Uncertain failed read retains requested allowance. Restart never refunds transfer based on retained file size. Headers, TCP/TLS and kernel prefetch are outside the existing body-byte contract. |
| Decompressed bytes | Whole scans use `ScanAccounting`; selected Parquet reserves group charge before prefetch/decode | JSONL/gzip charges actual returned bytes; whole Parquet charges canonical record bytes; projected Parquet charges selected compressed column metadata bytes | Existing units are preserved. **Not a universal pre-decode or physical-memory accounting guarantee:** JSONL readline/gzip decompression and whole Parquet materialization precede some charges. |
| Parser bytes | Structural limits in footer/Thrift/range/record/row-group admission; no durable cumulative parser-byte counter | Bounds gate parsing buffers/metadata and record sizes | No new durable parser lease. A restart repeats bounded parsing. Arrow/Python allocation amplification is not an OS memory limit. |
| Scanned records | Corrected `_BatchCommitter.scanned()` / `ScanAccounting.scanned()` persist lease before logical row scan | JSON parse/record processing consumes one; close settles exact count | Skipped rows remain charged. Parquet batch decode/to_pylist can precede logical per-row accounting, as before. Crash retains the active scan reservation. |
| Staging bytes | `_temp.consume(payload_len)` before selected writer call; separate full merge reservation before copying parallel chunks | Writer buffers/flushes under reserved capacity; close settles actual bytes to occupancy | Private remnants remain charged. Restart measures owned partials before retiring their disk reservations. Lost RAM buffers may safely be reclaimed only by that reconciliation. |
| Output bytes | Durable output reservation before hard-link publication | Settle actual size then mark completed | Reservation remains conservative if death occurs between steps, but publication restart is blocked; see §4. |
| Request count | Durable `record_request` before each outbound request, including redirects | Exact request counter, no batch refund | Restart cannot reuse a consumed request. Redirect target caching is observational/transport state, not permission to bypass counting. |
| Retries | Existing per-file attempt loop and `max_retries`; each request/body uses the above durable budgets | Exception classification/backoff unchanged | No new durable lifetime retry counter. Restart starts another invocation under the same durable request/byte/deadline limits. Matrix uses retries=0; focused tests cover existing retry behavior. |
| Temporary disk | Durable occupancy + reservations + nested usage; `_write` checks current journal and new atomic replacement size | Whole payload write or staging write; owned-tree reconciliation establishes occupancy | **Incomplete crash-wide scratch proof:** killed atomic journal replacements can remain outside the partial-tree reconciliation and are not added to the next `_write` check. Inherited behavior; see below. |
| Deadline | First bind persists `deadline_at`; lease paths cache this write-once authority | Checked before requests/work/lease boundaries and selected rows; request timeout remains bounded | Restart reuses original absolute deadline. Selected and whole crash cases assert it does not reset. Check granularity is not hard preemption of a native decoder. |

**Verdict: VERIFIED for the tested transfer/scan/staging admission and exact
successful accounting paths; NOT PROVEN for an unconditional all-resource
crash contract.** The previous-contract ordering of decompression/parser work
must not be described as stronger than it is. The matrix's pre-replace deaths
left four `.progress.json.<uuid>.tmp` files in the mature-case journal
directories; restart succeeded but those files remain. The source checks only
the current journal and proposed replacement, not accumulated orphan control
files. This inherited scratch-bound limitation and publication recovery need
a targeted follow-up; neither is hidden by a passing aggregate test count.

## 4. Lease bounds and actual process-death recovery

Default whole-file allowance starts at 64 KiB, grows geometrically, and caps at
16 MiB **per active lease**. Transfer and temp have separate allowances. Scan
leases start at 256 records and cap at 65,536 records. In-memory range reads use
their own 4 MiB initial setting. Generic indivisible `consume(amount)` may
reserve **max(window cap, amount)**, subject to the global remaining limit; a
16 MiB + 1 charge disproves an unconditional 16 MiB claim. The code comment was
corrected. Old stranded cumulative reservations can accumulate across repeated
crashes until the global limit is exhausted; there is no constant lifetime
stranding bound. There is no automatic refund of uncertain work.

Boundary tests cover 0, 1, 64 KiB ±1, 1 MiB ±1, 16 MiB ±1, exact remaining
capacity and one byte beyond it. Both ordinary and mature-window kill matrices
use real child `os._exit(73)`, real fsync, and a new process to restart, at:
before reserve, after reserve, during read, after read, after write, after
settlement, before checkpoint, after checkpoint, extension, final settlement,
publication. Windows: 64 KiB, 1 MiB, 8 MiB and 16 MiB. Mature mode waits for
the target window to be reached; 16 MiB is a lease size, not a 16 MiB read.
The final-settlement hook kills after the replacement journal is fsynced but
before `os.replace`. Each case has a fsynced witness, killed/restart logs and
independent source SHA verification.

| Matrix | Safety observations | Automatic successful restart |
|---|---|---|
| Original six-commit product | 44/44 conservative transfer and valid checkpoint observations | 16/44; early unowned partial and publication refusals |
| Corrected, early hooks | 44/44 conservative transfer and valid checkpoint observations | **40/44** |
| Corrected, mature hooks | 44/44 conservative transfer and valid checkpoint observations | **40/44** |
| Corrected selected-record cases | 20/20 conservative scan/staging observations | **16/20** |

The original matrix's final-settlement hook was after commit; the later two
explicitly cover the harder pre-replace boundary. These are finite injected
observations, not proof against power loss or every instruction boundary.

**Remaining blocker:** death after `os.link(partial, destination)` but before
settlement/completed journal state leaves a valid destination. Whole-file
restart raises `incomplete/untracked original exists; refusing overwrite`;
selection raises `incomplete selection destination; refusing replacement`.
No invalid prefix is trusted, no bytes are silently overwritten and no
duplicate output is published. However automatic logical completion is absent
in all four publication cases per matrix. A valid file existing is not a
successful recovery. This gap exists in the green implementation too; a
publication-intent/reconciliation change was not folded into this performance
review. Controller exit 0 means assertions and observations completed, **not**
that every restart succeeded.

## 5. Concurrent-budget verdict

Independent managers sharing one journal were stressed with 1/2/4/8/16 threads
and separately 1/2/4/8/16 **spawned processes**, near a 100,001-byte shared limit.
A barrier keeps grants simultaneously outstanding. Every snapshot obeys
consumed + reserved <= limit; after close, consumed equals the sum actually
used and active reservations are zero. Cross-process FileLock reload and
atomic replacement prevent lost updates; the restored-stat correction is
necessary to make the read authority sound. Process-fanout tests are `serial`
so Tier A does not multiply their workers. Shared preparation-budget behavior
also passed the existing acquisition/prepare-bound focused tests.

## 6. Journal reopen-elision verdict

**Unsafe as submitted; withdrawn by `3f393e7`.** On this Windows filesystem,
rewrite same-length journal bytes (cache_hits 1 -> 9) and restore mtime with
`os.utime`: inode/size/mtime match, yet the original cache returns 1. This is
a concrete stale-state counterexample, not a hypothetical timing concern.
Tests also retain atomic replacement freshness coverage. The correction
always reads bytes under FileLock, with the one shared ancestor-validation
walk retained. It handles an own-write/cache-update interruption without a
second cache authority. `journal_reads_elided` stays as a compatible telemetry
field and is zero. Non-cooperating concurrent writers are not made safe by
FileLock; malformed or mismatched state still fails closed.

## 7. Scanned/staging lease verdict

Original selected iterators incremented an in-memory list; the caller reserved
scan allowance only after a retained row was written. Seventy skipped JSONL
rows could therefore be parsed with durable scan allowance zero. The failing
test observes the actual on-disk journal at each `json.loads`, independently
of the fetcher's in-memory state. `de92ad9` replaces this with a pre-scan
callback in JSONL, gzip, exact Parquet and projected Parquet paths.

Twenty extra real-death cases cover those four formats/variants at skipped
scan, staging reserved, staging written+fsynced, scan/staging settled, and
publication. The first sixteen restart byte-identically with 20 selected
rows; four publication refusals are reported above. Durable spent cumulative
allowance is never reset; temp reconciliation uses actual private-tree
occupancy. Row-group preflight/parser/expansion limits and projected group
reservation remain in place. Their accounting units and pre-decode limitations
are explicitly distinguished in §3.

## 8. Successful-run exactness

Eight acquisitions were run against extracted green source and the corrected
source: whole opaque/JSONL/gzip/Parquet and selected JSONL/gzip/exact-Parquet/
projected-Parquet. All output hashes/bytes, selection order, plan/source and
selection identities, complete receipts, request/transfer/decompression/scan/
record counters and complete final `accounting.consumed` dictionaries match.
The same fixed localhost port keeps repository identity equal. Receipt
`created_at` is made deterministic by assigning the same authored `started_at`
fixture value before verification; no scientific field is excluded. Different
timings and internal reservation tokens are not scientific output.

## 9. Locator serializer

4,000 authored randomized JSON objects cover ASCII/Unicode/control escapes,
null/bool/int/finite float, nested arrays/objects and keys on either side of or
across `_xlm_acquisition`. Noncanonical raw, foreign canonical bytes, reserved
field and NaN refusal are exercised. Six mutation regressions cover scalar,
nested value, bool-versus-int, signed zero, key rename and nested numeric key
type. Five original cases failed before the fix; all six pass after it.

`1282b51` captures an immutable type-sensitive recursive snapshot. A changed
snapshot, non-string top-level key, noncanonical raw or mixed key ordering
uses the complete canonical serializer. If all string keys are greater than
the locator key, the locator is first; if all are less, it is last. Those are
the only splice positions. Original-record hash still hashes the supplied raw
bytes, matching the old serializer's behavior even after caller mutation.

The corrected optimization remains a modest measured win: on 50k authored
Parquet rows, median total CPU 1.844 -> 1.766 s across three repetitions,
identical digest `683b58642668a2ebd313191aa28c6f8fb3a8947f73054855b8edb73e5f809290`.
This is roughly 4% for the decode/serialize microbenchmark, not a claim that
the unchecked original speed is retained.

## 10–11. MinHash exactness and backend policy

All 528 independent cases match Python/NumPy/Arrow at 8/16/64/128/256/1024
permutations, plus the original 120 Arrow fuzz cases. Cases include empty
public signatures, singletons, repeated values/set semantics, 0, uint64 max,
P-1/P/P+1, and tile sizes 2047/2048/2049/4097. NumPy-unavailable dispatch is
exercised without uninstalling anything. No signature/banding identities change.

Keep **NumPy first; Arrow when NumPy is absent and shingles >=32; Python for
small sets or final fallback**. Median milliseconds/signature, default 128
permutations, five batches of ten after warmup:

| Shingles | Python | NumPy | Arrow |
|---:|---:|---:|---:|
| 16 | 0.635 | 0.182 | 0.427 |
| 64 | 2.151 | 0.352 | 0.686 |
| 512 | 18.441 | 3.472 | 3.610 |
| 4097 | 144.150 | 40.745 | 22.910 |

NumPy is not universally faster: Arrow wins the largest measured set. This
does not justify replacing NumPy for the common smaller sets or inventing a
new size-dispatch policy during review. NumPy 2.5.3 is installed in the copied
environment but not declared in the locked project runtime; Arrow is declared.
No dependency graph or installation policy was changed.

## 12–14. G: acquisition and P31 larger-read interaction

Hardware: Ryzen 5700X3D, 8 cores/16 threads, 77,231,341,568 bytes RAM; Windows
11 build 26200; G: is NTFS/4096 on Disk 1 CT2000BX500SSD1 SATA SSD, partition 3.
Python 3.12.13, NumPy 2.5.3, PyArrow 25.0.1, tokenizers 0.23.2, Torch
2.14.0+cpu, pytest 9.1.1, psutil 7.2.2. Existing environment was copied locally
and its editable path adjusted to review/src; no sync/install occurred.

P31 methodology was extracted from `1edacbd328d989aa44a5edd3d1af3ff4363ce8fe`.
The absent P31 constructor chunk option is adapted only in the measurement
harness by setting the Opus read constant. Lease windows and product defaults
are unchanged. The 1 GiB authored control alone raises the pilot transfer
ceiling/admission. Each point verifies every whole-file SHA. Real durable
writes are enabled. CPU/RSS are sampled in the same process as the whole-file
localhost server; lock/fsync durations sum across threads.

| Workers | 16×16 MiB, 64 KiB read | 16×16 MiB, 1 MiB read | 16×16 MiB, 8 MiB read | 16×64 MiB, 64 KiB read | 16×64 MiB, 8 MiB read |
|---:|---:|---:|---:|---:|---:|
| 1 | 67.660 | 66.358 | 66.605 | 144.291 | 145.837 |
| 2 | 16.659 | 84.097 | 83.247 | 205.062 | 203.745 |
| 4 | 13.558 | 90.428 | 91.745 | 30.560 | 210.285 |
| 8 | 73.723 | 91.308 | 87.819 | **234.212** | 30.681 |
| 16 | 89.690 | 88.355 | 90.299 | 229.598 | 30.243 |

All numbers decimal MB/s, one observation per point. Slow points are retained:
they have sharply elevated real fsync time, e.g. summed 173.766 s in the
35.504 s large-file/8 MiB/16-worker run. No causal claim about SSD firmware,
antivirus or background processes is made without evidence. No external
machine-wide exclusivity was assumed.

**Do not integrate P31's large-read product change on this evidence.** At 16
workers with 16 MiB files, normal reads give 89.690 MB/s / 89.2 MiB RSS versus
90.299 MB/s / 207.4 MiB RSS for 8 MiB reads. All read sizes have 283 journal
writes and 441 fsync calls; 507 transactions at 16 workers. P31-SSD's previous
8 MiB/16-worker point had 699 transactions, 347 journal writes, 409 fsyncs and
63.465 MB/s (old best across workers 64.785). Leases remove the per-read
accounting rationale for larger reads. The extra data checkpoint fsyncs during
initial geometric growth explain why fewer journal writes do not imply fewer
total fsync calls. Read size does not increase the 16 MiB lease exposure; it
does increase transient buffers.

Maximum observed here is **234.212 MB/s**, 16×64 MiB, eight workers, ordinary
64 KiB reads: 4.584 s, 3.562 CPU s, 134.9 MiB RSS, 595 transactions, 331 journal
writes, 537 fsyncs, journal hold 4.522 s, capacity wait 18.539 s. This is about
59% of the prior measured ~400 MB/s durable disk ceiling. It is a larger-file
control, not a size-matched 3.6× claim over the old 64.785 result. The
size-matched best is 91.745 MB/s, about 1.42× old best; ordinary-read best is
89.690 MB/s. Lease batching moves toward the ceiling but this corrected build
is **not demonstrated disk-ceiling-limited**. Complete CPU/RSS/locks/fsync
tables and all raw counters are linked above. There was no no-fsync throughput
substitution or repeat-until-fast procedure.

## 15. Selected Parquet

Same Opus fixture shape: eight files ×20,000 rows, 1,000-row groups, nominal
2,000-byte text, seed 7, zstd; source 143,069,356 bytes. Projection id/text,
full explicit row ranges, 1 MiB coalescing. Wire body bytes including metadata
rereads: 143,580,620; requests 192; selected records 160,000. Every worker
setting produces SHA `6de543a36797c56151636a3b1bcfe40d55020362449e8a757fa373a3608ad83c`.

Workers 1/2/4/8/16: **9.058 / 8.468 / 8.419 / 8.504 / 8.465 MB/s** transferred
compressed body bytes. Unique-source throughput is ~0.36% lower. Opus's
reported 11.5 MB/s is **not reproduced by the corrected implementation on G:**;
the green 1.36 MB/s is a historical Opus claim, not a newly measured G: baseline.
The new measured result is still in the same broad order, not a copied speedup.

One-worker cProfile (24.465 s, instrumentation overhead; not used as throughput)
attributes overlapping cumulative time to journal transactions 7.188 s,
`_load` 4.698 s, `encode_record` 3.546 s, `selected_record` 4.535 s, typed
snapshot 2.238 s, and range fetch 5.546 s (includes accounting). Socket/network
timing in telemetry includes local serving, not a real WAN ceiling. Isolated
50k-row current CPU-path component wall medians are Arrow decode ~0.192 s,
to_pylist ~0.049 s, record encode ~0.820 s, locator serialization ~0.622 s.
Those separate component observations avoid pretending cProfile exposes every
native Arrow cost. Serialization and durable accounting now dominate this
fixture; extra file threads do not improve it. CPU/RSS exclude the external
server process for this selected-record harness, unlike the whole-file harness.

## 16. Frozen 100k complete pipeline

**109.833620 s; exact comparator PASS (exit 0).** Clean=6, dedup=8, token=8,
ordinary 64 KiB acquisition reads. Source gzip SHA
`29b2405959c69d1388d54e2aa2c6c6120bfc8d4f46e1b84a1759972992d2d8aa`,
27,487,817 bytes. Tokenizer identity
`766a6b19a6c48e662ab78ba883e3fd939cefd6c969584f563fa5a4054f86453d`.
97,872 cleaned/surviving documents, 36,678,213 tokens. The existing exact
comparator checks payloads, manifests, cleaning/dedup decisions, token arrays,
offsets, metrics and loader targets; only its predeclared timestamps/durations
are excluded. Thirteen exact artifact SHA entries are in the saved result.

| Stage (seconds) | P31-SSD G run 1 | G run 2 | Corrected review |
|---|---:|---:|---:|
| Loopback acquisition | 0.766 | 0.684 | 0.546 |
| gzip decode/publication | 1.523 | 1.482 | 1.453 |
| Adapt | 6.172 | 6.289 | 5.833 |
| Canonical shard | 5.986 | 6.011 | 5.926 |
| Clean | 14.370 | 13.713 | 12.752 |
| Dedup | 46.626 | 47.063 | 43.155 |
| Split | 9.260 | 10.000 | 8.794 |
| Frozen tokenizer load | 0.113 | 0.072 | 0.088 |
| Tokenize | 30.720 | 46.400 | 28.036 |
| Token verification | 0.426 | 0.435 | 0.398 |
| Pack | 2.895 | 2.796 | 2.771 |
| Loader | 0.132 | 0.081 | 0.082 |
| **Total** | **118.990** | **135.026** | **109.834** |

The copied exact reference is P31's frozen D: artifact tree, also used to verify
both prior G: runs; its timing is 174.858 s. The comparator's reported speedup
uses that D: time, **not** either G: time. G: timing comparison above comes from
the previous P31-SSD evidence. One new run is 7.7% / 18.7% below the two G:
times, but unchanged downstream stages account for most of the difference:
this is not attributable wholesale to leases. Peak sampled tree RSS is
2,320,326,656 bytes (~2,212.8 MiB); sampled peak artifact footprint 2,478,252,454
bytes; final stage footprint 1,735,291,138 bytes. Sampling can miss shorter peaks.

## 17. Gates and evidence limits

| Selection | Result | Exit |
|---|---|---:|
| Initial scan + restored-stat reproductions | 2 failed as expected | 1 |
| Initial broader equivalence/mutation regression | 22 passed, 5 failed as expected | 1 |
| Corrected focused acquisition/bounds/prepare/dedup/lineage selection | **227 passed, 9 deselected, 221.49 s** | 0 |
| Tier A, 16 workers, worksteal, strict markers | **1,600 passed, 1 skipped, 103.50 s** | 0 |
| Final independent review regressions incl. process budgets | **36 passed, 25.54 s** | 0 |
| Explicit compiler-skip probe | 1 skipped, 6.20 s | 0 |
| Whole/selected crash controllers, exact comparisons, measurements | Completed; individual restart failures above are retained | 0 |

The Tier-A skip is `test_cpu_compile_eager_parity`: Torch Inductor cannot find
Windows compiler `cl`. It is **not a pass**; no compiler was installed. Final
process-budget tests were added after Tier A and run separately with `-n 0`;
the reported Tier-A count is not inflated to include them. Ruff/mypy results
and exact commands are in COMMANDS.md. Full six-leg acceptance, CUDA,
live-source compatibility, power-loss durability and hosted CI: **NOT RUN**.
Product review has unresolved recovery requirements, so the full acceptance
gate was intentionally not started.

## 18–19. Corrections and exact cherry-pick recommendation

| Correction | Scope |
|---|---|
| `3f393e7` | Always reload journal bytes under lock; regression for restored stat metadata. |
| `de92ad9` | Pre-scan callback including skipped rows; correct lease-stranding bound comment. |
| `1282b51` | Typed recursive mutation guard for locator splice; six regressions. |
| `f981464` | Persist empty-prefix ownership before first partial creation/write; restart regression. |

These are separate commits with their own tests. The first three repair
optimization defects; the fourth repairs the inherited early-crash ownership
case exposed by the requested matrix. Corrections prioritize correctness, not
preserving the unsafe cache's original timing.

Review-only tooling and independent concurrency/equivalence tests are retained
in `c765eec`; its one change to an original benchmark is formatting only.

**No release integration is recommended until the remaining recovery/scratch
requirements have an explicit disposition and acceptance is rerun.** For a
review/integration candidate based exactly on the green base, the complete
tested product sequence is:

```text
7da15f4 b15059d 5bf7bbb 84a04e5 2201589 87791e2
3f393e7 de92ad9 1282b51 f981464
```

Equivalent local first-six hashes are listed in §2. Do not cherry-pick both
sets of equivalents. Do not take the six original commits without the four
corrections, and do not add P31's larger-read product change. The benchmark
commit is included here to specify the exact reviewed series; it is not a
runtime dependency. Final review tooling/evidence commits are optional for a
product tree but required to retain this review's reproducibility. No merge,
push or deployment is performed by this recommendation.

## 20. P32 decision and requirement ledger

**Use the corrected lease design as P32's batching implementation, but do not
declare the proposed P32 project complete or replaced by the original series.**
There is no need for another broad accounting architecture. The remaining
work is narrow: crash-safe publication reconciliation with explicit ownership
and budget settlement, and bounded accounting/cleanup of orphan journal
replacement files. Preserve fail-closed handling of unowned or conflicting
files and successful exact totals. Clarify parser/decompression authority in
the frozen contract before making stronger claims than the green behavior.

| Requirement | Status |
|---|---|
| Six-commit independent review and reproduced defects | VERIFIED |
| Four narrow corrections | IMPLEMENTED / VERIFIED |
| Transfer leases, shared concurrent grants, successful exact totals | VERIFIED on bounded authored cases |
| Scan/staging pre-admission and selected crash probes | VERIFIED on bounded authored cases |
| Unconditional 16 MiB lifetime stranded bound | DISPROVED; corrected documentation |
| Automatic publication crash recovery | BLOCKED by observed inherited refusal |
| Full scratch cap including accumulated orphan journal replacements | BLOCKED by inherited accounting omission |
| Universal pre-decode/parser cumulative accounting | NOT IMPLEMENTED by this design; existing semantics documented |
| Serializer/MinHash scientific equivalence | VERIFIED on stated corpus and fuzz cases |
| G: sweep, selected profile, frozen 100k + exact comparator | VERIFIED |
| Focused and Tier A | VERIFIED with one explicit compiler skip |
| Full six-leg acceptance | NOT RUN because product review did not pass |
| Live acquisition, CUDA, research, new dependency graph | OUT OF SCOPE / NOT RUN |

All retained review artifacts totaled 22,686,276,988 bytes in 14,861 files at
the recorded end-of-measurement snapshot; G: had 165,982,457,856 bytes free.
Whole points had 4 GiB RSS/900 s watchdogs; pipeline had the frozen 1,800 s
and artifact caps. Crash subprocesses had 60 s timeouts and 120 s plan
deadlines. No multi-terabyte or unbounded data work was started.

**Next prompt:** “On a new isolated branch from the corrected Opus review
candidate, repair publication crash reconciliation and orphan journal scratch
accounting only. Preserve ownership, exact successful receipts and durable
spent budgets. Re-run the failing publication/pre-replace cases and focused
bounds tests before deciding whether the final six-leg acceptance is eligible.”
