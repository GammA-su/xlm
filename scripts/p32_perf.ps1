$ErrorActionPreference = 'Stop'
Set-Location 'G:\Project\xlm-p32-perf-closeout'
if ((git branch --show-current) -ne 'perf/p32-happy-path-closeout') { throw 'Wrong P32 branch' }
$env:UV_PROJECT_ENVIRONMENT="$PWD\.venv"
$env:UV_CACHE_DIR="$PWD\artifacts\p32-perf-closeout\uv-cache"
$env:UV_OFFLINE='1'
$env:UV_NO_SYNC='1'
$env:UV_PYTHON_DOWNLOADS='never'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONFAULTHANDLER='1'
$env:PYTHONPATH="$PWD\src;$PWD\scripts;$PWD\tests"
$env:MYPYPATH="$env:PYTHONPATH;$PWD\artifacts\p32-perf-closeout\methods"
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:XLM_HOME="$PWD\artifacts\p32-perf-closeout\home"
$env:TEMP="$PWD\artifacts\p32-perf-closeout\tmp"
$env:TMP=$env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
uv run --offline --locked --no-sync python @args
exit $LASTEXITCODE
