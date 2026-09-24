$ErrorActionPreference = 'Stop'
$p33Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $p33Root
$env:UV_PROJECT_ENVIRONMENT = Join-Path $p33Root '.venv-p33-cuda'
$env:UV_OFFLINE = 'true'
$env:UV_CACHE_DIR = Join-Path $p33Root 'artifacts/p33/uv-cache'
$env:XLM_HOME = Join-Path $p33Root 'artifacts/p33/home'
$env:TEMP = Join-Path $p33Root 'artifacts/p33/tmp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:MYPYPATH = Join-Path $p33Root 'src'
$env:TORCHINDUCTOR_CACHE_DIR = Join-Path $p33Root 'artifacts/p33/inductor'
$env:TRITON_CACHE_DIR = Join-Path $p33Root 'artifacts/p33/triton'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
uv run --offline --locked --no-sync --extra cuda @args
exit $LASTEXITCODE
