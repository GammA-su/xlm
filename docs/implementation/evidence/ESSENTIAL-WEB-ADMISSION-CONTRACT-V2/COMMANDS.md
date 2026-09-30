# Offline command record — 2026-09-30

Repository `F:\Project\xlm-data-ultrax`, starting HEAD
`5b0cf7ae78325463533ae0957f9c03914b0ee1d6`. Windows, Python 3.12.13,
uv 0.12.19. Existing locked CPU/eval environment; no dependency installation.

Prefix on every command below:

```powershell
$U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
```

Initial focused run, exit 1, 98 passed / 1 failed in 12.38 s. The invalid test
fingerprint `wrong` could not be published (minimum 8 characters). Repaired with
a foreign 64-character digest. Pytest cache permissions produced warnings.
Original output is in the session, not a retained log file.

```powershell
uv @U python -m pytest tests/test_essential_web_bootstrap.py tests/test_exclusion_receipt.py tests/test_operator.py -n 0 -q --basetemp=.bt-c04
```

Broader requested/regression selection, exit 1, 308 passed / 4 failed in 88.94 s.
Three positive readiness fixtures used legacy `clean` and were migrated to the
new decision contract. `tests/test_reports.py::test_cli_runs_list_empty_and_populated`
remains blocked by Windows application-control policy rejecting PyTorch `_C`.
No environment bypass, retry, assertion weakening or skip was applied. Original
output is in the session, not a retained log file.

```powershell
uv @U python -m pytest tests/test_essential_web_bootstrap.py tests/test_source_admission.py tests/test_source_discovery.py tests/test_data_catalog.py tests/test_exclusion_benchmark.py tests/test_exclusion_receipt.py tests/test_essential_web_readiness.py tests/test_essential_web_production_selector.py tests/test_acquisition_plan.py tests/test_acquisition_bounds.py tests/test_operator.py tests/test_reports.py -n 0 -q --basetemp=.bt-c04 -o cache_dir=.bt-c04-cache
```

After fixture migration and live-metadata reseal, focused run, exit 1, 133 passed /
1 failed in 12.60 s. `test_committed_bootstrap_freeze` still expected the prepared
fingerprint to be null and replay evidence; it was migrated to require the real
fingerprint, mitigated risk, pending approval and valid seal. Log:
`logs/focused-final.log`; sampled resources/command: `validation.json`.

```powershell
uv @U python -m pytest tests/test_essential_web_bootstrap.py tests/test_essential_web_readiness.py tests/test_exclusion_receipt.py tests/test_operator.py -n 0 -q --basetemp=.bt-c04 -o cache_dir=.bt-c04-cache
```

Exact repaired test plus directly related sealed workflow, exit 0, 2 passed in
1.58 s; `logs/resealed-package-tests.log`:

```powershell
uv @U python -m pytest tests/test_essential_web_bootstrap.py::test_committed_bootstrap_freeze tests/test_essential_web_bootstrap.py::test_prepare_and_sealed_operator_admission -n 0 -q --basetemp=.bt-c04 -o cache_dir=.bt-c04-cache
```

Final review bound the adapter code SHA-256 in the preparation and operator
comparison, and added refusal when the review denies acquisition/pretraining.
After these changes, exit 0, **51 passed in 9.89 s**, `logs/bootstrap-final.log`:

```powershell
uv @U python -m pytest tests/test_essential_web_bootstrap.py -n 0 -q --basetemp=.bt-c04 -o cache_dir=.bt-c04-cache
```

Offline preparation/reseal against existing live evidence, exit 0, 3 views
verified in a temporary store with simulated approval. Real admission was not
published. Run initially, then measured after normalizing/resealing review hashes,
then once more after adding the adapter-code binding. Final log:
`logs/prepare-final.log`; measured run: `logs/prepare-admission.log`, with
measurement/command in `validation.json`.

```powershell
uv @U python scripts/essential_web_bootstrap.py prepare-admission --reviews docs/implementation/evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP --store G:/XLM/xlm-home --receipt G:/XLM/calib/essential-web-production/schema-probe/schema-probe-20260930T115739Z.receipt.json --output docs/implementation/evidence/ESSENTIAL-WEB-ADMISSION-BOOTSTRAP/admission-decisions.json
```

Real-store read-only status, exit 1 expected, all three views pending operator
approval; `logs/real-store-status.log`:

```powershell
$env:XLM_HOME = 'G:/XLM/xlm-home'
uv @U python scripts/essential_web_bootstrap.py status
```

Final static checks, exits 0 / 0 / 0. All 13 changed Python files:

```powershell
$Files = @(
  'src/xlm/data/sources/admission.py',
  'src/xlm/data/sources/policy.py',
  'src/xlm/data/sources/essential_web_bootstrap.py',
  'src/xlm/data/exclusion/receipt.py',
  'src/xlm/operator/final.py',
  'src/xlm/cli/data_cmd.py',
  'src/xlm/cli/final_cmd.py',
  'scripts/essential_web_bootstrap.py',
  'tests/test_essential_web_bootstrap.py',
  'tests/test_essential_web_production_selector.py',
  'tests/test_exclusion_receipt.py',
  'tests/test_operator.py',
  'tests/test_essential_web_readiness.py'
)
uv @U ruff check @Files
uv @U ruff format --check @Files
uv @U mypy --strict @Files
```

Logs: `ruff-final.log`, `format-final.log`, `mypy.log`. Earlier check logs
`ruff.log` / `format.log` retain the final test's one overlong assertion, fixed
with `uv @U ruff format tests/test_essential_web_bootstrap.py` (exit 0). Initial
formatting used `ruff check --fix` (found line-length errors) and `ruff format`
(exit 0); subsequent eight-runtime-file Ruff/mypy checks passed. No dependencies
or lint rules were changed.

Unit/integration fixtures are authored synthetic data. Existing live schema
metadata is independently identified as real evidence. No network, corpus text,
protected benchmark text, new probe, calibration, acquisition or exclusion run.
Fast/full acceptance and CUDA tests were not run. A focused pass is not a
full-suite pass. The report CLI environment failure remains a failure.

Next operator action (not run here):

```powershell
. .\scripts\operator_storage.ps1
& .\docs\implementation\evidence\ESSENTIAL-WEB-ADMISSION-BOOTSTRAP\future-admit.ps1 -Operator $env:USERNAME
```
