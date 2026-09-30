#Requires -Version 5.1
# Focused offline recovery regressions, not the full repository acceptance gate.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$env:OMP_NUM_THREADS = '1'; $env:MKL_NUM_THREADS = '1'; $env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'; $env:TOKENIZERS_PARALLELISM = 'false'
$env:UV_OFFLINE = '1'; $env:HF_HUB_OFFLINE = '1'; $env:HF_DATASETS_OFFLINE = '1'
$checks = @(
    'test_essential_web_recovery', 'test_essential_web_fast', 'test_essential_web_calibration',
    'test_essential_web_bulk', 'test_essential_web_readiness', 'test_essential_web_production_selector',
    'test_essential_web_fasttrack_freeze', 'test_essential_web_selector_sweep',
    'test_essential_web_bootstrap', 'test_essential_web_live_certification', 'test_source_admission',
    'test_mix01_inventory', 'test_mix01_quotas_6b', 'test_mix01_views', 'test_acquisition_plan',
    'test_acquisition_verifier', 'test_acquisition_bounds', 'test_acquisition_fetcher',
    'test_acquisition_leases', 'test_hf_range_transport', 'test_production_ingest',
    'test_rowgroup_sampling', 'test_adapt_rejections', 'test_exclusion_receipt',
    'test_exclusion_benchmark', 'test_parquet_window_nested', 'test_parquet_window_sampling',
    'test_selected_record_concurrency', 'test_calibration_adopt'
)
$testPaths = @($checks | ForEach-Object { "tests/$_.py" })
$log = 'docs/implementation/evidence/ESSENTIAL-WEB-BATCH0-RECOVERY/regressions.log'
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest @testPaths -n 0 -q -p no:cacheprovider --basetemp .br0 --tb=short *> $log
$code = $LASTEXITCODE
$logText = [IO.File]::ReadAllText((Join-Path (Get-Location) $log)).Replace("`r`n", "`n")
[IO.File]::WriteAllText((Join-Path (Get-Location) $log), $logText, (New-Object Text.UTF8Encoding($false)))
Get-Content -LiteralPath $log -Tail 15
"Focused recovery regressions exit=$code"
exit $code
