# Future operator commands; NOT executed by the offline readiness freeze.
# BLOCKED until source admission and matching production plan authorization exist.
$ErrorActionPreference = 'Stop'
if (-not $env:XLM_DATA_ROOT) { throw 'Dot-source scripts/operator_storage.ps1 first' }
$storage = Get-Content recipes/operator/storage.json -Raw | ConvertFrom-Json
if ($env:XLM_DATA_ROOT -ne $storage.data_root) { throw 'Unexpected operator root' }
if ($env:XLM_HOME -ne (Join-Path $env:XLM_DATA_ROOT $storage.artifact_store_relative)) { throw 'Unexpected artifact store' }
$E = Join-Path (Get-Location) 'docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS'
$U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')
$R = Join-Path $env:XLM_DATA_ROOT 'calib/essential-web-production/calibration'
# Complete dry plan/admission review before executing this conditional sequence.
uv @U xlm data plan --source essential_web --view essential_science --catalog "$E/production-catalog.json" --files data/crawl=CC-MAIN-2014-15/train-01860-of-02772.parquet --mode selected_records --row-ranges "$E/calibration-00.rows.json" --adapter-spec essential_web_bnormal:essential_science --seed 20260930 --limits "$E/calibration-00.limits.json" --parquet-window-scan-rows 2048 --parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 --parquet-window-policy-version 2 --authorization-hash 446549cf88b9029f70a85a5bd7e7c6203cb1d90eeef5e153fe77e993d93df338 --output "$R/calibration-00.plan.json"
if ($LASTEXITCODE -ne 0) { throw 'plan failed' }
$oldHub = $env:HF_HUB_OFFLINE; $oldData = $env:HF_DATASETS_OFFLINE
try {
  $env:HF_HUB_OFFLINE = '0'; $env:HF_DATASETS_OFFLINE = '0'
  uv @U xlm data fetch --plan "$R/calibration-00.plan.json" --output-dir "$R/calibration-00/raw" --scratch-dir "$R/calibration-00/scratch"
  if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
} finally { $env:HF_HUB_OFFLINE = $oldHub; $env:HF_DATASETS_OFFLINE = $oldData }
uv @U xlm data verify --plan "$R/calibration-00.plan.json" --output-dir "$R/calibration-00/raw" --scratch-dir "$R/calibration-00/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'verification failed' }
uv @U xlm data adapt --plan "$R/calibration-00.plan.json" --adapter essential_web_bnormal --adapter-config essential_science --input "$R/calibration-00/raw/selected_records.jsonl" --output-dir "$R/calibration-00/essential_science" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-00.plan.json" --adapter essential_web_bnormal --adapter-config essential_practical --input "$R/calibration-00/raw/selected_records.jsonl" --output-dir "$R/calibration-00/essential_practical" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-00.plan.json" --adapter essential_web_bnormal --adapter-config essential_prose --input "$R/calibration-00/raw/selected_records.jsonl" --output-dir "$R/calibration-00/essential_prose" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data status --plan "$R/calibration-00.plan.json" --scratch-dir "$R/calibration-00/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'status failed' }
uv @U xlm data plan --source essential_web --view essential_science --catalog "$E/production-catalog.json" --files data/crawl=CC-MAIN-2015-32/train-01682-of-01920.parquet --mode selected_records --row-ranges "$E/calibration-01.rows.json" --adapter-spec essential_web_bnormal:essential_science --seed 20260930 --limits "$E/calibration-01.limits.json" --parquet-window-scan-rows 2048 --parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 --parquet-window-policy-version 2 --authorization-hash fd05c18384abd52f1a371523246982cc6cae5420097d0dadefe7035870dd6c4e --output "$R/calibration-01.plan.json"
if ($LASTEXITCODE -ne 0) { throw 'plan failed' }
$oldHub = $env:HF_HUB_OFFLINE; $oldData = $env:HF_DATASETS_OFFLINE
try {
  $env:HF_HUB_OFFLINE = '0'; $env:HF_DATASETS_OFFLINE = '0'
  uv @U xlm data fetch --plan "$R/calibration-01.plan.json" --output-dir "$R/calibration-01/raw" --scratch-dir "$R/calibration-01/scratch"
  if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
} finally { $env:HF_HUB_OFFLINE = $oldHub; $env:HF_DATASETS_OFFLINE = $oldData }
uv @U xlm data verify --plan "$R/calibration-01.plan.json" --output-dir "$R/calibration-01/raw" --scratch-dir "$R/calibration-01/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'verification failed' }
uv @U xlm data adapt --plan "$R/calibration-01.plan.json" --adapter essential_web_bnormal --adapter-config essential_science --input "$R/calibration-01/raw/selected_records.jsonl" --output-dir "$R/calibration-01/essential_science" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-01.plan.json" --adapter essential_web_bnormal --adapter-config essential_practical --input "$R/calibration-01/raw/selected_records.jsonl" --output-dir "$R/calibration-01/essential_practical" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-01.plan.json" --adapter essential_web_bnormal --adapter-config essential_prose --input "$R/calibration-01/raw/selected_records.jsonl" --output-dir "$R/calibration-01/essential_prose" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data status --plan "$R/calibration-01.plan.json" --scratch-dir "$R/calibration-01/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'status failed' }
uv @U xlm data plan --source essential_web --view essential_science --catalog "$E/production-catalog.json" --files data/crawl=CC-MAIN-2016-50/train-02156-of-03132.parquet --mode selected_records --row-ranges "$E/calibration-02.rows.json" --adapter-spec essential_web_bnormal:essential_science --seed 20260930 --limits "$E/calibration-02.limits.json" --parquet-window-scan-rows 2048 --parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 --parquet-window-policy-version 2 --authorization-hash 99eaabeb14c5b8b0a1beb3c7e4a398c5829cee0c36c5ca61e50096f2eb8b32da --output "$R/calibration-02.plan.json"
if ($LASTEXITCODE -ne 0) { throw 'plan failed' }
$oldHub = $env:HF_HUB_OFFLINE; $oldData = $env:HF_DATASETS_OFFLINE
try {
  $env:HF_HUB_OFFLINE = '0'; $env:HF_DATASETS_OFFLINE = '0'
  uv @U xlm data fetch --plan "$R/calibration-02.plan.json" --output-dir "$R/calibration-02/raw" --scratch-dir "$R/calibration-02/scratch"
  if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
} finally { $env:HF_HUB_OFFLINE = $oldHub; $env:HF_DATASETS_OFFLINE = $oldData }
uv @U xlm data verify --plan "$R/calibration-02.plan.json" --output-dir "$R/calibration-02/raw" --scratch-dir "$R/calibration-02/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'verification failed' }
uv @U xlm data adapt --plan "$R/calibration-02.plan.json" --adapter essential_web_bnormal --adapter-config essential_science --input "$R/calibration-02/raw/selected_records.jsonl" --output-dir "$R/calibration-02/essential_science" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-02.plan.json" --adapter essential_web_bnormal --adapter-config essential_practical --input "$R/calibration-02/raw/selected_records.jsonl" --output-dir "$R/calibration-02/essential_practical" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-02.plan.json" --adapter essential_web_bnormal --adapter-config essential_prose --input "$R/calibration-02/raw/selected_records.jsonl" --output-dir "$R/calibration-02/essential_prose" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data status --plan "$R/calibration-02.plan.json" --scratch-dir "$R/calibration-02/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'status failed' }
uv @U xlm data plan --source essential_web --view essential_science --catalog "$E/production-catalog.json" --files data/crawl=CC-MAIN-2018-05/train-03378-of-03429.parquet --mode selected_records --row-ranges "$E/calibration-03.rows.json" --adapter-spec essential_web_bnormal:essential_science --seed 20260930 --limits "$E/calibration-03.limits.json" --parquet-window-scan-rows 2048 --parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 --parquet-window-policy-version 2 --authorization-hash 753a5fbe4f16ee1d9c5dadd09e84d7767c9ac64b170194cbb3a1717f4100b2bb --output "$R/calibration-03.plan.json"
if ($LASTEXITCODE -ne 0) { throw 'plan failed' }
$oldHub = $env:HF_HUB_OFFLINE; $oldData = $env:HF_DATASETS_OFFLINE
try {
  $env:HF_HUB_OFFLINE = '0'; $env:HF_DATASETS_OFFLINE = '0'
  uv @U xlm data fetch --plan "$R/calibration-03.plan.json" --output-dir "$R/calibration-03/raw" --scratch-dir "$R/calibration-03/scratch"
  if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
} finally { $env:HF_HUB_OFFLINE = $oldHub; $env:HF_DATASETS_OFFLINE = $oldData }
uv @U xlm data verify --plan "$R/calibration-03.plan.json" --output-dir "$R/calibration-03/raw" --scratch-dir "$R/calibration-03/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'verification failed' }
uv @U xlm data adapt --plan "$R/calibration-03.plan.json" --adapter essential_web_bnormal --adapter-config essential_science --input "$R/calibration-03/raw/selected_records.jsonl" --output-dir "$R/calibration-03/essential_science" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-03.plan.json" --adapter essential_web_bnormal --adapter-config essential_practical --input "$R/calibration-03/raw/selected_records.jsonl" --output-dir "$R/calibration-03/essential_practical" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-03.plan.json" --adapter essential_web_bnormal --adapter-config essential_prose --input "$R/calibration-03/raw/selected_records.jsonl" --output-dir "$R/calibration-03/essential_prose" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data status --plan "$R/calibration-03.plan.json" --scratch-dir "$R/calibration-03/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'status failed' }
uv @U xlm data plan --source essential_web --view essential_science --catalog "$E/production-catalog.json" --files data/crawl=CC-MAIN-2019-09/train-00153-of-02577.parquet --mode selected_records --row-ranges "$E/calibration-04.rows.json" --adapter-spec essential_web_bnormal:essential_science --seed 20260930 --limits "$E/calibration-04.limits.json" --parquet-window-scan-rows 2048 --parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 --parquet-window-policy-version 2 --authorization-hash 1eaac483dadbfa43e519c077d80fae2ae3ea075d03a916513c23d6e5f594b8ca --output "$R/calibration-04.plan.json"
if ($LASTEXITCODE -ne 0) { throw 'plan failed' }
$oldHub = $env:HF_HUB_OFFLINE; $oldData = $env:HF_DATASETS_OFFLINE
try {
  $env:HF_HUB_OFFLINE = '0'; $env:HF_DATASETS_OFFLINE = '0'
  uv @U xlm data fetch --plan "$R/calibration-04.plan.json" --output-dir "$R/calibration-04/raw" --scratch-dir "$R/calibration-04/scratch"
  if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
} finally { $env:HF_HUB_OFFLINE = $oldHub; $env:HF_DATASETS_OFFLINE = $oldData }
uv @U xlm data verify --plan "$R/calibration-04.plan.json" --output-dir "$R/calibration-04/raw" --scratch-dir "$R/calibration-04/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'verification failed' }
uv @U xlm data adapt --plan "$R/calibration-04.plan.json" --adapter essential_web_bnormal --adapter-config essential_science --input "$R/calibration-04/raw/selected_records.jsonl" --output-dir "$R/calibration-04/essential_science" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-04.plan.json" --adapter essential_web_bnormal --adapter-config essential_practical --input "$R/calibration-04/raw/selected_records.jsonl" --output-dir "$R/calibration-04/essential_practical" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-04.plan.json" --adapter essential_web_bnormal --adapter-config essential_prose --input "$R/calibration-04/raw/selected_records.jsonl" --output-dir "$R/calibration-04/essential_prose" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data status --plan "$R/calibration-04.plan.json" --scratch-dir "$R/calibration-04/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'status failed' }
uv @U xlm data plan --source essential_web --view essential_science --catalog "$E/production-catalog.json" --files data/crawl=CC-MAIN-2021-04/train-00179-of-03315.parquet --mode selected_records --row-ranges "$E/calibration-05.rows.json" --adapter-spec essential_web_bnormal:essential_science --seed 20260930 --limits "$E/calibration-05.limits.json" --parquet-window-scan-rows 2048 --parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 --parquet-window-policy-version 2 --authorization-hash 67319d4ab28e255bad6f3d20327854feb0f9c788af0c8cc34c54cbec5ddc131e --output "$R/calibration-05.plan.json"
if ($LASTEXITCODE -ne 0) { throw 'plan failed' }
$oldHub = $env:HF_HUB_OFFLINE; $oldData = $env:HF_DATASETS_OFFLINE
try {
  $env:HF_HUB_OFFLINE = '0'; $env:HF_DATASETS_OFFLINE = '0'
  uv @U xlm data fetch --plan "$R/calibration-05.plan.json" --output-dir "$R/calibration-05/raw" --scratch-dir "$R/calibration-05/scratch"
  if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
} finally { $env:HF_HUB_OFFLINE = $oldHub; $env:HF_DATASETS_OFFLINE = $oldData }
uv @U xlm data verify --plan "$R/calibration-05.plan.json" --output-dir "$R/calibration-05/raw" --scratch-dir "$R/calibration-05/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'verification failed' }
uv @U xlm data adapt --plan "$R/calibration-05.plan.json" --adapter essential_web_bnormal --adapter-config essential_science --input "$R/calibration-05/raw/selected_records.jsonl" --output-dir "$R/calibration-05/essential_science" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-05.plan.json" --adapter essential_web_bnormal --adapter-config essential_practical --input "$R/calibration-05/raw/selected_records.jsonl" --output-dir "$R/calibration-05/essential_practical" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-05.plan.json" --adapter essential_web_bnormal --adapter-config essential_prose --input "$R/calibration-05/raw/selected_records.jsonl" --output-dir "$R/calibration-05/essential_prose" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data status --plan "$R/calibration-05.plan.json" --scratch-dir "$R/calibration-05/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'status failed' }
uv @U xlm data plan --source essential_web --view essential_science --catalog "$E/production-catalog.json" --files data/crawl=CC-MAIN-2021-49/train-00408-of-02895.parquet --mode selected_records --row-ranges "$E/calibration-06.rows.json" --adapter-spec essential_web_bnormal:essential_science --seed 20260930 --limits "$E/calibration-06.limits.json" --parquet-window-scan-rows 2048 --parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 --parquet-window-policy-version 2 --authorization-hash 6e96067cf2e2db2b43c308c486b8b9647690b6e5f42a25c6f8867f24fe1014a7 --output "$R/calibration-06.plan.json"
if ($LASTEXITCODE -ne 0) { throw 'plan failed' }
$oldHub = $env:HF_HUB_OFFLINE; $oldData = $env:HF_DATASETS_OFFLINE
try {
  $env:HF_HUB_OFFLINE = '0'; $env:HF_DATASETS_OFFLINE = '0'
  uv @U xlm data fetch --plan "$R/calibration-06.plan.json" --output-dir "$R/calibration-06/raw" --scratch-dir "$R/calibration-06/scratch"
  if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
} finally { $env:HF_HUB_OFFLINE = $oldHub; $env:HF_DATASETS_OFFLINE = $oldData }
uv @U xlm data verify --plan "$R/calibration-06.plan.json" --output-dir "$R/calibration-06/raw" --scratch-dir "$R/calibration-06/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'verification failed' }
uv @U xlm data adapt --plan "$R/calibration-06.plan.json" --adapter essential_web_bnormal --adapter-config essential_science --input "$R/calibration-06/raw/selected_records.jsonl" --output-dir "$R/calibration-06/essential_science" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-06.plan.json" --adapter essential_web_bnormal --adapter-config essential_practical --input "$R/calibration-06/raw/selected_records.jsonl" --output-dir "$R/calibration-06/essential_practical" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-06.plan.json" --adapter essential_web_bnormal --adapter-config essential_prose --input "$R/calibration-06/raw/selected_records.jsonl" --output-dir "$R/calibration-06/essential_prose" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data status --plan "$R/calibration-06.plan.json" --scratch-dir "$R/calibration-06/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'status failed' }
uv @U xlm data plan --source essential_web --view essential_science --catalog "$E/production-catalog.json" --files data/crawl=CC-MAIN-2024-26/train-01127-of-03168.parquet --mode selected_records --row-ranges "$E/calibration-07.rows.json" --adapter-spec essential_web_bnormal:essential_science --seed 20260930 --limits "$E/calibration-07.limits.json" --parquet-window-scan-rows 2048 --parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 --parquet-window-policy-version 2 --authorization-hash c1143c76c568d3546b979d1c5f5b54cdb0c184d6cfab89c36fb764fb27a7d70c --output "$R/calibration-07.plan.json"
if ($LASTEXITCODE -ne 0) { throw 'plan failed' }
$oldHub = $env:HF_HUB_OFFLINE; $oldData = $env:HF_DATASETS_OFFLINE
try {
  $env:HF_HUB_OFFLINE = '0'; $env:HF_DATASETS_OFFLINE = '0'
  uv @U xlm data fetch --plan "$R/calibration-07.plan.json" --output-dir "$R/calibration-07/raw" --scratch-dir "$R/calibration-07/scratch"
  if ($LASTEXITCODE -ne 0) { throw 'fetch failed' }
} finally { $env:HF_HUB_OFFLINE = $oldHub; $env:HF_DATASETS_OFFLINE = $oldData }
uv @U xlm data verify --plan "$R/calibration-07.plan.json" --output-dir "$R/calibration-07/raw" --scratch-dir "$R/calibration-07/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'verification failed' }
uv @U xlm data adapt --plan "$R/calibration-07.plan.json" --adapter essential_web_bnormal --adapter-config essential_science --input "$R/calibration-07/raw/selected_records.jsonl" --output-dir "$R/calibration-07/essential_science" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-07.plan.json" --adapter essential_web_bnormal --adapter-config essential_practical --input "$R/calibration-07/raw/selected_records.jsonl" --output-dir "$R/calibration-07/essential_practical" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data adapt --plan "$R/calibration-07.plan.json" --adapter essential_web_bnormal --adapter-config essential_prose --input "$R/calibration-07/raw/selected_records.jsonl" --output-dir "$R/calibration-07/essential_prose" --on-reject record --max-input-bytes 268435456
if ($LASTEXITCODE -ne 0) { throw 'adaptation failed' }
uv @U xlm data status --plan "$R/calibration-07.plan.json" --scratch-dir "$R/calibration-07/scratch" --json
if ($LASTEXITCODE -ne 0) { throw 'status failed' }
uv @U python scripts/essential_web_measure.py --root "$R" --freeze "$E" --stage calibration --output "$R/measurement.json"
if ($LASTEXITCODE -ne 0) { throw 'measurement failed' }
Get-Content "$R/measurement.json" -Raw
