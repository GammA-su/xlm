# Essential-Web evidence-v2.2: implementation report

Date: 2026-09-28. Branch: `data/mix01-ultrax-6b`. Starting HEAD: `3a54718`
(dirty only with preserved prior user/Astra docs). Implements ONLY
`essential-web-evidence-v2.2` from the normative amendment and its
freeze. No reselection, no text reads, no network, no acquisition.

Agent environment: Python 3.12.13, torch 2.14.0+cpu, pyarrow 25.0.1
(importlib.metadata, this session). No package sync occurred.

## 1. v2.2 freeze verification (recomputed, exit 0)

- Protocol bytes SHA-256 `fd698793…f4f49d` (match).
- Freeze canonical digest `b6602a44…ebd0c` (match); 6/6 artifacts
  bytes/hash/descriptor (match); parent v2.1 freeze `bac82d6b…` bound.
- G: parents verified by bytes/hash/digest: M complete `2ecf3eb9…`,
  M old `08b02d6a…`, T cost map `ed713a0a…`, T old `511d4c07…`,
  selection byte identity (23,807 B / `8424f966…` / `975ba3de…`),
  v2.1 children (M blocked `ac426c44…`, T dry `8cf4c377…`).
- v2.0 freeze, inventory, policy `f4357f61…` reverified. No STOP.

## 2. Scientific identity verification

118 locators, seeds (20260927/20260928), strata, censuses (29/25),
precedence, M files/windows, revision, policy/evaluator, rubric,
blinding/adjudication: all byte-identical. New receipts use version
v2.2 while ranking/selection/review-ID/review-order pin the v2.0
strings literally (tested against recomputed hashes). The selection
manifest was never rewritten.

## 3. M historical request reconciliation: conclusion A (BLOCKED)

Old attempt, first file: 3 measured range attempts (unit counter,
268,808 bytes — byte-identical to the complete pass, proving the same
ranges) + 1 measured failed range on file 2 (budget failed=1) + 4
proven redirect follows (the hop-4 refusal proves exactly 4 handler
invocations; the refused 4th belongs to file 2, so 3 completed inside
file 1). Old file-1 physical total: 3 + 3 = 6.

Complete pass, same file: 3 ranges + 3 follows (per-range hops
(1,1,1) recorded; `footer_requests_used=6`; 268,808 bytes). Distinct
run: different code generation (pre-fix receipt structurally lacks
per-range hop detail), no resume mechanism in the footer transport,
and full per-file re-fetch (6, not an incremental remainder).

Cumulative: 6 + 6 = **12 > 10**. No shared physical-event identities
can exist across separate processes (a claimed overlap raises rather
than reconciles). Evidence is sufficient, so C does not apply. File 2
is clean (1 + 6 = 7). The v2.2 prospective 16/file covers 12 carried
+ ≤4 revalidation, but the recorded v2.0/v2.1 violation stands and the
amendment pardons nothing.

## 4. M historical byte reconciliation: BLOCKED (no sound bound)

Recorded returned range-body bytes total 2,191,448 (per-file table in
the freeze). Redirect/error bodies are unmeasured at both code
generations. Code archaeology at exact revision 943b816 (bound in the
v2.2 freeze): redirect bodies pass through
`SafeRedirectHandler -> TransportBudget.read_body(fp,
budget.max_bytes)` with `max_bytes` = the 268,435,456-byte arm total;
the 4 MiB cap never applied to redirect bodies (it covers planned
range bodies only). Consumed bytes accumulated in-memory only and were
never exported to arm receipts; per-response Content-Length gating
existed but no lengths were recorded. The only mechanically enforced
bound (256 MiB per body) is vacuous; an analyst small estimate is
insufficient. Actual all-body bytes per file and arm remain unknown:
Arm M stays BLOCKED for acquisition on bytes.

## 5. M revalidation exact schedule

Local checks first (revision/path, parent hashes, schema/chunk/window
identities, projection, code/environment/plan binding, ledger,
reservations); then per file, immediately before its data stage, one
logical GET bytes 0-3 with accept on final 206 + exact Content-Range
0-3/recorded-length + 4-byte PAR1 + adopted strong ETag + allowlisted
host (weak/missing/different ETag, changed length, wrong range, 200,
bad magic, unallowlisted redirect, drift all STOP). No HEAD, footer
refetch, relisting, or new windows. Nominal 2 physical/file (observed
one redirect), max 4/file, 25/arm — nested in cumulative 16/file and
80/arm. History + nominal = 55 + 16 = 71/80. Retries consume
reservations; no new allowance on resume. Per-file remaining after
nominal: 2, 7, 8, 8, 8, 8, 8, 8 (sums to the frozen table values).

## 6. M exact physical data request schedule

Per plan nominal comes from the recorded planner estimate (85):
leaf-range estimate plus controls. Redirect-aware physical model under
cached target reuse (first resolution ≤3 follows, later ranges reuse):
max 88/plan ≤ 100; arm nominal 680, max 704 ≤ 800; combined
71 + 800 = 871 and 80 + 800 = 880 exactly. Chunk byte positions are
not recorded in M evidence, so range positions resolve at execution
from the revalidated immutable identity (stated in the artifact, not
hidden); counts and bytes are exact now. No data cap increase.

## 7. M remaining per-file/arm budgets (v2.2 prospective)

Per-file footer requests remaining at 16: 4, 9, 10, 10, 10, 10, 10, 10
(25 under the 80 arm cap; 9 after nominal revalidation). Recorded
range-body bytes remaining from the 4 MiB file ceiling are upper
limits only (computed from recorded bodies: 3,656,688 … 3,973,021 per
file; 31,362,984 arm-wide) — spendable only after missing costs
reconcile, which they do not.

## 8. T dictionary-inclusive range schedule (bug fixed)

All eight text chunks have dictionary_page_offset < data_page_offset;
the v2.1 schedule started at data offsets, omitting 17,539,154 bytes
of required dictionary prefixes and overrunning past true ends.
Corrected rule (chunk start = earliest dictionary/data offset, full
compressed length preserved): 47 nominal ≤4 MiB ranges,
179,963,169 nominal bytes — totals identical, positions corrected
(independently verified against the review's per-file geometry).
Whole-chunk decoding retained; no second decoder, no page redesign,
no selection change. Physical schedule: 63 logical (47 + 16 controls)
+ revalidation, cached reuse after first resolution per file, retries
as bounded stop paths; carry-in 54 → nominal 117 ≤ 800.

## 9. T historical gap reconciliation

Carry entries: 8 attempt-2 files (measured bytes + derived-uniform 6
requests, flagged) + 2014-15 prior (6/236,727 measured) = 54 /
2,231,492, each adopted once (duplicates logged, conflicts refuse).
Bounded gaps (tracked, non-blocking): attempt-2 redirect/error bodies
(48 req / 196,608 B analyst bound) and pre-fix prior gap (6 /
24,576 B). These bounds are NOT authoritative measurements: unknown
bodies/attempt attribution/timing remain explicit blockers, and gap
estimates are never treated as reservations. Unknown-event policy:
any registered explicit unknown blocks readiness.

## 10. T durable ledger fix

Save/load previously persisted adopted entries only, dropping live
request/body/elapsed charges (audit reproduced 1/123/7 → 0/0/0).
Fixed: save persists entries + full live snapshot + in-flight work;
load replays adoption, cross-checks replayed subtotals against saved
counters (refuses on regression), restores counters authoritatively,
and converts open in-flight attempts into crash reservations (bytes
charged, never refunded). Same-ID re-adoption with identical data is
idempotent; conflicting data refuses. Restart never resets budgets.
Footer closure optionally tracks in-flight ranges (default off,
behavior unchanged).

## 11. T remaining request/byte budgets (v2.1 caps, footer never subtracted)

Data headroom per file (data cap minus nominal data only):
2014-15: 8,947,282; 2015-32: 8,265,549; 2016-50: 11,412,546;
2018-05: 9,227,264; 2019-09: 7,820,233; 2021-04: 8,553,637;
2021-49: 7,764,769; 2024-26: 9,707,087. Arm data headroom 38,140,639;
combined recorded-bodies headroom 52,686,363. Request remaining per
file 79–87; arm 683. Tightest file allows 0 full re-attempts (bytes
bind) — execution gets one nominal shot there. Footer usage is never
subtracted from data subcaps; totals bind both stages.

## 12. Memory supervisor status: mechanism implemented, live proof pending

Reservation formula per file (staged compressed + decompressed Arrow
upper + 4 MiB range buffer + 32 MiB parser + retained output + one
duplicate temp copy): 93,613,610…105,045,302 bytes = 89.28…100.18 MiB
(bytes/2^20; earlier 93.6..105.0 figures were MB, corrected here).
All fit 256 MiB with ≥163 MiB margin. `ProcessTreeSupervisor`
measures self + recursive children + registered owned handles via
psutil with pid dedup; over-cap or unobservable refuses (tested with
synthetic trees). Not yet run live: memory_supervision_ok stays False
until supervised enforcement executes. A supplied-RSS function is not
presented as a supervisor.

## 13. Disk schedule status: staged gates modeled, final conditional

`DiskInventory` stages cumulatively with fail-closed intermediate
checks (scratch peak enforced independently; atomic tmp counts
old+new; release requires existence). Acquisition-stage schedule:
high-water 102,098,902 with realistic 9.5 MiB review package —
scratch/final/combined fit. Stress case (package at full 16 MiB cap):
final ~17.9 MiB exceeds the cap, so final-disk stays CONDITIONAL on
measured labeling volume. Staged publication gate: acquisition outputs
must fit reserved final first; later labels reserve remaining space
before every write; overflow publishes bounded INCOMPLETE (never
truncate/drop/resample). Current verdict: disk_schedule_ok False
pending measured label sizes and live enforcement.

## 14. Deadline status: machinery ready, history unknown

Persisted monotonic elapsed + per-request/file/arm earliest-deadline
enforcement implemented and tested (restart-safe). All recorded
historical elapsed values are 0 (unmeasured placeholders). Past T
planning (54 requests, 30 s/request timeout bound) consumed an
unknown duration in [0, 1620] s with unbounded inter-run gaps, so no
certifiable remaining time exists: deadlines_ok False for both arms.
Reported, not erased.

## 15. Parent/provenance binding

v2.2 children bind complete descriptors (role, path, bytes, SHA-256,
canonical digest, original version/status) for all 8 freeze parents:
v2.2/v2.1 freezes, M old/complete, M blocked child (historical), T
cost map/old, T dry child (reviewed-defective), selection. Nothing
relabeled; original statuses preserved (COMPLETE stays planning-only,
INCOMPLETE stays incomplete, DRY stays dry).

## 16. Arm M readiness vector: BLOCKED

scientific_identity_ok True; lineage_ok True;
historical_accounting_ok False (12 > 10 recorded violation);
range_schedule_ok True (counts exact, positions deferred);
requests_ok False (cumulative overrun); response_bytes_ok False
(unknown bodies); decompression_ok True; scan_ok True;
memory_supervision_ok False; disk_schedule_ok False; deadlines_ok
False. No M execution child plan exists — blocked-plan receipt only.

## 17. Arm T readiness vector: BLOCKED

scientific_identity_ok True; lineage_ok True;
historical_accounting_ok False (unknown bodies/timing);
range_schedule_ok True (47 corrected ranges); requests_ok True
(117 nominal ≤ 800); response_bytes_ok False (unknown bodies);
decompression_ok True (311 M ≤ 536 M); scan_ok True (38,400);
memory_supervision_ok False (mechanism only); disk_schedule_ok False
(final conditional); deadlines_ok False (unknown history). T dry
child plan built; no authorization.

## 18. Test results

27 new v2.2 tests + 109 prior evidence tests + 67 sampling/bounds
regressions: **203 pass** (`-n 0`, sockets blocked, pinned threads).
Covers every mission bullet: identity/lineage/namespace/selection,
16/file + unchanged caps, historical-12-under-16 with violation mark,
17th-request refusal, unknown-bytes block, proven-bound reservation,
revalidation accept/reject matrix, data schedule fits, dictionary
regression (17,539,154) with no-overrun, 47-range reproduction,
carry-in/dedupe/restart/unknown/conflict/crash, supervisor/children/
over-cap/unobservable, disk phase/overflow/atomic/cumulative/release,
deadline persistence/restart/unknown, parent descriptors/roles,
readiness semantics. `ruff check` / `format --check` clean; scoped
`mypy` clean (19 files). No live/network/CUDA/full suites.

## 19. Files changed

New: `carry.py` (durable fix), `schedule.py` (v2.2: dict spans,
physical/T/M-data/revalidation/remaining), `reserves.py`
(supervisor/disk/deadlines/staged gate), `lineage.py` (v2.2 +
descriptors), `m_audit.py` (byte audit), `child_plans.py` (v2.2
bundle + readiness), `tests/test_evidence_v22.py`,
`ESSENTIAL-WEB-EVIDENCE-V2.2-CHILD/` (9 artifacts + README), this
report. Modified: `budgets.py` (nested transfer, per-file requests,
combined caps, split subcaps, redirect notes, history adoption),
`frozen.py` (V22 constants, additive), `text_costs.py` (optional caps
threading), `footer.py` (in-flight wiring, diagnostics),
`scripts/evidence_v2.py` (verify-v22, reconcile, audit-m,
build-child-plans). Unchanged: protocol, freezes, selection, caps
beyond v2.2, reader, selectors, transport.

## 20. Requirement ledger

| Requirement | Status |
|---|---|
| v2.2 freeze/parents/selection verification | VERIFIED offline |
| v2.0 namespace + scientific identity | VERIFIED + tested |
| Only M 16/file cap change (+ nothing else) | IMPLEMENTED + tested |
| M request history reconciliation (55 physical) | RECONCILED (12>10 recorded) |
| M byte unknowns bound-or-block | BLOCKED (no sound bound) |
| M revalidation + data schedules + remaining | IMPLEMENTED + tested |
| T dictionary ranges + physical schedule + remaining | IMPLEMENTED + tested |
| Durable ledger repair | IMPLEMENTED + tested |
| T caps unchanged + headroom correction | IMPLEMENTED + tested |
| Memory supervisor / disk / deadlines | MECHANISMS + tested; live proof pending |
| Parent descriptors + 9 children | IMPLEMENTED (paths §21) |
| Acquisition/text/network/training/push | NOT RUN / OUT OF SCOPE |

## 21. Child artifact paths + digests (offline, DRY, no authorization)

- `docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.2-CHILD/arm_m_child_plan.json`
  `b68513c5…`
- `.../arm_t_child_plan.json` `b69600a2…`
- `.../readiness_review.json` `3f1ae11d…`
- `.../ledger_reconciliation.json` `979511b0…` (same content as frozen capture)
- `.../disk_schedule.json` `d8fb9f4c…`
- `.../memory_schedule.json` `c73e997f…`
- `.../deadline_schedule.json` `b3810228…`
- `.../range_schedule_m.json` `96319fbb…` (this run; positions deferred)
- `.../range_schedule_t.json` `4285e63c…`
- Regenerate: `build-child-plans --out-dir <fresh-empty-dir>` (offline).

## 22. What remains BLOCKED

1. Arm-M historical resource compliance (12 > 10 recorded; unknown
   redirect/error bytes with no sound bound).
2. Arm-T historical unknown bodies/timing/durations.
3. Final-disk fit pending measured labeling volume.
4. Revalidation of adopted observations under remaining budgets.
5. Measured RSS/disk/runtime proof (reservations are not measurements).
6. Any acquisition (no authorization in any freeze or task).

## 23. Acquisition authorization review readiness

Neither arm is ready — verdict below. No acquisition commands are
provided because inherited integrity/resource blocks are unresolved.

## 24. Commands and exit statuses (this task)

- `verify-v21-freeze`, `verify-freeze` — exit 0.
- `reconcile-carry-in` — exit 0 (receipt `979511b0…`).
- `audit-m-history` — exit 0 (conclusion A).
- `build-child-plans --out-dir …` — exit 0 (9 digests above).
- Focused pytest (203) — exit 0. `ruff check`, `ruff format
  --check`, scoped `mypy` — clean.
- Installed packages: torch 2.14.0+cpu, pyarrow 25.0.1, Python 3.12.13.
