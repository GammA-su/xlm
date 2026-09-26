$ErrorActionPreference = 'Stop'
$hardeningRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../../..')).Path
Set-Location -LiteralPath $hardeningRoot
$env:UV_PROJECT_ENVIRONMENT = 'G:\Project\xlm-p35-m3\.venv-p35-m3'
$env:UV_OFFLINE = 'true'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$hardeningRoot\src;$hardeningRoot\tests"
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
$env:CUBLAS_WORKSPACE_CONFIG = ':4096:8'
$hardeningScratch = Join-Path $hardeningRoot '.hardening-scratch'
New-Item -ItemType Directory -Force -Path $hardeningScratch | Out-Null
$env:TEMP = $hardeningScratch
$env:TMP = $hardeningScratch
$env:TMPDIR = $hardeningScratch
$env:UV_CACHE_DIR = Join-Path $hardeningScratch 'uv-cache'
# Windows PowerShell wraps native stderr as ErrorRecord when redirected.
# Preserve complete native diagnostics and use the native exit status.
$ErrorActionPreference = 'Continue'
uv run --offline --locked --no-sync --extra cuda --extra eval @args
exit $LASTEXITCODE
