# OPERATOR ONLY. Review authorization preview before executing.
Set-Location F:\Project\xlm-common-pile
$ErrorActionPreference = 'Stop'
$env:XLM_DATA_ROOT = 'G:\XLM'
$env:XLM_HOME = 'G:\XLM\xlm-home'
$env:XLM_SCRATCH_ROOT = 'C:\XLM-scratch'
$env:HF_HOME = 'G:\XLM\hf-cache'
$env:HF_DATASETS_CACHE = 'G:\XLM\hf-cache\datasets'
$env:PYTHONUTF8 = '1'
try {
  $env:HF_HUB_OFFLINE = '0'
  $env:HF_DATASETS_OFFLINE = '0'
  uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/jsonl_gz_sample.py --source-key common_pile --data-root G:\XLM --label common-pile-cal01 --rows 256 --authorized-components libretexts,news,oercommons,pressbooks,public_domain_review --target libretexts/libretexts.chunk.39.jsonl.gz --target libretexts/libretexts.chunk.40.jsonl.gz --target news/news.chunk.29.jsonl.gz --target news/news.chunk.44.jsonl.gz --target oercommons/oercommons.chunk.49.jsonl.gz --target oercommons/oercommons.chunk.24.jsonl.gz --target pressbooks/pressbooks.chunk.18.jsonl.gz --target pressbooks/pressbooks.chunk.26.jsonl.gz --target public_domain_review/public_domain_review.chunk.26.jsonl.gz --target public_domain_review/public_domain_review.chunk.04.jsonl.gz --chunk-bytes 131072 --max-requests-per-file 128 --max-bytes-per-file 4194304 --max-total-bytes 41943040 --max-line-bytes 1048576 --max-total-decoded-bytes 134217728 --max-output-bytes 134217728 --timeout 30 --deadline 600 --output-dir G:\XLM\calib\common_pile_cal01
  if ($LASTEXITCODE -ne 0) { throw 'Calibration refused; stop and inspect' }
  uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/jsonl_gz_sample.py --source-key common_pile --data-root G:\XLM --label common-pile-cal02 --rows 32 --authorized-components project_gutenberg --target project_gutenberg/project_gutenberg.chunk.43.jsonl.gz --target project_gutenberg/project_gutenberg.chunk.27.jsonl.gz --chunk-bytes 131072 --max-requests-per-file 128 --max-bytes-per-file 4194304 --max-total-bytes 8388608 --max-line-bytes 1048576 --max-total-decoded-bytes 134217728 --max-output-bytes 134217728 --timeout 30 --deadline 600 --output-dir G:\XLM\calib\common_pile_cal02
  if ($LASTEXITCODE -ne 0) { throw 'Calibration refused; stop and inspect' }
  uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.cli.main data probe --source common_pile --view common_pile_prose --catalog manifests/datasets.catalog.yaml --live --budget-mib 8 --probe-id common-pile-balanced-cal01 --publish --json
  if ($LASTEXITCODE -ne 0) { throw 'Metadata probe refused; stop and inspect' }
} finally {
  $env:HF_HUB_OFFLINE = '1'
  $env:HF_DATASETS_OFFLINE = '1'
}
# STOP: return the text-free receipts/probe outcome for offline review.
# No admission, production policy, production plan, authorization or production run.
