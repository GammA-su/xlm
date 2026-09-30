# Future operator commands; NOT executed by the offline readiness freeze.
# BLOCKED until source admission and matching production plan authorization exist.
$ErrorActionPreference = 'Stop'
if (-not $env:XLM_DATA_ROOT) { throw 'Dot-source scripts/operator_storage.ps1 first' }
$storage = Get-Content recipes/operator/storage.json -Raw | ConvertFrom-Json
if ($env:XLM_DATA_ROOT -ne $storage.data_root) { throw 'Unexpected operator root' }
if ($env:XLM_HOME -ne (Join-Path $env:XLM_DATA_ROOT $storage.artifact_store_relative)) { throw 'Unexpected artifact store' }
$E = Join-Path (Get-Location) 'docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS'
$U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')
$R = Join-Path $env:XLM_DATA_ROOT 'calib/essential-web-production/probe'
# Complete dry plan/admission review before executing this conditional sequence.
uv @U xlm data plan --source essential_web --view essential_science --catalog "$E/production-catalog.json" --files data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet --mode selected_records --row-ranges "$E/probe-00.rows.json" --adapter-spec essential_web_bnormal:essential_science --seed 20260930 --limits "$E/probe-00.limits.json" --parquet-window-scan-rows 2048 --parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 --parquet-window-policy-version 2 --authorization-hash 04db5c763959ebf7187ba69d23a4f99b5d4e2e77c2f07bc4350e4b427f52b04c --output "$R/probe-00.plan.json"
if ($LASTEXITCODE -ne 0) { throw 'plan failed' }
$oldHub = $env:HF_HUB_OFFLINE; $oldData = $env:HF_DATASETS_OFFLINE
try {
  $env:HF_HUB_OFFLINE = '0'; $env:HF_DATASETS_OFFLINE = '0'
  uv @U xlm data fetch --plan "$R/probe-00.plan.json" --output-dir "$R/probe-00/raw" --scratch-dir "$R/probe-00/scratch"
  if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
} finally { $env:HF_HUB_OFFLINE = $oldHub; $env:HF_DATASETS_OFFLINE = $oldData }
uv @U xlm data verify --plan "$R/probe-00.plan.json" --output-dir "$R/probe-00/raw" --scratch-dir "$R/probe-00/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'verification failed' }
uv @U xlm data adapt --plan "$R/probe-00.plan.json" --adapter essential_web_bnormal --adapter-config essential_science --input "$R/probe-00/raw/selected_records.jsonl" --output-dir "$R/probe-00/essential_science" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/probe-00.plan.json" --adapter essential_web_bnormal --adapter-config essential_practical --input "$R/probe-00/raw/selected_records.jsonl" --output-dir "$R/probe-00/essential_practical" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/probe-00.plan.json" --adapter essential_web_bnormal --adapter-config essential_prose --input "$R/probe-00/raw/selected_records.jsonl" --output-dir "$R/probe-00/essential_prose" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data status --plan "$R/probe-00.plan.json" --scratch-dir "$R/probe-00/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'status failed' }
uv @U python scripts/essential_web_measure.py --root "$R" --freeze "$E" --stage probe --output "$R/measurement.json"
if ($LASTEXITCODE -ne 0) { throw 'measurement failed' }
Get-Content "$R/measurement.json" -Raw
