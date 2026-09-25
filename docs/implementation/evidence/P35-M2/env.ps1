$ErrorActionPreference = 'Stop'
$m2Root = (Resolve-Path (Join-Path $PSScriptRoot '../../../..')).Path
Set-Location -LiteralPath $m2Root
# Read the worktree's existing locked CUDA+eval environment (verified with
# `uv sync --offline --locked --dry-run --extra cuda --extra eval`: "Would make
# no changes"). Never synchronize or install from here.
$env:UV_PROJECT_ENVIRONMENT = Join-Path $m2Root '.venv-p35-m2'
$env:UV_OFFLINE = 'true'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$m2Root\src;$m2Root\tests"
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
uv run --offline --locked --no-sync --extra cuda --extra eval @args
exit $LASTEXITCODE
