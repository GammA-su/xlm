# Essential-Web evidence v2.1: protocol amendment review

Date: 2026-09-28. Repository: G:/Project/xlm-data-ultrax.
Starting HEAD: aaa674d8b1d0da459b88450dc4eb6c68ca52e933.
This is the task-specific PXX report required by AGENTS.md, not a new numbered
research milestone. Decision: **READY TO IMPLEMENT EVIDENCE V2.1**.
No implementation, acquisition authorization or commit is included.

The [new normative protocol](ESSENTIAL-WEB-EVIDENCE-V2.1-PROTOCOL.md) changes
only Arm-T transfer allocations. v2.0 remains unchanged and physically
infeasible. New scientific selection is unnecessary. Execution readiness
remains blocked by accounting and resource-verification work.

## 1. Independent receipt audit

Recomputed canonical self-digests, file SHA-256 and lengths for the v2.0 freeze,
inventory, M footer receipt, T attempt-2 receipt, selection and two earlier
incomplete receipts. Verified all four v2.0 freeze-bound artifact descriptors,
source revision/policy parents, inventory file membership, 118 unique locators,
exact per-file cost-map membership, group membership, chunk extents within
remote lengths, all chunk sums, scan batch formulas, workspace and legacy
request arithmetic, file fit flags, aggregate sums/maxima and refusal reasons.
No raw source footer was fetched; no corpus content or selected text was read.
Historical acquisition authenticity is not independently attested by hashes.

| Crawl | Selected | Compressed text | Transfer upper | Decompressed upper | Scan rows | Workspace upper |
|---|---:|---:|---:|---:|---:|---:|
| 2014-15 | 16 | 22,512,998 | 26,707,302 | 38,450,828 | 5,888 | 72,005,260 |
| 2015-32 | 14 | 23,191,731 | 27,386,035 | 40,088,762 | 1,280 | 73,643,194 |
| 2016-50 | 11 | 20,044,734 | 24,239,038 | 34,378,348 | 1,536 | 67,932,780 |
| 2018-05 | 11 | 22,230,312 | 26,424,616 | 38,296,749 | 4,864 | 71,851,181 |
| 2019-09 | 14 | 23,637,047 | 27,831,351 | 41,297,124 | 8,448 | 74,851,556 |
| 2021-04 | 13 | 22,903,643 | 27,097,947 | 39,415,672 | 5,376 | 72,970,104 |
| 2021-49 | 18 | 23,692,511 | 27,886,815 | 41,244,759 | 7,168 | 74,799,191 |
| 2024-26 | 21 | 21,750,193 | 25,944,497 | 37,895,921 | 3,840 | 71,450,353 |

All byte values are exact. One chunk and one wanted group per file.
Transfer = compressed + 4,194,304; decompression = full uncompressed chunk;
workspace = decompression + 33,554,432. The safe whole-chunk transfer model
is retained; no sparse-page estimator is proposed.

- Transfer sum **213,517,601 bytes (203.63 MiB)**; max **27,886,815 (26.59 MiB)**.
- Decompression sum **311,068,163 bytes (296.66 MiB)**; max **41,297,124 (39.38 MiB)**.
- Scan sum **38,400**; selected **118/118**; receipt files feasible **0/8**.
- Latest T planning: **48 requests / 1,994,765 recorded bytes**; status
  INCOMPLETE, exit 1, stopped_early=false. INCOMPLETE is preserved.
- M COMPLETE receipt: eight 512-row windows; each reports 85 future requests
  and future_plan_feasible=true. Planning: **48 requests / 1,922,640 bytes**,
  zero recorded failures/retries in that pass. Scans match the supplied
  768, 2816, 7936, 2304, 7168, 7168, 8960, 9728.

Corrections and limits discovered independently:

1. The T aggregate transfer value omits consumed footer bytes. Latest pass
   plus future bound is **215,512,366**, not 213,517,601. The earlier failed
   T pass adds **236,727 bytes / 6 requests**; known carried total plus future
   bound becomes **215,749,093 bytes**, with **54 planning requests**.
2. Six future requests/file reproduces the formula
   chunk_count + wanted_group_count + 4, but is not a physical upper bound.
   A 4 MiB body cap forces 5 or 6 ranges for raw chunk bytes alone; redirects,
   retries, reserved framing and controls require more. The map's group-level
   two requests cannot cover these chunks. The recorded 48 future / 96 combined
   requests cannot certify execution. Correct accounting can still fit the
   unchanged request caps; require an actual schedule, not that assertion alone.
3. M projected transfer is **582,200..1,657,177 bytes**, sum **11,692,530**,
   rather than 0.56..0.58 MiB/file. Its future-feasibility code uses planner
   estimates, so these flags are not an independent safe-allocation certificate.
4. Latest M ranges record **24 redirect transitions**, even though its budget
   snapshot redirects field is zero; total requests=48 includes the hops.
   Do not infer no redirects from that field. The old M incomplete receipt
   undercounts physical attempts under the previously documented bug.
5. Runtime, disk, scan and decompression counters are zero for planning
   receipts. Zero scanned corpus rows is consistent with footer-only planning;
   zero runtime/disk does not prove zero elapsed work/storage. No process-tree
   acquisition memory peak is present.

The request's statement that all non-transfer limits fit is therefore supported
for the reported text decompression/scan arithmetic, not fully verified for
requests, total RSS, disk or runtime.

## 2. Scientific decision and exact cap amendment

The failure is physical transport granularity, not evidence of bad scientific
selection or poor text quality. v2.0 explicitly allowed refusal on whole-chunk
bounds. On the documented footer-only exposure timeline, an openly versioned
transport amendment preserves selection preregistration. It does not upgrade
T from development evidence or justify changing any review/scoring rule.

Choose **amend to v2.1**. The next binary total capacities, with unchanged
footer reserve, are reasonable bounded engineering allowances. Small-sample
minimum-plus-epsilon budgeting and cost-based document replacement are rejected.
A redesigned study could be cheaper, but changes the sampling design and
requires another freeze; fewer rows within these chunks often cost the same.
Keeping v2.0 blocked remains the correct historical result.

| Scope | Footer bytes | Data bytes | Total bytes |
|---|---:|---:|---:|
| File | 2,097,152 | 31,457,280 | 33,554,432 |
| Arm T | 16,777,216 | 251,658,240 | 268,435,456 |

All response bodies and attempts, including prior planning, failed/partial
responses, redirect/error bodies and retries, share the original arm ledger.
No reset on resume/version change. The 4 MiB single-body/range buffer and
8 MiB retained text ceilings remain unchanged and are different quantities.

Minimum data headroom/file: **3,570,465 bytes**. Data headroom/arm:
**38,140,639 bytes**. Total arm headroom after all known T planning:
**52,686,363 bytes**. Even the full 16 MiB footer reservation fits.
This leaves meaningful margin but does not guarantee a full extra 4 MiB retry
at every file; all actual work must still stop at the ceilings.

All unchanged physical limits, exact margins, and unmeasured obligations are
listed in normative section 4. Decompression and scanning have substantial
numeric margin. Workspace max 74,851,556 is not measured RSS. Retained text
has an arithmetic maximum of 7,733,248 bytes, but the 16 MiB final-artifact cap
also covers packages, labels, receipts and copies. Disk high water and runtime
remain NOT RUN. No non-transfer ceiling is changed.

## 3. Evidence lineage and an inherited Arm-M block

Adopt the original M receipt **by hash as footer/window observations**, without
network replanning or relabeling. Keep its original protocol version, COMPLETE
planning status, producer hashes and future-feasibility qualifications.
Bind T attempt-2 as **historical INCOMPLETE cost-map motivation**, not a successful
v2.1 acquisition. Bind the two earlier failures as budget-history parents.

M's prior incomplete attempt recorded 268,808 bytes / four requests; these must
not disappear. Combined recorded M bytes are at least **2,191,448**. Its earlier
implementation report reconstructs three first-file logical ranges with three
redirects; adding the six requests for that file in the later pass implies
**12 physical requests against the unchanged 10/file footer planning cap**.
The earlier receipt lacks per-range details. Resource-compliance adoption is
BLOCKED pending authoritative reconciliation, and a confirmed overrun is not
repairable by this T-only amendment. An observation can remain usable evidence
without being certified as a compliant execution. Do not silently reset M's
budget, enlarge its cap, label this resolved, or rerun its footers.

Source review also found implementation handoff concerns:
the CLI creates fresh ledgers for planning invocations; ledger snapshots omit
per-file request breakdowns; T file-request checks are per kind rather than
the combined footer+data total; byte accounting mixes file category counters;
and future request estimates ignore bounded range splitting. These explain why
a constant-only patch is insufficient. They are obligations to enforce existing
requirements, not permission to change the reader, scientific design or limits.
No source repair was performed or tested in this review.

## 4. Freeze and exact identity

The [new freeze](../evidence/ESSENTIAL-WEB-EVIDENCE-V2.1/freeze.json) binds:

- New normative protocol exact bytes/SHA-256 plus audit/report/command artifacts.
- v2.0 freeze, M receipt, T incomplete cost map and unchanged selection, each
  with original canonical digest, byte hash and length.
- Earlier failure receipts, explicit adoption roles, old/new exact transfer
  caps, inherited M/all non-transfer caps, rationale and no-acquisition scope.
- Scientific identity namespace essential-web-evidence-v2.0, separately from
  new receipt version essential-web-evidence-v2.1.

The 118-locator selection remains **byte/digest-identical**, 23,807 bytes,
SHA-256 **8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27**,
canonical digest
**975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474**.
No new row or file selection was made. No review secret or order was generated.
The freeze records its own canonical digest excluding only its top-level digest;
the protocol uses a file SHA-256. Do not confuse those two identities.
The freeze digest is reported in STATUS.md and the final response, outside the
artifacts it hashes, to avoid a cycle.

## 5. Exact Muse implementation prompt

> Implement only essential-web-evidence-v2.1 from
> docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2.1-PROTOCOL.md and
> docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1/freeze.json.
> First read AGENTS.md, CONTRACTS.md, STATUS.md, the v2.0 protocol/freeze and
> implementation report, and this amendment review. Verify the new freeze
> self-digest, every bound artifact byte hash/length and canonical parent digest.
> Keep v2.0 readable and behaviorally unchanged. Add explicit versioned v2.1
> handling; do not mutate global v2.0 defaults or globally replace version strings.
> Preserve the scientific hash namespace essential-web-evidence-v2.0 for file
> ranking, text selection, review IDs and review order. Reuse the byte-identical
> 118-locator manifest (digest 975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474),
> original eight files/windows, seeds, source revision, selectors, strata,
> evaluator, rubric, blinding and M-before-T sealing. No reselection or text read.
>
> Only Arm-T transfer ceilings change: footer/file 2097152, data/file 31457280,
> total/file 33554432; footer/arm 16777216, data/arm 251658240, total/arm 268435456.
> Keep every other limit and Arm-M limit unchanged. Retain the current safe
> whole-text-column-chunk reader and bound; no sparse-page claim, alternative
> decoder, dependency default change or production adapter/admission change.
>
> Implement explicit by-hash adoption: M as original v2.0 footer/window
> observations; T attempt-2 as original INCOMPLETE motivation; selection unchanged;
> prior incomplete attempts as budget-history parents. Do not relabel old
> evidence or overwrite it. New child artifacts bind both original parents and
> new protocol/freeze. Build durable, per-file and per-arm, per-category and
> combined request/body ledgers across stages/resumes/versions. Count redirects,
> failures, partial/error bodies, retries and planning exactly once per event.
> Carry at least the documented T 2231492 bytes / 54 requests and all other
> reconciled historical costs; unknown is not zero. Preserve original deadlines
> and count parser/decode repeated work, disk copies/caches/partials and RSS.
>
> Correct the six-request estimate using concrete <=4194304-byte range schedules
> plus HEAD/revalidation, redirects, retries and carry-in. Distinguish estimates,
> reservations and runtime measurements. Do not certify workspace as measured
> process-tree RSS or elapsed_seconds=0 as timing evidence. Establish bounded
> decoder allocations, supervised RSS/deadlines and disk reservations under
> existing ceilings or refuse. Enforce combined footer+data per-file totals,
> explicit data subcaps and atomic persisted budget state. No constant-only
> success path, hidden reset or fabricated plan readiness.
>
> Reconcile the earlier M redirect-counting failure without network. Its
> implied 12 first-file footer requests exceed the unchanged 10/file cap.
> If authoritative local evidence cannot resolve that within the existing
> contract, keep M execution/resource compliance BLOCKED; do not waive the
> violation or add another protocol amendment. Evidence adoption may still be
> implemented as observation-only reuse. Missing historical meters must remain
> explicit execution blockers, not requests to silently restart an arm.
>
> Use authored synthetic offline fixtures and focused tests for v2.0 regression,
> v2.1 cap boundaries and exactly-over-cap refusal, immutable selection/rank/ID/
> order namespace, parent tampering/version mixing, INCOMPLETE preservation,
> adoption without network, range splitting, redirect/retry/error-body charging,
> planning carry-in, per-file and arm totals, crash/restart no-reset behavior,
> missing-history refusal, allocator/disk/deadline failure and no false success.
> Use one pytest controller and explicit worker count; single nodes -n 0;
> OMP_NUM_THREADS/MKL_NUM_THREADS/OPENBLAS_NUM_THREADS/NUMEXPR_NUM_THREADS=1 and
> TOKENIZERS_PARALLELISM=false. Use uv run --offline --locked --no-sync
> --extra cpu --extra eval with pinned Python 3.12.13; record actual installed
> packages. No full acceptance, live or CUDA tests by default.
>
> Produce an implementation report, requirement ledger, exact commands/exit
> statuses, unchanged-identity checks and hash-bound offline derived plans or
> explicit unresolved refusal reports. Preserve user edits. No network,
> acquisition, corpus-text inspection, sample/selector changes, tokenizer,
> training, admission, human labeling, push or campaign is authorized.
> Stop after offline implementation and focused verification. Present concrete
> results and remaining gates before seeking any separate acquisition authorization.

## 6. Verification, requirement ledger and stop

[COMMANDS.md](../evidence/ESSENTIAL-WEB-EVIDENCE-V2.1/COMMANDS.md) contains
the exact parent verifier, embedded one-off independent audit, freeze creation
and final validation commands. Parent verifier: exit 0; independent audit:
exit 0. Audit self-digest:
d5d9cad0a01d36c770ff99b66588def053df19d69cd1401ae57acb9a70ced33d.

Environment: Windows 11 build 26200, PowerShell, uv offline/locked/no-sync,
Python 3.12.13; installed package metadata torch 2.14.0+cpu and pyarrow 25.0.1.
Torch was not imported and no CUDA availability test was run.
Dependency pins, lock, Python version and CPU/CUDA installation policy remain
unchanged. No package synchronization or install occurred.

Audit-only measurements: 1,132,941 input bytes read, 0.0966134000045713 seconds
before output; endpoint working set 28,172,288 bytes and peak before output
29,814,784 bytes via Windows GetProcessMemoryInfo. These are not process-tree
acquisition measurements. Audit made zero network requests and read zero corpus
text bytes. Inputs are existing real-source numeric/locator receipts, not
authored synthetic acquisition fixtures or a new live compatibility test.

| Requirement | Status |
|---|---|
| New normative transfer amendment, lineage, scientific assessment, handoff | IMPLEMENTED as documentation |
| Parent hashes, map arithmetic, selection/cost membership, transfer margins | VERIFIED offline |
| Original v2.0 identities/semantics and selection artifact preserved | VERIFIED by byte bindings |
| v2.1 acquisition/tool implementation | NOT RUN; outside this review |
| Complete request/resource plan and historical budget reconciliation | BLOCKED pending implementation/evidence |
| M observation adoption | IMPLEMENTED as protocol; VERIFIED parent integrity, not runtime compliance |
| M cumulative resource compliance | BLOCKED; historical redirect/cap issue disclosed |
| Disk/RSS/runtime execution and live source compatibility | NOT RUN |
| Unit/fast/full/live/CUDA suites | NOT RUN; protocol-only review |
| Network/acquisition/text inspection/training/admission/push | OUT OF SCOPE and not authorized |

Files created: the v2.1 PROTOCOL.md and REVIEW.md reports; v2.1 evidence
COMMANDS.md, verification.json and freeze.json. STATUS.md updated. No executable
product or test code changes. Existing dirty selector-review files preserved.
No commit made; commit SHA: **none**.

Stop at the completed protocol/freeze and the exact Muse implementation prompt
above. **READY TO IMPLEMENT EVIDENCE V2.1** is not READY TO ACQUIRE.

