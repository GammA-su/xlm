# Exact commands and results — offline production readiness

2026-09-30; authoritative checkout `F:\Project\xlm-data-ultrax`, branch
`data/mix01-ultrax-6b`. Starting HEAD
`29c3814a076122e6f86e98491dbf7986fe0062fa`.

Environment: Windows PowerShell 5.1; Python **3.12.13**, uv **0.12.19**,
torch **2.14.0+cpu**, PyArrow **25.0.1**. Existing locked CPU/eval environment,
offline and no-sync. `pyproject.toml`, `uv.lock`, `.python-version` unchanged.
CPU/CUDA installation policy remains in `docs/runbooks/windows.md`; no install
was performed. No network, live source execution, GPU work, bulk job or push.

Common PowerShell setup for the recorded Python checks:

```powershell
$U = @('run','--offline','--locked','--no-sync','--extra','cpu','--extra','eval')
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:PYTHONUTF8='1'
$E='docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS'
```

All pytest runs used explicit `-n 0 -q -p no:cacheprovider`. No full/fast selection
was invoked. No skipped tests count as a pass. Test fixtures are authored except
the specifically named stored-real-row certification test module. Reproduction
and footer/inventory freeze commands are separate real **local evidence** checks.

## Before editing

`git status --short`, `git rev-parse HEAD`, `git log --oneline -15`, and
`git branch --show-current`: exit 0. The two commands below each exited 0:

```powershell
git merge-base --is-ancestor 9586778ef5ac594900efb4b9bf78daaa0ee5c6ce HEAD
git merge-base --is-ancestor 29c3814a076122e6f86e98491dbf7986fe0062fa HEAD
```

Initial dirty state: modified `docs/implementation/STATUS.md`; untracked
`ESSENTIAL-WEB-EVIDENCE-V4.1-REVIEW/` and `ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW/`
evidence directories, and reports `ESSENTIAL-WEB-EVIDENCE-V3.0-REVALIDATION.md`,
`ESSENTIAL-WEB-EVIDENCE-V4.1-REVIEW.md`,
`ESSENTIAL-WEB-SELECTOR-POLICY-REVIEW.md`,
`ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW-AUDIT.md`,
`ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW.md`. All preserved and excluded from commits.

## Freeze and reproduce

```powershell
$env:XLM_HOME='G:\XLM\xlm-home'
uv @U python scripts/essential_web_readiness.py --footer-root G:/Project/xlm-evidence-v4.1/essential-web --repository-metadata D:/Project/xlm-operator-pilot/adapter-cert-essential-web01/repository-meta.json --output $E
uv @U python scripts/essential_web_production_selector.py verify-development --recon-dir G:/XLM/recon/essential_web --development-dir F:/Project/xlm-selector-sweeps/essential-web-v2 --output "$E/development-reproduction.json"
uv @U python scripts/essential_web_production_selector.py verify-m --derived-input F:/Project/xlm-selector-sweeps/essential-web-v4.1-m-analysis/m_derived_analysis_input.jsonl --output "$E/confirmation-reproduction.json"
```

Final exits **0 / 0 / 0**. Freeze uses the real CLI in-process for all nine dry
plans, not network transports. First attempt exited 1 because the original
catalog's Essential revision is null; fixed by a scoped pinned catalog artifact,
leaving the original catalog unchanged. Final freeze log: `logs/freeze-final.log`.
Both selector results match row/per-crawl counts, conserve 4096 rows and have zero
overlap. Source footer payloads read: 1,398,384 bytes. No T text read.

## Tests

```powershell
uv @U python -m pytest tests/test_essential_web_readiness.py tests/test_essential_web_production_selector.py -n 0 -q -p no:cacheprovider
```

Early result: exit 1, **86 passed / 1 failed**. The existing admission fixture
needed the new selector binding and expected the obsolete legacy-adapter error.
After repair and the measurement test: exit 0, **88 passed**, 6.93 s.
Later focused result: exit 0, **90 passed**, 6.77 s
(`logs/readiness-final.log`; before the additional PowerShell root smoke test).

```powershell
uv @U python -m pytest tests/test_essential_web_readiness.py::test_shared_measurement_counts_transfer_once_and_allows_empty_views tests/test_mix01_inventory.py tests/test_acquisition_plan.py -n 0 -q -p no:cacheprovider
```

Exit **0**, **50 passed**, 6.97 s. Offline plan/inventory/measurement regressions.

```powershell
uv @U python -m pytest tests/test_essential_web_readiness.py tests/test_essential_web_production_selector.py tests/test_essential_web_selector_sweep.py tests/test_essential_web_live_certification.py tests/test_source_admission.py tests/test_adapt_rejections.py tests/test_production_ingest.py tests/test_source_doc_ids.py tests/test_mix01_views.py tests/test_mix01_quotas_6b.py tests/test_mix01_inventory.py tests/test_acquisition_plan.py -n 0 -q -p no:cacheprovider
```

Exit **1**, **301 passed / 1 failed**, 103.90 s (`logs/focused-regressions.log`).
The new measurement helper shadowed its imported `selector` after a typing fix;
renamed the instance `frozen_selector`, then reran its focused tests (no weakened
assertions). The 301 passes were not relabeled as a full-suite pass.

```powershell
uv @U python -m pytest tests/test_operator_driver.py tests/test_operator_driver_stages.py tests/test_operator_driver_window.py tests/test_calibration_adopt.py -n 0 -q -p no:cacheprovider
uv @U python -m pytest tests/test_essential_web_readiness.py tests/test_operator_driver.py tests/test_operator_driver_stages.py tests/test_operator_driver_window.py tests/test_calibration_adopt.py -n 0 -q -p no:cacheprovider
```

Exits **0 / 0**: **43 passed**, 65.34 s (`logs/driver.log`);
**76 passed**, 65.56 s (`logs/final-repairs.log`). No skips.
The latter includes final raw-source-validator checks and empty-view measurement.
Subsequent root/driver-default repairs are covered by the final narrow command
recorded below, not by claiming this earlier driver run covered them.

## Lint and strict types

The exact 11-file scope:

```powershell
$Scope = @('src/xlm/data/adapters/malformed.py','src/xlm/data/adapters/mix01_adapters.py','src/xlm/data/adapters/rejections.py','src/xlm/data/sources/admission.py','src/xlm/data/sources/essential_web_readiness.py','src/xlm/cli/data_cmd.py','scripts/mix01_inventory.py','scripts/essential_web_readiness.py','scripts/essential_web_measure.py','tests/test_essential_web_readiness.py','tests/test_essential_web_production_selector.py')
uv @U ruff check @Scope
uv @U ruff format --check @Scope
uv @U python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-D01-D02-REPAIR/mypy_interpreted.py --strict @Scope
```

Final exits **0 / 0 / 0**: all checks passed, 11 formatted files, no strict typing
issues in 11 files. The existing interpreted runner avoids the known compiled
mypy application-control failure; no settings or installation changed. Initial
mypy failed on implicit re-exports and one pre-existing unused NumPy type-ignore;
fixed direct imports and removed that obsolete comment. Initial lint reported
import order/line lengths and the same local-variable shadowing; all repaired.

## PowerShell and frozen configuration

The PowerShell parser accepted `operator_storage.ps1`, the remaining calibration
driver and both generated future scripts (zero parse errors). An initial direct
dot-source was refused by this machine's execution policy (PowerShell emitted an
error despite overall shell exit 0); this was **not a successful setup**. In a
process-local `-ExecutionPolicy Bypass` shell, the first setup test then exited 1:
`$PSScriptRoot` was empty while evaluating a dot-sourced parameter default. Moved
path resolution into the script body in both helpers.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command '. ./scripts/operator_storage.ps1; Get-Item Env:XLM_DATA_ROOT,Env:XLM_HOME,Env:HF_HOME,Env:TEMP | Format-Table -HideTableHeaders'
git diff --quiet 29c3814 -- recipes/mixtures recipes/selectors scripts/essential_web_selector_sweep.py
git -c core.whitespace=cr-at-eol diff --check
```

Final exits **0 / 0 / 0**. Paths: `G:\XLM`, `G:\XLM\xlm-home`,
`G:\XLM\hf-cache`, `G:\XLM\temp`. The CRLF-aware diff check preserves the existing
CLI file's line endings; the ordinary check reports its existing CRLF convention
as trailing carriage returns. Mixture, quotas and frozen selector unchanged.

No live command was executed. Exact future scripts are `future-probe.ps1` and
`future-calibration.ps1`, with root setup and blocking conditions in the report.
Do not infer authorization or successful admission from a generated plan hash.
Offline process peak memory and live resource usage are NOT MEASURED.

Final narrow repair verification:

```powershell
uv @U python -m pytest tests/test_essential_web_readiness.py tests/test_operator_driver.py::test_native_wrapper_contract -n 0 -q -p no:cacheprovider
```

Exit **0**, **35 passed**, 10.05 s (`logs/root-final.log`): all 34 readiness tests
plus the real PowerShell native-wrapper harness. This covers the final root setup,
legacy Essential refusal with implicit checkout resolution, source validator
measurement check, inventory, capacity and command generation. Ruff check/format
and strict mypy over the exact 11-file scope above were repeated after this final
repair: all exit **0**. No broader rerun was needed or claimed.

Artifact seal (exit **0**, local hashes only):

```powershell
uv @U python docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/seal_artifacts.py
```

The manifest excludes itself, binds code commit `57cb42f` and records exact bytes
and SHA-256 for package artifacts, changed code and the unchanged frozen inputs.
PowerShell logs are normalized to UTF-8/LF with trailing whitespace removed;
failure diagnostics and results are retained.
Local implementation commit: `57cb42f` (`feat: harden Essential-Web production acquisition path`).
