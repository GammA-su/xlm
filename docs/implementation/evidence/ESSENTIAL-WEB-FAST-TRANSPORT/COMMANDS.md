# Exact commands and results — fast Essential-Web transport

2026-09-30; checkout `F:\Project\xlm-data-ultrax`, branch `data/mix01-ultrax-6b`.
Starting HEAD `a5bd2151d096d338a71b1dc1172c380480bafe95`.

Environment: Windows 11 Pro 10.0.26200, Windows PowerShell 5.1; Python
**3.12.13**, PyArrow **25.0.1** (bundled zstd), Ryzen 7 5700X3D (8 cores,
16 threads). Existing locked CPU/eval environment, offline, no sync.
`pyproject.toml`, `uv.lock` and `.python-version` are unchanged; no dependency
was added; the CPU/CUDA installation policy stays in `docs/runbooks/windows.md`.
**No network request, fetch, GPU work or push.**

Evidence classes:

- **Real local evidence, read-only**: the sealed calibration and its executed
  raw records under `G:\XLM\calib\essential-web-production\calibration`, the
  eight retained footers under `G:\Project\xlm-evidence-v4.1\essential-web`, the
  historical campaign evidence, the frozen inventory, free space on `C:` and `G:`.
- **Authored fixtures**: every test. Transport tests use a loopback HTTP server.
  They prove transport logic, not live endpoint behaviour.
- **Not run**: `benchmark`, `plan`, `authorize` and `run` on the real roots.

Setup for every command:

```powershell
. .\scripts\operator_storage.ps1
$U = @('run','--offline','--locked','--no-sync','--extra','cpu','--extra','eval')
$env:UV_OFFLINE='1'
$env:OMP_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'; $env:TOKENIZERS_PARALLELISM='false'
$C = @('--calibration-root','G:/XLM/calib/essential-web-production/calibration','--footer-root','G:/Project/xlm-evidence-v4.1/essential-web')
```

## Real-row replay

```powershell
uv @U python scripts/essential_web_fast.py replay @C
```

Exit **0**, `replay IDENTICAL`. Writes `local-replay.json`: eight units, 16,384
rows; every selected-record stream equals the sealed `raw_sha256`; all 24
document and 24 ledger hashes equal the seal, with one and with three row
groups. About 35 s. Work files under `C:\XLM-scratch\ew-fast\replay-*` were
removed. No document text is written to the evidence.

## Local processing rate

```powershell
uv @U python scripts/essential_web_fast.py bench-local @C --repeat 16 --copies 6
```

Exit **0**, 23 min. 48 files of 32,768 rows (calibration rows repeated 16 times;
repeats are for timing only). Writes `local-bench.json`:

| Processes | Rows/s | Wall | Mean machine CPU | Peak memory |
|---:|---:|---:|---:|---:|
| 1 | 2,047 | 768.3 s | 12% | 0.51 GB |
| 4 | 6,064 | 259.4 s | 29% | 1.11 GB |
| 8 | 11,573 | 135.9 s | 57% | 1.87 GB |
| 12 | 15,413 | 102.0 s | 82% | 2.65 GB |
| 16 | 16,685 | 94.3 s | 98% | 3.34 GB |

Two earlier samples are not kept as evidence and are reported for the variance
they show: 16 files of 16,384 rows gave 2,179 / 8,180 / 13,015 / 12,157 / 15,947
rows/s, where 16 jobs do not divide evenly over 12 processes; 48 files of 8,192
rows gave 2,136 / 5,836 / 10,627 / 14,606 / 15,907.

## Models and campaign freeze

```powershell
uv @U python scripts/essential_web_fast.py model
```

Exit **0**. Output: `campaign: d7b1a503055d72e7082e74b1c58eaa9c1c52ed5282ec2f12df4876979cf45822 batch files: 32 process workers: 12 benchmark: 5f865608bb75a6272200c73d4a912d58182b7d991c65fff6ab5ed866aa308115`.
Writes `transport-comparison.json`, `storage-model.json`, `batch-policy.json`,
`throughput-model.json`, `scratch-policy.json`, `concurrency-policy.json`,
`raw-artifact-contract.json`, `benchmark-plan.json`, `benchmark-expectation.json`
and `campaign.json`. The command refuses to freeze if the replay is missing,
failed or was made with other code, if whole-file bytes exceed 1.35 times the
projected bytes, or if the batch size would change the frozen membership.

Measured volumes at that time: `G:` 999.1 GB free of 1,000.2 GB; `C:` 863.1 GB
free of 999.2 GB.

## Dry run and gates on the real roots (offline)

```powershell
uv @U python scripts/essential_web_fast.py dry-run
uv @U python scripts/essential_web_fast.py gate --batch 0
.\scripts\operator_essential_web_fast.ps1 -Batch 0 -Stage Show
```

Exits **0 / 1 / 0**. `dry-run.json`: batch 0, batch 1 and all 33 batches have the
same membership as the historical campaign; the benchmark files are the batch-0
prefix; the parity file is inventory rank 13,757, beyond the ceiling; zero sealed
units. The gate prints `REFUSE the transport benchmark and real-byte parity check
have not passed`, as it must. The driver listed the 32 files of batch 0
(membership `c8e886c0…8e5`). `G:\XLM\plans` stayed empty.

## Tests

```powershell
uv @U python -m pytest tests/test_essential_web_fast.py -n 0 -q -p no:cacheprovider
```

Exit **0**, **28 passed**.

```powershell
uv @U python -m pytest tests/test_essential_web_fast.py tests/test_essential_web_calibration.py tests/test_essential_web_bulk.py tests/test_essential_web_readiness.py tests/test_essential_web_production_selector.py tests/test_essential_web_fasttrack_freeze.py tests/test_essential_web_selector_sweep.py tests/test_essential_web_bootstrap.py tests/test_essential_web_live_certification.py tests/test_source_admission.py tests/test_mix01_inventory.py tests/test_mix01_quotas_6b.py tests/test_mix01_views.py tests/test_acquisition_plan.py tests/test_acquisition_verifier.py tests/test_acquisition_bounds.py tests/test_acquisition_fetcher.py tests/test_acquisition_leases.py tests/test_hf_range_transport.py tests/test_production_ingest.py tests/test_rowgroup_sampling.py tests/test_adapt_rejections.py tests/test_exclusion_receipt.py tests/test_exclusion_benchmark.py tests/test_parquet_window_nested.py tests/test_parquet_window_sampling.py tests/test_selected_record_concurrency.py tests/test_calibration_adopt.py -n 0 -q -p no:cacheprovider --basetemp .bt-fast
```

Exit **0**, **708 passed**, 0 failed, 0 skipped, 205.05 s
(`logs/focused-regressions.log`). That run was made before the evidence existed
in its final form; the new module was rerun afterwards (above). The short
`--basetemp` avoids the known Windows path-length failure of two
acquisition-bounds tests; the directory was removed.

This is a focused selection. The fast and full repository selections and CUDA
tests were **not run**; a focused pass is not a full-suite pass.

Failures during development, all repaired without weakening an assertion:

- the equivalence test passed an unpopulated `plan_hash` from a hand-built plan
  (test error);
- the benchmark divided by a zero-length copy time on a tiny fixture file (tool
  error; rates are now `null` when the clock does not advance).

Strict mypy first reported one error in the pipeline (`wait` argument type) and
one in the test module.

## Lint and strict types

```powershell
$S = @('src/xlm/data/acquisition/source_parquet.py','src/xlm/data/sources/essential_web_local.py','src/xlm/data/sources/essential_web_fast.py','scripts/essential_web_fast.py','tests/test_essential_web_fast.py')
uv @U ruff check @S
uv @U ruff format --check @S
uv @U mypy --strict @S
```

Exits **0 / 0 / 0**: all checks passed, five files formatted, no issues in five
source files. The compiled mypy ran normally today.

## Resources

Offline work only. Disk written: the evidence files in this directory; temporary
work under `C:\XLM-scratch\ew-fast` (at most 4.5 GB during the local benchmark),
all removed; the empty directory `C:\XLM-scratch\ew-fast` remains. Nothing was
written under `G:\XLM`.

## Future operator commands (NOT executed)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass
Set-Location -LiteralPath 'F:\Project\xlm-data-ultrax'
. .\scripts\operator_storage.ps1
.\scripts\operator_essential_web_fast.ps1 -Stage Benchmark -Authorize 5f865608bb75a6272200c73d4a912d58182b7d991c65fff6ab5ed866aa308115
.\scripts\operator_essential_web_fast.ps1 -Batch 0 -Stage Prepare
.\scripts\operator_essential_web_fast.ps1 -Batch 0 -Stage Run -Authorize <digest printed by Prepare>
```

`Benchmark` transfers about 3.6 GB (cap 7.0 GiB) and retains nothing. `Prepare`
is offline. `Run` fetches. None was run here.
