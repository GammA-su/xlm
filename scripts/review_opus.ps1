$ErrorActionPreference = 'Stop'
Set-Location 'G:\Project\xlm-opus55-review'
if ((git branch --show-current) -ne 'review/opus55-product') { throw 'Wrong review branch' }
$env:UV_PROJECT_ENVIRONMENT="$PWD\.venv"
$env:UV_CACHE_DIR="$PWD\artifacts\opus-review\uv-cache"
$env:UV_OFFLINE='1'
$env:UV_NO_SYNC='1'
$env:UV_PYTHON_DOWNLOADS='never'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH="$PWD\src;$PWD\scripts;$PWD\tests"
$env:MYPYPATH="$PWD\src;$PWD\artifacts\opus-review\methods"
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:XLM_HOME="$PWD\artifacts\opus-review\home"
$env:TEMP="$PWD\artifacts\opus-review\tmp"
$env:TMP=$env:TEMP
uv run --offline --locked --no-sync python @args
exit $LASTEXITCODE
