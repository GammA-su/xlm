# FinePDFs b2 scratch accounting investigation (2026-10-01)

**FINEPDFS SCRATCH ACCOUNTING STILL BLOCKED.** The requested offline
reproduction succeeded, but the user's Task 5 stop condition applies:
the scratch root already exceeds the authorized cap even when the complete
adopted input requires zero new source bytes. No implementation or successful
benchmark retry is claimed. No files were removed to make the budget fit.

**B2 AUTHORIZATION REMAINS VALID.** This investigation changes no executable
code, plan, authorization, admission, adapter, source pin, membership or limit.

## Proven arithmetic

The budget root is `C:\XLM-scratch\finepdfs`, not just `bench-b2`.
`ScratchBudget.occupied()` counts the logical size of every regular file below
that root, including each hard-link path and abandoned staging files.

| Quantity | Exact bytes | Computation/source |
| --- | ---: | --- |
| Authorized scratch cap | 5,542,772,736 | b2 `limits.scratch_cap_bytes` |
| Authorized per-file bound | 5,542,772,736 | b2 `limits.max_file_bytes` |
| Current scratch usage | 5,678,988,129 | `ScratchBudget.occupied()` |
| Candidate adopted input | 2,771,021,138 | `f00000.parquet.part.stat().st_size` |
| Existing reservations | 0 | `ScratchBudget.reserved()` |
| Foreign/unowned usage | 2,907,966,991 | occupied minus candidate |
| Requested candidate total reservation | 5,542,772,736 | `run_pipeline` passes `limits.max_file_bytes` |
| Implied additional source growth | 2,771,751,598 | requested total minus existing candidate |
| Free logical capacity | -136,215,393 | cap minus occupied |
| Maximum reservable candidate total | 2,634,805,745 | cap minus foreign usage |
| Pre-fix projected occupancy | 8,450,739,727 | foreign + reservations + requested total |
| Corrected source-only projected occupancy | 5,678,988,129 | foreign + actual complete input |
| Excess before creating any processing output | 136,215,393 | occupied minus cap |
| Physical volume minimum free reserve | 34,359,738,368 | b2 `scratch_min_free_bytes` |

The reproduction dashboard reported physical free space of 851,311,640,576 B.
Physical free space is not the failing condition: the logical-cap comparison
returns false first. There is **no separate processing/temp reservation** at
this scheduler call. Benchmark processing writes documents, rejection data,
summary and progress under scratch; those costs cannot be treated as zero.
The source-only lower bound already proves this run cannot fit the current
root under its current contract, without needing an output estimate.

Exact root inventory (metadata only; no corpus text read for display):

| Relative path | Bytes |
| --- | ---: |
| `bench-b1/f00000.parquet.part` | 2,771,021,138 |
| `bench-b1/f00000.state.json` | 896 |
| `bench-b1/staging/f00000/c80d20f5d270/documents.jsonl` | 136,943,760 |
| `bench-b1/staging/f00000.progress.json` | 99 |
| `bench-b1/staging/f00000.progress.lock` | 0 |
| `bench-b2/f00000.parquet.part` | 2,771,021,138 |
| `bench-b2/f00000.state.json` | 1,098 |

## Exact failing path and reuse invariant

`source_benchmark.run_benchmark` constructs an `ObservedScratch` over the
whole source scratch root. `essential_web_local.run_pipeline` calls
`scratch.reserve(unit.key, limits.max_file_bytes, unit.partial)` at line 644.
`ScratchBudget.reserve` subtracts the existing candidate size before testing
`foreign + reserved + amount > cap_bytes`. It returns false. With no held
units, `run_pipeline` constructs `ScratchCapError("scratch cap leaves no room
for one file")` at line 646 and raises it at line 709.

Thus the hypothesis needs qualification: existing candidate bytes are not
simply added to a second full reservation. The scheduler requests the maximum
final source size, needlessly allowing 2,771,751,598 B of source growth for a
verified complete input. Separately, retained b1 work makes even the corrected
zero-growth calculation fail.

The benchmark does not call `source_run.classify` before scheduling.
`prepare_units(durable=False)` supplies a URL-bearing unit; the complete-cache
path in `_Download.run` occurs only after reservation and stream submission.
The read-only classifier already reports `local_complete_reuse=1`, with
`sealed_skip=0`, `local_processing_retry=0`, `resumable_partial=0` and
`fresh_download=0`. Both known and worst-case required network bytes are **0**.
No after-fix classification exists because implementation stopped.

Correct invariant for a future narrow repair: existing logical occupancy plus
all safely bounded **new** source growth and processing/metadata growth must
fit the scratch cap; physical free space minus outstanding growth must retain
the minimum free reserve. Existing bytes are never exempted from occupancy.

| Restart class | Required behavior |
| --- | --- |
| `sealed_skip` | No new work or reservation; retained scratch leftovers still count. |
| `local_complete_reuse` | Verify state, source identity and hashes before using actual final length; zero new source growth; account for output and metadata growth. |
| `local_processing_retry` | Verified durable source needs no scratch source copy; account for processing scratch and all existing leftovers. |
| `resumable_partial` | A validated prefix and bound total permit remaining-growth reservation; unverified tails still count until safely truncated. Preserve drift checks and safe restart-in-place behavior. |
| `fresh_download` | Reserve conservative full-file growth, plus required overhead. |

`Path.samefile` confirmed b1 and b2 inputs are hard links to the same file.
Hard-linking did not cause the scheduler to choose the wrong reservation;
the same defect applies to a physical copy. Exempting hard-linked paths from
the logical budget would change the accounting contract and would not solve
the general copy case. No such exemption was made.

## Identity and history

The adopted bytes were rehashed offline and reproduce
`4eeb58bc769a194f472100ed4acd1d960e35c669caecd96fd4ce6425fabca38d`.
The complete state has matching SHA-256, prefix SHA-256, repository-declared
linked ETag, length, verified bytes, linked size, name, canonical URL, version
and resolved revision. Inspection used a socket-connect refusal guard.

- Benchmark digest: `5a9080d424b18b39f10fd81325515ec8a870e9b81984f4e7a0f40bf4839bd298`.
- Plan hash: `25bcb8ae9121d1744d945d9a9a687f918a28b1ce54cb9a6945c0aaa277f5bffb`.
- Adoption digest: `373603d1364652d8310332fa52355e6b50903ed8a6d286dfbdaa67e591c44cfb`.

`check_digest`, `load_acquisition_plan`, `validate_plan_authorization`, and
`source_benchmark._minted` all verified the existing identities. The exact CLI
reproduction passed its existing admission gate before failing on scratch.
The benchmark binds its data record and limits; `compute_behavioral_hash`
binds acquisition behavior/limits, not the scheduler's executable code.
The admission bridge separately binds adapter code, which is untouched.
An accounting-only scheduler correction would not itself require new b2
authorization under these identity contracts. This is not authorization to
alter limits or discard retained work.

FinePDFs record/parser limits remain 33,554,432 B; UltraX's generic record
limit remains 8,388,608 B. All other authorized ceilings are unchanged.

b1's failed RecordLimitError receipt and retained work remain. b2's original
`performance-00.json`, plan, authorization and adoption remain. Reproduction
appended the normal events and created **`performance-01.json`**, digest
`93e67aa02ed4e546827c1d6f619ea8d01250f08c58fd32c02b4adf8007253ef6`, at
`G:\XLM\plans\finepdfs\benchmarks\b2`. It records ScratchCapError,
0 requests, 0 transferred bytes, 0 processed rows and 0 documents. The monitor
took 0 resource samples before immediate failure; there are no whole-file
throughput, CPU or peak-memory measurements to report.

## Commands, environment and requirement ledger

Windows 11 build 26200, Python **3.12.13**, existing locked CPU/eval uv
environment with `--no-sync`; no installation. Dependency files and CPU/CUDA
policy unchanged. Set for all Python commands:

```powershell
$env:UV_OFFLINE='1'
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:XLM_DATA_ROOT='G:\XLM'
$env:XLM_HOME='G:\XLM\xlm-home'
$env:XLM_SCRATCH_ROOT='C:\XLM-scratch'
$env:PYTHONUTF8='1'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark run --source-key finepdfs --label b2
```

Exit **1**, exact requested ScratchCapError reproduced. No download was
submitted: the rejection occurs before `streams.submit(download_source, ...)`.
An initial attempt to dot-source `scripts/operator_storage.ps1` was blocked
by PowerShell execution policy, and the CLI refused missing scratch roots
(exit 1). Setting the configured roots explicitly produced the reproduction;
execution policy was not changed.

The read-only Python inspection ran via a PowerShell here-string piped to
`uv run --offline --locked --no-sync --extra cpu --extra eval python -`:
exit **0**, 4.75 s. It rehashed the real local input, checked identities and
classification, and called `ScratchBudget.reserve` on a fresh in-memory
budget with both the original maximum and the actual complete-file size.
**Both returned false.** No source state or bytes were changed by inspection.
These are real retained-artifact checks, not synthetic tests or live probes.

`git diff --check`: exit **0**. The pre-existing status changes (101 added
lines) remain; this session adds 14 status lines. The UltraX command in
`MIX01-HIGH-THROUGHPUT-ACQUISITION.md` still has
`--max-input-bytes 134217728`; that historical report was not edited.

| Requirement | Status | Evidence/limit |
| --- | --- | --- |
| Offline reproduction and precise arithmetic | VERIFIED | CLI failure plus actual root inventory and both reservation calculations |
| Existing bytes and authorization identity | VERIFIED | Hash/state checks, plan reconstruction and actual gate execution |
| Restart class / required network | VERIFIED | One complete local reuse; zero required network bytes |
| Generic implementation | BLOCKED | Task 5 requires stopping when corrected reuse cannot fit |
| Code fix | NOT RUN | No implementation claimed; no new IMPLEMENTED path |
| Real successful b2 retry / whole-file metrics | NOT RUN | Scratch cap already exceeded before output |
| New focused tests / requested related suites | NOT RUN | Stopped before repair; no test pass claimed |
| Ruff / format / strict mypy | NOT RUN | No executable edits; Task 5 stop, not an observed mypy application-control result |
| Range benchmark, production, C05, tokenizer, training, network, push | OUT OF SCOPE | None executed |

Only this report, an additive status entry and the relevant runbook note are
changed. Pre-existing dirty status content and all untracked user work are
preserved. No commit: the conditional instruction was to commit a sound fix,
and no fix was implemented. HEAD remains
`9bf6f42657f3cfa850db773fe4ca151858b2683f` on `data/mix01-ultrax-6b`.

Next operator command (review only; do not retry b2 or run the range half):

```powershell
Get-Content -LiteralPath docs/implementation/reports/FINEPDFS-SCRATCH-ACCOUNTING.md
```

Next prompt: decide how to preserve retained benchmark history while making
the entire scratch footprint, including new processing outputs, fit the
unchanged cap; then resume the generic reservation repair and focused tests.
No migration, cleanup, physical-accounting redesign or new plan is authorized
or performed by this report. FinePDFs is **not ready** for the range benchmark.
