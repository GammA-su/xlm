$ErrorActionPreference = 'Stop'
Set-Location 'G:\Project\xlm-p32-recovery'
if ((git branch --show-current) -ne 'fix/p32-recovery-closeout') { throw 'Wrong P32 branch' }
$env:UV_PROJECT_ENVIRONMENT="$PWD\.venv"
$env:UV_CACHE_DIR="$PWD\artifacts\p32-recovery\uv-cache"
$env:UV_OFFLINE='1'
$env:UV_NO_SYNC='1'
$env:UV_PYTHON_DOWNLOADS='never'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH="$PWD\src;$PWD\scripts;$PWD\tests"
$env:MYPYPATH="$PWD\src;$PWD\scripts;$PWD\tests;$PWD\artifacts\p32-recovery\methods"
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:XLM_HOME="$PWD\artifacts\p32-recovery\home"
$env:TEMP="$PWD\artifacts\p32-recovery\tmp"
$env:TMP=$env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
uv run --offline --locked --no-sync python @args
exit $LASTEXITCODE
