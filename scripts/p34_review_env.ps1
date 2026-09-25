$ErrorActionPreference = 'Stop'
$reviewRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $reviewRoot
# Borrow an already locked environment read-only; never sync or install.
$env:UV_PROJECT_ENVIRONMENT = 'G:\Project\xlm-opus55-p34\.venv-opus55-p34'
$env:UV_OFFLINE = 'true'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$reviewRoot\src;$reviewRoot\tests"
$env:MYPYPATH = "$reviewRoot\src"
$env:XLM_HOME = "$reviewRoot\artifacts\p34-review\home"
$env:TEMP = "$reviewRoot\artifacts\p34-review\tmp"
$env:TMP = $env:TEMP
$env:TORCHINDUCTOR_CACHE_DIR = "$reviewRoot\artifacts\p34-review\inductor"
$env:TRITON_CACHE_DIR = "$reviewRoot\artifacts\p34-review\triton"
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
uv run --offline --locked --no-sync --extra cuda @args
exit $LASTEXITCODE
