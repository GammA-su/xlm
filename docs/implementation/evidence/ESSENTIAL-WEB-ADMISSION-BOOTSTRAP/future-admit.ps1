# Operator admission: offline. Read the four review files in $B first.
param([Parameter(Mandatory = $true)][string]$Operator)
$ErrorActionPreference = 'Stop'
if (-not $env:XLM_DATA_ROOT) { throw 'Dot-source scripts/operator_storage.ps1 first' }
$B = Join-Path (Get-Location) 'docs/implementation/evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP'
$U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')
$R = Join-Path $env:XLM_DATA_ROOT 'calib/essential-web-production/schema-probe'
uv @U python scripts/essential_web_bootstrap.py admit --reviews $B --operator $Operator --operator-approve --prepared "$B/admission-decisions.json" --output "$R/admission-record.json"
if ($LASTEXITCODE -ne 0) { throw 'admission refused' }
uv @U python scripts/essential_web_bootstrap.py status
if ($LASTEXITCODE -ne 0) { throw 'views are not admitted' }
