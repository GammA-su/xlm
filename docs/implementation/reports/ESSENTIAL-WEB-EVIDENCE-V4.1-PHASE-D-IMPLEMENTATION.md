# Essential-Web evidence v4.1 — Phase-D acquisition implementation report

Date 2026-09-29, branch `data/mix01-ultrax-6b`, authoritative Windows checkout
`F:\Project\xlm-data-ultrax`. Starting HEAD
`ed8efcf3c85c265828a88579a264f08a2048640f` (passed Phase-P result review).
Protocol/freeze commit `d58693822ce06baddf1d62a21f69ecf0bb81f2bd`. The
implementation commit carries this report. Nothing was pushed.

Verdict: **READY FOR NARROW PHASE-D AUTHORIZATION REVIEW.**

No network, no live Phase D, no corpus data download, no document-text
inspection, no human review, no scientific reselection, no Phase-P mutation,
no Phase-D root creation.

## 1. Frozen values

| Item | Value |
|---|---|
| Protocol | [`ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-PROTOCOL.md`](ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-PROTOCOL.md), SHA-256 `bb2bca6f571c9e02b02536bc518cdc1b61fdf0f56b0c6970eaa257c8d198769f` |
| Freeze digest | `9c4612fe860ef49c26ff09b25f8fdaf00f0a7015d7eab4fd7cfe28a0e1675228` |
| Phase-D plan digest | `23a26ffc87d27e4c8fa5de86d7ec446c260bfd65442dc5b1dfd2651cd62222b7` |
| Phase-P parent binding / scientific adoption | `de27e2a56f83970a91f9f3224b3bad7cefc977018612522679838ebd15b61559` / `4a2de146e53b4a21f1596f1f3518d0a12f5fec2b5760bfab20fd3eabcdea2cba` |
| Reviewed dry plan (parent) | `ee82125d1b0d2b6d0a6c420144910db954659d5b757283d39123ddc3d0080356`, independently re-verified (self-digest and blob equality with `ed8efcf`) |
| Execution root | `G:\Project\xlm-evidence-v4.1\essential-web-phase-d` (fresh; not created) |
| Phase-P parent root | `G:\Project\xlm-evidence-v4.1\essential-web` (read-only) |

The plan digest is new because it now fixes the execution profile, limits,
root and parent bindings. Its ranges are the dry plan's, endpoint for
endpoint.

## 2. COMPLETE Phase-P parent verification

The freeze builder read the real parent read-only: `read_bytes`/`stat` only,
SQLite hashed as bytes, footers parsed metadata-only. It required every
binding below and took a stat inventory before and after the build, which
matched.

- Receipt, manifest and both layouts reproduce the reviewed digests
  (`ebd5704b…`, `1bb6bad6…`, `2c9f2f79…`, `364bda46…`) and the dry plan's
  bytes/SHA-256. `state.sqlite` equals the dry plan's hash, and the request
  receipt equals the receipt and manifest bindings.
- The receipt is COMPLETE/COMPLETE with a null stop reason, and the manifest
  is COMPLETE and binds it. All four exports bind the v4.1 plan, protocol,
  freeze and selection. 40/40 operations are complete.
- All 24 footer/trailer payloads equal their manifest entries.

Independently, the implementation's own parent check (`_verify_parents`, the
code a live run executes first) passed against the real root:
`check_real_parent.py`, exit 0, 30/30 artifacts verified, layouts equal plan,
88-entry inventory unchanged (inventory hash `838bdcb4…96cf`), Phase-D root
absent, 0 network requests.

## 3. Scientific identity preservation

Namespace `essential-web-evidence-v2.0`, selection `975ba3de…78474`, revision
`ce4eccc7…113d`, policy `f4357f61…dd07` and the v4.0 identity digest
`080caebb…91c6` are adopted unchanged. The builder verified:

- M files, windows, ETags, lengths, chunk counts and payload bytes equal the
  v4.1 plan;
- T files, spans and piece counts equal the v4.1 plan's `data_range_count`;
- the 118 plan locators equal the frozen selection manifest's identities
  (SHA-256 `8424f966…af27`) as a set.

No stratum or other selection category was copied into any artifact. The
Phase-D freeze commit touched no scientific code. The implementation commit
changes no `evidence_v2`/`evidence_v3` file, and 69 scientific regressions
pass.

## 4. Exact plans

**M: 8 operations, 11,692,530 bytes, 4,096 rows.** One exact range per
file: the union of 81 exactly adjacent projected leaf chunks
(`eai_taxonomy.*`, `quality_signals.*`), each inside the frozen row group and
before the footer. The builder proved the union equals the dry-plan range,
the Phase-P layout chunks and the footer metadata leaves.

**T: 47 operations, 179,963,169 bytes, 118 locators.** Each frozen row
group's dictionary-inclusive `text` chunk (dictionary page offset = span
start < data page offset, proven from the footers) is split from its start
into 4,194,304-byte pieces plus the remainder: 6/6/5/6/6/6/6/6 pieces with
16/14/11/11/14/13/18/21 locators. There is no coalescing across gaps or the
4 MiB ceiling.

Full per-file tables are in protocol sections 3–4. `show-plan` prints all 55
endpoints.

## 5. Operational caps

M ≤ 64 physical attempts and ≤ 67,108,864 response-body bytes. T ≤ 320 and
≤ 536,870,912. Per response ≤ 4,194,304. ≤ 3 redirects per logical request,
≤ 2 extra tries, a 120-second absolute attempt deadline (B02), a root of
≤ 1,073,741,824 bytes, and ≥ 2,147,483,648 bytes free before a run. Checks are
pre-emptive: before each attempt, count < cap, body + 4,194,305 ≤ arm cap,
and root + 4,194,305 ≤ root cap. Decoded outputs must fit the root cap
before they are written. No cap is ever raised automatically.

## 6. Implementation

The reviewed v4 engine is reused by subclassing, not cloned.

| File | Change |
|---|---|
| `src/xlm/data/evidence_v4/state.py` | `temp_name`/`payload_name` take an operation-ID pattern (default: the Phase-P grammar); `Store.op_id_pattern` class attribute. Default behaviour byte-identical. |
| `src/xlm/data/evidence_v4/phase_p.py` | `_Engine.store_class` (default `state.Store`); reconciliation passes the store's pattern to the name derivations. Default behaviour identical. |
| `src/xlm/data/evidence_v4/phase_d_plan.py` (new) | Frozen constants (digests, roots, limits, `PROFILE` = v4.1 network/host policy) and the strict plan grammar. `validate_plan` re-derives all operations and exposes the fetch side as an ordinary `frozen.Plan`. |
| `src/xlm/data/evidence_v4/phase_d_decode.py` (new) | `SparseSource`, M/T decoders, assemblers; reuses the frozen `sparse.classify_retained`/`check_retained_budget`. |
| `src/xlm/data/evidence_v4/phase_d.py` (new) | `_PhaseDEngine(phase_p._Engine)`: Phase-D caps, parent verification, decode orchestration, exports, COMPLETE re-verification; `PhaseDStore` (+`decoded_outputs`); `run_live`, `run_offline`, `inspect`, `verify_repository`. |
| `scripts/evidence_v41_phase_d.py` (new) | CLI: `verify`, `show-plan`, `phase-d-status`, `phase-d --confirm-plan-digest`. |
| `tests/evidence_v41_phase_d_support.py`, `tests/test_evidence_v41_phase_d.py` (new) | Authored fixtures and 75 offline tests. |
| `tests/test_evidence_v41.py` | Two assertions updated (section 9). |

Transport, URL/redirect policy, identity checks, retries, B01, B02, attempt
accounting and restart reconciliation are the unchanged v4.1 code paths.

**M decode.** The M payload and the hash-verified Phase-P footer are loaded
into a sparse virtual file of the frozen remote length, which holds only the
acquired segment. PyArrow is given the pre-parsed footer metadata (no footer
read) and `pre_buffer=False`. Before decoding, the footer's projected leaves
must equal the 81 frozen chunks. Only the two projected columns of the
frozen row group are read, and exactly the frozen window rows are retained.
Each line is canonical JSON with an `_xlm_acquisition` locator (repository,
revision, file, row, row group, row-in-group, ordinal). The assembler requires
every window row exactly once, in order. Records are ≤ 1 MiB and the bundle
≤ 64 MiB.

**T decode and filter.** The retained pieces are concatenated into the
dictionary-inclusive chunk. The footer's text leaf must equal the frozen span
and the dictionary and data offsets. The decoder then iterates 256-row batches
of `text` in the frozen row group up to the batch holding the greatest
selected row. Values are read only at selected indices, as raw bytes
(zero-copy binary view), and decoded as strict UTF-8. Each locator gets
exactly one frozen status: full text ≤ 65,536 bytes; oversize (length only,
no text); or missing/invalid (never coerced). The assembler requires the
frozen locators exactly once and in order, and enforces ≤ 8,388,608 retained
unique and total text. Nothing is truncated or normalized. Unselected rows
are only counted. Errors carry exception type names, never data.

**Blinding and provenance.** Phase D generates no custodian secret, review
ID, key commitment or reviewer order, and writes no stratum or category
anywhere (a test scans every output). Locator-bearing T material is under
`sealed/`: `t_selected_documents.jsonl` and `t_provenance.json`, which maps
each locator to its file, row group, span, source operations, status and hash.
The unsealed `t_acquisition_manifest.json` has counts and hashes only, with no
locators and no text.

**Restart.** The attempt row is committed before the request, as in v4.
COMPLETE operations are hash-verified and skipped. An IN_PROGRESS attempt
takes its actual temp size, becomes INTERRUPTED and is followed by a new
attempt ID. Decoding is deterministic after acquisition: a crash during
decode restarts with zero requests and decodes again. A STOPPED root refuses
further runs. A COMPLETE root re-verifies every artifact, the request receipt
and the `decoded_outputs` table, and makes no request.

## 7. No arbitrary range API

- The CLI parser has exactly four subcommands. `phase-d` accepts only
  `--confirm-plan-digest`, and `allow_abbrev=False` is set. `--url`,
  `--file`, `--range`, `--etag`, `--host`, `--root`, `--locator`, `--plan`,
  `--force` and abbreviations are rejected (tested). The recorded CLI refused
  `--url` with exit 2.
- `run_live(*, confirm_plan_digest)` is the only live entry. It refuses any
  digest other than the compiled plan digest (the Phase-P, dry-plan, uppercase
  and zero digests were tested). It loads only the committed plan and checks
  the committed dry plan. It wires the fixed root, parent and
  `LiveHttpsTransport(policy=v41.HOSTS)`, which a recording test proves.
- The plan validator re-derives every operation. Injected, removed,
  reordered, widened or re-kinded operations are refused even when resealed
  (8 real-plan and 16 synthetic mutations tested).
- `run_offline` accepts only synthetic plans and scripted transports. It
  refuses every frozen root (v3, v4.0, v4.1 Phase-P, Phase-D) as either the
  root or the parent.

## 8. Synthetic Phase-D end-to-end result

The authored fixture has 2 M files and 2 T files. The synthetic COMPLETE
Phase-P parent is produced by the reviewed Phase-P engine offline. T file 0
has a 4.38 MB dictionary-inclusive chunk (dictionary page at 19,054, data
pages from 1,676,518), so it takes two pieces. T file 1 has an oversized and a
null selected value. Every unselected row carries a sentinel string.

The E2E test runs the public offline engine with these steps:

1. Origin → signed-target redirect on every logical request.
2. Mid-body interruption at T-00-d00 (process death after 100,000 bytes).
3. Restart.
4. One retryable 503 on T-00-d01, then a retry.
5. COMPLETE.

Verified outcomes:

- **Exact planned ranges only.** Every call equals a plan range. The restart
  requests only the three unfinished T ranges.
- **Byte accounting.** The attempt table equals the receipt totals, and
  response bytes equal payload + 7 redirect bodies + 100,000 partial bytes +
  the 503 body.
- **Attempts.** T by outcome is REDIRECT 5 / SUCCESS 3 / INTERRUPTED 1 /
  HTTP_ERROR 1. The interrupted body is retained and manifest-bound.
- **Decoded M.** Rows equal an independent PyArrow read of the authored
  files.
- **Decoded T.** Documents are exactly the 7 selected locators with the
  authored texts. Statuses are 5 full, 1 oversize, 1 missing.
- **No unselected text exported.** The sentinel is absent from every output,
  receipt and log. It appears only inside the raw compressed payload/temp
  bytes, as the protocol states.
- **Output hashes.** Every manifest entry reproduces.
- **Parent.** The parent tree is unchanged.
- **Rerun.** A third run is COMPLETE with zero requests.

## 9. Verification

Environment: Windows 11 Pro 26200 AMD64, CPython 3.12.13, PyArrow 25.0.1,
the existing locked environment (`--offline --locked --no-sync`, extras
`cpu`, `eval`), with no dependency change or index access. Test settings:
`OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`, `TOKENIZERS_PARALLELISM=false`,
`-n 0`, `-p no:cacheprovider`. Sockets are patched to refuse in every test.
Logs and scripts are in
[`evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-IMPLEMENTATION/`](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-IMPLEMENTATION/)
(see `COMMANDS.md`).

| # | Command (prefix `uv run --offline --locked --no-sync --extra cpu --extra eval`) | Exit | Result |
|---|---|---|---|
| 1 | `python docs/…/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D/build_phase_d_freeze.py freeze` (twice) | 0 | digests of section 1; the rerun is byte-identical |
| 2 | `python -m pytest -n 0 tests/test_evidence_v41_phase_d.py` (measured) | 0 | **75 passed**; 19.56 s; peak working set 159,285,248 bytes |
| 3 | `python -m pytest -n 0` over all v4 + v4.1 + Phase-D test files (7 files) | 0 | **412 passed** (337 existing + 75 new) |
| 4 | `python -m pytest -n 0 tests/test_evidence_v3.py tests/test_evidence_v2_core.py tests/test_evidence_v2_text.py` | 0 | 69 passed |
| 5 | `ruff check` on 23 v4/v4.1/Phase-D files (11 src, 3 CLI, 2 support, 7 tests) | 0 | all checks passed |
| 6 | `ruff format --check` on the same 23 | 0 | already formatted |
| 7 | `mypy --strict` on the same 23 | 0 | no issues |
| 8 | `python scripts/evidence_v41_phase_d.py verify` | 0 | every binding and dry-plan range reproduces; root absent |
| 9 | `… show-plan` / `phase-d-status` | 0 / 0 | 55 operations; `NOT_STARTED`, root does not exist |
| 10 | `… phase-d --confirm-plan-digest <v4.1 Phase-P digest>` | 1 | refused, no root created |
| 11 | `… phase-d --confirm-plan-digest x --url …` | 2 | argparse rejects `--url` |
| 12 | `python docs/…/PHASE-D-IMPLEMENTATION/check_real_parent.py` | 0 | real parent binds; unchanged; no Phase-D root |

**Two existing v4.1 tests** (`test_verify_reproduces_every_v41_binding`,
`test_v41_root_is_fresh_and_distinct`) asserted that the v4.1 Phase-P root
does not exist. That was a pre-live precondition, and it is false since the
reviewed live Phase-P run. On this machine they fail on **pristine HEAD
`ed8efcf`** too: a temporary detached worktree importing HEAD code gave
2 failed, exit 1, and the worktree was then removed. This is the same kind
of issue `91be1a1` fixed for v4.0. They were updated to state-independent
assertions: `verify` must report the root's existence exactly, and the v4.1
root must stay distinct and frozen, with the offline refusal of it tested
unchanged by the next test. Row 3 includes them passing.

Resource measurement is limited to row 2. Production Phase-D performance,
live transfer time and live decode memory were not measured (NOT RUN).

## 10. Requirement ledger

| Requirement | Status |
|---|---|
| Starting HEAD `ed8efcf`; unrelated dirty/untracked files preserved | VERIFIED (only listed paths staged) |
| Dry-plan digest `ee82125d…0356` independently verified | VERIFIED (builder; `verify`) |
| COMPLETE Phase-P parent bound; refuse if different | IMPLEMENTED, VERIFIED (real root read-only; 4 tamper tests; drift-during-run test) |
| Scientific identity unchanged; no reselection | VERIFIED |
| Exact 8 M / 47 T operations equal the dry plan; injection refused | IMPLEMENTED, VERIFIED |
| M decode: two columns, 512/file, 4,096 total, dup/missing refused | IMPLEMENTED; VERIFIED synthetic (window sizes of the real plan verified structurally) |
| T decode: dictionary-inclusive, exact locator filter, 118, dup/missing refused, size rule | IMPLEMENTED; VERIFIED synthetic (real 118 locators verified structurally) |
| Unselected text never exported or logged | VERIFIED (sentinel scans of outputs, logs, stdout/stderr) |
| Blinding: no category, secret or review ID; sealed provenance | IMPLEMENTED, VERIFIED |
| Transport, B01, B02 unchanged | VERIFIED (337 existing tests; Phase-D B01 wire replay) |
| Restart: crash mid-M, mid-T, during decode; completed skipped; new attempt | VERIFIED |
| Caps: M 64 / 64 MiB, T 320 / 512 MiB, 4 MiB, 1 GiB root and outputs, 2 GiB free | VERIFIED (boundary ±1 each) |
| Outputs hash-bound; COMPLETE only when both arms are complete | VERIFIED |
| Synthetic E2E (redirect, retry, interruption, restart, completion) | VERIFIED |
| Two commits, protocol unchanged after commit 1 | VERIFIED (protocol blob `bb2bca6f…` unchanged) |
| Live Phase D | NOT RUN (requires narrow authorization review) |
| Real M/T decoding on actual bytes | NOT RUN (no acquisition authorized) |
| Full offline acceptance suite | NOT RUN (focused selection only, per test policy) |
| B01/B02 probe replay script (`probe_b01_b02.py`) | NOT RUN (the wire tests cover B01/B02) |
| Push | NOT RUN (not requested) |

## 11. Limitations

- Real Parquet decoding has only been proven on authored files. If PyArrow
  needs bytes outside the acquired ranges on the real files (for example
  old-writer padding), the sparse source refuses, the arm is INCOMPLETE, and
  the ranges are never widened. The retained bytes then allow a reviewed
  re-decode without refetching.
- Memory is not enforced. The expected peak is one T chunk (≤ ~23.7 MB) plus
  its ≤ 41.3 MB uncompressed text plus PyArrow overhead.
- Remote-header evidence follows the v4.1 receipt model. DNS time is
  unbounded (unchanged).
- `STATUS.md` has a new local notice and remains unstaged, because it carries
  pre-existing uncommitted notices from earlier sessions.

## 12. Next command — DO NOT RUN until authorized

    uv run --offline --locked --extra cpu --extra eval python scripts/evidence_v41_phase_d.py phase-d --confirm-plan-digest 23a26ffc87d27e4c8fa5de86d7ec446c260bfd65442dc5b1dfd2651cd62222b7

Exact next operator action: request the narrow Phase-D authorization review
of `d586938` (protocol/freeze) and the implementation commit. The reviewer
reruns `python scripts/evidence_v41_phase_d.py verify`,
`docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-IMPLEMENTATION/check_real_parent.py`
and the focused test command offline, and answers protocol section 15. Run
the command above only after that review passes, and check first that G: has
≥ 2 GiB free.

**READY FOR NARROW PHASE-D AUTHORIZATION REVIEW**
