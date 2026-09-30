# Live schema probe: NOT executed by the offline freeze. About 8 requests, cap 24.
$ErrorActionPreference = 'Stop'
if (-not $env:XLM_DATA_ROOT) { throw 'Dot-source scripts/operator_storage.ps1 first' }
$B = Join-Path (Get-Location) 'docs/implementation/evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP'
$U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')
$R = Join-Path $env:XLM_DATA_ROOT 'calib/essential-web-production/schema-probe'
$oldHub = $env:HF_HUB_OFFLINE
try {
  $env:HF_HUB_OFFLINE = '0'
  uv @U python scripts/essential_web_bootstrap.py probe --plan "$B/schema-probe-plan.json" --output-dir "$R" --authorize-network
  if ($LASTEXITCODE -ne 0) { throw 'schema probe refused' }
} finally { $env:HF_HUB_OFFLINE = $oldHub }
