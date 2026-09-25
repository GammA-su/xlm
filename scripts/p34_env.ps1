$ErrorActionPreference = 'Stop'
$p34Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $p34Root
$env:UV_PROJECT_ENVIRONMENT = Join-Path $p34Root '.venv-opus55-p34'
$env:UV_OFFLINE = 'true'
$env:UV_CACHE_DIR = Join-Path $p34Root 'artifacts/p34/uv-cache'
$env:XLM_HOME = Join-Path $p34Root 'artifacts/p34/home'
$env:TEMP = Join-Path $p34Root 'artifacts/p34/tmp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:MYPYPATH = Join-Path $p34Root 'src'
$env:TORCHINDUCTOR_CACHE_DIR = Join-Path $p34Root 'artifacts/p34/inductor'
$env:TRITON_CACHE_DIR = Join-Path $p34Root 'artifacts/p34/triton'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
uv run --offline --locked --no-sync --extra cuda @args
exit $LASTEXITCODE
