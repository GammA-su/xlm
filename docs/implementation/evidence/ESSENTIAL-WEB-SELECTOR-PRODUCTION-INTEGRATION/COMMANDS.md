# Essential-Web frozen selector production integration — exact commands and exit statuses

2026-09-30. Windows 11 build 26200; CPython 3.12.13; uv 0.12.19, offline,
locked, no sync. Working directory `F:\Project\xlm-data-ultrax`, branch
`data/mix01-ultrax-6b`, on freeze commit
`9586778ef5ac594900efb4b9bf78daaa0ee5c6ce`. No network. Real metadata
evidence (no text) for the reproduction commands; the real operator store
read-only for readiness; authored synthetic fixtures for pytest, except the
certification test, which reads three real pinned rows.

Common prefix (`$U`):

```
uv run --offline --locked --no-sync --extra cpu --extra eval
```

Test-run environment: `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false
PYTHONUTF8=1`.

## B-normal reproduction through the production adapters (real evidence)

```
$U python scripts/essential_web_production_selector.py verify-development \
  --recon-dir G:/XLM/recon/essential_web \
  --development-dir F:/Project/xlm-selector-sweeps/essential-web-v2 \
  --output docs/implementation/evidence/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION/development_reproduction.json
```

Exit **0** ([log](logs/verify-development.log)). 4,096 rows; science 29,
practical 108, prose 371, unassigned 36, rejected 3552; all eight crawls
match; 0 overlaps. Input `selected_records.jsonl`, 15,135,064 bytes, SHA-256
`42e07c350d849c608ef202348971e3f7ea48aa80775d413a39dd3373628c58e1`, bundle
digest `ac1c13b656d9dc3bc506726bfe3f1db925444efb899733052496be0425c58eb3`:
both equal the frozen development binding in the M seal.

```
$U python scripts/essential_web_production_selector.py verify-m \
  --derived-input F:/Project/xlm-selector-sweeps/essential-web-v4.1-m-analysis/m_derived_analysis_input.jsonl \
  --output docs/implementation/evidence/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION/m_reproduction.json
```

Exit **0** ([log](logs/verify-m.log)). 4,096 rows; science 24, practical
117, prose 372, unassigned 45, rejected 3538; all eight crawls match; 0
overlaps. Input 13,838,037 bytes, SHA-256
`839e1296940154e70e91cc15c1059a7708d4f1833da329ebb6146739ef625676`, the
derived input bound in the M seal.

Both commands refuse any `t_*` or `sealed/` path and any row that carries
`text`. A stat inventory (path, size, mtime) of
`G:\XLM\recon\essential_web\raw` and `F:\Project\xlm-selector-sweeps` was
identical before and after ([log](logs/inputs-stat.log)).

## Dry plan and readiness

```
$U python scripts/essential_web_production_selector.py dry-plan \
  --output docs/implementation/evidence/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION/production_selection_dry_plan.json
```

Exit **0** ([log](logs/dry-plan.log)). Nothing is run or authorized.

```
$U python scripts/essential_web_production_selector.py readiness \
  --development-reproduction docs/implementation/evidence/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION/development_reproduction.json \
  --m-reproduction docs/implementation/evidence/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION/m_reproduction.json \
  --xlm-home G:/XLM/xlm-home \
  --inventory G:/XLM/inventories/essential_web.inventory.json \
  --calibration G:/XLM/calib/calibration.json \
  --output docs/implementation/evidence/ESSENTIAL-WEB-SELECTOR-PRODUCTION-INTEGRATION/readiness.json
```

Exit **0** ([log](logs/readiness.log)). Vector: `selector_frozen` true,
`selector_integration_ok` true, `source_revision_ok` true,
`production_admission_ok` false, `inventory_ready` false,
`acquisition_plan_ready` false. Exit 0 means the vector was computed, not
that acquisition is ready. The operator store was only read.

## Parents re-verified after integration

| Command | Exit | Result |
|---|---:|---|
| `$U python scripts/essential_web_m_analysis.py verify …` (arguments as in the freeze COMMANDS) | 0 | seal `afc972cc…` verified ([log](logs/m-seal-verify.log)) |
| `$U python scripts/essential_web_fasttrack_freeze.py verify …` (arguments as in the freeze COMMANDS) | 0 | freeze `c6f32a65…` verified ([log](logs/freeze-verify.log)) |

## Tests

| Command | Exit | Result |
|---|---:|---|
| `$U python -m pytest tests/test_essential_web_production_selector.py -n 0 -q -p no:cacheprovider` | 0 | 57 passed ([log](logs/pytest-production-selector.log)) |
| `$U python -m pytest tests/test_essential_web_live_certification.py tests/test_mix01_views.py tests/test_production_ingest.py tests/test_source_doc_ids.py tests/test_adapt_rejections.py tests/test_mix01_quotas_6b.py tests/test_ultrax_probe_compat.py tests/test_ultrax_ultrafineweb.py -n 16 --dist=worksteal --max-worker-restart=0 -m "not serial" -q -p no:cacheprovider` | 0 | 123 passed ([log](logs/pytest-adapter-registry-regressions.log)) |
| the same eight files with `-n 0 -m "serial"` | 5 | no tests collected: these files hold no `serial`-marked test, 123 deselected ([log](logs/pytest-adapter-registry-regressions-serial.log)) |
| `$U python -m pytest tests/test_operator_driver.py tests/test_operator_driver_stages.py tests/test_operator_driver_window.py tests/test_calibration_adopt.py -n 0 -q -p no:cacheprovider` | 0 | 43 passed, 64.05 s ([log](logs/pytest-calibration-driver.log)) |
| `$U python -m pytest tests/test_essential_web_selector_sweep.py tests/test_essential_web_m_analysis.py tests/test_essential_web_fasttrack_freeze.py <5 identity nodes> -n 0 -q -p no:cacheprovider` | 0 | 126 passed (60 evaluator, 29 M analysis, 32 freeze, 5 identity), 87.20 s ([log](logs/pytest-frozen-evaluator-and-freeze.log)) |

`-p no:cacheprovider`
only avoids a pytest cache-directory permission warning on this machine.
NOT RUN: the fast and full offline selections, CUDA tests,
network-authorized tests. A focused pass is not a full-suite pass.

## Lint, format, types

Files: `src/xlm/data/adapters/essential_web_selector.py`,
`src/xlm/data/adapters/mix01_adapters.py`,
`src/xlm/data/adapters/columns.py`,
`src/xlm/data/sources/essential_web_production.py`,
`scripts/essential_web_production_selector.py` (mypy: these five), plus
`tests/test_essential_web_production_selector.py` and
`tests/test_essential_web_live_certification.py` (ruff).

| Command | Exit | Result |
|---|---:|---|
| `$U ruff check <7 files>` | 0 | All checks passed ([log](logs/ruff-check.log)) |
| `$U ruff format --check <7 files>` | 0 | 7 files already formatted ([log](logs/ruff-format.log)) |
| `$U python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-D01-D02-REPAIR/mypy_interpreted.py --strict <5 files>` | 0 | Success: no issues found in 5 source files ([log](logs/mypy-interpreted.log)) |

Compiled `mypy` was not attempted: Windows application control blocks it on
this machine. Scratch trial runs of both reproduction commands into a
temporary directory preceded the recorded runs and were discarded.
