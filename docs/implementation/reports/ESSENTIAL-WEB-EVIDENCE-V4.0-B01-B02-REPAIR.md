# Essential-Web evidence v4.0 — B01/B02 narrow repair

Date 2026-09-29. Branch `data/mix01-ultrax-6b`. Starting HEAD
`55d45f46fc1ef5a7a2c6a27c1f33e573df1635ae` (Astra narrow review, on top of the
implementation `791d3b8` and the frozen protocol `2ede38f`). The working tree
was clean at start.

This repairs only the two blockers in
[ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW.md](ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW.md):
B01 and B02. The protocol, freeze, plan, scientific sample, caps and all other
engine behaviour are unchanged. The repair is in the transport, plus two
classification lines in the engine.

## Frozen identities (unchanged)

`scripts/evidence_v4.py verify` exits 0 before and after the repair, and
reproduces:

| Identity | Value |
|---|---|
| Protocol SHA-256 | `4c7f11b15adb7d0601977fef61ab04d62fae0e9e64091fa22dbd99e1caf65727` |
| Freeze digest | `747b82a2df7702db2b111ea62c2f27f3de3d22a37428472fdc0550af90767f57` |
| Plan digest | `16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3` |

`git diff 2ede38f` on the protocol, freeze, plan and adoption files is empty.
The plan was not regenerated.

## B01 — partial body bytes lost on `IncompleteRead`

**Old failure.** An authored chunked 206 response sends one complete `PAR1`
chunk and then reaches EOF before the terminating chunk. The pre-repair tree
behaves as follows:

- Each of three attempts physically supplied 4 body bytes (12 in total).
- All three attempts were recorded as `TRANSPORT_ERROR` with 0 bytes.
- All three temp files were empty.

This reproduces Astra's finding.

**Root cause.** `_LiveResponse.read` called `HTTPResponse.read(amount)`. For a
chunked body, the stdlib `_read_chunked` collects body bytes across several
underlying reads before returning. When a later framing step fails, the
collected bytes are either discarded or carried only in
`IncompleteRead.partial`. The wrapper turned that exception into a
message-only `TransportError`, so the engine never saw the bytes. The same
collection is also lost when an `OSError` or timeout is raised mid-call.

**Repair** (`src/xlm/data/evidence_v4/transport.py`):

- `_LiveResponse.read` now calls `HTTPResponse.read1(amount)`.
- For a chunked or length-delimited body, `read1` does at most one body
  receive and returns those bytes before any later framing step runs.
- So when a later call fails (with `IncompleteRead`, a reset or the deadline),
  no body byte is left inside the parser. An earlier call already returned
  it, and the engine had written and hashed it.
- The engine keeps its existing order: write and account each chunk, then
  finish the attempt as failed.
- A clean end of body (`b""`) is reported only after correct HTTP framing: the
  terminating chunk, the full Content-Length, or close-delimited EOF.
- Any `IncompleteRead` is still `TRANSPORT_ERROR`. It is retryable and never
  promoted, even when the received bytes equal the requested range.
- `IncompleteRead.partial` from `read1` can only hold chunk framing (the CRLF
  after a chunk), never body, so it is not appended to the body.

**Parser-level regression** (`tests/test_evidence_v4_wire.py`, the real stdlib
`HTTPResponse` through the production `tp.exchange` / `_LiveResponse`):

- Positive control: the same wire makes the unmodified stdlib `read(65536)`
  raise `IncompleteRead(partial=b"PAR1")`.
- `_LiveResponse.read` returns `b"PAR1"`. The next read raises a
  `TransportError` for the `IncompleteRead`.
- Public engine (`run_offline`), three tries, then STOP with "retries
  exhausted after 3 tries":

  | | try 1 | try 2 | try 3 | total |
  |---|---|---|---|---|
  | bytes physically supplied | 4 | 4 | 4 | 12 |
  | temp file | `PAR1` | `PAR1` | `PAR1` | 12 bytes |
  | SQLite `response_bytes` | 4 | 4 | 4 | 12 (receipt, `inspect` and totals agree) |
  | attempt outcome | TRANSPORT_ERROR (http 206) | TRANSPORT_ERROR | TRANSPORT_ERROR | — |
  | operation | not COMPLETE, no payload | | | run STOPPED/INCOMPLETE |

- Two broken tries followed by a clean one give three distinct attempt rows.
  The two failed rows keep `PAR1` / 4 bytes each, and the run totals include
  them.

## B02 — the 120 s physical-attempt deadline was not absolute

**Old failure.** Astra's buffered-parser probe waits 60 virtual seconds for
the PAR1 chunk and 70 for the terminating chunk. On the pre-repair tree:

- `read` returned `PAR1` at **130 s**.
- The socket timeouts armed were `[120, 120, 120]`.
- The engine-level attempts each lasted 130 s before being marked TIMEOUT.

**Root cause.** The socket timeout was armed once per wrapper call.
`HTTPResponse` and `BufferedReader` can issue several blocking receives inside
one call, and each receive got the full armed timeout. The TCP connect loop in
`socket.create_connection` also gives every address the full timeout.

**Deadline mechanism** (option A, deadline-aware reads). The engine computes
one deadline per physical attempt: `clock() + 120`. The transport uses it as
follows:

- `_remaining(deadline, clock)` returns `deadline - clock()`, or raises
  `DeadlineError` when that is ≤ 0.
- Before **every** blocking socket operation, the timeout is set to
  `_remaining(...)`:
  - each TCP connect, trying each resolved address under the same deadline;
  - the TLS handshake (done explicitly with `do_handshake_on_connect=False`);
  - each `send` of the request;
  - each underlying receive for the status line, headers, chunk framing and
    body. `_DeadlineReader`, a `RawIOBase` under the parser's
    `BufferedReader`, re-arms the timeout before every `recv_into`.
- A socket timeout can therefore only mean the absolute deadline was reached,
  and it is raised as `DeadlineError`, a `TransportError` subclass.
- The engine records `DeadlineError` as `TIMEOUT`, which is retryable. This
  applies whether it happens before the response head or while streaming.
- As a complement, the engine also rejects an end-of-body observed at or after
  the deadline.
- `LiveHttpsTransport(clock=time.monotonic)` uses the same clock as the
  engine's `run_live`.
- Name resolution (`getaddrinfo`) stays out of scope, as v4 allows. Its time
  still counts against the same deadline.
- The request bytes are the exact request `HTTPSConnection` sent before:
  request line, `Host`, `Range`, `Accept-Encoding: identity`, `User-Agent`.

**Boundary.** An attempt is valid only if every operation finishes strictly
before `start + 120 s`. A wait that reaches the remaining time fails at
exactly `start + 120 s`.

**Parser/buffer regressions** (virtual clock; the production timeout stays
120; no sleeps):

| Case | Result |
|---|---|
| Fixture control: stdlib parser, timeout armed once at 120 | one `read` returns `PAR1` at 130 s (reproduces the defect) |
| Same parser over `_DeadlineSocket`, one `read(65536)` | raises at exactly 120 s; timeouts `[120, 120, 60]` |
| `_LiveResponse` 60 + 70 | `PAR1` at 60 s, then `DeadlineError` at exactly 120 s; timeouts `[120, 120, 120, 60]` |
| Split head/chunks 10, 15, 30, 40, 20 s | completes at 115 s; timeouts `[120, 120, 110, 95, 65, 25]` |
| Connect: refused after 10 s, next address 30 s, TLS handshake 79 s | succeeds at 119 s; timeouts 120, 110, 80 |
| Handshake of 80 or 81 s after a 40 s connect | `DeadlineError` at exactly 120 s; TLS socket closed |
| First connect would take 500 s | `DeadlineError` at 120 s; no fresh time for another address |
| `LiveHttpsTransport.open` (5 s connect) + 60 + 70 | timeouts `[115, 115, 115, 55]`; fails and closes at 120 s |

**Engine boundary results** (public `run_offline`, first hop answers 206 +
PAR1 chunk after 60 s, then the terminating chunk after `x` s):

| Total | Outcome of try 1 | Bytes | Socket life |
|---|---|---|---|
| 119 s (x = 59) | SUCCESS, payload `PAR1` | 4 | 119 s |
| 120 s (x = 60) | TIMEOUT | 4 | 120 s |
| 121 s (x = 61) | TIMEOUT | 4 | 120 s |
| 130 s (x = 70) | TIMEOUT; try 2 gets a new attempt row and completes | 4 | 120 s |
| 130 s on every try | 3 × TIMEOUT, STOP "retries exhausted"; 12 bytes accounted | 4 each | 120 s each |
| head delayed 130 s | TIMEOUT, http status NULL, 0 bytes, empty temp retained; retried | 0 | 120 s |
| head at 0 s, body delayed 125 s | TIMEOUT, http 206, 0 bytes | 0 | 120 s |
| end-of-body observed at/after the deadline | TIMEOUT, 4 bytes kept, not promoted | 4 | — |

No bytes are refunded. A failed attempt keeps its temp file and hash, and the
operation stays incomplete unless a later try succeeds.

## Independent-style probes

`docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-B01-B02-REPAIR/probe_b01_b02.py <tree>`
runs the same authored wires through the public engine. It was run against a
scratch worktree of the pre-repair HEAD `55d45f4` and against the repaired
tree:

| Probe | pre-repair `55d45f4` | repaired |
|---|---|---|
| B01 supplied / SQLite / temp | 12 / **0** / empty ×3 | 12 / 12 / `PAR1` ×3 |
| B02 parser read events | `PAR1` @130 s, EOF @130 s | `PAR1` @60 s, `DeadlineError` @120 s |
| B02 engine attempt duration | 130 s ×3 | 120 s ×3 |

The new regression file, run against the pre-repair tree, gives
**21 failed, 2 passed**. The two passes are the stdlib fixture controls. The
injection probe (`probe_injection.py`) inserts an `M_DATA_CHUNK` row and a
`T_TEXT_PAGE` row into a partial root. Both STOP with "stored operations differ
from the frozen plan" and make 0 requests.

## Verification (this repair)

Environment: Linux cloud container, CPython **3.12.3** (`/usr/bin/python3.12`).
The pinned 3.12.13 is not installable here. The tools were uv 0.8.17,
PyArrow 25.0.1 and pytest 9.1.1. The locked base + `dev` group was installed
into a scratch venv with `uv sync --locked` from PyPI. The `cpu`/`eval` extras
were not installed because the PyTorch index is blocked, and v4 imports
neither. Every test and probe is offline: sockets are patched to refuse, there
is no HF request, the fixtures are synthetic, and the roots are disposable.
Test-run settings: `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`,
`TOKENIZERS_PARALLELISM=false`, `-p no:cacheprovider`. Commands used
`UV_PYTHON=/usr/bin/python3.12` and `uv run --offline --locked`.

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `python -m pytest -n 0 -q tests/test_evidence_v4_{plan,transport,engine,e2e,wire}.py` | 0 | **180 passed** (157 existing + 23 new), 22.61 s; peak RSS 175,325,184 bytes |
| 2 | `python -m pytest -n 0 -q tests/test_evidence_v4_wire.py` | 0 | 23 passed |
| 3 | `python -m pytest -n 0 -q tests/test_evidence_v3.py tests/test_evidence_v2_core.py tests/test_evidence_v2_text.py` | 0 | 69 passed (science regressions: whole v3 identity/lineage file + v2 core/text) |
| 4 | `ruff check` on the 14 v4 files (13 original + `test_evidence_v4_wire.py`) and the probes | 0 | all checks passed |
| 5 | `ruff format --check` on the same files | 0 | 15 files already formatted |
| 6 | `mypy` (strict project config) on the 14 v4 files | 0 | no issues |
| 7 | `python scripts/evidence_v4.py verify` / `phase-p-status` | 0 / 0 | identities above; root NOT_STARTED, absent |
| 8 | `python scripts/evidence_v4.py phase-p --confirm-plan-digest 000…0` | 1 | refused |
| 9 | `probe_b01_b02.py` on the pre-repair worktree / the repaired tree | 0 / 0 | table above |
| 10 | new wire tests on the pre-repair worktree | 1 (expected) | 21 failed, 2 passed |

Outputs are in `docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-B01-B02-REPAIR/`.

Test changes:

- The two transport tests that monkeypatched `http.client.HTTPSConnection` now
  script the socket seam instead. They keep the same assertions: one exact
  request, no redirect following, and policy/deadline refusals before any
  connection.
- The exact request bytes, including `Host`, are now asserted.

## Existing guarantees re-confirmed

All of these pass in run 1:

| Guarantee | Tests |
|---|---|
| Wrong plan digest refuses | `test_wrong_plan_digest_refuses_without_touching_the_root`; CLI run 8 |
| Arbitrary range/URL/file refuses | `test_cli_has_no_arbitrary_input`, `test_public_entry_points_take_no_url_file_range_or_etag`, `test_real_plan_refuses_any_edit` |
| M data-chunk / T text-page injection refuses | `test_injected_or_altered_operations_are_refused`, `test_adversarial_operation_injection_stops_safely`, `test_phase_p_never_requests_data_chunks_or_text_pages`, `probe_injection.py` |
| Wrong ETag / Content-Range refuses | `test_identity_mismatch_stops`, `test_identity_refuses`, `test_adversarial_responses_stop_safely` |
| Forbidden redirect / fourth redirect refuses | `test_bad_redirect_stops`, `test_redirect_destinations_refused`, `test_fourth_redirect_stops` |
| Retry maximum | `test_retry_maximum_is_enforced`, `test_transport_timeout_is_retryable_then_stops` |
| >4 MiB response / >200 attempts / >64 MiB arm | `test_single_response_over_4_mib_stops`, `test_attempt_cap_refuses_the_201st_attempt`, `test_arm_body_cap_is_never_exceeded` |
| Restart/resume | `test_synthetic_phase_p_end_to_end_with_process_crash` (real process exit), `test_interrupted_temp_file_is_reconciled_on_restart`, `test_crash_between_commit_and_rename_is_completed` |
| Corrupt completed output refuses | `test_corrupted_output_of_a_complete_root_stops`, `test_adversarial_corruption_stops_safely` |
| COMPLETE requires all planned operations | `test_complete_outputs_are_hash_bound`, `test_completed_operations_are_verified_and_never_rerun`, E2E |

## Requirement ledger

| Requirement | Status |
|---|---|
| B01 partial bytes retained, persisted, accounted; attempt failed; never promoted; retry = new attempt; totals include failures | IMPLEMENTED, VERIFIED (real parser, public engine) |
| B02 single absolute deadline over connect, TLS, send, head and body reads; timeout keeps received bytes | IMPLEMENTED, VERIFIED (virtual clock, parser/buffer and engine level) |
| Frozen protocol/freeze/plan identities | VERIFIED unchanged |
| Live HTTPS against real hosts (real TLS/DNS timing) | NOT RUN (forbidden) |
| DNS bound | OUT OF SCOPE (v4) |
| Pinned CPython 3.12.13 / Windows run of this repair | NOT RUN here (3.12.3 on Linux); rerun on the operator checkout |
| Full offline acceptance suite, CUDA | NOT RUN (not an acceptance gate) |

`docs/implementation/STATUS.md` is deliberately not edited by this commit. The
authoritative checkout carries uncommitted STATUS/selector changes, and this
commit must apply to it without conflicts.

## Remaining limitations

- The mechanism is proven offline with scripted sockets. Behaviour against real
  TLS stacks and HF servers is still unobserved.
- A timeout set to `remaining` is measured by the OS from the call's start.
  Scheduling jitter between computing it and entering the call can therefore
  overshoot the deadline by microseconds, never by a whole wait.

## Next operator action

Commission the final narrow v4 recertification of B01/B02 only, against this
commit. Rerun it on the Windows checkout with CPython 3.12.13:

```text
uv run --offline --locked python scripts/evidence_v4.py verify
uv run --offline --locked python -m pytest -n 0 -q -p no:cacheprovider tests/test_evidence_v4_plan.py tests/test_evidence_v4_transport.py tests/test_evidence_v4_engine.py tests/test_evidence_v4_e2e.py tests/test_evidence_v4_wire.py
uv run --offline --locked python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-B01-B02-REPAIR/probe_b01_b02.py .
```

Suggested prompt:

> Narrowly recertify only B01/B02 of ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW.md
> against the B01/B02 repair commit. Rerun the Astra partial-read and 60+70 s
> probes through the real parser, the focused v4 selection and the science
> regressions, offline. Do not reopen passed requirements. No network, no live
> Phase P, no real root, no Phase D, no push.

Phase P stays unauthorized until that recertification passes.
