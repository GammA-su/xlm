# Essential-Web v4.0 final B01/B02-only recertification

**V4 NARROW PHASE-P REVIEW PASSED** — 2026-09-29. **M: PASS. T: PASS.**

Starting HEAD / repair reviewed: `a9f89c0d13ef01e9ab387104c4acd4da948d2366`,
branch `data/mix01-ultrax-6b`. This supersedes the B01/B02 blocking decision in
[the original narrow review](ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW.md).
It is a final offline pre-live review, not a Phase-P execution receipt.
No implementation, frozen-plan or scientific changes were made.

Evidence: [commands and reproduction instructions](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION/COMMANDS.md),
[machine command records](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION/commands.json),
[independent assertions and results](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION/independent-corrected.txt),
[supplied probe rerun](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-FINAL-RECERTIFICATION/repair-probe.txt).

## Frozen identity

Independently recomputed with stdlib SHA-256 / JSON, without the production
canonicalizer. Protocol checkout bytes and Git blob both reproduce its hash.
Freeze and plan canonicalization excludes only their top-level `digest`.
The frozen artifact directory and protocol have no diff from `2ede38f`.

| Binding | Verified value |
|---|---|
| Protocol SHA-256 | `4c7f11b15adb7d0601977fef61ab04d62fae0e9e64091fa22dbd99e1caf65727` |
| Freeze canonical digest | `747b82a2df7702db2b111ea62c2f27f3de3d22a37428472fdc0550af90767f57` |
| Plan canonical digest | `16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3` |
| Selection binding in unchanged freeze/plan | `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474` |
| Source revision | `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d` |

The first three were recomputed; selection/revision bindings were compared.
No corpus or locator-manifest reread was needed; the earlier scientific review
is retained, not reopened. The production `verify` command separately passed.

## B01 — independently reproduced repair

An authored chunked 206 emits one complete `PAR1` chunk and then EOF without
the terminal chunk. The real stdlib `HTTPResponse.read` positive control raises
`IncompleteRead(partial=b'PAR1')`. Production `exchange` / `_LiveResponse`, using
the real HTTP parser, returns those bytes before the later framing failure.
The public `run_offline` engine shared with the live path records:

| Attempt ID / try | Physically delivered body | Retained temp | SQLite bytes | Outcome |
|---|---:|---:|---:|---|
| 1 / 1 | 4 | 4 (`PAR1`) | 4 | TRANSPORT_ERROR |
| 2 / 2 | 4 | 4 (`PAR1`) | 4 | TRANSPORT_ERROR |
| 3 / 3 | 4 | 4 (`PAR1`) | 4 | TRANSPORT_ERROR |
| Total | **12** | **12** | **12** | retries exhausted |

Each retained body hash matches SHA-256 of `PAR1`. Operation `M-00-head` remains
`PENDING`; receipt/run are `INCOMPLETE` / `STOPPED`, and no successful payload
is created. Matching the requested four-byte length does not promote failure.

The supplied repair probe was rerun, not trusted from pasted output. Its
physical-byte field is fixture-assigned; the additional independently authored
review probe instead counts byte intersections actually copied by `recv_into`,
then compares them against files and direct SQLite queries. Both agree.
Only the existing synthetic Parquet/plan fixture builder is reused; socket,
clock, delivered-byte counter and assertions are review-authored.

## B02 and combined partial-byte / timeout case

Real parser and production deadline reader: head at t=0, body chunk after a
60-second virtual wait, terminal chunk requiring another 70 seconds.
`PAR1` returns at **t=60**; the next read raises **DeadlineError at t=120**.
Socket timeout sequence is exactly **[120, 120, 120, 60]** (send, head, body,
terminal framing). No completion at t=130 occurs.

Three repeated engine attempts each close after **120 virtual seconds**, each
retain `PAR1`, and each record `TIMEOUT` with four SQLite bytes. Actual delivered,
retained-temp and SQLite totals are **12 / 12 / 12**. Attempt IDs and try numbers
are 1, 2, 3; operation stays `PENDING`, result `INCOMPLETE` / `STOPPED`.

| Potential total | Independent result |
|---|---|
| 119 s (60 + 59) | SUCCESS at 119; four-byte payload promoted |
| 120 s (60 + 60) | TIMEOUT at 120; four bytes retained/accounted |
| 121 s (60 + 61) | TIMEOUT at 120; four bytes retained/accounted |
| 130 s (60 + 70) | TIMEOUT at 120; four bytes retained/accounted |

The boundary is strict: completion must precede the absolute deadline.
In a separate timeout-then-success case, attempt 1 remains TIMEOUT/4 bytes;
try 2 receives new attempt IDs 2 (redirect, 39 bytes) and 3 (SUCCESS, 4 bytes).
All 47 bytes remain in that operation's ledger; no refund occurs. COMPLETE is
possible only after the later successful retry and all other synthetic operations.

The focused wire regressions also passed for multiple buffered receive steps,
connect-address attempts, TLS handshake, late EOF, and timeout before body/head.
These are virtual-clock/scripted-socket checks, not measured live TLS timing.

## Minimal regression ledger

All rows below are **VERIFIED offline** in the five-file selection unless the
independent artifact is specified. No broader architecture audit was performed.

| Requested guarantee | Evidence |
|---|---|
| 1. Frozen identities | Independent hashes above; production verifier |
| 2. Wrong digest refuses | `test_wrong_plan_digest_refuses_without_touching_the_root` |
| 3. Arbitrary range/file refuses | `test_real_plan_refuses_any_edit`; `test_injected_or_altered_operations_are_refused` |
| 4–5. M data / T text injection refuses | Independent `probe_injection.py` rerun: both STOP, zero requests; isolation tests |
| 6–7. Wrong ETag / Content-Range refuses | `test_identity_mismatch_stops`; transport identity adversaries |
| 8–9. Forbidden / fourth redirect refuses | `test_bad_redirect_stops`; `test_fourth_redirect_stops` |
| 10. Retry maximum | `test_retry_maximum_is_enforced`; independent three-failure cases |
| 11. >4 MiB response | `test_single_response_over_4_mib_stops` |
| 12. >200 attempts | `test_attempt_cap_refuses_the_201st_attempt`; 200th allowed boundary |
| 13. >64 MiB arm bytes | `test_arm_body_cap_is_never_exceeded` |
| 14. Crash/restart | Real-process crash E2E; temp reconciliation; commit-before-rename repair |
| 15. Corrupt COMPLETE output | `test_corrupted_output_of_a_complete_root_stops`; E2E corruption |
| 16. All frozen operations required | Plan verifies 40 real operations; synthetic E2E and `test_complete_outputs_are_hash_bound` require exact plan completion |

| Requirement | Status |
|---|---|
| B01 repair mechanism | IMPLEMENTED in reviewed commit; VERIFIED offline |
| B02 repair mechanism and byte preservation | IMPLEMENTED in reviewed commit; VERIFIED offline |
| Narrow review blockers | None reproduced; no BLOCKED requirement remains in this scope |
| Live Phase P / live HF identity and redirect compatibility | NOT RUN |
| Full repository acceptance, additional science suite, CUDA, performance campaign | NOT RUN |
| v3 RSS/active-time certification, approvals/capabilities, exactly-once genesis, Phase-D reservations, bounded DNS | OUT OF SCOPE |

## Execution, limitations and preservation

Windows 11 build 26200 AMD64, CPython **3.12.13**, uv **0.12.19**, existing locked
environment with CPU/eval extras. Pins and CPU/CUDA installation policy unchanged.
All executed fixtures are authored synthetic data; no network, live acquisition,
corpus text, real-root creation, Phase D, implementation edits or push.

- Production verifier: exit **0**.
- Focused five-file suite: **180 passed**, zero failures/skips, exit **0**,
  pytest **17.18 s** (command wall time 17.688 s); `-n 0 -p no:cacheprovider`.
- Supplied B01/B02 probe: exit **0**, 0.810 s.
- Independent review probe first run: exit **1** during temp cleanup. Its
  review-only SQLite query used a transaction context without explicitly closing
  the connection, causing Windows WinError 32. This is a harness error, not a
  production failure. The traceback is retained in `independent.txt`.
- After changing only that review connection to `contextlib.closing`, the
  independent probe: exit **0**, 1.518 s command time, all assertions pass.
  No product test rerun or assertion weakening. Fixture files measured
  **561,586 bytes** before successful cleanup; this is retained fixture size,
  not peak disk use. Peak memory / test-run peak scratch were not measured.
- Exact M-data/T-text injection probe: exit **0**, 0.936 s; both STOP before requests.

Live redirect/ETag compatibility remains explicitly permitted uncertainty:
unexpected behavior must STOP with no broader hosts, weaker identity or
reselection. Real OS/TLS scheduling and performance remain unmeasured; the
offline evidence verifies deadline propagation and the specified boundaries.
DNS bounding remains outside v4. No new authorization framework is introduced.

Pre-existing dirty/untracked files were hashed before review writes. STATUS.md
receives a new notice with all its previous bytes preserved as a suffix; it stays
unstaged to avoid committing unrelated user changes. Only the new review report
and its dedicated evidence directory are included in the review commit. All
other prior files remain byte-identical. The original blocked review stays as
historical evidence. Frozen G: execution root remains absent / NOT_STARTED.

## Exact next operator action

When the operator elects to start the reviewed Phase P, run from the repository:

```powershell
uv run --offline --locked --extra cpu --extra eval python scripts/evidence_v4.py phase-p --confirm-plan-digest 16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3
```

This is the operator's explicit live action under the existing v4 protocol.
`uv --offline` prevents dependency downloads; the `phase-p` program itself uses
the network. **This review did not run that command.** Phase D remains separately
gated. M and T both pass the narrow pre-live review.
