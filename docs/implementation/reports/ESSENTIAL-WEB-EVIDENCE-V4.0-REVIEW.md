# Essential-Web evidence v4.0 narrow Phase-P review

**V4 NARROW PHASE-P REVIEW BLOCKED** — 2026-09-29. Both M and T are
blocked by two shared production-transport defects in guarantees explicitly
retained by v4. No implementation changes were made.

Starting/current implementation HEAD: `791d3b866db7276180c2e8a93d13d1b775256baf`,
branch `data/mix01-ultrax-6b`; protocol commit
`2ede38f21d4b9d9ba45989eec812213ce9851bee`. This review is uncommitted; no staging,
commit or push. Pre-existing dirty/untracked user files were preserved. A new
review-status notice was prepended to STATUS.md with its prior bytes preserved.

Evidence and exact commands:
[ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW/COMMANDS.md](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW/COMMANDS.md).
All execution was offline with authored synthetic fixtures. No network, live
acquisition, corpus-text inspection, real v4 execution-root creation or Phase D.

## Frozen identities and scientific adoption

Independently recomputed with stdlib JSON/SHA-256, separately from the production
verifier; all equal the requested values:

| Binding | Recomputed value |
|---|---|
| Protocol SHA-256 | `4c7f11b15adb7d0601977fef61ab04d62fae0e9e64091fa22dbd99e1caf65727` |
| Freeze canonical digest | `747b82a2df7702db2b111ea62c2f27f3de3d22a37428472fdc0550af90767f57` |
| Plan canonical digest | `16b93183b485ced924466028b84a8b4cfd84a65f596dc95c80879e32464f35d3` |
| Selection canonical digest | `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474` |
| Selector policy | `f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07` |
| Source revision | `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d` |

The v4 scientific identity equals both the frozen v3 identity and CHILD adoption.
Namespace remains `essential-web-evidence-v2.0`. All 29 scientific-code/dependency
bindings match HEAD and checkout, accounting for the recorded CRLF representation.
The original locator manifest on F: independently reproduces its 23,807-byte
binding and SHA-256 `8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27`.
Only locator-manifest structure was inspected: 118 unique locators, eight unchanged
development files, unchanged seed/policy. No text-bearing corpus file was read.

M retains eight exact files/windows, 512 rows/file, 4,096 rows, seed 20260927,
projection `eai_taxonomy`, `quality_signals`, and 81 projected chunks/file.
T retains its strata/censuses/precedence/rubric/blinding and review-order seed
20260928 through identical scientific artifacts and unchanged bound code.
No reselection or replacement was performed.

Arithmetic clarification, **non-blocking**: frozen M footer-plus-trailer ranges
sum to **1,398,384 bytes**; adding eight four-byte headers gives **1,398,416**.
The protocol correctly labels the latter as the entire M Phase-P payload. The
request calls that value footer-plus-trailer payload; the 32-byte naming difference
does not alter a range or scientific membership.

Historical records/closure confirm v2.x `CLOSED_NON_EXECUTABLE` and
`HISTORICALLY_UNCERTIFIABLE`. v3 is valid frozen scientific/planning lineage;
execution was abandoned while authorization was blocked, before real acquisition,
M scientific-outcome inspection or selected T text inspection. v4 is prospective
execution of that same scientific membership. This is a records-based historical
qualification, not a new operator-authentication claim.

## Ten-question requirement ledger

IMPLEMENTED means the mechanism exists; VERIFIED is limited to the described
offline evidence. BLOCKED identifies an actual v4 guarantee failure. A synthetic
pass establishes logic, not live HF compatibility.

| Question | Status | Evidence / conclusion |
|---|---|---|
| 1. Scientific membership | VERIFIED | Independent hashes, original 118-locator manifest, 29 bindings, v3 equality, 63 scientific regressions. |
| 2. Arbitrary production request injection | VERIFIED | `run_live` accepts only digest confirmation; fixed compiled plan/root; strict CLI with abbreviations disabled. Wrong digest refuses before state. Synthetic entry refuses real plan/repository/revision, live transport and v3/v4 roots. |
| 3. Exact Phase-P ranges | VERIFIED | 16 M operations (heads/frozen footer+trailers); 24 T structural operations, eight footer ranges derived only from frozen N and validated L/trailer. Injected M data-reaching range, T text operation, arbitrary footer, file/kind edits refuse before a request. Trailer overlap/length adversaries refuse. |
| 4. Remote identity | VERIFIED | 206, exact range endpoints/total, exact strong ETag, body length, structured canonical resource and prescribed PAR1 checks; wrong status/range/total, short/long body, weak/changed ETag, wrong resource/query-only revision and bad magic refuse. Signed-host identity is derived from the actual validated redirect chain plus frozen ETag/length, as §7 allows. |
| 5. Network/retry/redirect bounds | BLOCKED | HTTPS/443 and the two exact hosts, real Location handling, three redirects, three tries and delays 1/2 s pass. Absolute 120 s physical-attempt bound fails B02 below. DNS exclusion is accepted. |
| 6. Durable attempts/bytes | BLOCKED | Attempt commit before request and ordinary redirect/error/success/crash accounting pass. Production parser interruption silently drops received complete chunk bytes: B01. No crash/checkpoint allowance explains that loss. |
| 7. Restart/resume | VERIFIED, exercised scope | Real process exit 17, temp reconciliation, new interrupted retry, COMPLETE hash verification/skip, commit-before-rename repair, corruption/inconsistent state refusal all pass. Restart cannot restore bytes already discarded by B01. |
| 8. Scientific isolation | VERIFIED | Closed operations contain no M data-chunk or T text-page retrieval; layout parser uses structural fields and never accesses statistics, key/value metadata or document decoding for scientific decisions. Raw T footer retention and PyArrow metadata parsing are allowed. |
| 9. Output integrity/completeness | VERIFIED structure; BLOCKED byte-total binding | All required receipts/layouts/manifest are produced and hash-bound in successful synthetic runs; exact operation completion is required. Exports correctly reproduce SQLite, but SQLite's received-byte totals are incomplete under B01. |
| 10. Operational caps | IMPLEMENTED; VERIFIED boundary checks; BLOCKED actual-byte assurance | 201st attempt refused, 200th boundary exercised; arm-byte, >4 MiB response, root-size and free-space guards pass. B01 prevents certifying those arm-byte counts as actual returned bytes. No Phase-D reservation is required. |

## B01 — received completed chunk bytes disappear on read interruption

**Blocking: questions 6, 9 and the actual-byte assurance in 10; both arms.**
Location: [transport.py:220](../../../src/xlm/data/evidence_v4/transport.py#L220),
`_LiveResponse.read`, and [phase_p.py:386](../../../src/xlm/data/evidence_v4/phase_p.py#L386).

The production wrapper catches `http.client.IncompleteRead` through the generic
`HTTPException` branch, converts it to a message-only `TransportError`, and loses
`exc.partial`. The engine records only chunks that the wrapper returned normally.

Independent authored wire: a 206 chunked response containing one **complete**
four-byte `PAR1` chunk, followed by EOF without the terminal chunk. A real stdlib
`HTTPResponse` positive control raises `IncompleteRead(partial=b'PAR1')`. The same
parser, through the production wrapper and public offline engine, produces:

- three persistent attempts, all `TRANSPORT_ERROR`, then INCOMPLETE/STOPPED;
- 12 completed response-body bytes supplied across the attempts;
- **zero** recorded body bytes and three empty retained temp files.

This is ordinary handled read failure, not process death between checkpoints.
The SQLite export and manifest agree with each other but omit the received bytes.
Repair must retain/account partial body bytes while keeping the attempt failed;
it must not promote an interrupted response merely because its partial length
matches a requested range. Add regressions through the actual HTTP parser.

## B02 — one buffered HTTP read can exceed the absolute attempt timeout

**Blocking: question 5; both arms.** Locations: `_LiveResponse.read` and
`_arm_socket` in [transport.py:220](../../../src/xlm/data/evidence_v4/transport.py#L220);
the outer-loop-only deadline check in
[phase_p.py:383](../../../src/xlm/data/evidence_v4/phase_p.py#L383).

The socket is armed once before `HTTPResponse.read(amount)`. That parser can
perform multiple blocking reads internally with the same timeout. The independent
probe uses the real `HTTPResponse` and `io.BufferedReader` over authored raw I/O:
60 seconds of virtual wait for a data chunk, then 70 for its terminating chunk.
Each underlying wait is less than the armed 120 s timeout. The wrapper returns
normally after **130 virtual seconds**, with only `[120.0]` recorded as socket
timeout settings. No DNS, real sockets or wall-clock sleeps are involved.

The engine can detect expiration on a subsequent loop, but that does not bound
the preceding physical read to the protocol's deadline. Two supporting engine
EOF probes also return COMPLETE when virtual EOF advances beyond the deadline;
these isolate the missing post-read check, and are not claims of observed live
server timing. The buffered-parser probe supplies the production-path evidence.

This is the **120 s physical-attempt rule in v4 §§4–5**, not v3's abandoned
30/600/1800 active-time accounting. Repair must bound actual blocking I/O by
remaining time and reject late completion while preserving received-byte counts.
Adding only an outer-loop or post-read check cannot bound the time already spent
inside a buffered read.

## Independent execution and dependency result

Existing focused v4 selection: **157 passed**, exit 0, 15.40 s. Existing science
selection: **63 passed**, exit 0, 1.35 s. Independent adversarial evidence:
**four distinct failed assertions** across three invocations (2 + 1 + 1), exits
1; two shared defect mechanisms above. No xfail, skip, assertion weakening,
source mutation or retry-until-green. Ruff check/format and mypy on the original
13 v4 source/CLI/test files each exit 0; format reports 13 files unchanged.
The final documentation `git diff --check` exits 2 for CRLF whitespace in the
preserved dirty files/new same-format notice; it is not a source-check pass.

The successful E2E test independently reran the public `run_offline` engine shared
with `run_live`: 2 M + 2 T authored files, 10 logical operations, three-transition
redirect, retryable 503, real process death, restart/resume, COMPLETE and no-request
second resume. Assertions verified 25 physical attempts (M 11/T 14), IDs 1–25,
1,048,576 durable bytes before death versus 1,310,720 retained interrupted bytes,
reconciled totals, exact requested ranges and payload slices, hashes and manifest.
Its nine adversarial variants also passed. Those fake-reader successes do not
exercise B01's real-parser partial exception or B02's buffered blocking steps.
JUnit records preserve the independent results; temporary fixture roots are
disposable pytest artifacts, not the real root or scientific evidence.

Dependency assertion is substantially correct with one harmless correction:
importing v2 canonical also initializes `xlm.data.evidence_v2.frozen` through the
package initializer. That module is data-only. AST inspection and a fresh-process
runtime module inventory show no generic acquisition, sources transport or v3
execution import on the v4 path. Unrelated generic-acquisition Windows failures
are **NON-BLOCKING** and were not rerun.

Environment: Windows 11 build 26200 AMD64, CPython 3.12.13, uv 0.12.19, existing
locked project environment. The independent identity/dependency script measured
1.021 s and 34,816,000-byte peak process working set. Full test-run peak memory
was not independently measured; no prior implementation memory number is adopted.
No production performance claim is made. Dependency pins/install policy unchanged.

## Boundaries, root and next action

Frozen `G:\Project\xlm-evidence-v4\essential-web` remains absent/NOT_STARTED.
G: free space measured **999,668,580,352 bytes**, comfortably above 1 GiB. The
production CLI uses that G: root and has no F: fallback. Synthetic test roots on
other drives do not change the production root.

**NOT RUN:** live HF requests, real Phase P, full repository acceptance, CUDA,
real-source performance. Live redirect/ETag compatibility remains unknown and
is explicitly permitted if unexpected behavior stops without fallback, broader
hosts, weaker identity or reselection.

**OUT OF SCOPE:** all deliberately dropped v3 requirements, including signatures,
encoded approval/capabilities, exactly-once genesis, certified RSS/active-time,
Phase-D reservations, generalized state/reservation/readiness frameworks, bounded
DNS and malicious-local-operator tamper resistance. None is used as a blocker.

M Phase P: **BLOCKED**. T Phase P: **BLOCKED**. Exact next operator action is to
request a narrow repair of B01/B02, preserving frozen scientific membership and
all v4 identities, followed by focused offline recertification. Suggested prompt:

> Repair only B01/B02 in ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW.md. Preserve all frozen
> v4/scientific identities. Add actual-parser partial-byte and absolute-attempt
> deadline regressions, then rerun the focused v4 selection and independent
> review probes. No network, live acquisition, corpus text, real root, Phase D
> or push. Do not launch Phase P until the narrow review passes.

No repair or live command is authorized/executed by this review. Report/evidence
remain uncommitted; the request's approval-commit condition was not met.
