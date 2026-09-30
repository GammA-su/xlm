# Recovery scope fix: offline execution evidence

Date: 2026-09-30. Repository: `F:/Project/xlm-data-ultrax`, branch
`data/mix01-ultrax-6b`, starting commit
`5c775cb7087e178e7ebfc92604eda06f1b1b48ed`.
Environment: Windows 11, PowerShell, Python 3.12.13, existing uv-locked
CPU/eval environment. No dependency installation, external network,
production execution, redownload or push. Test transport uses authored
loopback HTTP fixtures only; it does not establish live-provider compatibility.

## Authoritative audits and code binding

Commands below ran from the repository root, each with exit **0**.
The before audit preceded the code edit; the after audit followed the fix.
Neither decodes source rows or writes operator data.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/essential_web_scope_audit.py --output docs/implementation/evidence/ESSENTIAL-WEB-RECOVERY-SCOPE/before.json
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/essential_web_scope_freeze.py
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/essential_web_scope_audit.py --output docs/implementation/evidence/ESSENTIAL-WEB-RECOVERY-SCOPE/after.json --compare docs/implementation/evidence/ESSENTIAL-WEB-RECOVERY-SCOPE/before.json
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/essential_web_fast.py --data-root G:/XLM --scratch-root C:/XLM-scratch resume-check --batch 1 --output docs/implementation/evidence/ESSENTIAL-WEB-RECOVERY-SCOPE/batch1-resume.json
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/essential_web_fast.py --data-root G:/XLM --scratch-root C:/XLM-scratch gate --batch 1
```

Before: **23.609 s**. After: **23.218 s**. Comparison reports
`all_operator_artifacts_unchanged=true`: 398 files, 10,782,777,731 bytes,
identical SHA-256, size and mtime. Batch 0: 32/32 sealed, 2,604,815 rows;
Batch 1: zero raw/canonical/sealed/staging/scratch files, no scratch directory,
valid existing plan and authorization. Snapshot evidence is in `before.json`
and `after.json` beside this file. Hashing uses bounded chunks; peak RSS and
physical disk I/O were not measured. No corpus output was generated.

The freeze writes only `code-compatibility.json` in this evidence directory.
It does not rewrite the original recovery amendment or operator authorization.
Its digest is
`94ab7cdbebea1dc20af2c875d9774d4d5655adf0743cca12379c7ffa4c47fab6`.
The dry resume schedules f00032 through f00063, with no already-sealed unit
scheduled, `recovery_digest=null`; its 32 `network_units` are future work,
not requests performed. Gate: **RUN**.

Stored Batch-1 C04 admission, exit **0**, output
`Batch-1 current stored C04 admission: valid (offline)`:

```powershell
$env:XLM_HOME='G:\XLM\xlm-home'
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
@'
import sys
from pathlib import Path
sys.path.insert(0, 'scripts')
import essential_web_fast as driver
from xlm.data.acquisition.plan import load_acquisition_plan
plan = load_acquisition_plan(Path('G:/XLM/plans/ew-fast/b0001/batch.plan.json'))
driver.check_admission(plan)
print('Batch-1 current stored C04 admission: valid (offline)')
'@ | uv run --offline --locked --no-sync --extra cpu --extra eval python -
```

The original failed Batch-1 call's zero-network conclusion also uses code
inspection: its exception precedes admission, scratch creation and executor /
pipeline entry. Store absence is corroborating evidence, not a packet capture
or a claim about unrelated processes.

## Requested regression selection

Set numerical libraries to one thread and use one serial pytest controller:

```powershell
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
$checks='essential_web_recovery_scope essential_web_recovery essential_web_fast essential_web_calibration essential_web_bulk essential_web_readiness essential_web_production_selector essential_web_fasttrack_freeze essential_web_selector_sweep essential_web_bootstrap essential_web_live_certification source_admission mix01_inventory mix01_quotas_6b mix01_views acquisition_plan acquisition_verifier acquisition_bounds acquisition_fetcher acquisition_leases hf_range_transport production_ingest rowgroup_sampling adapt_rejections exclusion_receipt exclusion_benchmark parquet_window_nested parquet_window_sampling selected_record_concurrency calibration_adopt'.Split(' ')
$testPaths=@($checks | ForEach-Object { "tests/test_$_.py" })
$log=Join-Path (Get-Location) 'docs/implementation/evidence/ESSENTIAL-WEB-RECOVERY-SCOPE/regressions.log'
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest @testPaths -n 0 -q -p no:cacheprovider --basetemp .bscope --tb=short *> $log
$testExit=$LASTEXITCODE
$logText=[IO.File]::ReadAllText($log).Replace("`r`n","`n")
[IO.File]::WriteAllText($log,$logText,(New-Object Text.UTF8Encoding($false)))
Get-Content -LiteralPath $log -Tail 15
exit $testExit
```

Exit **0**, **744 passed in 288.16 s**, zero skipped. See `regressions.log`.
This includes fast/dashboard, recovery, campaign/resume, acquisition and
selector regressions. The 11 new scope cases are authored synthetic fixtures.
Test scratch is isolated from operator data. This is not full acceptance.

Earlier focused feedback commands, both exit **0**:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_essential_web_recovery_scope.py tests/test_essential_web_recovery.py tests/test_essential_web_fast.py -n 0 -q -p no:cacheprovider --tb=short
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_essential_web_recovery_scope.py::test_exact_recovery_still_requires_approval_and_verified_content -n 0 -q -p no:cacheprovider --tb=short
```

Results: **63 passed in 55.07 s**, then the newly added approval/content case
**1 passed in 2.10 s**. Both are covered by the final 744-test selection.

## Static checks

Each final check below exited **0** on the five changed/new Python files:

```powershell
$files=@('scripts/essential_web_fast.py','src/xlm/data/sources/essential_web_recovery.py','scripts/essential_web_scope_audit.py','scripts/essential_web_scope_freeze.py','tests/test_essential_web_recovery_scope.py')
uv run --offline --locked --no-sync --extra cpu --extra eval ruff check @files
uv run --offline --locked --no-sync --extra cpu --extra eval ruff format --check @files
uv run --offline --locked --no-sync --extra cpu --extra eval mypy --strict @files
git diff --check
```

Ruff: all checks passed; formatter: five files already formatted; strict mypy:
no issues in five source files (existing unused `lm_eval` override note).
An initial pre-format ruff check reported line-length issues, corrected by the
formatter before these final checks; no assertion or test was weakened.

## Not run

Real Batch 1, external provider checks, full repository acceptance, CUDA and
the full research campaign: **NOT RUN**. Selector, mixture, quota and dashboard
changes: **OUT OF SCOPE**. No production performance inference from fixtures.
Next operator command, intentionally not executed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command '. .\scripts\operator_storage.ps1; .\scripts\operator_essential_web_fast.ps1 -Batch 1 -Stage Run'
```
