# Essential-Web evidence v3.0: offline implementation

> **Superseded execution design (2026-09-29).** A second independent review
> blocked Phase-P authorization for commit `37c9fcc`. The helper modules
> described in §7–§15 below (`authz`, `epoch`, `guards`, `ledger`, the old
> executor/transport) were deleted and replaced by one trusted Phase-P
> boundary in commit `3dd5ebce0edb7d8e676966c9195b73fb5a1978c9`; the CHILD
> artifacts were regenerated against it. The current design, exploit
> reproduction, tests, digests and limits are in
> [OPUS-REMEDIATION](ESSENTIAL-WEB-EVIDENCE-V3.0-OPUS-REMEDIATION.md). The
> scientific identity, lineage, caps and dry arithmetic below are unchanged.

2026-09-29. Baseline reviewed commit: `4051e5c4052e5e04da85f14b791a8b406bb9e8b3`.
Frozen protocol commit: `52569c525a6613faab096d17b60167b4aaa0f214`.
Remediation commit: see §14.
Current checkout: `F:\Project\xlm-data-ultrax`, branch `data/mix01-ultrax-6b`.
Frozen execution root remains `G:/Project/xlm-evidence-v3/essential-web` (absent,
never created or remapped here).

**READY TO REPEAT PHASE-P AUTHORIZATION REVIEW.** The independent review's
BLOCKED verdict (enforcement gaps; identity/arithmetic had passed) is
remediated below and in
[REMEDIATION](ESSENTIAL-WEB-EVIDENCE-V3.0-REMEDIATION.md). Data authorization
remains blocked until separately authorized P supplies complete validated plans.
No live authorization ticket, no operator approval, and no `epoch_start.json`
were generated.

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
mismatch.

Remediation (independent review): the permissive digest-only check is replaced
by the strict 26-field Phase-P schema in `authz.py`
(kind `essential-web-evidence-v3-phase-p-authorization`, schema 1,
`phase = "P"`, `reviewer_decision = "APPROVED"` only, exact frozen
epoch/root/namespace/selection/revision/caps/plan/commit/code bindings,
stale-review refusal; self-digest excludes only `digest`). Cap-map digest
`e2485cd4…54e0f7b` is recomputed from `freeze.json` before use. Operator
approval is a separate explicit artifact bound to the authorization core;
no `--yes`/`--force`/boolean/env bypass exists. Genesis claims are
exclusive (`O_CREAT|O_EXCL`) with a fully bound record. The integrated
`PhasePExecutor` is the only Phase-P path, gating epoch, authorization,
approval, PhaseGate, inventory, ledgers, memory, disk, runtime, transport
policy, body accounting, and remote identity. No CLI bypass, no raw
transport bypass.

## 8. Fresh root / contamination control

Frozen root `G:/Project/xlm-evidence-v3/essential-web` verified absent
(`verify-v3` reports `execution_root_absent: true`). Genesis requires absent
OR empty pristine directory, pre-write inventory, no old caches/partials/rows/
text/ledgers copied in. Only hash-allowlisted planning metadata may be
imported (with lineage/role/bytes/SHA), counted against disk. Unknown files
STOP. `G:` absence after the Windows reinstall is reported as a readiness
item; no `F:` substitute was created.

## 9. Runtime guard repairs (vs v2.2 substrate, plus Astra remediation)

- `FailClosedSupervisor` + authoritative `OwnedProcessRegistry`: exit proof
  is a registry-issued nonce-bound `ExitProof`; booleans and forged proofs
  refuse. Missing owned PIDs and unreadable children are unobservable →
  refuse. `enforce_around` wraps work with pre/post plus periodic monitor
  hooks; the executor checks memory around every operation (256 MiB cap).
  Parser/allocation reservations qualified.
- `PhysicalDiskInventory`: strict in-root containment (`..`, absolute,
  symlink/reparse, ancestor redirection, alternate volume all refuse);
  reservation totals must cover actual writes or the write refuses and the
  object is marked incomplete; atomic replace counts old+temp into a real
  peak-physical mark from filesystem walks; inventory persists;
  unknown-file STOP; release after verified deletion; allowlisted,
  hash-verified, disk-counted imports.
- `ActiveRuntime`: open-segment elapsed included in every budget query
  (an over-allowance open segment reports zero); anchors never cleared
  without charging (refused `end_segment` keeps anchor + elapsed);
  open-segment state persists and is conserved at restart; pauses require
  explicit sealed quiescent state and refuse mid-segment; earliest
  request/file/arm deadline wins. No wall-clock subtraction.
- `V3Ledger`: fsynced append-only journal with hash-chained lines and
  RESERVED/ISSUED/RESPONSE_STARTED/COMPLETE/FAILED/REFUSED/CRASH_RESERVED
  states; reserve-before-I/O; incremental body metering; explicit category
  persistence; replay-is-truth recovery with truncation/corruption/rollback
  refusal.
- `transport.py`: HTTPS/443 only, exact frozen hosts, no IP/loopback/
  userinfo, every redirect hop revalidated, ≤3 transitions.

## 10. Dry child artifacts

Under `docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-CHILD/`
(195,941 bytes total after remediation), each sealed with canonical digest +
file SHA-256 + Git-blob code identity + extended environment identity +
checkout representation, `authorization: NONE`, `executable: false`:

| Artifact | Bytes | SHA-256 (prefix) | Canonical (prefix) |
|---|---|---|---|
| arm_m_phase_p_dry.json | 16289 | 5cc312c53241 | 48f1c59dae0f |
| arm_m_phase_d_dry.json | 14444 | 6f7991dbd6be | dea195bfa79e |
| arm_t_phase_p_dry.json | 14398 | 771bf4da87f3 | bf96b340f0ee |
| arm_t_phase_d_dry.json | 16446 | 72d01ee57872 | 36c1f49bac58 |
| prospective_budget_m.json | 13231 | c5788c13ea4d | 4ea7d9010de5 |
| prospective_budget_t.json | 13289 | a039e8f4de88 | add7a1d98737 |
| adopted_planning_evidence.json | 13827 | 223e2183e7f3 | 2067b6738a76 |
| scientific_identity_adoption.json | 14577 | 9da79b29fc0d | 59807ebab0f4 |
| memory_guard.json | 12245 | fcc1776b5c07 | 09171eed8270 |
| disk_schedule.json | 12118 | 5b86fb21998d | a6802e3753e3 |
| runtime_schedule.json | 12060 | 5b79db6ea065 | 7216fa13aaa7 |
| epoch_genesis_template.json | 12438 | 0bddf221b5f8 | 819887861b0d |
| readiness_review.json | 14410 | ef78c957fa3f | e26bcbcbaac6 |
| artifact_manifest.json | 16169 | df753a0164ea | edf50c62bfe2 |

Manifest digest: `edf50c62bfe2d6247ceb4b3fd44041a6643a47da3e39f544f078dfa7c0111f63`.
Old committed child artifacts remain in Git history; this remediation
replaces the current derived set.

## 11. Readiness vector

Readiness is derived, not assigned: `readiness.derive_arm_readiness`
evaluates one evidence check per item (the memory/disk/runtime items run
live mechanism self-tests during the build; the builder fails closed when
any arm is not ready). Per-item evidence strings ship in
`readiness_review.json`.

M and T: all 13 items true, authorization NONE →
**READY_FOR_PHASE_P_AUTHORIZATION_REVIEW**.
D authorization is not requested (P has not run/sealed).

## 12. Tests (offline/synthetic only)

- `tests/test_evidence_v3_remediation.py`: 71 tests covering every Astra
  finding (strict authn schema, exclusive genesis, journaled ledger,
  transport policy, body accounting, registry memory, contained disk,
  conserved runtime, integrated executor, derived readiness, P accounting,
  env identity). All pass (1 environment-conditional symlink skip where
  the volume forbids symlinks).
- `tests/test_evidence_v3.py`: 41 passed. Covers lineage, epoch
  genesis, ledger basics, M P/D, T P/D, totals, P/D separation,
  fail-closed memory, physical disk, monotonic runtime, no-network guard.
- Evidence-v2 regressions: `test_evidence_v22` + `test_evidence_v21`
  passed; `test_evidence_v2_core/footer/text` passed;
  `test_prepare_bounds` passed (see final §20 for the exact run).
- Genesis mechanism tests plus the CLI synthetic genesis probe ran only
  against disposable temp roots; probe artifacts deleted.
- `ruff check` clean; `ruff format --check` clean; scoped `mypy`
  (`src/xlm/data/evidence_v3`, `scripts/evidence_v3.py`) clean.
- Full suite, live-source, and CUDA tests NOT RUN (explicit final gate only).

Environment: Windows 11 build 26200, Python 3.12.13, torch 2.14.0+cpu,
CUDA False, pyarrow 25.0.1, psutil 7.2.2, uv 0.12.19. Commands used
`uv run --offline --locked --no-sync --extra cpu --extra eval` with
`-n 0` for single-file runs and `-p no:cacheprovider` (repo cache
directory is not writable). No network, acquisition, corpus text, or push.

## 13. Requirement ledger (remediation status)

| Requirement | Status | Evidence |
|---|---|---|
| v3 protocol/freeze/epoch/lineage verification | VERIFIED | `verify-v3` exit 0; digests match |
| v2 parent graph + terminal BLOCKED preserved | VERIFIED | 39 history + 29 code bindings; terminal digest match |
| Scientific identity adoption (M/T/policy/namespace) | VERIFIED | Byte bindings; frozen-identity tests |
| Planning-observation adoption (no v3 spend import) | VERIFIED | Role flags; ledger refusal test |
| Strict Phase-P authorization schema | IMPLEMENTED | `authz.py`; 11 schema tests; `check-auth` CLI |
| Distinct operator approval binding | IMPLEMENTED | `validate_operator_approval`; no bypass flags |
| Exclusive exactly-once genesis | IMPLEMENTED | `O_CREAT|O_EXCL` claim; concurrency test; synthetic only |
| Journaled ledger (reserve-before-I/O, crash-conserving) | IMPLEMENTED | `ledger.py`; 7 durability tests |
| Integrated Phase-P executor (only path) | IMPLEMENTED | `executor.py`; 7 integration tests; never run live |
| Strict transport policy (exact hosts, 443, ≤3 hops) | IMPLEMENTED | `transport.py`; 8 policy tests |
| Full body accounting (all responses metered) | IMPLEMENTED | Meter-while-reading; 4 body tests |
| Exact remote identity (206/range/ETag/revision/PAR1) | IMPLEMENTED | `verify_remote_identity`; STOP semantics |
| Enforcing memory supervision + exit-proof registry | IMPLEMENTED | `OwnedProcessRegistry`; 6 tests |
| Contained disk inventory + real peak accounting | IMPLEMENTED | `_resolve_inside`; 8 disk tests |
| Conserved monotonic runtime + sealed pauses | IMPLEMENTED | Open-segment budgets; 5 runtime tests |
| Derived readiness (no hard-coded booleans) | IMPLEMENTED | `readiness.py`; builder fails closed |
| Git-blob code identity + extended environment | IMPLEMENTED | `envidentity.py`; blob/exe/uv/lib/OS/arch |
| M phase-P accounting (16/24/40; 32 D; 8 spare) | VERIFIED | 1,398,416 B; 32,156,016 left; 3,991,024 min |
| T phase-P accounting (24/32/48; 32 D; 80 combined) | VERIFIED | No fabricated headroom before L |
| M phase-D dry schedule (648 chunks, 688/720) | VERIFIED | Reproduced arithmetic |
| T phase-D dry schedule (47 ranges, 95/127) | VERIFIED | Corrected spans; no old schedule |
| Fresh-root enforcement (no remap/creation) | VERIFIED | G: volume present; root absent; no F: substitute |
| DRY child artifacts + manifest | IMPLEMENTED | 14 files; manifest `edf50c62…0111f63` |
| v2.x acquisition lineage | BLOCKED | Permanently closed (provenance only) |
| Real epoch genesis / network / acquisition / text | NOT RUN | Epoch NOT_STARTED, root absent |
| Training/tokenizer/admission/promotion/push | OUT OF SCOPE | Not undertaken |

## 14. Files changed (remediation)

- `src/xlm/data/evidence_v3/authz.py` (new: strict Phase-P schema + operator approval)
- `src/xlm/data/evidence_v3/transport.py` (new: exact-host policy)
- `src/xlm/data/evidence_v3/executor.py` (new: integrated Phase-P path)
- `src/xlm/data/evidence_v3/readiness.py` (new: derived readiness)
- `src/xlm/data/evidence_v3/envidentity.py` (new: blob/env identity)
- `src/xlm/data/evidence_v3/epoch.py` (exclusive claim + strict genesis record)
- `src/xlm/data/evidence_v3/ledger.py` (journal durability rewrite)
- `src/xlm/data/evidence_v3/guards.py` (registry/containment/conservation rewrite)
- `src/xlm/data/evidence_v3/schedules.py` (P-capacity accounting detail)
- `src/xlm/data/evidence_v3/frozen_v3.py` (cap-map digest + protocol commit constants)
- `src/xlm/data/evidence_v3/dry.py` (blob identity + extended environment)
- `scripts/evidence_v3.py` (derived readiness, `check-auth`, strict synthetic genesis)
- `tests/test_evidence_v3_remediation.py` (new, 71 adversarial tests)
- `tests/test_evidence_v3.py` (sealed-pause + module-list updates)
- `docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-CHILD/` (regenerated 14 artifacts)
- `docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-REMEDIATION.md` (new, this task)
- `docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-IMPLEMENTATION.md` (updated §§7,9–14)

Frozen protocol, review, freeze, epoch definition, lineage closure, and all
v2 artifacts are untouched. User/Astra working-tree changes preserved.

## 15. Remaining blockers / unknowns

1. Repeat Phase-P authorization review (no ticket or operator approval issued here).
2. G: volume is present (999.7 GB free) but `G:\Project` and the execution
   root are absent; future genesis must verify the intended volume,
   absent/pristine root, ancestor containment, and initial inventory.
3. T footer actual lengths L unknown until P runs (bounded by 2 MiB/file reserve).
4. M data offsets unresolved until authorized P seals them.
5. No live-source compatibility claim (synthetic fixtures only).
6. No measured peak RSS / disk / timing (formula reservations + guard design only).

## 16. Exact next operator action

Repeat the Phase-P authorization review against the frozen protocol, freeze,
regenerated dry child plans, this report, and the remediation record. Do NOT
create `epoch_start.json` or the G: execution root, do NOT run
`prepare-epoch` in real mode, do NOT issue any data/text acquisition command.
The only in-scope commands were the offline `verify-v3`, `build-v3-dry-plans`,
`check-auth` (schema only, no ticket), and synthetic-temp `prepare-epoch`
probes already executed and cleaned.
