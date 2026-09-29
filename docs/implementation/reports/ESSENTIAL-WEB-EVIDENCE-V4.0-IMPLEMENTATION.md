# Essential-Web evidence v4.0 — Phase-P fetcher implementation

**READY FOR NARROW V4 PHASE-P REVIEW.** 2026-09-29, branch
`data/mix01-ultrax-6b`.

- Starting HEAD `60ed59c5078114dd54297b8923c45495014f7874` (v3 authorization
  review 3), whose parent is the v3 children commit `12d85158…`.
- Commit 1 (protocol/freeze only): `2ede38f21d4b9d9ba45989eec812213ce9851bee`.
- Commit 2: this implementation.
- Normative protocol:
  [ESSENTIAL-WEB-EVIDENCE-V4.0-PROTOCOL.md](ESSENTIAL-WEB-EVIDENCE-V4.0-PROTOCOL.md),
  SHA-256 `4c7f11b15adb7d0601977fef61ab04d62fae0e9e64091fa22dbd99e1caf65727`.
- Freeze digest `747b82a2df7702db2b111ea62c2f27f3de3d22a37428472fdc0550af90767f57`.
- Plan digest `16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3`.
- Evidence: [commands and results](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-IMPLEMENTATION/COMMANDS.md).

Every run here was offline: no network, no acquisition, no corpus text, no real
v4 root, no Phase D, no push. Nothing in commit 1 changed during
implementation. `docs/implementation/STATUS.md` was **not** edited, because it
carries pre-existing uncommitted user changes that this task must not stage.
This report supplies the status instead.

## Lineage (unchanged by this commit)

- **v2.x:** `CLOSED_NON_EXECUTABLE`, historically uncertifiable.
- **v3.0:** the scientifically valid frozen planning lineage. Its execution
  implementation was abandoned before any real acquisition, and it never passed
  authorization (review 3 verdict: BLOCKED).
- **v4.0:** a new prospective simple execution lineage. It adopts the exact
  scientific membership. v3 code and artifacts are untouched.

## Design

| Module | Role |
|---|---|
| `src/xlm/data/evidence_v4/frozen.py` | compiled freeze constants; single plan validator; committed-plan loader (fixed path + compiled digest); T-footer derivation rule |
| `transport.py` | structural URL policy, redirect-destination check, 206 identity check, the one live single-hop HTTPS transport |
| `state.py` | `state.sqlite` schema/store (run, operations, attempts, outputs); exclusive lock |
| `layout.py` | metadata-only Parquet footer checks, M projected-chunk and T text-chunk layouts |
| `phase_p.py` | engine, restart reconciliation, exports; `run_live`, `run_offline` (synthetic only), `inspect` |
| `verify.py` | offline recomputation of every freeze binding and the v3 cross-check |
| `scripts/evidence_v4.py` | `verify`, `show-plan`, `phase-p-status`, `phase-p --confirm-plan-digest` |

The only non-v4 imports are `xlm.data.evidence_v2.canonical` (the canonical-JSON
definition behind every lineage digest), PyArrow (footer metadata only) and the
stdlib. None of the v2/v3 acquisition, authorization, genesis, journal, memory
or readiness code is imported.

## Requirement ledger

Statuses: IMPLEMENTED = mechanism exists; VERIFIED = a stated offline test or
check passed; NOT RUN = not executed; OUT OF SCOPE = deliberately excluded. A
synthetic pass proves logic, not live-source compatibility.

| Requirement | Status | Evidence |
|---|---|---|
| Scientific membership byte/semantically identical | VERIFIED | commit-1 builder and `verify` recompute the adoption from the committed v3.0 freeze and CHILD dry plans; external selection manifest (SHA-256 `8424f966…`, 118 unique locators in the 8 T files), M observations and T cost map are hash-verified read-only; science regressions pass (63 tests) |
| Plan: 8 M + 8 T files, 16 M ops, 24 T ops after trailers, no Phase-D range | VERIFIED | `test_evidence_v4_plan.py` |
| No arbitrary URL/file/range/ETag/kind/plan/root/output input (CLI and API) | VERIFIED | argparse rejects every such option (exit 2); signature tests; plan loaded only from its fixed path with its compiled digest; output names derived from op IDs |
| Wrong plan digest refuses before any state | VERIFIED | CLI/API tests; frozen root not created |
| Live path issues exactly the frozen first request (URL, `0-3`, 120 s) | VERIFIED (recording stand-in for the live class; no socket) | `test_cli_live_path_issues_exactly_the_first_frozen_request_and_stops` |
| HTTPS/443/exact hosts; reject http, userinfo, localhost, IP literals, other hosts or subdomains, downgrade | VERIFIED | transport unit tests; engine redirect tests |
| Redirects: actual `Location` validated, ≤3 transitions, 4th is STOP | VERIFIED | engine and adversarial tests |
| Identity: 206, exact Content-Range and total, strong and exact ETag, length, path, PAR1 | VERIFIED | unit and engine parametrized tests (200, wrong range/total, weak/changed ETag, short/long body, bad PAR1, wrong path, query-only revision) |
| Retries ≤2 additional (delays 1 s, 2 s); 120 s timeout passed and enforced | VERIFIED | retry, timeout and transport tests |
| Attempt committed before transport; byte counts for success, redirect, error, partial and interrupted bodies | VERIFIED | accounting tests; real-process-death E2E |
| Restart: completed ops hash-verified and skipped; in-progress becomes INTERRUPTED; retry is a new attempt; commit-then-rename crash completed; inconsistency is STOP | VERIFIED | restart tests (8 damage variants, crash-before-temp regression) |
| Isolation: no M projected chunk, no T text page; forged trailer reaching text stops before any footer request | VERIFIED (synthetic) | isolation tests compare every request with authored chunk offsets |
| Caps: 200 attempts, 64 MiB arm body, 4 MiB response, 256 MiB root, ≥1 GiB free | VERIFIED | cap tests incl. boundaries |
| Outputs: COMPLETE only when all ops verified; totals equal attempt table; hashes reproduce; layouts bound | VERIFIED | output tests; E2E |
| Second process refused (exclusive SQLite lock) | VERIFIED | `test_second_process_is_refused` |
| Live compatibility with huggingface.co / cas-bridge | NOT RUN | forbidden in this task; synthetic mocks do not prove it |
| Live `phase-p` | NOT RUN | operator action after review |
| Phase D, training, tokenizer, admission | OUT OF SCOPE | protocol §13 |
| Dropped v3 guarantees (authorization objects, genesis, RSS, active-time, D reservations, readiness) | OUT OF SCOPE | protocol §3 |

## Synthetic end-to-end result (`test_evidence_v4_e2e.py`)

The fixture has 2 M + 2 T authored Parquet files (10 logical operations). The
second T file carries a padded footer of 1,635,266 bytes.

1. A separate Python process runs the library path. Along the way:
   - `M-00-head` follows a 3-transition redirect chain (307 canonical →
     302 signed → 302 signed).
   - `M-01-footer` gets one retryable 503, and the retry succeeds.
   - The process dies via `os._exit(17)` 1,310,720 bytes into the `T-01-footer`
     206 body.
2. Checked after the death:
   - exactly one IN_PROGRESS attempt, whose durable count is 1,048,576 bytes;
   - its temp file holds 1,310,720 bytes;
   - the run is still RUNNING and there is no receipt.
3. Restart in the test process, with the network blocked:
   - reconciliation marks that attempt INTERRUPTED with 1,310,720 bytes;
   - only the two requests for `T-01-footer` are issued, as try 2;
   - the run ends COMPLETE.
4. Exact final state:
   - 25 physical attempts (M 11, T 14) with IDs 1..25;
   - per-operation outcomes, hops and tries match the scenario exactly;
   - total body bytes = redirect bodies + 503 body + 1,310,720 interrupted
     bytes + every exact structural range;
   - every retained payload equals the authored byte slice, and its SHA-256
     matches the receipt;
   - the manifest reproduces;
   - the interrupted temp body is retained;
   - a further run makes zero requests.

The adversarial variants all stop safely: the receipt is INCOMPLETE, the run
STOPPED, receipt totals equal the attempt table, no body exceeds 4,194,305
bytes, and reruns are refused. The variants are:

- wrong ETag;
- wrong range;
- bad redirect;
- fourth redirect;
- overlarge body;
- timeout ×3;
- altered retained temp body;
- corrupted output;
- injected `T_TEXT_PAGE` operation.

## Results

| Selection | Result |
|---|---|
| Focused v4 tests (`-n 0`): plan 34, transport 52, engine 61, e2e 10 | 157 passed, 0 failed, 0 skipped; 14.86 s wall; peak working set 187,428,864 bytes |
| Frozen-science regressions: v3 identity/lineage nodes + `test_evidence_v2_core.py` + `test_evidence_v2_text.py` | 63 passed |
| ruff check / ruff format --check / mypy (strict), 13 files | all exit 0 |
| `verify`, `show-plan`, `phase-p-status` | exit 0 |
| `phase-p` with a wrong digest | exit 1, refused |

These are focused runs, not a full-suite pass. Unrelated generic acquisition
suites were not run; v4 does not import that code.

During implementation, a bug in the restart path was found and fixed. If the
process died after the attempt row was committed but before its temp file
existed, the first restart would reconcile correctly, but a second restart
would then refuse. Reconciliation now retains an empty body file. The
regression test fails without the fix.

## Open limitations (not certified)

- Live HF behaviour is unproven. If HF redirects to a `huggingface.co` path
  other than the canonical resource, or sends a response body with no length
  and no connection close, the run STOPs or times out. That is a safe stop,
  but a protocol amendment would be needed to continue.
- The 120 s limit is enforced on socket operations and while streaming the
  body. DNS resolution is not bounded.
- Wall-clock timestamps are recorded. Active time and memory are not certified.
- The state store is not tamper-proof against local edits between runs.
  Detected inconsistencies are STOP.

## Next operator action

1. Commission the narrow v4 Phase-P review (protocol §14's ten questions)
   against commits `2ede38f` and this implementation commit.
2. Only after that review, with `G:` mounted and at least 1 GiB free, the
   operator runs (not run here):

   ```text
   uv run --offline --locked python scripts/evidence_v4.py verify
   uv run --offline --locked python scripts/evidence_v4.py phase-p --confirm-plan-digest 16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3
   uv run --offline --locked python scripts/evidence_v4.py phase-p-status
   ```

   (`--offline` applies only to uv dependency resolution; the `phase-p`
   process itself contacts the two allowlisted hosts.)
