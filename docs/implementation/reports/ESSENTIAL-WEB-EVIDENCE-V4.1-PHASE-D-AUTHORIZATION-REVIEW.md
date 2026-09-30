# Essential-Web Evidence-v4.1 Phase-D authorization review

**PHASE-D AUTHORIZATION REVIEW BLOCKED**

2026-09-29. M authorization: **BLOCKED**. T authorization: **BLOCKED**.
Two shared Phase-D contract failures remain despite 412 prescribed focused tests
and 69 scientific regressions passing. Independent review probes: **20 passed,
2 failed**, exit 1. No implementation changes were made.

The B01/B02 labels below identify this review's findings. They are distinct
from the previously repaired transport B01 partial-accounting and B02 deadline
issues, whose regressions pass here.

Review scope is the frozen acquisition only. Phase-P architecture and the passed
Phase-P result review remain accepted. No network, live Phase D, scientific
reselection, human text review, real Phase-D root creation or push occurred.
Real parent files were read/hash-checked; retained footers were inspected for
structural offsets only, without statistics, key/value metadata or column reads.
All document decoding used authored synthetic fixtures in temporary roots.

[Exact commands and run history](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/COMMANDS.md),
[review result](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/review-result.json),
[independent probes](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/test_review_probes.py).

The reviewed branch is `data/mix01-ultrax-6b`. Starting and finishing HEAD is
`b1e1d10bbfcde78e708ba21409c7a0a765f9f5be` (implementation), with protocol/freeze
commit `d58693822ce06baddf1d62a21f69ecf0bb81f2bd` and accepted Phase-P result-review
commit `ed8efcf3c85c265828a88579a264f08a2048640f`. Review artifacts are uncommitted;
the request's review-commit instruction was conditional on PASS. The existing
dirty STATUS and unrelated untracked reviews were preserved; a notice was
prepended to STATUS without changing its previous bytes. Nothing is staged.

**B01 — parent changes during acquisition can still produce COMPLETE.**
Frozen protocol section 1 requires: “A change during a run is STOP.”
[`run`](../../../src/xlm/data/evidence_v4/phase_d.py#L161) verifies all parents
once before opening the store. After acquisition, only the individual footer/
trailer files read by decoding are rechecked through `_parent_bytes` (line 240).
The manifest, receipt, layouts, state and request export are not rechecked at
that boundary or before COMPLETE publication.

The independent probe copies the synthetic parent, appends a single space to
its `artifact_manifest.json` after `_acquire` returns and before decoding, then
lets the unmodified production decode/export path continue. Result: overall
COMPLETE, M COMPLETE, T COMPLETE, even though the parent manifest no longer
matches its bound bytes/hash. The same wrong manifest present at startup is
correctly refused before root creation. A subsequent invocation would detect
the change; that does not make the first COMPLETE claim valid.
Evidence: `test_parent_manifest_drift_during_run_stops` and
[`parent-drift-observation.json`](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/parent-drift-observation.json).
Only the copied synthetic parent was changed. Repair must enforce the existing
Phase-D parent-drift STOP rule through completion, with receipts preserved.

**B02 — root-cap enforcement omits later export/store growth.**
Frozen protocol section 6 includes every file: payloads, temp bodies, SQLite,
exports and outputs, with a maximum of 1,073,741,824 bytes.
[`_decode_arms`](../../../src/xlm/data/evidence_v4/phase_d.py#L322) checks current
root bytes plus decoded arm files. [`_write_exports`](../../../src/xlm/data/evidence_v4/phase_d.py#L541)
subsequently writes request JSONL, decoded-output database records, receipt and
manifest without another root-size guard. The size check does not cover all
of that growth.

The independent boundary probe adds a constant simulated existing occupancy to
the real temporary-root measurement so the 28,741 decoded-output bytes fit
exactly at the check. After exports, before the store closes, accounted size is
**1,073,758,572 bytes**, **16,748 over the cap**, while the run returns COMPLETE.
Closing SQLite removes its journal: final accounted size is 1,073,737,540 bytes,
4,284 below the cap. The transient file bytes still fall under the frozen rule.
This is an accounting-boundary simulation, not an observed 1 GiB production run
or a claim that normal successful traffic reaches the cap. It does not introduce
an RSS, active-time or v3 reservation requirement. The existing whole-root limit
must cover Phase-D exports and store growth too.
Evidence: `test_root_cap_includes_exports_at_decode_boundary` and
[`cap-boundary-observation.json`](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/cap-boundary-observation.json).

All applicable JSON self-digests below were independently recomputed with stdlib
JSON canonicalization and SHA-256, separate from the production verifier. File
SHA-256 values and lengths for the five freeze artifacts and dry plan are in
[`independent-bindings.json`](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW/independent-bindings.json).

| Binding | Verified value |
|---|---|
| Protocol byte SHA-256 | `bb2bca6f571c9e02b02536bc518cdc1b61fdf0f56b0c6970eaa257c8d198769f` |
| Freeze canonical digest | `9c4612fe860ef49c26ff09b25f8fdaf00f0a7015d7eab4fd7cfe28a0e1675228` |
| Phase-D plan digest | `23a26ffc87d27e4c8fa5de86d7ec446c260bfd65442dc5b1dfd2651cd62222b7` |
| Parent dry-plan digest | `ee82125d1b0d2b6d0a6c420144910db954659d5b757283d39123ddc3d0080356` |
| Phase-P parent-binding digest | `de27e2a56f83970a91f9f3224b3bad7cefc977018612522679838ebd15b61559` |
| Scientific adoption digest | `4a2de146e53b4a21f1596f1f3518d0a12f5fec2b5760bfab20fd3eabcdea2cba` |
| Phase-P receipt digest | `ebd5704be8a871034ef8048c0641e9d735d017f458003b4efc50ab124c8bd965` |
| Phase-P manifest digest | `1bb6bad6e3690758c005e260c272eac3c0adcb6d040fde960a502d0ced90d7fe` |
| Phase-P M layout digest | `2c9f2f79448e1bb1d59b57f1ddec276dcd05bc79dddaea4014c9942a340d340a` |
| Phase-P T layout digest | `364bda46c766e8f9fdebd5732408743420cbe5c6f0b00fbd6a10d09a8bd53274` |

The real parent at `G:\Project\xlm-evidence-v4.1\essential-web` remains COMPLETE,
40/40 operations. All 30 plan-bound artifacts match, including `state.sqlite`
and request export as bytes and all 24 retained footer/trailer payloads.
SQLite was not opened by this review; its exact bytes bind the previously
reviewed database state. The before/after inventory includes the root itself:
89 entries (86 files), 3,241,704 file bytes, identical sizes, mtimes and SHA-256s.
Inventory SHA-256: `afc34680972b9fe265b94dca7be6841f15a2380735c6e25dcfdf4321fc053b24`.
The supplied checker excludes the root directory, so its 88-entry inventory
hash is `838bdcb49afa185fa2bb9ae0c1c38ef5c786951a7dfce86fec3ace7d315596cf`.
The real Phase-D root remains absent. B01 is a synthetic counterexample to the
Phase-D implementation, not a finding that this real parent changed.

| Requirement / review question | Status | Result and evidence |
|---|---|---|
| Exact commits/protocol/freeze/plan | VERIFIED | Expected HEAD; independent digests and production `verify`, exit 0 |
| COMPLETE parent at invocation; read-only behavior | IMPLEMENTED, VERIFIED | 30 artifacts; real whole-review inventory unchanged; wrong synthetic manifest refused before root creation |
| Parent drift during execution must STOP | BLOCKED | B01; altered synthetic manifest accepted through COMPLETE |
| Scientific identity | VERIFIED | Namespace `essential-web-evidence-v2.0`; selection `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474`; revision `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`; policy `f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07`; all 118 locators equal the original frozen selection manifest |
| Exact M acquisition | VERIFIED | 8 operations / 11,692,530 bytes; 81 adjacent projected chunks/file; both live layouts and reviewed dry ranges match; metadata-only footer offsets independently reproduced |
| Exact T acquisition | VERIFIED | 47 operations / 179,963,169 bytes; dictionary-inclusive spans; splits 6/6/5/6/6/6/6/6; no gaps or extra ranges; all frozen locators map exactly |
| No arbitrary live source/range API | IMPLEMENTED, VERIFIED | Fixed `run_live(*, confirm_plan_digest)` and CLI; M/T injection/widening and wrong digest refused; synthetic API rejects frozen roots/live transports |
| M decoding/extraction | IMPLEMENTED, VERIFIED synthetic | Only two projected columns; frozen row identities; missing/duplicate/reordered/misaligned rows refused; 512/file and 4,096 real rows verified structurally, not decoded live |
| T decoding/filtering | IMPLEMENTED, VERIFIED synthetic | Exactly selected locators, missing/duplicate/reordered refused; sentinel absent from exports/database/logs; unselected compressed bytes remain only as permitted acquisition evidence |
| T selected-document limits | IMPLEMENTED, VERIFIED synthetic | <=65,536 UTF-8 bytes/full document and <=8,388,608 retained text; oversize and missing/invalid statuses; no normalization or truncation; COMPLETE does not imply 118 reviewable texts |
| Blinding/provenance | IMPLEMENTED, VERIFIED synthetic | No human scoring, category decision, reviewer ID/order or secret; locator-bearing T documents/provenance under `sealed/`; no scientific categories copied |
| Transport/accounting | VERIFIED | Same reviewed engine/transport and 19-host v4.1 policy; HTTPS/443, 3 redirects, 2 extra retries, 120-second physical deadline, exact 206/range/length/strong ETag; B01 partial-body/B02 deadline regressions pass |
| Durable restart | IMPLEMENTED, VERIFIED synthetic | COMPLETE payload hashes/sizes checked and skipped; interrupted temp sizes reconciled/new attempt IDs; decode crash restarts with zero requests; no silent duplicate acquisition |
| Attempt/body/response/free-space caps | IMPLEMENTED, VERIFIED synthetic | M 64/67,108,864; T 320/536,870,912; 4,194,304 per response (+one-byte overflow detection then STOP); >=2 GiB free; receipts retained, no widening |
| Whole-root cap | IMPLEMENTED, BLOCKED | Acquisition/decode checks present but B02 exposes unchecked Phase-D export/store growth |
| Output completeness | IMPLEMENTED, VERIFIED for row/locator checks; BLOCKED overall | Both arms required; required exports/hash bindings tested; B01/B02 permit COMPLETE despite other frozen STOP conditions |
| PyArrow out-of-range refusal | IMPLEMENTED, VERIFIED synthetic | Sparse reader has no transport; forced padding reads in each decoder cause STOPPED/INCOMPLETE and no extra request/fallback/widening |
| Two historical-root test edits | VERIFIED | Only assertions in `tests/test_evidence_v41.py`; real COMPLETE Phase-P root may exist; exact existence reporting and frozen-root exclusion preserved; no production change in those edits |
| Live M/T decoding and performance | NOT RUN | Permitted uncertainty remains: actual PyArrow reads may need unavailable bytes and must stop |
| Full offline acceptance; CUDA; lint/typecheck reruns | NOT RUN | Focused review only; production and dependencies unchanged |
| Phase-P redesign, v3 RSS/active-time/reservations, scientific reselection | OUT OF SCOPE | Not introduced or reconsidered |

The implementation commit's production edits to reviewed Phase-P code are the
protocol-permitted default-preserving store-class and operation-ID-pattern hooks;
`__init__.py` changes are documentation. Transport/host-policy files are unchanged.
The separate historical-root assertion updates do not weaken production safety.
The supplied 412-test selection covers these hooks and existing wire behavior.

Environment: Windows 11 build 26200 AMD64; CPython 3.12.13; PyArrow 25.0.1;
uv 0.12.19; existing `uv.lock`, CPU/eval extras, offline locked no-sync execution.
Threads OMP/MKL/OPENBLAS/NUMEXPR = 1, TOKENIZERS_PARALLELISM=false, pytest `-n 0`,
cache provider disabled. No dependency installation or pin change. Review probes
block socket connection and resolution; supplied tests use their existing
offline guards/scripted transports.

| Execution | Exit | Exact result |
|---|---:|---|
| Production repository verifier | 0 | All requested bindings/shape; Phase-D root absent |
| Supplied real-parent checker | 0 | 30 verified; parent unchanged; no Phase-D root |
| Prescribed seven-file focused suite | 0 | 412 passed in 43.72 s |
| Recorded scientific regression selection | 0 | 69 passed in 1.40 s |
| Final independent probes, measured in process | 1 | 20 passed, 2 failed in 3.83 s; runner 4.012 s; peak working set 139,292,672 bytes |
| Whole-review before/after inventory | 0 / 0 | Exact equality; unrelated user files preserved |

Initial probe run: 19 passed, 2 failed (exit 1). One failure was a reviewer
assumption requiring canonical file bytes for every JSON, although the reviewed
dry plan legitimately has a trailing newline. The corrected probe requires
canonical bytes for the executable Phase-D plan and independently hashes all
JSON bodies and original file bytes. The other failure was B01, retained.
The initial cap probe checked only after SQLite close and missed journal bytes;
measurement was extended to the pre-close export boundary. Focused rerun: the
binding probe passed and B02 failed. Final run adds metadata-only footer checks
and captures both failure observations: 20 passed, 2 failed. Logs retain every
run. No xfail, skip, weakened production assertion or retry-until-green.

Only review-probe process resources and the static parent size were measured.
Live latency, bandwidth, decoder memory and peak scratch usage were NOT RUN or
not measured. Frozen limitations remain unchanged: no process memory enforcement,
unbounded DNS resolution time in the reviewed transport, and no persisted raw
response headers. No new Phase-P guarantee is required here.

Exact next operator prompt:

> Repair only B01 and B02 in ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW.md:
> enforce the existing bound-parent drift STOP rule through Phase-D completion,
> and the frozen 1 GiB whole-root cap through Phase-D export/store writes, retaining
> STOP receipts. Preserve protocol, plan, selection and completed Phase-P bytes.
> Use offline synthetic regressions, including the two failing review probes,
> then request another narrow Phase-D authorization review. No network, live
> Phase D, real Phase-D root creation, Phase-P mutation, reselection or push.

Do not run the live acquisition command while this verdict is blocked.

**PHASE-D AUTHORIZATION REVIEW BLOCKED**
