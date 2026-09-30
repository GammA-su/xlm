#Requires -Version 5.1
# Offline only: reuse the acquired probe; no plan, fetch, probe or calibration.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
Push-Location -LiteralPath $repo
try {
    . ./scripts/operator_storage.ps1
    $env:HF_HUB_OFFLINE = '1'
    $env:HF_DATASETS_OFFLINE = '1'
    $env:UV_OFFLINE = '1'
    $U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')
    $R = Join-Path $env:XLM_DATA_ROOT 'calib/essential-web-production/probe'
    $plan = Join-Path $R 'probe-00.plan.json'
    $unit = Join-Path $R 'probe-00'
    $raw = Join-Path $unit 'raw/selected_records.jsonl'
    $measurement = Join-Path $R 'measurement.json'
    if (-not (Test-Path -LiteralPath $plan -PathType Leaf)) { throw 'Existing plan missing' }
    if (-not (Test-Path -LiteralPath $raw -PathType Leaf)) { throw 'Existing raw probe missing' }
    $storedPlan = Get-Content -LiteralPath $plan -Raw | ConvertFrom-Json
    if ($storedPlan.plan_hash -ne '04db5c763959ebf7187ba69d23a4f99b5d4e2e77c2f07bc4350e4b427f52b04c') {
        throw 'Existing probe plan identity differs from the reviewed plan'
    }
    if ((Get-Item -LiteralPath $raw).Length -ne 2793802 -or
        (Get-FileHash -LiteralPath $raw -Algorithm SHA256).Hash -ne
        'a1c2b8078b662af59d0a4f56a131710b8654c6ab5e9103d4d4760cb462b0a9f5') {
        throw 'Existing raw probe differs from the reviewed 256-row acquisition'
    }
    if (Test-Path -LiteralPath $measurement) {
        throw 'measurement.json already exists; inspect it instead of overwriting completed evidence'
    }
    # --no-publish avoids a new timestamped immutable verification publication.
    uv @U xlm data verify --plan $plan --output-dir "$unit/raw" --scratch-dir "$unit/scratch" --no-publish --json
    if ($LASTEXITCODE -ne 0) { throw 'Existing raw verification failed' }
    foreach ($view in @('essential_science', 'essential_practical', 'essential_prose')) {
        $out = Join-Path $unit $view
        uv @U python scripts/calibration_adopt.py adapt --plan $plan --output-dir $out
        $adoption = $LASTEXITCODE
        if ($adoption -eq 2) { continue }
        if ($adoption -ne 0) { throw "Incomplete or incompatible adaptation: $view; preserved for review" }
        # Failed staged adaptation left an empty directory on this probe. Keep it.
        # Refuse unknown partial files; never erase them or overwrite a manifest.
        if ((Test-Path -LiteralPath $out) -and
            @(Get-ChildItem -LiteralPath $out -Force).Count -ne 0) {
            throw "Nonempty partial output: $out; preserved for review"
        }
        uv @U xlm data adapt --plan $plan --adapter essential_web_bnormal --adapter-config $view --input $raw --output-dir $out --on-reject record --max-input-bytes 268435456
        if ($LASTEXITCODE -ne 0) { throw "Adaptation failed: $view" }
    }
    uv @U xlm data status --plan $plan --scratch-dir "$unit/scratch" --json
    if ($LASTEXITCODE -ne 0) { throw 'Status failed' }
    # Standard paths retained; measurement rechecks plan/raw/canonical hashes and components.
    uv @U python scripts/essential_web_measure.py --root $R --freeze $PSScriptRoot --stage probe --output $measurement
    if ($LASTEXITCODE -ne 0) { throw 'Measurement failed' }
    Get-Content -LiteralPath $measurement -Raw
} finally {
    Pop-Location
}
