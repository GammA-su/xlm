# Essential-Web evidence v4.1 Phase D — D01/D02 repair

**READY FOR FINAL NARROW PHASE-D RECERTIFICATION**

2026-09-30, branch `data/mix01-ultrax-6b`, starting HEAD
`b626fb484504def7d4d98ddb0c4596521d6485ac` (implementation `b1e1d10`, protocol/freeze
`d586938`). This repairs only the two blockers of the
[Phase-D authorization review](ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-AUTHORIZATION-REVIEW.md)
(there labelled B02 and B01; here **D01** = whole-root cap, **D02** = parent
binding). Offline and synthetic only: no network, no live Phase D, no real
Phase-D root, no Phase-P mutation, no reselection, no push.

Changed: `src/xlm/data/evidence_v4/phase_d.py` and
`tests/test_evidence_v41_phase_d.py`. The frozen protocol, freeze, plan, parent
binding, scientific adoption, `phase_d_plan.py`, `phase_d_decode.py`, the
reviewed Phase-P engine, transport, state store and CLI are byte-unchanged.

Evidence: [`ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-D01-D02-REPAIR/`](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-D01-D02-REPAIR/).

## 1. Frozen identities (unchanged)

`scripts/evidence_v41_phase_d.py verify`, exit 0, before and after the repair
(`verify-before.log`, `cli-verify.log`, identical):

| Identity | Value |
|---|---|
| Protocol SHA-256 | `bb2bca6f571c9e02b02536bc518cdc1b61fdf0f56b0c6970eaa257c8d198769f` |
| Freeze digest | `9c4612fe860ef49c26ff09b25f8fdaf00f0a7015d7eab4fd7cfe28a0e1675228` |
| Plan digest | `23a26ffc87d27e4c8fa5de86d7ec446c260bfd65442dc5b1dfd2651cd62222b7` |
| Dry-plan digest | `ee82125d1b0d2b6d0a6c420144910db954659d5b757283d39123ddc3d0080356` |
| Selection (Astra probe, passing) | `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474` |
| Source revision (Astra probe, passing) | `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d` |
| M | 8 operations, 11,692,530 bytes, 4,096 rows (512 per file) |
| T | 47 operations, 179,963,169 bytes, 118 locators |

## 2. D01 — the whole-root cap through export and store writes

**Reproduction (unmodified HEAD).** Astra's probe, copied verbatim to
`test_astra_probes.py`: with simulated occupancy so the 28,741 decoded bytes
fit exactly, the root measured **1,073,758,572 bytes** after the exports —
**16,748 over** the 1,073,741,824-byte cap — and the run returned COMPLETE
(`astra-probes-before-fix.log`, `cap-boundary-observation-before-fix.json`).

**Root cause.** The cap was checked in two places only: before each request
(`_precheck`) and once before decoded outputs (`_decode_arms`). Everything
written afterwards had no check: the request receipt, the five arm outputs'
`decoded_outputs` rows, the receipt, the manifest, SQLite page and journal
growth, and the temp file of each atomic replace. COMPLETE was set in SQLite
before any of those writes, with no final measurement.

**Fix.** One measurement, `_root_bytes(root)` (unchanged: every regular file
under the root, recursively — SQLite and its `-journal`/`-wal`/`-shm`,
`tmp/`, `payload/`, `sealed/`, exports, `.tmp`, `.superseded`, anything else),
and one guard built on it:

- `_root_room(growth, what)` — STOP unless measured root + `growth` ≤ cap.
- `_publish(name, raw)` — every export goes through it. It refuses **before**
  creating the temp file unless root + `len(raw)` fits. Because the old
  destination is still inside the measured root, this is exactly the
  old + temp peak. It measures again after the replace. Identical bytes are
  not rewritten.
- Store writes after acquisition (`record_decoded`, the COMPLETE mark) need
  `STORE_WRITE_RESERVE_BYTES` = 65,536 bytes of room first and are measured
  after. SQLite growth of a single-row transaction is not knowable before it
  commits; the reserve is a projection, not a cap change.
- `_acquire` measures the whole root after every completed operation.
- `_seal` (section 4) projects receipt + manifest + the store reserve before
  publishing them, and measures after the COMPLETE mark.
- A COMPLETE root is measured again on every later invocation.
- STOP path: exports are published through the same guard. If they do not
  fit, nothing is written: existing exports are renamed to `.superseded`
  (no new bytes) and the STOPPED reason and every attempt row stay in
  `state.sqlite`. Nothing is deleted, truncated or raised.

The protocol's pre-request rule (root + 4,194,305 ≤ cap) and the
decoded-output rule are unchanged.

**Boundary results** (all synthetic; occupancy is simulated by adding a
constant to the real measurement):

| Case | Result |
|---|---|
| root = cap − 10,000, export 16,748 bytes | STOP before the temp file; root unchanged; never COMPLETE |
| root + export = cap exactly | published; root = 1,073,741,824 |
| root + export = cap + 1 | STOP; nothing written |
| replace 12,000-byte file with 12,000 bytes, root = cap − 11,999 (result would fit, old + temp would not) | STOP; old file intact, no temp; at cap − 12,000 it is allowed |
| SQLite auxiliaries | `-journal`, `-wal`, `-shm`, `.tmp`, `.superseded` and a stray file all counted; the live journal is observed in the root at the seal and included |
| seal projection = cap exactly / cap + 1 | COMPLETE / STOPPED with an INCOMPLETE receipt and manifest |
| 1-byte extra file added after the exports, before the COMPLETE mark | STOPPED (`marking COMPLETE`); control without the file is COMPLETE |
| COMPLETE root re-run at exactly the cap / with 1 extra byte | COMPLETE / STOPPED, root size unchanged, exports demoted |
| Astra's probe after the fix | STOPPED / INCOMPLETE; 1,073,739,376 bytes before the store closes (2,448 under), 1,073,718,344 after |

## 3. D02 — the Phase-P parent bound through completion

**Reproduction (unmodified HEAD).** Astra's probe appends one space to the
copied parent `artifact_manifest.json` after acquisition; the run returned
COMPLETE with both arms COMPLETE (`parent-drift-observation-before-fix.json`).

**Root cause.** `_verify_parents` ran once, before the store opened. After
that only the footer and trailer files were re-hashed as decoding read them.
The receipt, manifest, layouts, `state.sqlite` and request receipt were never
read again, and nothing was checked before COMPLETE.

**Fix.** The existing verification is now the single `_check_parents`
(unchanged logic: all bound artifacts by bytes and SHA-256, the four JSON
self-digests, plan digest, COMPLETE receipt bound by the manifest, payloads
equal to the manifest, M/T layouts equal to the plan). It runs at four points:

| Point | On a difference |
|---|---|
| before any state change (`_verify_parents`) | RefusedError, as before |
| before decoding | STOP |
| in the seal, before the COMPLETE receipt/manifest are published | STOP |
| in the seal, immediately before the COMPLETE mark | STOP |

There is no second verifier and no cached parent metadata. Decoding still
hashes each footer/trailer as it loads it. A missing or unreadable file
(`OSError`) is now a difference too. The parent is only ever read.

**Drift regressions** (disposable copies of the synthetic parent, changed
after the whole acquisition; each at all three later boundaries — 33 cases):

| Change | Result |
|---|---|
| manifest, +1 byte | STOPPED / INCOMPLETE |
| M layout, +1 byte | STOPPED / INCOMPLETE |
| T layout, +1 byte | STOPPED / INCOMPLETE |
| removed T trailer; removed receipt | STOPPED / INCOMPLETE |
| same-size change: manifest, M layout, T layout, M footer, `state.sqlite`, request receipt | STOPPED / INCOMPLETE (hash mismatch) |
| unchanged parent (3 controls) | COMPLETE; parent copy byte-identical |

Each stopped case also asserts: both arms INCOMPLETE, run row STOPPED, receipt
and manifest INCOMPLETE with no outputs, no live arm output file, all payloads
and attempt rows retained, the fixture parent untouched, and a rerun refused.

## 4. The COMPLETE gate

`run` no longer sets COMPLETE. A run with no stop and both arms decoded stays
RUNNING and enters `_seal`; any failure there is STOPPED / INCOMPLETE with both
arms INCOMPLETE. In order:

1. publish the request receipt and arm outputs, each under the cap;
2. both arms COMPLETE, and the published bundles read back from disk hold
   exactly the frozen M window rows and T locators, in order, none missing or
   duplicated (4,096 and 118 for the real plan), every T status terminal;
3. parent verification;
4. root + receipt + manifest + store reserve ≤ cap;
5. publish the receipt and manifest;
6. every manifest artifact exists and reproduces its bytes and SHA-256; the
   manifest binds the receipt; the request receipt equals the attempt table;
   `decoded_outputs` equals the receipt outputs and is the full required set;
   all required exports are in the manifest; root ≤ cap;
7. parent verification again;
8. root + store reserve ≤ cap, then the COMPLETE mark;
9. root ≤ cap, or the run is STOPPED.

## 5. Commands and results

Windows 11 Pro 26200; CPython 3.12.13; PyArrow 25.0.1; SQLite 3.53.1;
uv 0.12.19; `U` = `uv run --offline --locked --no-sync --extra cpu --extra eval`;
`OMP/MKL/OPENBLAS/NUMEXPR=1`, `TOKENIZERS_PARALLELISM=false`, `-n 0 -q -p no:cacheprovider`.
Everything is synthetic fixtures except the read-only real-parent checks.

| Command | Exit | Result |
|---|---:|---|
| `U python -m pytest … test_astra_probes.py` on unmodified HEAD | 1 | 20 passed, 2 failed (D01, D02 reproduced) |
| same, after the fix | 0 | 22 passed |
| `U python -m pytest … tests/test_evidence_v4_{plan,transport,engine,e2e,wire}.py tests/test_evidence_v41.py tests/test_evidence_v41_phase_d.py` | 0 | **460 passed** in 50.13 s (412 existing + 48 new) |
| `U python -m pytest … tests/test_evidence_v41_phase_d.py -k "test_d01 or test_d02"` | 0 | 48 passed (11 D01, 37 D02) |
| `U python -m pytest … tests/test_evidence_v3.py tests/test_evidence_v2_core.py tests/test_evidence_v2_text.py` | 0 | 69 passed |
| `U ruff check` / `U ruff format --check` on the 23 v4 files | 0 / 0 | clean / 23 already formatted |
| `U mypy --strict` on the 23 files | 1 | **did not start**: Windows application control blocked mypy's compiled module (`mypy-compiled-blocked.log`) |
| `U python mypy_interpreted.py --strict --no-incremental` on the 23 files | 0 | mypy 2.3.1 (compiled: no): no issues in 23 source files |
| `U python scripts/evidence_v41_phase_d.py verify` / `phase-d-status` | 0 / 0 | section 1; `NOT_STARTED`, root does not exist |
| `U python …/PHASE-D-IMPLEMENTATION/check_real_parent.py` | 0 | 30 artifacts verified, layouts equal plan, parent unchanged, no Phase-D root, 0 requests |
| `U python parent_inventory.py before` … `after` … `compare before after` | 0 | identical |

One new test failed on its first run (a test-authoring error: it compared the
stop message with a later peak instead of the usage at the gate) and was
corrected to the exact gate values. No skip, xfail, retry-to-green or weakened
assertion. The 23-file lint scope is the implementation report's; the verbatim
copy of Astra's probe file is outside it and was not reformatted.

**Real Phase-P parent** `G:\Project\xlm-evidence-v4.1\essential-web`, read-only:
88 entries, 86 files, 3,241,704 bytes before and after; size, mtime and
SHA-256 of every file identical (`parent-before.json`, `parent-after.json`);
the checker's own inventory hash is
`838bdcb49afa185fa2bb9ae0c1c38ef5c786951a7dfce86fec3ace7d315596cf`, equal to the
review's. `G:\Project\xlm-evidence-v4.1\essential-web-phase-d` does not exist.

## 6. Requirement ledger

| Requirement | Status |
|---|---|
| Root cap covers exports, store writes, temp files, SQLite auxiliaries | IMPLEMENTED, VERIFIED (synthetic) |
| COMPLETE impossible with the root over the cap | IMPLEMENTED, VERIFIED (synthetic) |
| Parent verified before decoding and at completion, one verifier | IMPLEMENTED, VERIFIED (synthetic) |
| Single fail-closed COMPLETE gate | IMPLEMENTED, VERIFIED (synthetic) |
| Frozen protocol/freeze/plan/selection/ranges/caps unchanged | VERIFIED |
| Previously passed contract (412 tests, 20 Astra probes, 69 science) | VERIFIED by regression |
| Real parent binds and is unchanged | VERIFIED (read-only) |
| `mypy --strict`, compiled invocation | BLOCKED by OS application control; interpreted run of the same mypy passes |
| Full offline acceptance suite, CUDA | NOT RUN (focused repair) |
| Live Phase D, real decoding, peak memory / scratch of a real run | NOT RUN |
| Protocol, plan, scientific membership, Phase-P engine changes | OUT OF SCOPE |

## 7. Limitations

- The STOPPED mark itself is not pre-reserved: a stop must always be
  recorded. It is a one-row in-place update.
- Store writes during acquisition belong to the reviewed Phase-P engine,
  which was not changed. They are bounded by the unchanged pre-request rule
  and by the new measurement after every operation, so SQLite growth within
  one operation is detected as STOP rather than prevented. The frozen arm caps
  (603,979,776 body bytes in total) keep a real run far from the root cap.
- The COMPLETE receipt and manifest are published just before the COMPLETE
  mark. A process death between them leaves a RUNNING root holding COMPLETE
  drafts; the next run re-decodes and re-seals, or replaces/demotes them on a
  stop. `state.sqlite` is the authority for run status.
- Exact-boundary results are simulated occupancy, not a 1 GiB root.
- Measured: wall time of the focused selection only. Peak memory was not measured.

## 8. Next

No remaining D01/D02 blocker is known. Next operator action: request the
final narrow Phase-D recertification of this commit (the two failing probes
plus the contract regressions). Do not run the live `phase-d` command before
that review passes.

**READY FOR FINAL NARROW PHASE-D RECERTIFICATION**
