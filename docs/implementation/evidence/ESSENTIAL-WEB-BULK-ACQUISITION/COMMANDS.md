# Exact commands and results — bulk acquisition campaign

2026-09-30; checkout `F:\Project\xlm-data-ultrax`, branch `data/mix01-ultrax-6b`.
Starting HEAD `b2e13840b92483a7483c157cf5dec536d2d44c8d`.

Environment: Windows 11 Pro 10.0.26200, Windows PowerShell 5.1.26100; Python
**3.12.13**, uv **0.12.19**, PyArrow **25.0.1**, pydantic **2.13.5**, torch
**2.14.0+cpu**. Existing locked CPU/eval environment, offline, no sync.
`pyproject.toml`, `uv.lock` and `.python-version` are unchanged; the CPU/CUDA
installation policy stays in `docs/runbooks/windows.md`. **No network request,
footer read, fetch, GPU work or push.**

Evidence classes:

- **Real local evidence, read-only**: the sealed calibration, the eight retained
  Phase-P footers under `G:\Project\xlm-evidence-v4.1\essential-web`, the frozen
  23,200-path inventory, the operator store (for `xlm data plan` revision
  lookup), and free space on `G:\XLM`.
- **Authored fixtures**: every test. The fetch in the tests is a mock that
  writes authored records and a completed journal. It proves campaign logic,
  not live dataset compatibility.
- **Not run**: the `layout` command and the driver's `Layout` and `Run` stages.

Setup for every command:

```powershell
. .\scripts\operator_storage.ps1
$U = @('run','--offline','--locked','--no-sync','--extra','cpu','--extra','eval')
$env:UV_OFFLINE='1'
$env:OMP_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'; $env:TOKENIZERS_PARALLELISM='false'
```

## Models and campaign freeze

```powershell
uv @U python scripts/essential_web_bulk.py model --footer-root G:/Project/xlm-evidence-v4.1/essential-web
```

Exit **0**. Output: `campaign: 644be917fc40c124f3682eed0852101e1b66c8862adab73b65513cb2686b0ce9 batch files: 32 max batches: 33`.
Writes `footer-layouts.json`, `science-capacity-model.json`,
`physical-cost-model.json`, `inventory-capacity.json`, `batch-policy.json`,
`disk-budget.json`, `calibration-entries.json`, `headroom-estimate.json` (the
existing `mix01_inventory estimate`), `stop-policy.json` and
`bulk-campaign.json`. Footer bytes read: 1,398,384. No row was decoded.

An earlier model run gave campaign digest `ab4b3133…7df2`. It changed once,
when the footer read-ahead (65,548 bytes per open) and the observed largest
record were added to the frozen limits. Repeated runs since are identical.

## Dry run

```powershell
uv @U python scripts/essential_web_bulk.py dry-run
uv @U python scripts/essential_web_bulk.py dry-slices
```

Exit **0 / 0**. `dry-run.json`: batch 0 and batch 1 membership and digests,
disjointness, restart reproduction, distinctness through the ceiling,
calibration file ranks, zero campaign rows counted, and the readiness block.
`dry-slices/`: ten slice plans for the eight footer-certified files, created by
the real `xlm data plan` command in process. All ten are production plans
refused by `validate_plan_authorization` without authorization. They are a
demonstration, not a campaign batch.

## Operator driver and gates on the real root (offline stages only)

```powershell
.\scripts\operator_essential_web_bulk.ps1 -Batch 0 -Stage Show
uv @U python scripts/essential_web_bulk.py gate --batch 0
uv @U python scripts/essential_web_bulk.py gate --batch 1
uv @U python scripts/essential_web_bulk.py plan --batch 0
```

Results: driver succeeded and listed 32 files (membership `c8e886c0…8e5`);
gate exit **0** (`RUN`); gate exit **1** (`REFUSE batch 1 cannot run before
batch 0 is complete`); plan exit **1** (`layout.json is missing`, as it must be
before the footer read). `G:\XLM\plans` stayed empty. `HF_HUB_OFFLINE` and
`HF_DATASETS_OFFLINE` stayed `1`.

## Tests

```powershell
uv @U python -m pytest tests/test_essential_web_bulk.py -n 0 -q -p no:cacheprovider --basetemp G:\XLM\temp\ewb
```

Exit **0**, **25 passed**, 9.19 s, no skips.

```powershell
uv @U python -m pytest tests/test_essential_web_calibration.py tests/test_essential_web_bulk.py tests/test_essential_web_readiness.py tests/test_essential_web_production_selector.py tests/test_essential_web_fasttrack_freeze.py tests/test_essential_web_bootstrap.py tests/test_essential_web_live_certification.py tests/test_source_admission.py tests/test_mix01_inventory.py tests/test_mix01_quotas_6b.py tests/test_acquisition_plan.py tests/test_acquisition_verifier.py tests/test_acquisition_bounds.py tests/test_adapt_rejections.py tests/test_exclusion_receipt.py tests/test_exclusion_benchmark.py tests/test_parquet_window_nested.py tests/test_parquet_window_sampling.py tests/test_selected_record_concurrency.py tests/test_calibration_adopt.py -n 0 -q -p no:cacheprovider --basetemp G:\XLM\temp\ewb
```

Exit **0**, **505 passed**, 0 failed, 0 skipped, 99.55 s
(`logs/focused-regressions.log`). An earlier run of the same selection, before
the last script edits, also gave 505 passed in 87.56 s.

The short `--basetemp` avoids the known Windows path-length failure of two
acquisition-bounds tests under the long default temporary path. This is a
focused selection. The fast and full repository selections and CUDA tests were
**not run**; a focused pass is not a full-suite pass.

One test reads the real committed campaign evidence
(`test_committed_campaign_matches_its_frozen_inputs`) and one invokes the real
PowerShell parser on the driver. `test_essential_web_live_certification.py`
uses three previously stored real rows.

Failures during development, all repaired without weakening an assertion: none
in pytest. Strict mypy first reported three errors (one variable reuse in
`cumulative`, two missing annotations in the test module).

## Lint and strict types

```powershell
$S = @('src/xlm/data/sources/essential_web_calibration.py','src/xlm/data/sources/essential_web_bulk.py','scripts/essential_web_calibration_seal.py','scripts/essential_web_bulk.py','tests/test_essential_web_calibration.py','tests/test_essential_web_bulk.py')
uv @U ruff check @S
uv @U ruff format --check @S
uv @U python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-D01-D02-REPAIR/mypy_interpreted.py --strict @S
```

Exits **0 / 0 / 0**: all checks passed, six files formatted, no issues in six
source files.

## Resources

Offline work only. Seal about 15 s; model, dry run and dry slices a few seconds
each. Process peak memory was not measured. Disk written: the evidence files in
this directory (about 110 KB) and nothing under `G:\XLM` except a pytest
temporary directory that was removed.

## Future operator commands (NOT executed)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass
Set-Location -LiteralPath 'F:\Project\xlm-data-ultrax'
. .\scripts\operator_storage.ps1
.\scripts\operator_essential_web_bulk.ps1 -Batch 0 -Stage Prepare
.\scripts\operator_essential_web_bulk.ps1 -Batch 0 -Stage Run -Authorize <digest printed by Prepare>
```

`Prepare` reads 32 footers (network). `Run` fetches. Neither was run here.
