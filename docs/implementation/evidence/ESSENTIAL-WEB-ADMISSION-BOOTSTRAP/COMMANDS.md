# Essential-Web admission bootstrap — commands and results (2026-09-30)

Environment: Windows 11, Python 3.12.13, uv 0.12.19, pyarrow 25.0.1, locked
environment (`--offline --locked --no-sync --extra cpu --extra eval`), with
`HF_HUB_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`, one thread per worker and
`TOKENIZERS_PARALLELISM=false`. Starting HEAD
`9aab98a1afa8cec778710f15cd1adc5cbeb1a506` on `data/mix01-ultrax-6b`.

`$U` below is `uv run --offline --locked --no-sync --extra cpu --extra eval`.

## Network use

The project pipeline made no network request. No probe, calibration or
acquisition ran. The agent read three public pages with its WebFetch tool for
the rights review (dataset card at the pinned revision, Common Crawl Terms of
Use, arXiv abstract); see `external-source-evidence.json`.

## Offline commands

| Command | Exit | Result |
|---|---:|---|
| `git merge-base --is-ancestor 57cb42f HEAD`, same for `9aab98a` | 0 | both ancestors |
| `$U python scripts/essential_web_bootstrap.py freeze --footer-root G:/Project/xlm-evidence-v4.1/essential-web` | 0 | plan digest `0b887739…dd3b4`; `logs/freeze.log` |
| `$U python -m pytest tests/test_essential_web_bootstrap.py -n 0 -q` | 0 | 35 passed; `logs/bootstrap-tests.log` |
| `$U python -m pytest <17 files> -m "not serial and not network" -n 8 --dist=worksteal --max-worker-restart=0 -q` | 1 | 427 passed, 2 failed; `logs/focused-regressions.log` |
| the two failing nodes, `-n 0` | 1 | 2 failed; `logs/acquisition-bounds-serial.log` |
| the two failing nodes, `-n 0 --basetemp=.bt` | 0 | 2 passed; `logs/acquisition-bounds-short-basetemp.log` |
| after the last edit: five files (bootstrap, readiness, production selector, source admission, calibration adopt), `-n 0` | 0 | 160 passed; `logs/final-focused.log` |
| `XLM_HOME=G:/XLM/xlm-home $U python scripts/essential_web_bootstrap.py status` | 1 | read-only; no view admitted; `logs/operator-store-status.log` |
| `$U ruff check` and `$U ruff format --check` on the five changed Python files | 0 | clean |
| `$U mypy --strict` on the three changed `src` modules (compiled mypy) | 0 | no issues |
| `$U python …/mypy_interpreted.py --strict` on all five changed Python files | 0 | no issues |

The 17 files of the regression selection: `test_essential_web_bootstrap`,
`test_essential_web_readiness`, `test_source_admission`, `test_source_discovery`,
`test_calibration_adopt`, `test_essential_web_production_selector`,
`test_acquisition_plan`, `test_acquisition_bounds`, `test_data_catalog`,
`test_data_adapters`, `test_essential_web_fasttrack_freeze`,
`test_essential_web_live_certification`, `test_essential_web_selector_sweep`,
`test_exclusion_benchmark`, `test_exclusion_receipt`, `test_pool_freeze_regime`,
`test_cli_pool_freeze`.

## The two failures

`test_acquisition_bounds.py::test_production_fetch_with_verified_admission` and
`::test_public_plan_fetch_status_verify_prepare` fail under the default pytest
temporary root with `[Errno 2] No such file or directory` on a journal temporary
file whose full path is about 270 characters. With `--basetemp=.bt` (a short
root) both pass unchanged. The cause is the Windows path-length limit under this
machine's `TEMP`, not the code under test. They were not retried until green:
the default-root failure is recorded above as a failure.

## Not run

The fast and full repository selections, `serial`-marked tests, `network`
tests, CUDA tests, the live schema probe, the production probe, calibration and
any acquisition. A focused pass is not a full-suite pass.

## Fixture and live distinction

All 35 new tests use authored Parquet images, authored cards and fake
transports. They prove probe and gate logic, not live compatibility. The
offline freeze replays retained real Phase-P footer bytes with an authored card
and labels the result `synthetic_fixture`; the admission gate refuses it. The
first real observation of the dataset card and footer by this code is the live
schema probe, which has not run.

## Resource use

New evidence directory: 12 JSON/script files plus logs, under 200 KB. The
offline freeze reads one 203,276-byte footer. Peak memory was not measured.
