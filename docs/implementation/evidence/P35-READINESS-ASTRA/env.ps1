$ErrorActionPreference = 'Stop'
$astraRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../../..')).Path
Set-Location -LiteralPath $astraRoot
$env:UV_PROJECT_ENVIRONMENT = 'G:\Project\xlm-p35-m3\.venv-p35-m3'
$env:UV_OFFLINE = 'true'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$astraRoot\src;$astraRoot\tests"
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
$env:CUBLAS_WORKSPACE_CONFIG = ':4096:8'
$astraScratch = Join-Path $astraRoot '.astra-scratch'
New-Item -ItemType Directory -Force -Path $astraScratch | Out-Null
$env:TEMP = $astraScratch
$env:TMP = $astraScratch
$env:TMPDIR = $astraScratch
$env:UV_CACHE_DIR = Join-Path $astraScratch 'uv-cache'
# Windows PowerShell wraps native stderr as ErrorRecord when redirected.
# Preserve complete native diagnostics and use the native exit status.
$ErrorActionPreference = 'Continue'
uv run --offline --locked --no-sync --extra cuda --extra eval @args
exit $LASTEXITCODE
