# P32 acquisition recovery closeout — 2026-09-24

The two P32 recovery blockers are implemented and verified on authored fixtures.
**Final acceptance failed: P32 is not COMPLETE and release integration remains
BLOCKED.** A Windows access violation killed the heavy-leg worker during
runtime-inventory fixture teardown. The result is preserved; no unchanged test
was retried to obtain a green gate. The correction also has a substantial
measured throughput cost.

Scope: publication reconciliation and owned scratch/control-file accounting.
No lease/window tuning, parser redesign, new preprocessing architecture,
dependency change, external network, live source, research training, push or
merge. All work is in `G:\Project\xlm-p32-recovery`, branch
`fix/p32-recovery-closeout`. HTTP tests use authored localhost fixtures only.

## 1. Starting point

Starting HEAD: `26c1238bb015494fa8646efe6ec3277bc3add0f2`, the corrected Opus
review candidate. Branch, HEAD, clean status and worktree list were verified
before edits. The parent review worktree and all other worktrees remain
unmodified. An existing environment was copied locally; only its editable
source path was adjusted. Python 3.12.13 and the existing locked dependency
graph are retained. All commands use uv offline/locked/no-sync.

Evidence and complete commands: [COMMANDS](../evidence/P32-RECOVERY/COMMANDS.md),
[results](../evidence/P32-RECOVERY/results.json),
[tables](../evidence/P32-RECOVERY/TABLES.md), and
[raw evidence hashes](../evidence/P32-RECOVERY/sha256.json).

## 2–3. Root cause and durable ownership

Previously, exclusive `os.link(partial, destination)` and removal of the
private name happened before two separate journal operations: output settlement
and file completion. A process death left valid bytes with no durable evidence
that distinguished this publication from a foreign destination. Refusal was
safe but permanent.

`FileProgress.publication` now carries a bounded `PublicationIntent`:

- behavioral plan hash and relative destination;
- private partial path within this plan's bound partial root;
- expected SHA-256, exact size, device and file identifier;
- unique `publication_<uuid>` output reservation token, also identifying the attempt;
- ETag and verified record count for the final file state.

The journal already binds plan ID/hash, source validators and storage roots.
Plan hash binds source/repository/revision, selection, authorization behavior
and limits. The intent is written **in the same transaction as the output
reservation**, before the link. There is no completed marker before publication.
File identity is an additional ownership check: an unrelated file with identical
bytes is rejected. A filename or checksum alone is never treated as intent.
Source data cannot supply or execute intent metadata.

The existing plan execution FileLock excludes simultaneous acquisition runs;
journal RLock/FileLock serializes intent, reservations and completion across
independently constructed managers. Cooperative writers must use these locks
and the private roots. This is not protection against an attacker who can
forge the authoritative journal or arbitrarily mutate the process/filesystem.

## 4–5. Whole-file and selected-record recovery

The same publication helper serves whole originals, serial selected output
and the deterministic parallel selected merge. Before any restart disk
reconciliation or network work, the fetcher resolves durable intents:

1. Validate plan/destination membership, relative paths and plain-path rules.
2. Require the matching exact outstanding output reservation. Ambiguous
   settlement evidence fails closed.
3. Verify any private partial against intent file ID, size and SHA.
4. If the destination exists, verify the same evidence and retire only the
   verified private name. Do not overwrite or redownload the destination.
5. If publication had not occurred, verify the private payload and perform the
   existing exclusive hard-link publication; then verify the destination.
6. Atomically settle output occupancy, retire that exact token, set completed
   file metadata and clear intent in one durable journal replacement.

Only after that can receipt verification observe completion. A failure before
the replacement leaves the old intent/reservation durable; another restart
converges. A failure after commit sees normal completed output and performs no
additional settlement. Missing/wrong-size/wrong-hash/wrong-inode payloads,
foreign destinations, mismatched plans, path escapes and missing reservation
evidence are refused without deleting or replacing unrelated bytes.

An I/O exception during publication is surfaced as recoverable-by-restart
progress corruption, rather than restarting the network body inside the same
file-attempt loop. This prevents accidental retransfer after a successful link.

## 6. Settlement and compatibility

Output reservation removal and completed state share one atomic journal
commit. There is no durable “settled but completion unknown” window. Repeated
reconciliation cannot charge the same known successful publication twice.
Transfer, requests, decompression and scan consumption are not rewritten by
publication recovery. Complete successful-run occupancy and reservation maps
are compared in addition to receipts and cumulative consumed counters.

The new reader accepts old schema-v2 journals without the optional intent.
Previously completed files retain their existing validation behavior. A legacy
untracked destination with no intent remains refused: evidence cannot be
retroactively invented. Old binaries may reject journals containing the new
field; downgrade/resume is not certified. Journal changes do not change plan,
selection, source, receipt or scientific output identities.

Parallel selection can leave per-source chunk files when the process dies
before its existing `finally` cleanup. Those surviving private files remain
measured and charged as temp occupancy. Recovery does not refund them merely
because the merged logical output completed. Four extra two-worker publication
cases verify exact output and unchanged cumulative totals with those retained
bytes honestly accounted; no unsafe general staging cleanup is introduced.

## 7–8. Orphan replacement ownership and cleanup

The committed journal is authoritative; an uncommitted sibling is never
promoted into state. With the journal FileLock held, startup and every
transaction inspect only bounded direct directory entries. A deletable journal
orphan must have the exact journal-name prefix, 32 lowercase hex characters
and `.tmp` suffix, be a regular single-link file with plain ancestors, be at
most 8 MiB, and parse as schema-v2 journal state for the same plan ID and hash.
No recursive adoption occurs. Other namespaces are neither adopted nor deleted.
Malformed or suspicious members of this journal's namespace block admission.

The entire candidate set is validated before any member is removed. Cleanup
is limited to 64 files and 16 MiB, with the existing 10,000-entry directory
enumeration bound. Exceeding a bound refuses further work. The same FileLock
is held by every journal writer through write/fsync/replace, so another live
writer's replacement cannot be deleted. A valid obsolete replacement is
unlinked before new scratch is admitted; the next crash does not accumulate
another ignored control file. Interrupted cleanup is safely repeatable.

Acquisition diagnostics also live under the job scratch root. Their current
bytes, atomic replacement bytes and small lock files are now included in the
control allowance. Diagnostic writes use the same journal lock and are bounded
to 1 MiB. Diagnostic orphans require the analogous exact name/private-file rule
and matching plan ID/performance format. When insufficient scratch remains,
optional telemetry is omitted; data/accounting results are not changed.

## 9–10. Repeated death, bootstrap and competing processes

A real spawned process dies after journal replacement flush/fsync and before
`os.replace`, sixteen times under an 8 KiB scratch cap. Every death leaves
exactly one bounded orphan; every restart removes it before admission. Actual
owned control file lengths stay within the cap and agree with
`control_disk_bytes`. The summed would-have-been-stranded bytes exceed 8 KiB,
so this exercises the original accumulation defect, not just one cleanup.
Uncommitted counter increments never become authoritative.

A separate process pauses with a fully written replacement while holding
FileLock. A competing manager cannot finish opening the journal or remove the
replacement until the writer releases the lock. Both processes then complete
and observe the committed update. Hostile-name, foreign-state, truncated JSON,
hard-link, directory and cleanup-count-limit tests retain suspect files and
fail closed. Symlink/junction rejection logic is explicitly exercised. Creating
a real Windows symlink is unavailable under this account and is reported as
a capability skip, not a pass.

The full scratch-contract review also found initialization writes preceding
limit binding. The original candidate writes **1,103 bytes** under a deliberate
128-byte cap before refusing. Limits and storage roots are now bound together
before the first journal write. The regression now refuses without a journal
or replacement file. This is a narrowly related scratch-admission correction;
it does not change lease sizes, units or parser accounting.

## 11–12. Crash matrices and successful exactness

The final matrices achieved **44/44 early**, **44/44 mature**, **20/20 selected**
automatic recoveries, plus **4/4** two-worker selected publications. Actual final
counts and exits are in the evidence tables. The original hooks and assertions
are retained; successful restart is now mandatory. Publication cases also
require identical pre-death/final cumulative consumed counters, exact output
occupancy and no outstanding output token. No `.tmp` journal replacement may
remain after recovery. Each killed child exits 73; each restart must exit 0.

All eight fresh baseline/current acquisitions passed comparisons of whole opaque/JSONL/gzip/
Parquet and selected JSONL/gzip/exact-Parquet/projected-Parquet. They require
identical bytes/order, plan/source/selection identities, complete receipts,
request/transfer/decompression/scan/output totals, consumed dictionaries,
occupancy and final reservations. Only the authored receipt timestamp is made
deterministic explicitly. The baseline source is extracted locally from
`26c1238`; no other worktree is executed or modified.

## 13. Performance regression check

The unchanged representative fixture is 16×64 MiB, seed 31, ordinary 64 KiB
reads, workers 1/8/16, all scratch/output/journals on G: SATA SSD. All file
hashes are checked. Baseline and corrected runs use the same copied environment,
frozen P31 methodology and real fsync. Complete before/after CPU/RSS/transactions/
writes/fsync/lock tables are linked above. No speed tuning or no-fsync run was
performed. Fsync outliers are retained; single observations are not a ceiling.

The correction adds full payload verification and bounded control inventory
under the journal lock. The observed throughput penalty is substantial, not
merely described as “modest metadata overhead.” Journal transactions/writes
remain batched and do not return to per-read growth. The exact measured cost
must be considered when integrating; this task does not claim a performance
win or reopen the lease architecture to recover that cost.

| Workers | Corrected review baseline MB/s | Recovery correction MB/s |
|---|---:|---:|
| 1 | 143.581 | 91.263 |
| 8 | 227.321 | 125.916 |
| 16 | 226.137 | 29.326 |

The sixteen-worker correction run contains a large fsync stall. Earlier
pre-bootstrap measurements also contain a slow eight-worker point; all raw
observations are preserved. Each final point writes the journal 315 times
versus 331 for the baseline, with 521 versus 537 total fsync calls. Throughput
loss is not a return to per-read journal persistence. Normal publication now
performs two full verification reads, adding 2 GiB of logical reads for this
1 GiB fixture, and CPU time rises from approximately 3.4–3.7 s to 7.5–8 s.

## 14–16. Test sequence and acceptance

Initial publication probes: 20 passed; related lease/selection regressions:
36 passed. Orphan validation: 12 passed / one symlink-capability skip.
The broad focused acquisition/prepare/bounds/lease set passed **190 tests,
one skip**, in 231.21 s. Its optional in-process resource sampler was interrupted
by a test's intentional `psutil.Process` mock, producing one warning. Functional
test outcomes are valid; those RSS/CPU samples are incomplete and not claimed
as full-run measurements.

After the bootstrap correction, publication/orphan/acquisition-bounds/prepare-
bounds tests passed **85 tests, one capability skip**, in 82.09 s. The initial
Tier A passed 1,631 tests with two skips, but the following acceptance attempt
was deliberately stopped during core when the bootstrap gap was identified.
Those results are preserved under `gate-before-bootstrap`, explicitly not a
final acceptance pass. No failed product test was retried unchanged until green.

The final source is revalidated with fresh crash/exact roots, Tier A, then all
five complementary legs. One xdist controller runs at a time, explicit worker
counts, native math/tokenizer thread limits and offline flags. Each leg has a
1,800-second / 24 GiB sampled-tree watchdog. Missing/failed groups, nonzero
exits or worker failures fail the aggregate. Skips remain distinct from passes.
The final gate table is generated from pytest node/phase evidence; it also
checks selected-node coverage and disjointness across the six legs.

Final Tier A: **1,632 passed, two skips**, 103.13 s pytest time (104.25 s
external runner). Core: **68 passed**, 354.46 s. Exclusive: **8 passed**,
28.90 s, including both new real-process orphan cases. Heavy: pytest reports
**7 passed and 1 failed**, 336.82 s, because the queue campaign's call passed
before its worker crashed in teardown. The evidence table counts that node
only as failed: **6 fully passed nodes and 1 failed node**. Worker restart was
disabled and the runner stopped at this failure. The untouched scale and
optional selections were then run with `finish-unrun`, preserving the failed
heavy result and without repeating any completed leg.

Scale: **8 passed**, 103.35 s. Installed optional dependencies: **57 passed**,
197.66 s. All six selections completed, covering **1,782 distinct nodes**:
**1,779 passed, 2 capability skips, 1 failed**. All selected nodes have recorded
outcomes; the selections are disjoint. The two skips are real symlink creation
without Windows privilege and CPU Inductor compilation without `cl`.
The aggregate is **FAILED**, and the evidence collector records
`full_gate_passed: false`. All fifteen changed Python files pass Ruff, format
checking and mypy. Doc/tool-only finalization did not rerun the product suite.

The fatal stack is `runtime_seed` teardown → `RuntimeSeed.verify_unchanged`
→ `installed_runtime` → `_hash_batch` → `Path.relative_to`. The interpreter
reported **Windows fatal exception: access violation**; xdist reported `gw1`
not properly terminated. The runner did not trigger its deadline/RSS watchdog
(337.813 s, 2.823 GiB peak sampled process-tree RSS). These runtime-inventory
source/fixture files are unchanged from `26c1238`. That is location evidence,
not proof that the crash is pre-existing or unrelated to this series: its
native root cause remains unresolved. Changing inventory, suppressing teardown,
or repeating unchanged tests would exceed this narrow recovery closeout.

## 17. Complete scratch contract and remaining limits

The limit covers **logical file bytes owned by this acquisition job**:

| Class | Accounting / admission |
|---|---|
| Whole-file partial payloads | Durable temp leases before writes; verified-prefix and owned-tree reconciliation. |
| Selected staging/chunks/merged staging | Pre-reserved before writes/copies; interrupted private leftovers remain measured and charged. |
| Output files | Durable output reservation; intent-proven completion settles exact logical size once; output-tree reconciliation. |
| Journal and atomic replacement | Existing journal plus proposed replacement checked before writing; obsolete validated replacements retired under lock. |
| Diagnostic/control and lock files | Existing bounded sidecar/locks included in control bytes; proposed diagnostic replacement checked before writing; owned orphan retirement under the same lock. |
| Nested preparation scratch/published artifacts | Existing explicit `nested_temp`/published occupancy and reservation contracts retained. |

`temp_disk_bytes` retains its historical payload/staging-occupancy meaning;
`control_disk_bytes` exposes the additional current journal/diagnostic/lock
bytes. Admission includes both, nested occupancy, outstanding reservations and
the proposed atomic replacement. `output_disk_bytes` remains output plus
explicit published occupancy. Hard-linked private/final names can be
conservatively charged in both logical roots during publication.

This is not physical-filesystem-wide accounting: allocation slack, directory
entries, NTFS metadata, OS cache/RSS and unrelated files/jobs are not included.
External artifacts/caches/logs outside declared job roots require their own
parent budget. Unknown/foreign control files are not silently adopted. Truncated
or otherwise unprovable replacements fail closed rather than being guessed
safe. Power-loss/filesystem durability is not inferred from process-death
tests; Windows directory-fsync limitations remain.

Parser/decompression semantics are unchanged: some JSONL/gzip/Parquet decode
and materialization occurs before cumulative charges; projected Parquet's
existing metadata-derived charge is not an allocator/memory measurement. No
new universal pre-decode guarantee is claimed. Conservative cumulative lease
allowance can still strand across crashes, bounded by the shared total budget.

Requirement ledger for the implemented closeout scope:

| Requirement | Status | Evidence / boundary |
|---|---|---|
| Whole and selected publication intent, validation and atomic settlement | IMPLEMENTED / VERIFIED | 20 dedicated cases; final 44/44/20/4 process-death matrices; repeated idempotent reconciliation. |
| Owned journal/diagnostic orphan retirement and control-byte admission | IMPLEMENTED / VERIFIED | Exact ownership bounds; 16 real deaths under 8 KiB; competing writer/manager exclusion. |
| Initial scratch admission | IMPLEMENTED / VERIFIED | Baseline exceeds 128-byte cap; corrected run refuses before first journal write. |
| Successful acquisition contract | VERIFIED | Eight exact baseline/current comparisons, including full accounting maps. |
| Existing leases and acquisition/prepare bounds | VERIFIED | Focused regressions and final gate, with capability skips separate. |
| Full six-leg release acceptance | BLOCKED | Heavy worker access violation during fixture teardown; aggregate fails regardless of other legs. |
| G: workers 1/8/16 regression measurement | VERIFIED | Real fsync, exact outputs, material slowdown recorded; no performance-win claim. |
| Real Windows symlink creation and CPU Inductor compilation | BLOCKED | Account lacks symlink privilege; installed environment lacks `cl`. Logic tests still execute; skips are not passes. |
| Power-loss durability and live-source compatibility | NOT RUN | Process deaths and authored localhost fixtures do not certify these. |
| Parser/decompression redesign, CUDA, further optimization, research training | OUT OF SCOPE | Existing semantics retained; none performed. |
| Release merge/push | NOT RUN | Explicitly prohibited for this task. |

## 18–20. Commits, completion and integration disposition

Product commits in order:

1. `3864646` — durable publication intent and interrupted completion recovery.
2. `522cffa` — locked orphan retirement and complete owned control-file admission.
3. `7321bd6` — enforce scratch bounds before initial journal writes.

The final certification/tooling/evidence commit follows these three. No
unrelated performance change is included. Integration would start from the
corrected Opus candidate `26c1238`, not the uncorrected six original commits.

The requested publication and scratch corrections are IMPLEMENTED / VERIFIED,
but **P32 is not COMPLETE** because full release acceptance failed. **The
corrected Opus series is not yet eligible for integration into the green release
branch.** The failed worker must be diagnosed and resolved with evidence before
a new release gate can certify it. Even a subsequent green gate must retain
the material throughput tradeoff above. No merge or push is part of this task.

Next prompt: diagnose the preserved heavy-leg Windows access violation during
runtime-inventory teardown, starting from `gate-heavy.log.gz` and its node/phase
evidence. Keep the three recovery corrections, preserve the failed gate, and
use a focused reproducer before considering another full release gate. Do not
reintroduce unsafe journal caching or unverified output adoption; retain the
measured performance cost in any integration decision.
