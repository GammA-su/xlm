# Essential-Web evidence-v2.1: implementation report

Date: 2026-09-28. Branch: `data/mix01-ultrax-6b`. Starting HEAD: `1c05bd3`
(dirty only with preserved prior user/Astra docs). Implements ONLY
`essential-web-evidence-v2.1` from the normative amendment and its
freeze. No reselection, no text reads, no network, no acquisition.

Agent environment: Python 3.12.13, torch 2.14.0+cpu, pyarrow 25.0.1
(importlib.metadata, this session). No package sync occurred.

## 1. Freeze and parent verification (recomputed, exit 0)

- Protocol bytes SHA-256 `1c437881…fcdb4b` (match).
- Freeze canonical digest `bac82d6b…76832a7cd` (match); 4/4 artifacts
  bytes/hash/descriptor (match); parent freeze `fe215779…` bound (match).
- G: parents verified by bytes/hash/digest: M footer evidence
  `2ecf3eb9…` (879,128 bytes), T attempt-2 cost map `ed713a0a…`
  (24,617 bytes), old M/T incompletes, selection manifest byte identity
  (23,807 bytes, `8424f966…`, digest `975ba3de…`, 118 locators).
- v2.0 freeze, inventory, and policy `f4357f61…` reverified. No STOP.

## 2. Scientific identity preservation

118 locators, strata, censuses (29/25), precedence, seeds (20260927 /
20260928), M files/windows, revision, policy, rubric, blinding rules:
all unchanged. New receipts use version `essential-web-evidence-v2.1`
while ranking/selection/review-ID/review-order pin the v2.0 strings
literally (tested against recomputed hashes). The selection manifest
was not rewritten. Only Arm-T transfer ceilings changed.

## 3. Exact cap changes implemented (v2.1, additive code)

Per file: footer 2,097,152 (unchanged), data 14,680,064 -> 31,457,280,
total 16,777,216 -> 33,554,432. Arm: footer 16,777,216 (unchanged),
data 117,440,512-implied -> 251,658,240 (= 8 x 31,457,280, no extra
allowance), total 134,217,728 -> 268,435,456. Unchanged: 4 MiB bodies,
64 MiB/file / 512 MiB/arm decompression, 16,384/131,072 scan, 32 MiB
parser, 256 MiB RSS, 64 KiB documents, 8 MiB retained text, 16 MiB
final artifacts, disk 520/16/536 MiB, 100/file / 800/arm requests,
30/600/1800 s deadlines, 2 retries (1 s/2 s), 3 hops, one file/worker,
host allowlist. v2.0 defaults untouched; v2.1 paths opt in explicitly.

Enforcement repairs (review handoff obligations, same frozen values):
combined footer+data per-file request total (T), per-file request
breakdown in snapshots, per-category file/arm transfer cells with
footer/data subcaps plus combined totals (no borrowing either way),
redirect/error body capture via transport-meter deltas, redirect
transition counting in the ledger.

## 4. T durable carry-in reconciliation (54 requests / 2,231,492 bytes)

Adopted once each (entry IDs `receipt:file:category`, duplicates
logged never recharged): attempt-2 per file (bytes measured per file,
requests 6 each derived-uniform from the measured arm total 48 —
receipts do not break requests down per file, flagged as such),
2014-15 prior (6 requests / 236,727 bytes, measured). Per-file carry:
2014-15 = 12 / 473,454; others = 6 each + measured bytes. Bounded
gaps (tracked, non-blocking): attempt-2 redirect/error bodies
(48 req / 196,608 B analyst bound) and pre-fix prior redirect gap
(6 / 24,576). No explicit unknowns: readiness ready=true on history.
Restart persistence replays entries idempotently (tested). Reconcile
command prints/seals the entry-level receipt (digest `979511b0…`).

## 5. T range schedule (offline, from recorded chunk spans)

One text chunk per file; each span split into <=4 MiB exact,
non-overlapping, fully covering ranges: 6 ranges for seven files, 5
for 2016-50 — 47 nominal data ranges, 179,963,169 nominal bytes
(reproduces the review's independent 47 exactly). Plus 2 nominal
control requests/file (HEAD identity + ETag revalidation): 63 nominal
requests; +54 carried = 117 <= 800. Max full re-attempts per file from
the tighter of request/byte caps (2014-15: 10 by requests, 0 by bytes
-> 0 re-attempts; nominal fits with 8,470,828 data bytes and 80
requests remaining). Retries are ceilings that stop execution, never
promised paths.

## 6. T remaining transfer/request budgets (v2.1 caps, after carry-in)

Per file data remaining: 2014-15: 8,470,828; 2015-32: 8,003,713;
2016-50: 11,192,610; 2018-05: 8,796,170; 2019-09: 7,138,726;
2021-04: 8,547,700; 2021-49: 7,415,267; 2024-26: 10,086,049.
Per file requests remaining: 80, 86, 87, 87, 86, 87, 82, 79.
Arm: data 251,658,240 - 213,517,601 - 2,231,492 = 35,909,147;
total 268,435,456 - 215,749,093 = 52,686,363 (matches review).
Decompression sum 311,068,163 (max 41,297,124, both fit); scan sum
38,400 (max 8,448, both fit).

## 7. Decompression/scan status

Fit under unchanged caps with the margins above. Decompression bounds
are whole-chunk footer values including repeated work allowance, not
measured decoder output. Scan bounds are batch-covering estimates, not
measured reads.

## 8. Memory reservation and enforcement design

Per-file reservation = staged compressed + decompressed Arrow upper +
4 MiB range buffer + 32 MiB parser + retained output + one duplicate
temp copy. Results: 93.6–105.0 MiB per file (max 2021-49 at
105,045,302), all <= 268,435,456 with >=163 MiB margin. Enforcement:
refuse unless the reservation fits AND a process-tree supervisor
reports measured RSS; missing measurement refuses (tested). Workspace
numbers are reservations, not measured RSS.

## 9. Disk high-water reservation

One file at a time; adopted artifacts ~68 KiB persist. Worst file
stage ~102.0 MiB scratch. With a realistic review package (~9.5 MiB:
7.7 MiB text + forms/labels/mapping + 1 MiB logs): high-water
102,098,902, final 10,581,984 — scratch/final/combined all fit.
Stress case (package at the full 16 MiB cap): final reaches
~17.9 MiB, exceeding the cap by ~1.1 MiB. Final-disk therefore stays
CONDITIONAL on measured labeling volume; atomic tmp copies are in the
schedule. Not a green light, a computed gate.

## 10. Runtime/deadline enforcement

Monotonic deadlines 1800/arm, 600/file, 30/request, earliest wins
(`charge_time` refuses past caps; tested). All recorded
`elapsed_seconds=0` values are unmeasured placeholders, never timing
evidence. No runtime was consumed in this task.

## 11. Arm-M historical accounting audit: conclusion A (BLOCKED)

Old attempt, first file (train-01860): 3 measured successful ranges
(unit `footer_requests_used=3`, 268,808 bytes — byte-identical to the
complete pass, proving the same ranges) + 1 measured failed range
(budget `failed_requests=1`, file 2's header) + 4 proven redirect
follows (the hop-4 refusal proves exactly 4 handler invocations; the
refused 4th belongs to file 2, so 3 completed inside file 1).
Old file-1 physical total: 3 + 3 = 6.

Complete pass, same file: 3 ranges + 3 follows (per-range hops
(1,1,1) recorded; `footer_requests_used=6`; 268,808 bytes). Distinct
run: different code generation (pre-fix receipt has no per-range hop
detail by construction), no resume mechanism in the footer transport,
and full per-file re-fetch (6, not an incremental remainder).

Cumulative first-file planning usage: 6 + 6 = **12 > 10** (even
excluding every failure: 3+3 and 3+3). No shared physical-event
identities can exist across separate processes (B refuted — a claimed
overlap raises rather than reconciles). Evidence is sufficient
(receipts + reason strings + code semantics at both commits), so C
does not apply. File 2 is clean (1 + 6 = 7 <= 10).

Consequence: Arm-M historical resource compliance is BLOCKED. No M
execution child plan is emitted (blocked-plan receipt instead); the M
observations remain adopted as observation-only evidence with this
disclosed limitation. The v2.1 T-only amendment cannot cure it. No
reset, waiver, deletion, rerun, or relabeling was performed.

## 12. Arm-M readiness: BLOCKED. Arm-T readiness: conditional

M: BLOCKED on cumulative 12 > 10 (blocked-plan artifact
`arm_m_blocked_plan.json`, digest `ac426c44…`, executable false).
T: implementation complete — v2.1 caps enforced, carry-in reconciled
(54/2231492 + bounded gaps), exact 47-range schedule, remaining
budgets computed, reservations fit (memory/disk-combined/scratch;
final-disk conditional per §9), deadlines designed. T acquisition
itself remains NOT AUTHORIZED (dry child plan
`arm_t_child_plan_dry.json`, digest `8cf4c377…`, executable false,
authorization NONE). M-before-T sealing order is preserved.

## 13. Parent/adoption receipt semantics

M footer evidence: adopted_observation (COMPLETE planning status kept).
T attempt-2: historical_incomplete_cost_map (INCOMPLETE kept; never a
success). Old incompletes: budget_history (+ M_unreconciled marker).
Selection: byte_identical_selection. New artifacts bind all parents
plus v2.1 protocol/freeze digests separately. Nothing overwritten.

## 14. Tests and results

27 new v2.1 tests + 109 prior evidence tests + 67 sampling/bounds
regressions: **203 pass** (`-n 0`, sockets blocked, pinned threads).
Covers: v2.1 freeze/parents/protocol/selection identities, namespace
pinning (literal recomputed rank/ID/order hashes), exact amended caps,
no-borrowing, combined totals, body cap, v2.0 default stability,
carry-in totals/dedupe/restart/unknown-blocking, range splitting and
bounds, M audit A/B/C paths, memory/disk/deadline guards, lineage
roles, child-plan gating. `ruff check` / `format --check` clean;
scoped `mypy` clean (19 files). No live/network/CUDA/full suites.

## 15. Files changed

New: `carry.py`, `schedule.py`, `reserves.py`, `lineage.py`,
`m_audit.py`, `child_plans.py`, `tests/test_evidence_v21.py`,
`ESSENTIAL-WEB-EVIDENCE-V2.1-IMPLEMENTATION.md` (this file),
`ESSENTIAL-WEB-EVIDENCE-V2.1-CHILD/` (`arm_m_blocked_plan.json`,
`arm_t_child_plan_dry.json`, README). Modified: `budgets.py`
(ledger extensions), `frozen.py` (V21 constants, additive),
`text_costs.py` (optional caps threading), `footer.py` (redirect notes,
body capture), `scripts/evidence_v2.py` (verify-v21, reconcile,
audit-m, child-plans), STATUS.md. Unchanged: protocol, freeze,
selection, caps beyond the amendment, reader, selectors.

## 16. Requirement ledger

| Requirement | Status |
|---|---|
| v2.1 freeze/parents/selection verification | VERIFIED offline |
| Scientific identity preservation (v2.0 namespace) | VERIFIED + tested |
| Amended T transfer caps + no-borrowing enforcement | IMPLEMENTED + tested |
| Durable carry-in (54/2231492) + reconciliation | IMPLEMENTED + tested |
| Exact range schedule (47 ranges) + remaining budgets | IMPLEMENTED + tested |
| Memory/disk/runtime reservations | IMPLEMENTED (final-disk conditional) |
| M historical audit | A/BLOCKED, proven |
| M execution child plan | BLOCKED-plan receipt (no executable plan) |
| T dry child plan | IMPLEMENTED (no authorization) |
| Acquisition/text/network/training/admission/push | NOT RUN / OUT OF SCOPE |

## 17. Child-plan artifact paths (offline, DRY, no authorization)

- `docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1-CHILD/arm_m_blocked_plan.json`
- `docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2.1-CHILD/arm_t_child_plan_dry.json`

Regenerate byte-identically with:
`uv run --offline --locked --no-sync --extra cpu --extra eval python
scripts/evidence_v2.py build-child-plans --out-dir <fresh-empty-dir>`
(offline; reads local receipts only).

## 18. What remains BLOCKED

1. Arm-M historical resource compliance (12 > 10, uncurable here).
2. Any acquisition (no authorization in any freeze or task).
3. Final-disk fit pending measured labeling volume.
4. Revalidation of adopted observations under remaining budget.
5. Measured RSS/disk/runtime proof (reservations are not measurements).

Exact next operator action: none authorized. A future acquisition
authorization review must explicitly resolve blocks 1–5 first; no
acquisition commands are provided because inherited blocks are
unresolved.

## 19. Commands and exit statuses (this task)

- `evidence_v2.py verify-v21-freeze` — exit 0.
- `evidence_v2.py reconcile-carry-in` — exit 0 (receipt `979511b0…`).
- `evidence_v2.py audit-m-history` — exit 0 (conclusion A).
- `evidence_v2.py build-child-plans --out-dir …` — exit 0 (digests above).
- Focused pytest (136 + 67) — exit 0. `ruff check`, `ruff format
  --check`, scoped `mypy` — clean.
