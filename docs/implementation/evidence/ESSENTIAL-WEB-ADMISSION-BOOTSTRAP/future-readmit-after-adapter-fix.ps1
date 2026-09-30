#Requires -Version 5.1
# Superseding operator admission after adapter fix 5fb37c9: offline, no fetch or probe.
# Publishes the next decision attempt; attempt 1 and its record stay untouched.
param([Parameter(Mandatory = $true)][string]$Operator)
$ErrorActionPreference = 'Stop'
if (-not $env:XLM_DATA_ROOT) { throw 'Dot-source scripts/operator_storage.ps1 first' }
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:UV_OFFLINE = '1'
$B = Join-Path (Get-Location) 'docs/implementation/evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP'
$U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')
$R = Join-Path $env:XLM_DATA_ROOT 'calib/essential-web-production/schema-probe'
$record = Join-Path $R 'admission-record.attempt02.json'
if (-not (Test-Path -LiteralPath (Join-Path $R 'admission-record.json') -PathType Leaf)) {
    throw 'First admission record missing; this script only supersedes a recorded admission'
}
if (Test-Path -LiteralPath $record) { throw 'Superseding admission record already exists; inspect it' }
# The prepared seal binds the current adapter code hash; admit refuses any difference.
uv @U python scripts/essential_web_bootstrap.py admit --reviews $B --operator $Operator --operator-approve --prepared "$B/admission-decisions.json" --output $record
if ($LASTEXITCODE -ne 0) { throw 'admission refused' }
uv @U python scripts/essential_web_bootstrap.py status
if ($LASTEXITCODE -ne 0) { throw 'views are not admitted' }
