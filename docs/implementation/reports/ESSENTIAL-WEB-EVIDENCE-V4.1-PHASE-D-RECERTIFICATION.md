# Essential-Web v4.1 Phase-D final narrow recertification

**PHASE-D AUTHORIZATION REVIEW PASSED**

2026-09-30. **M = PASS; T = PASS.** D01 and D02 pass independent replay and
completion-path checks. No relevant regression found. This is the narrow
authorization review, not a live acquisition or a full acceptance run.

Reviewed and finishing HEAD: `33339663a2e9e15316c119193c81f38ff2d47652`, branch
`data/mix01-ultrax-6b`. Protocol/freeze: `d58693822ce06baddf1d62a21f69ecf0bb81f2bd`;
original implementation: `b1e1d10bbfcde78e708ba21409c7a0a765f9f5be`.
The only production/test differences from the original implementation are
`phase_d.py` and `test_evidence_v41_phase_d.py`. The freeze directory and
protocol are unchanged from the freeze commit. Reviewed source bytes equal HEAD.

[Evidence directory](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RECERTIFICATION/),
[exact commands and exits](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RECERTIFICATION/commands.json),
[command notes](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RECERTIFICATION/COMMANDS.md),
[additional independent probes](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RECERTIFICATION/test_final_gate.py).
The Astra probe file was copied byte-for-byte to the new evidence directory,
so its observations do not overwrite previous review or repair evidence.

## Frozen identities

Independent stdlib JSON canonicalization/SHA-256 in the Astra probes and the
production verifier both pass. The probes also compare the exact ranges to
the dry plan and real-parent layouts, and membership to the frozen selection.
The real footer inspection is structural only; no real document column decoding.

| Identity | Recomputed value |
|---|---|
| Protocol SHA-256 | `bb2bca6f571c9e02b02536bc518cdc1b61fdf0f56b0c6970eaa257c8d198769f` |
| Freeze | `9c4612fe860ef49c26ff09b25f8fdaf00f0a7015d7eab4fd7cfe28a0e1675228` |
| Plan | `23a26ffc87d27e4c8fa5de86d7ec446c260bfd65442dc5b1dfd2651cd62222b7` |
| Dry plan | `ee82125d1b0d2b6d0a6c420144910db954659d5b757283d39123ddc3d0080356` |
| Selection | `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474` |
| Source revision | `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d` |
| M | 8 operations; 11,692,530 bytes; 4,096 rows, 512 per file |
| T | 47 operations; 179,963,169 bytes; 118 unique frozen locators |

## D01: whole-root cap

**PASS.** `_root_bytes` sums every regular file recursively; there is no
extension or export allowlist in the measurement. Payloads, temporary bodies,
sealed outputs, SQLite and its journal/WAL/SHM, receipts, manifests, JSON/JSONL,
atomic-write temporary files, `.superseded` and stray files count.

The original Astra reproduction now returns **STOPPED / INCOMPLETE**.
At the same simulated decode boundary, decoded output size is 28,741 bytes;
usage after exports and before store close is **1,073,739,376 bytes**,
**2,448 below** the 1,073,741,824-byte cap. After close it is 1,073,718,344.
The historical pre-repair 16,748-byte overrun was not rerun: its original
failure evidence remains in the repair directory. This review independently
reran the unchanged probe against the requested repaired HEAD.

| Boundary / adversary | Verified result |
|---|---|
| cap minus 10,000; export 16,748 bytes | Refused before temporary publication; no bytes written |
| root plus export equals cap / cap plus one | Allowed / refused |
| Replacement result fits but old plus temporary replacement exceeds cap | Refused with old file intact; exact peak boundary allowed |
| SQLite auxiliary and unknown files | Included; actual open SQLite journal observed at seal |
| Full acquisition/decode path at original failing boundary | STOPPED, both arms INCOMPLETE; payloads and attempt rows retained |
| Receipt + manifest + store reserve equals cap / cap plus one | COMPLETE / STOPPED with INCOMPLETE exports |
| One byte injected just before final mark, at the reserve boundary | STOPPED at `marking COMPLETE`; unchanged control completes |
| Additional independent post-store-write measurement at cap / cap plus one | COMPLETE / STOPPED at `after COMPLETE` |
| Previously COMPLETE root at cap / with one added byte | COMPLETE / STOPPED, exports demoted, no acquisition or root growth |

`_publish` checks old destination plus new temporary bytes before writing and
measures after replacement. Store writes have the unchanged repair's 65,536-byte
projection and a measurement afterward. The COMPLETE mark has both checks;
an overrun detected after that mark is converted to STOPPED before returning.
The post-store-write probe specifically exercises this latter path, independently
of the repair's pre-mark probe. Boundary occupancy is simulated, not a physical
1 GiB fixture. The store reserve is conservative and does not enlarge the cap.

## D02: parent binding

**PASS.** `_check_parents` is the single canonical verifier. `_verify_parents`
uses it before any state change; `_recheck_parents` uses it before decoding,
before COMPLETE receipt/manifest publication and immediately before the final
store/cap gate. It re-reads every bound artifact, with byte count and SHA-256,
the four JSON self-digests, COMPLETE receipt/manifest binding, plan identity,
payload bindings and M/T layout equality. Missing/unreadable artifacts refuse
at entry or STOP at later boundaries. No cached metadata substitutes for reads.

The original independent manifest-drift probe now returns STOPPED / INCOMPLETE
with both arms INCOMPLETE. The repair's 33 adversarial runs also pass: manifest,
M layout, T layout, removed trailer/receipt, and same-size changes to manifest,
both layouts, footer, SQLite and request receipt, at all three later boundaries.
Three unchanged-parent controls complete; the fourth verifier-call regression
confirms one canonical verifier at all four call sites.

Additional review-only probes remove and same-size-mutate **every artifact in
the authored synthetic parent's binding**, one disposable copy per case, after
export reconciliation and before the final parent check. Every case stops at
`at completion`; the original fixture parent stays byte-identical. This is
synthetic logic evidence, not mutation of the 30-artifact real parent.

## Final COMPLETE gate and narrow regressions

**PASS.** The seal requires both arms COMPLETE, reads the published M/T bundles
back and compares their identities in order to the frozen plan, checks terminal
T statuses, rechecks the parent, checks publication room, publishes the drafts,
reconciles artifact hashes and receipt/manifest/decoded-output bindings, rechecks
the parent and checks whole-root room before and after marking COMPLETE.
Required exports must be present in the manifest; the request receipt binds
the attempt table. The plan fixes the real counts at 4,096 M rows and 118 T
locators; actual decoding was exercised only on authored smaller fixtures.

The extra probes corrupt published M/T identities (missing, duplicated,
reordered), remove an export, alter output bytes, corrupt the receipt digest,
or remove a decoded-output store row. All yield STOPPED / INCOMPLETE. Successful
controls complete, including the exact final-cap control. These are actual
`run_d`/seal paths, not isolated helper-only assertions.

The focused regression selection passes exact 8 M / 47 T ranges, M extraction,
selected-locator filtering, unselected-text exclusion, reviewed v4.1 transport
B01/B02 accounting/deadline behavior, restart, PyArrow out-of-range refusal and
operational attempt/body caps. Reviewed engine, transport, store, plan parser,
decoders and CLI are unchanged by this repair. No broader architecture audit.

## Execution evidence

Windows 11 build 26200; CPython 3.12.13 (matching `.python-version`), PyArrow
25.0.1, SQLite 3.53.1, uv 0.12.19, pytest 9.1.1, ruff 0.16.8, mypy 2.3.1.
Existing CPU/eval environment, `uv run --offline --locked --no-sync`; no
dependency changes or installation. OMP/MKL/OPENBLAS/NUMEXPR threads = 1,
TOKENIZERS_PARALLELISM=false; pytest `-n 0 -q -p no:cacheprovider`.

| Selection / command | Exit | Result |
|---|---:|---|
| Seven v4/v4.1/Phase-D files prescribed by repair | 0 | 460 passed, 50.78 s |
| Phase-D `-k "test_d01 or test_d02"` | 0 | 48 passed, 75 deselected, 12.57 s |
| v3/v2-core/v2-text science selection | 0 | 69 passed, 1.42 s |
| Verbatim Astra probes | 0 | 22 passed, 3.79 s |
| Additional review-only final-gate probes | 0 | 14 passed, 10.82 s |
| ruff check / format check, original 23-file scope | 0 / 0 | Clean / 23 already formatted |
| Interpreted mypy `--version` | 0 | 2.3.1, compiled: no, zero compiled mypy modules loaded |
| Same interpreted mypy, `--strict --no-incremental`, 23 files | 0 | No issues; existing unused-config-section note only |
| CLI verify / phase-d-status | 0 / 0 | All bindings; NOT_STARTED, root absent |
| Committed real-parent checker | 0 | 30 artifacts, layouts equal plan, unchanged inventory, zero requests |
| Parent before/after comparison / final audit | 0 / 0 | Identical; source and freeze unchanged; original STATUS suffix preserved |

All tests passed on their first execution here; no retry, skip or xfail.
The 48 repair cases are part of the 460, not 48 additional unique tests.
The normal compiled mypy invocation was **NOT RUN** again: its documented OS
application-control block was accepted. Independent provenance verification
matched installed mypy 2.3.1 to `uv.lock` and verified all **195 mypy Python
source files** against the installed wheel RECORD hashes and sizes. The supplied
interpreted launcher uses those sources, without modifying the environment.

## Real Phase-P integrity

`G:\Project\xlm-evidence-v4.1\essential-web` was only read. All 30 bound artifacts
verify; layouts equal the plan. Before, after and final inventories are identical:
88 entries, 86 files, **3,241,704 bytes**, with exact sizes, mtimes and SHA-256s.
The committed checker's inventory digest is
`838bdcb49afa185fa2bb9ae0c1c38ef5c786951a7dfce86fec3ace7d315596cf`, matching the
previous review. The separate repair-style inventory uses null directory sizes
and consequently has a different digest,
`953677b9010710e4107a7da8745c569ab8d809a0240d025cf78dc6ffe0b2451d`;
its before/after/final values agree. Real Phase-D root remains absent.

## Requirement ledger and permitted uncertainty

| Requirement | Status |
|---|---|
| D01 whole-root cap through publication/store/seal | IMPLEMENTED, VERIFIED (synthetic boundaries and code review) |
| D02 parent binding through completion | IMPLEMENTED, VERIFIED (synthetic drift; real parent read-only) |
| Final COMPLETE gate | IMPLEMENTED, VERIFIED (synthetic) |
| Frozen identities and narrow existing contract | VERIFIED |
| Real parent integrity; no real Phase-D root | VERIFIED |
| Compiled mypy launcher | BLOCKED historically by OS; NOT RUN here; equivalent interpreted check VERIFIED |
| Full offline acceptance, CUDA, live Phase D and real M/T decoding | NOT RUN |
| Live peak memory/scratch, live performance | NOT RUN / NOT MEASURED |
| Protocol/plan/science changes, Phase-P redesign, general reservation framework | OUT OF SCOPE |

Permitted limitations remain: STOPPED writes are not pre-reserved; SQLite
growth within an operation is detected afterward; a crash between COMPLETE
draft publication and the state mark leaves RUNNING for resealing; boundary
occupancy is simulated. The tests establish the frozen fail-closed behavior,
not immunity to every concurrent machine failure. Real PyArrow reads may still
need unavailable bytes and must STOP; live completion, latency, bandwidth and
peak memory/scratch remain unknown. Only test wall times and static parent
storage were measured here. No invented live resource measurements.

No network, corpus acquisition, live Phase D, real Phase-D root creation,
Phase-P mutation, scientific reselection, implementation change or push.
Existing user changes are preserved. New review artifacts and a STATUS prefix
are uncommitted; **no review commit**, HEAD unchanged, index empty.

## Exact next operator action

The narrow authorization gate passes for both arms. The operator may now run
the frozen bounded Phase-D command from the repository; it was **not run** here:

```powershell
uv run --offline --locked --extra cpu --extra eval python scripts/evidence_v41_phase_d.py phase-d --confirm-plan-digest 23a26ffc87d27e4c8fa5de86d7ec446c260bfd65442dc5b1dfd2651cd62222b7
```

`--offline` restricts uv dependency access; this future program command uses
the protocol's allowlisted network and creates the frozen Phase-D root.
Review its receipts and result before any scientific use or human text review.

**PHASE-D AUTHORIZATION REVIEW PASSED**
