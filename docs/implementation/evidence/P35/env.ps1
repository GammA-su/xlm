$ErrorActionPreference = 'Stop'
$p35Root = (Resolve-Path (Join-Path $PSScriptRoot '../../../..')).Path
Set-Location -LiteralPath $p35Root
# Read the existing locked CUDA environment; do not synchronize or install.
$env:UV_PROJECT_ENVIRONMENT = 'G:\Project\xlm-opus55-p34\.venv-opus55-p34'
$env:UV_OFFLINE = 'true'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = "$p35Root\src;$p35Root\tests"
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
uv run --offline --locked --no-sync --extra cuda @args
exit $LASTEXITCODE
