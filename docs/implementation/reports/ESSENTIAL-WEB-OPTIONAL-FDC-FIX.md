# Essential-Web optional FDC hierarchy compatibility repair — 2026-09-30

**ESSENTIAL-WEB ADAPTER FIXED — READY TO RESUME PROBE OFFLINE**

Starting HEAD `c643f2ef0b7a7e5f4e53fddc6d65de0dfbb26d9a`, branch
`data/mix01-ultrax-6b`. This repairs one renderer assumption. The frozen selector,
render-before-policy ordering, FDC primary code, mixture and malformed thresholds
are unchanged. Existing production admission was already recorded by the operator
(user-provided context); this task neither re-admits nor acquires anything.

## Live evidence, read offline

Input: `G:\XLM\calib\essential-web-production\probe\probe-00\raw\selected_records.jsonl`.
The existing acquisition verifier passes with `--no-publish`: 256 records,
2,793,802 bytes, SHA-256
`a1c2b8078b662af59d0a4f56a131710b8654c6ab5e9103d4d4760cb462b0a9f5`.
Plan hash: `04db5c763959ebf7187ba69d23a4f99b5d4e2e77c2f07bc4350e4b427f52b04c`.
This is local integrity verification against the acquisition journal, not an
independent publisher checksum or new admission decision. No document text was
printed or copied into repository evidence.

The replay uses bounded binary JSONL iteration, never `str.splitlines()`. The
document text includes one U+0085 and four U+2028 separators; they do not create
additional JSONL records.

| Scope | Level | Absent | Null | Empty string | Whitespace only | Nonempty string | Nonstring |
|---|---|---:|---:|---:|---:|---:|---:|
| All 256 | level_1 | 0 | 0 | 0 | 0 | 256 | 0 |
| All 256 | level_2 | 0 | 0 | 42 | 0 | 214 | 0 |
| All 256 | level_3 | 0 | 0 | 70 | 0 | 186 | 0 |
| Selected 31 | level_1 | 0 | 0 | 0 | 0 | 31 | 0 |
| Selected 31 | level_2 | 0 | 0 | 5 | 0 | 26 | 0 |
| Selected 31 | level_3 | 0 | 0 | 9 | 0 | 22 | 0 |

Empty strings explain **all 108** original base-render failures: 42 first fail at
level_2 and 66 at level_3. Four rows have both empty. Among the selected rows,
five first fail at level_2 (prose 3, practical 2), and eight at level_3 (prose 6,
practical 2); one selected row has both empty. Every failure is `MissingFieldError`:
`adapter 'essential_web' requires upstream field 'level_2' to be a non-empty string when present.`
or the identical reason naming `level_3`. There is no other cause.

## Repair and results

`_optional_fdc_taxonomy_label` is used only for the three optional FDC hierarchy
levels. Missing, null, empty and whitespace-only values omit that metadata key.
Null/missing already had omission semantics in the old helper; no null was observed
in this sample. A nonstring still raises `MissingFieldError`. Nonblank strings
remain verbatim, including surrounding whitespace. The general optional-string
helper and other classifiers remain strict. No hierarchy label is synthesized.

| Renderer | Before | After |
|---|---:|---:|
| Selected prose | 17/26 | 26/26 |
| Selected practical | 1/5 | 5/5 |
| All base rows rendered | 148/256 | 256/256 |
| All base rows malformed | 108/256 | 0/256 |

All three real production adapters were replayed over all 256 existing rows:

| Pass | Accepted | Policy rejected | Unassigned | Other component | Malformed | Other exception |
|---|---:|---:|---:|---:|---:|---:|
| essential_science | 0 | 223 | 2 | 31 | 0 | 0 |
| essential_practical | 5 | 223 | 2 | 26 | 0 | 0 |
| essential_prose | 26 | 223 | 2 | 5 | 0 | 0 |

Before the fix each production pass had 108 malformed rows. Unique row-level
B-normal totals remain exactly rejected 223, prose 26, practical 5, unassigned 2,
science 0; validity-stage failures remain zero. The loader verifies the unchanged
frozen evaluator/policy identities. Canonical text equals upstream text for every
successful base and production rendering. This 256-row prefix is a partial live
compatibility diagnostic, not a corpus-wide claim or calibration result.

## Validation and environment

Windows, Python 3.12.13, uv 0.12.19, existing locked CPU/eval environment.
`pyproject.toml`, `uv.lock`, `.python-version` and CPU/CUDA installation policy
are unchanged. All Python/tool commands use
`uv run --offline --locked --no-sync --extra cpu --extra eval` (`$U` below).
Tests set `HF_HUB_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`,
`OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=NUMEXPR_NUM_THREADS=1`,
`TOKENIZERS_PARALLELISM=false`.

The 32 new authored regression cases cover all three levels, absent/null/empty/
whitespace/nonempty values, five nonstring types, verbatim text, unchanged selector
and code, the live-shaped empty-level fixture, and continued strictness/order for
other metadata. Synthetic tests do not constitute live evidence. Separate stored
certification tests use three historical real rows; the new replay uses the actual
256-row probe. Acquisition tests use authored local loopback HTTP fixtures; no
external source/network service was contacted. No source fetch, probe, calibration,
bulk acquisition, dependency installation or push ran.

| Check | Exit | Result |
|---|---:|---|
| Before/after bounded offline replay | 0 / 0 | Exact counts above; socket connection/DNS/sendto audit guards enabled |
| Existing raw `data verify --no-publish --json` via data app | 0 | Local journal/plan/raw integrity passes; socket audit guard enabled |
| 14-file focused selection, one xdist controller, 16 workers | 1 | 372 passed, two known Windows long-path failures in acquisition tests |
| Exact two failing nodes with shorter `.bf` path, `-n 0`, plus stored certification suite | 0 | 11 passed in 15.98 s; no assertion/code changes to those tests |
| Ruff check, format check, strict mypy (three changed Python files) | 0 / 0 / 0 | Clean; mypy reports an unused pre-existing lm_eval override note |
| PowerShell parser, resume script | 0 | Syntax valid; script execution NOT RUN |

The initial xdist run took 14.75 s; its `.bt-fdc/popen-gw*` paths still exceeded
Windows journal temporary-file path capacity. Removing the worker/path overhead
addresses the observed environment failure; the original failed run remains in
evidence. Both runs emitted a nonfatal pre-existing pytest cache permission warning.
Combined coverage: **383 distinct tests passed across the focused runs**, including
all 374 initially selected tests and nine stored-row certification tests. This is
not a full-suite pass. Fast/full acceptance, CUDA and external network tests were
NOT RUN. No unrelated repair or threshold relaxation was made.

Exact commands and evidence: [COMMANDS.md](../evidence/ESSENTIAL-WEB-OPTIONAL-FDC-FIX/COMMANDS.md),
[before.json](../evidence/ESSENTIAL-WEB-OPTIONAL-FDC-FIX/before.json),
[after.json](../evidence/ESSENTIAL-WEB-OPTIONAL-FDC-FIX/after.json),
[verification.json](../evidence/ESSENTIAL-WEB-OPTIONAL-FDC-FIX/verification.json).

Replay resource measurements: before 0.226 s, peak working set 72,351,744 bytes;
after 0.246 s, peak working set 72,941,568 bytes. These measure the local diagnostic
process, not production throughput. Raw is 2,793,802 bytes and unchanged. The
receipt's historical 9,511,581 transferred bytes and 105 requests belong to the
already completed acquisition; this task incurred no additional source transfer.
Broader peak disk/test-worker memory and production adaptation performance were
not measured; no extrapolated capacity claim is made.

## Resume and requirement ledger

The raw acquisition is reusable. The failed science adaptation left an **empty**
output directory; practical/prose output paths and measurement.json are absent.
The existing staged writer accepts that empty directory. No fresh output identity,
deletion, relocation or measurement-path change is necessary. The resume script
checks the exact plan/raw identity, locally verifies without republishing, uses
existing `calibration_adopt.py adapt` checks to reuse completed output, refuses
nonempty incomplete output, performs the three adaptations, reads status, and
produces `G:\XLM\calib\essential-web-production\probe\measurement.json`. It refuses
to overwrite an existing measurement. The standard measurement implementation
revalidates plan/journal/raw/canonical identities and each retained component.

The script was **not executed** in this task. Only read-only raw verification and
in-memory real adapter replay ran. Production canonical artifacts and measurement
remain NOT RUN, ready for the operator. The script contains no fetch or network
command; offline flags stay enabled. All original acquisition evidence is retained.

| Requirement | Status |
|---|---|
| Live shape census, exact failure cause | VERIFIED |
| Scoped optional-label repair | IMPLEMENTED, VERIFIED |
| Frozen selector, code, text and ordering preserved | VERIFIED |
| All-256 base and three selected-adapter replays | VERIFIED |
| Focused adapter/readiness/acquisition/C04/C05 tests | VERIFIED across recorded runs |
| Offline resume script | IMPLEMENTED; parser VERIFIED; execution NOT RUN |
| Production adaptation/measurement artifacts | NOT RUN; next operator command |
| Final-pool C05 screening / official benchmark claims | BLOCKED pending existing C05 obligations |
| Calibration, full campaign, selector/mixture/threshold changes, push | OUT OF SCOPE, NOT RUN |

From `F:\Project\xlm-data-ultrax`, the exact next command is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\docs\implementation\evidence\ESSENTIAL-WEB-PRODUCTION-READINESS\resume-probe-adaptation.ps1
```

Changed files: renderer; production-selector regression tests; text-free replay
script, before/after/verification evidence and test logs; resume PowerShell script;
this report, COMMANDS.md, STATUS.md, P13.md, production-readiness report and Windows
runbook. Pre-existing user edits/untracked reviews are preserved and excluded from
the repair commit. No push.

Automatic approval review blocked cleanup of this task's `.bf` and `.bt-fdc`
scratch directories; they remain untracked and are excluded from the commit.
