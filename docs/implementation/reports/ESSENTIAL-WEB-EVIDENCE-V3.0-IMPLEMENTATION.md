# Essential-Web evidence v3.0: offline implementation

2026-09-29. Baseline reviewed commit: `4051e5c4052e5e04da85f14b791a8b406bb9e8b3`.
Current checkout: `F:\Project\xlm-data-ultrax`, branch `data/mix01-ultrax-6b`.
Frozen execution root remains `G:/Project/xlm-evidence-v3/essential-web` (absent,
never created or remapped here).

**READY FOR INDEPENDENT PHASE-P AUTHORIZATION REVIEW.** Data authorization
remains blocked until separately authorized P supplies complete validated plans.
No live authorization ticket or `epoch_start.json` was generated.

## 1. Lineage closure (exact)

v2.0–v2.2 are permanently `CLOSED_NON_EXECUTABLE` /
`HISTORICALLY_UNCERTIFIABLE` (`lineage_closure.json`
`ceaa2fbc6c0f638d333b3e08fd1591d2619bfe645c75ce7028c0e2bae96dda9b`).
Old receipts and reports are preserved byte-identically. Old network usage
(55 M requests / 2,191,448 range-body bytes; 54 T requests / 2,231,492 bytes,
plus unknown redirect/error bodies and elapsed time) remains historical
provenance. It is never imported as v3 execution-budget consumption.

v3 is a newly frozen prospective execution contract whose budget interval
begins only at its own epoch genesis. Reports must present v3 metered costs
separately from historical lower bounds and unknowns.

## 2. v3 epoch semantics

Epoch ID `essential-web-evidence-v3.0:essential-web:epoch-0001`, initial state
`NOT_STARTED`, authorization `NONE`. At epoch start v3 network counters are
zero because no v3 event has yet occurred. There is no reset, second genesis,
automatic epoch replacement, or restart refund. Every v3 event is durably
metered (request reservation, physical request, redirects, retries, failures,
response-body bytes, partials, decompressed work, scanned rows, elapsed active
time, scratch/final reservations, memory supervision events). Same event ID +
same event replays idempotently; same ID + conflict refuses. Crash uncertainty
reserves attempted work rather than refunding it.

## 3. Scientific identity adoption (byte-identical)

Adopted by reference, never regenerated:

- ARM M: same eight frozen files, eight deterministic 512-row windows,
  metadata seed 20260927, revision `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`,
  projection `[eai_taxonomy, quality_signals]`, 512 rows/file.
- ARM T: same exact 118 locators, strata/censuses/precedence, review rubric,
  blinding, review-order construction (seed 20260928).
- Selector policy digest `f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07`.
- Hash namespace `essential-web-evidence-v2.0`; selection canonical digest
  `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474`
  (raw SHA-256 `8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27`).

## 4. Planning-evidence adoption

Old footer/cost records are `adopted_planning_observation` only:

- M file/window/footer observations (`2ecf3eb9…`),
- T whole-chunk cost observations (`ed713a0a…`),
- corrected T range schedule,
- dictionary-inclusive chunk knowledge, lengths/ETags/schema identities.

They inform prospective dry schedules subject to v3 identity revalidation.
Remote state must still be minimally revalidated under v3 budget before data.

## 5. Dry schedules (independently reproduced, not trusted constants)

M phase P (per file): GET 0–3 identity, then exact recorded full footer plus
trailer `[start, N-1]`; 2 logical/file, 3 nominal physical/file, 5 cold/file;
arm 16 / 24 / 40. Footer/control cold chain (P 40 + D identity 32) = 72 ≤ 80.

M phase D (after sealed P + separate D auth): fresh GET 0–3/file, then 81
projected full chunks/file (648 arm). Nominal 688, cold-no-retry 720.
Data payload 11,692,530 B; footer both-phases 1,398,448 B; total 13,090,978 B.
Fits: footer 16/file 80/arm; data 100/file 800/arm; all 880.

T phase P (per file): GET 0–3, GET N-8..N-1, trailer-resolved GET N-8-L..N-9;
3 logical/file, 4 nominal/file, 6 cold/file; arm 24 / 32 / 48. No text read.
Footer reserve 2 MiB/file within existing caps.

T phase D (after sealed P + separate D auth): 47 dictionary-inclusive ranges
(6/file except 5 for 2016-50), 179,963,169 compressed bytes, conservative bound
213,517,601. Nominal 95, cold-no-retry 127. No page-decoder redesign; the older
schedule omitting 17,539,154 dictionary-prefix bytes is not used.

## 6. Caps (unchanged)

M: footer/data requests 16/file 80/arm and 100/file 800/arm, all 880; bytes
4/28/32 MiB per file, 32/224/256 MiB per arm; body 4 MiB; decompress 64/512 MiB;
scan 16384/131072; parser 32 MiB; RSS 256 MiB; disk 448/64/512 MiB; time
1800/600/30 s. T: requests 100/file 800/arm; bytes 2/30/32 per file,
16/240/256 per arm; same body/decompress/scan/parser/RSS; disk 496/16/512 MiB;
document 64 KiB, retained 8 MiB, final 16 MiB. No borrowing, no growth.

## 7. Phase authorization design

Per-arm state machine `NOT_STARTED → P_AUTHORIZED → P_RUNNING →
P_COMPLETE_SEALED → D_AUTHORIZED → D_RUNNING → D_COMPLETE`
(with `INCOMPLETE`/`REFUSED` terminals). P authorization never authorizes D.
D authorization binds the sealed P digest inside the artifact and refuses on
mismatch. Wrong digests refuse; no boolean CLI bypass (`prepare-epoch`
requires `--authorization <artifact>` plus `--synthetic-root` for mechanism
tests; real genesis is refused in this task).

## 8. Fresh root / contamination control

Frozen root `G:/Project/xlm-evidence-v3/essential-web` verified absent
(`verify-v3` reports `execution_root_absent: true`). Genesis requires absent
OR empty pristine directory, pre-write inventory, no old caches/partials/rows/
text/ledgers copied in. Only hash-allowlisted planning metadata may be
imported (with lineage/role/bytes/SHA), counted against disk. Unknown files
STOP. `G:` absence after the Windows reinstall is reported as a readiness
item; no `F:` substitute was created.

## 9. Runtime guard repairs (vs v2.2 substrate)

- `FailClosedSupervisor`: missing owned PIDs without terminated proof and
  unreadable children are unobservable → refuse. No zero-RSS insertion, no
  silent skip. Covers child inclusion, dedup, over-cap, exit race.
- `PhysicalDiskInventory`: reservation BEFORE write, atomic tmp+fsync+replace,
  release only after verified deletion, per-phase filesystem reconciliation,
  unknown-file STOP, staged 16 MiB cumulative final gate preserved.
- `ActiveRuntime`: persisted arm/file totals + monotonic anchors, absolute
  30 s request bound clipped by remaining file/arm time, crash uncertainty
  conserved, only sealed quiescent pauses suspend charging. No wall-clock
  subtraction. `begin_range` inclusive/half-open conventions are pinned in
  dry schedules; durable pre-I/O issuance is enforced in `V3Ledger.apply`.

## 10. Dry child artifacts

Under `docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-CHILD/`
(79,462 bytes total), each sealed with canonical digest + file SHA-256 +
producer/code/environment identity, `authorization: NONE`, `executable: false`:

| Artifact | Bytes | SHA-256 (prefix) | Canonical (prefix) |
|---|---|---|---|
| arm_m_phase_p_dry.json | 8695 | c5ab00ef2e6b | b15037742b86 |
| arm_m_phase_d_dry.json | 6850 | 1b6c8ecf3f54 | 62e274d11ea0 |
| arm_t_phase_p_dry.json | 6804 | 22a8da562707 | 47fb722a20e8 |
| arm_t_phase_d_dry.json | 8852 | 898ccd832b41 | 516d01150af0 |
| prospective_budget_m.json | 5637 | 3c81cdea7b8a | 6e167aefa59d |
| prospective_budget_t.json | 5695 | facc3589a21f | 694e97f43e65 |
| adopted_planning_evidence.json | 6233 | cf5a82c643df | b670e201b8ee |
| scientific_identity_adoption.json | 6983 | 8c42e0a14b1b | e7d8a9351084 |
| memory_guard.json | 4651 | 47ed97fc3608 | c58771a3617b |
| disk_schedule.json | 4524 | 1e7ee362e3de | 6ad3347b088e |
| runtime_schedule.json | 4466 | de5c1fc9cbd9 | 2221403e2c56 |
| epoch_genesis_template.json | 4844 | 306f170d064d | 8b798805f813 |
| readiness_review.json | 5228 | 15fae63a5421 | f1d8bad2272e |
| artifact_manifest.json | 8211 | a4b9498def2b | dd394bf4b43a |

Manifest digest: `dd394bf4b43a252289def6462b82c67186f3302323941d5d0a07409453963945`.

## 11. Readiness vector

M: scientific_identity_ok true, lineage_closed_v2_ok true,
v3_epoch_semantics_ok true, planning_adoption_ok true, phase_p_schedule_ok
true, phase_d_schedule_ok true, prospective_requests_ok true,
prospective_bytes_ok true, decompression_ok true, scan_ok true,
memory_supervision_ok true, disk_schedule_ok true, runtime_accounting_ok true,
authorization NONE → **READY_FOR_PHASE_P_AUTHORIZATION_REVIEW**.
T: identical → **READY_FOR_PHASE_P_AUTHORIZATION_REVIEW**.
D authorization is not requested (P has not run/sealed).

## 12. Tests (offline/synthetic only)

- `tests/test_evidence_v3.py`: 40 passed, 0 skipped. Covers lineage, epoch
  genesis (ID, NOT_STARTED, auth required, once-only, pristine root,
  inventory, atomic publish), ledger (zero start, first charge, restart,
  idempotence, conflict refusal, crash reservation, no v2 import), M P/D,
  T P/D, request/byte totals, P/D authorization separation, fail-closed
  memory (child/dedup/unobservable/over-cap/exit race), physical disk
  (fresh root, unknown refusal, reservation-before-write, atomic temp,
  verified deletion, reconciliation, staged publication), monotonic runtime
  (persisted resume, earliest deadline, sealed pause), no-network static guard.
- Evidence-v2 regressions: `test_evidence_v22` + `test_evidence_v21` 54 passed;
  `test_evidence_v2_core/footer/text` 109 passed.
- `ruff check` clean; `ruff format --check` clean; scoped `mypy`
  (`src/xlm/data/evidence_v3`, `scripts/evidence_v3.py`) clean.
- Full suite, live-source, and CUDA tests NOT RUN (explicit final gate only).

Environment: Windows 11, Python 3.12.13, torch 2.14.0+cpu, CUDA False,
pyarrow 25.0.1, psutil 7.2.2. Commands used `uv run --offline --locked
--no-sync --extra cpu --extra eval` with `-n 0` for single-file runs.
No network, acquisition, corpus text, or push.

## 13. Requirement ledger

| Requirement | Status | Evidence |
|---|---|---|
| v3 protocol/freeze/epoch/lineage verification | VERIFIED | `verify-v3` exit 0; digests match |
| v2 parent graph + terminal BLOCKED preserved | VERIFIED | 39 history + 29 code bindings; terminal digest match |
| Scientific identity adoption (M/T/policy/namespace) | VERIFIED | Byte bindings; 40 v3 tests |
| Planning-observation adoption (no v3 spend import) | VERIFIED | Role flags; ledger refusal test |
| Epoch genesis mechanism (one-shot, auth-bound, atomic) | IMPLEMENTED | `epoch.py`; synthetic test only |
| V3 ledger (zero, durable, idempotent, crash-conserving) | IMPLEMENTED | `ledger.py`; focused tests |
| M phase-P dry schedule (8 files, 0-3 + footer, ≤80) | VERIFIED | 16/24/40; cold 72 ≤ 80 |
| M phase-D dry schedule (648 chunks, 688/720) | VERIFIED | Reproduced arithmetic |
| T phase-P dry schedule (24/32/48, no text) | VERIFIED | Pattern + reserve checks |
| T phase-D dry schedule (47 ranges, 95/127) | VERIFIED | Corrected spans; no old schedule |
| Phase separation + digest-bound authorization | IMPLEMENTED | `PhaseGate`; P≠D tests |
| Fail-closed memory supervision | IMPLEMENTED | `FailClosedSupervisor`; 5 tests |
| Physical disk inventory + reconciliation | IMPLEMENTED | `PhysicalDiskInventory`; 5 tests |
| Monotonic runtime accounting + sealed pauses | IMPLEMENTED | `ActiveRuntime`; 3 tests |
| Fresh-root enforcement (no remap/creation) | VERIFIED | Absent; readiness item |
| DRY child artifacts + manifest | IMPLEMENTED | 14 files, manifest digest above |
| v2.x acquisition lineage | BLOCKED | Permanently closed (provenance only) |
| Real epoch genesis / network / acquisition / text | NOT RUN | Epoch NOT_STARTED, root absent |
| Training/tokenizer/admission/promotion/push | OUT OF SCOPE | Not undertaken |

## 14. Files changed

- `src/xlm/data/evidence_v3/__init__.py` (new)
- `src/xlm/data/evidence_v3/frozen_v3.py` (new)
- `src/xlm/data/evidence_v3/epoch.py` (new)
- `src/xlm/data/evidence_v3/ledger.py` (new)
- `src/xlm/data/evidence_v3/schedules.py` (new)
- `src/xlm/data/evidence_v3/guards.py` (new)
- `src/xlm/data/evidence_v3/dry.py` (new)
- `scripts/evidence_v3.py` (new)
- `tests/test_evidence_v3.py` (new, 40 tests)
- `docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-CHILD/` (14 new dry artifacts)
- `docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-IMPLEMENTATION.md` (this report)
- `docs/implementation/STATUS.md` (index entry prepended)

Frozen protocol, review, freeze, epoch definition, lineage closure, and all
v2 artifacts are untouched. User/Astra working-tree changes preserved.

## 15. Remaining blockers / unknowns

1. Independent phase-P authorization review (no ticket issued here).
2. Frozen execution root `G:/Project/...` is absent on this machine; activation
   requires resolving that location explicitly (no remap performed).
3. T footer actual lengths L unknown until P runs (bounded by 2 MiB/file reserve).
4. M data offsets unresolved until authorized P seals them.
5. No live-source compatibility claim (synthetic fixtures only).
6. No measured peak RSS / disk / timing (formula reservations + guard design only).

## 16. Exact next operator action

Run the independent phase-P authorization review against the frozen protocol,
freeze, dry child plans, and this implementation report. Do NOT create
`epoch_start.json`, do NOT run `prepare-epoch` in real mode, do NOT issue any
data/text acquisition command. The only in-scope commands were the offline
`verify-v3` and `build-v3-dry-plans` invocations already executed.
