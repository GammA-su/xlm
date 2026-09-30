# Batch-0 recovery: commands and results

2026-09-30, `F:/Project/xlm-data-ultrax`, branch `data/mix01-ultrax-6b`, starting
HEAD `327a0db546a1790f819bbadf3d4d4147c352dff4`. Windows 11 10.0.26200,
PowerShell 5.1, CPython **3.12.13**, PyArrow **25.0.1**. Existing locked
CPU/eval environment; no install or dependency change. CPU/CUDA installation
policy remains in `docs/runbooks/windows.md`; `.python-version`, `pyproject.toml`
and `uv.lock` are unchanged.

No external network or redownload. Authored offline transport regressions use
loopback HTTP. Real evidence is the existing local campaign/receipts and raw
Parquet, not live-source tests. No CUDA, training, full campaign or push.

Common abbreviation below:

```powershell
$U = @('run','--offline','--locked','--no-sync','--extra','cpu','--extra','eval')
$E = 'docs/implementation/evidence/ESSENTIAL-WEB-BATCH0-RECOVERY'
$env:OMP_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'; $env:TOKENIZERS_PARALLELISM='false'
```

## Real local evidence

```powershell
uv @U python scripts/essential_web_batch0_audit.py --output "$E/audit.json"
uv @U python scripts/essential_web_freeze_recovery.py
uv @U python scripts/essential_web_recovery_replay.py --work C:/XLM-scratch/ew-fast/recovery-proof-20260930 --output "$E/local-replay.json"
uv @U python scripts/essential_web_fast.py --data-root G:/XLM --scratch-root C:/XLM-scratch resume-check --batch 0 --output "$E/resume-check.json"
uv @U python scripts/essential_web_batch0_audit.py --verify-immutability "$E/audit.json" --output "$E/immutability.json"
```

All final invocations **exit 0**. Freeze was refreshed during development after
code/audit changes; it writes only repository evidence, never authorization.
Final digest: `51c09f0b15e6b19ebeb9854c6d65ec1994b473467b555723f0924606574c24db`.

- Audit: 31/32 sealed; all raw/canonical/summary/ledger hashes verify; scan of
  all 69,697 remaining rows; sole oversized row 58,327, 11,494,172 raw bytes.
  Both record encoders agree on every row. Practical adapter accepts that row.
  Final audit 38.718 s, sampled process RSS 231,383,040 bytes.
- Private replay: 69,697 rows, 24 malformed, 269/1,427/4,571 documents;
  all canonical hashes/counts and canonical document contracts reconcile.
  Socket connections were blocked. 47.5 s wall, 45.796875 s process CPU,
  sampled peak RSS 358,625,280 bytes; 63,352,737 output bytes in the named
  scratch directory. This is a diagnostic replay, not campaign progress.
- Resume: sealed=31, scheduled=1, already-sealed scheduled=0, network units=0,
  scheduled key `f00026`, recovery authorized=false. Same planner as execution.
- Immutability: 341 retained artifact size/mtime pairs unchanged, across 31
  seals. Resume had just rechecked their artifact hashes against the receipts.

The audit first exited **1** because the new diagnostic helper treated
`encode_record`'s bytes result as an object with `.raw_bytes`; the helper was
corrected to compare bytes. No production code or artifact was changed by that
failure. A first resume invocation tried dot-sourcing storage in the default
restricted PowerShell process; that failed and the CLI exited **1** for a
missing root, before scheduling. The successful command above supplies roots
explicitly. The documented operator command starts PowerShell with the same
per-process execution policy convention as the existing operator scripts.

## Authored tests

```powershell
uv @U python -m pytest tests/test_essential_web_recovery.py tests/test_essential_web_fast.py -n 0 -q --tb=short -p no:cacheprovider
uv @U python -m pytest tests/test_essential_web_recovery.py::test_large_valid_practical_record_keeps_canonical_semantics tests/test_essential_web_recovery.py::test_offline_run_refuses_missing_source_before_pipeline -n 0 -q -p no:cacheprovider --tb=short
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check_essential_web_recovery.ps1
```

Results: **0 / 0 / 0**. First successful two-module run: **51 passed, 41.08 s**;
two additional regressions: **2 passed, 2.24 s**. Final requested related
selection: **733 passed, 0 failed, 0 skipped, 285.22 s**. The exact 29 modules,
thread settings, `-n 0`, short Windows `--basetemp .br0` and log destination are
in [the runner](../../../../scripts/check_essential_web_recovery.ps1).
[Final log](regressions.log). This is a focused related selection, not the full
repository acceptance gate. No xdist controller or unbounded worker pool ran.

The 20 new parametrized cases cover exact/one-byte-over bounds, an 11,494,172-byte
record, a valid large practical canonical output, malformed refusal under the
larger bound, real authored 31/32 scheduling/execution with download forbidden,
sealed immutability and corruption refusal, a retained source with its interrupted
checkpoint, recovery code/digest authorization, offline refusal of missing raw,
0/partial/31-of-32/100% rendering, unknown totals, campaign target percentages,
TTY/non-TTY rendering and event permanence, insufficient/rate/stall/retry/complete
ETA behavior. Existing tests cover interrupted partial transfers and two real
worker processes. TTY tests use a simulated terminal stream; no interactive
production terminal or production restart was exercised.

Development failures before that pass: collection exit **2** from importing
`RecordLimitError` from the wrong module; then **49 passed, 1 failed, 1 error**
(exit **1**) from a missing imported fixture and the expected committed-code
freeze refusal before the amendment existed. The dedicated 31/32 execution
test then passed **1/1 in 7.19 s**. Assertions were not weakened; no skips,
xfails or retry-until-green were added. Initial default pytest cache writes
were denied, so successful final checks disable the cache provider.

## Static checks

```powershell
$S = @(
 'scripts/essential_web_batch0_audit.py', 'scripts/essential_web_freeze_recovery.py',
 'scripts/essential_web_recovery_replay.py', 'scripts/essential_web_fast.py',
 'src/xlm/data/sources/essential_web_recovery.py',
 'src/xlm/data/sources/essential_web_progress.py',
 'src/xlm/data/sources/essential_web_monitor.py',
 'src/xlm/data/sources/essential_web_local.py',
 'src/xlm/data/acquisition/source_parquet.py', 'tests/test_essential_web_recovery.py'
)
uv run --offline --locked --no-sync ruff check @S
uv run --offline --locked --no-sync ruff format --check @S
uv run --offline --locked --no-sync mypy --strict @S
$errors=$null; $tokens=$null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path scripts/operator_essential_web_fast.ps1), [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count -gt 0) { throw 'PowerShell parse failed' }
git diff --check
```

Final exits **0 / 0 / 0 / 0 / 0**, ten Python files checked. Ruff import/line
formatting and strict type issues in development helpers/tests were repaired.
One final test import-spacing fix was made after the 733-test pass; no executable
logic changed. A mixed-CRLF documentation edit was normalized back to LF before
the final diff check. Mypy notes an unused existing `lm_eval` override only.

## Artifacts and work left

Permanent repository evidence: `audit.json`, `local-replay.json`, `recovery.json`,
`resume-check.json`, `immutability.json`, this command record and the regression
log. No corpus text in Git. Private real replay output remains at the scratch
path above, counted by the campaign's recursive scratch budget. Failed original
production staging and the original source/scratch checkpoint were preserved.
The final log was normalized to UTF-8/LF without changing its content. The
authored test directory `.br0` remains untracked: automatic approval review
rejected its cleanup as "blocked by policy"; no alternative deletion was tried.

**NOT RUN:** operator recovery authorization, production resume/seal, live
provider requests, full repository acceptance, CUDA. Original campaign, batch
record, plan and authorization remain unchanged. The next operator command and
its required explicit authorization are in the
[recovery report](../../reports/ESSENTIAL-WEB-BATCH0-RECOVERY.md#operator-command).
