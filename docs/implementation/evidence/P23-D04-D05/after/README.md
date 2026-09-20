# P23 D04/D05 — after-evidence

Captured 2026-09-20 on branch `fix/d04-d05` at `bae905b`, in the isolated
workspace `D:\Project\xlm-d0405` with its own venv, `XLM_HOME` and basetemp.
Offline throughout: no network, no dataset download, no official benchmark
content, no GPU.

| File | What it is |
|---|---|
| `tests_after.xml` | JUnit XML for the full offline suite |
| `full_suite.log` | pytest stdout, including the slowest-12 durations |
| `mypy.txt` | Tail of `mypy src` (strict) |

## Environment

```text
xlm      -> D:\Project\xlm-d0405\src\xlm\__init__.py
lm_eval  -> 0.4.13
torch    -> 2.14.0+cpu
python   -> 3.12.13
PYTHONPATH cleared; UV_PROJECT_ENVIRONMENT=D:\Project\xlm-d0405\.venv-d0405
```

## Commands and exit statuses

```powershell
uv sync --offline --locked --extra cpu --extra eval                          # exit 0

uv run --offline --locked --no-sync --extra cpu --extra eval `
    pytest tests/test_eval_declared_inputs.py tests/test_eval_inputs.py `
           tests/test_eval_coverage.py -q                                    # exit 0

uv run --offline --locked --no-sync --extra cpu --extra eval `
    pytest -q -rs -m "not network and not cuda and not operator" `
      --durations=12 --junitxml=tests_after.xml                              # exit 1

uv run --offline --locked --no-sync --extra cpu --extra eval mypy src        # exit 1
uv run --offline --locked --no-sync --extra cpu --extra eval `
    ruff check <changed files>                                               # exit 0
uv run --offline --locked --no-sync --extra cpu --extra eval `
    ruff format --check <changed files>                                      # exit 0
```

## Results

| Check | Result |
|---|---|
| Focused D04/D05 tests | **75 passed, exit 0** (372.12 s) |
| Full offline suite | **1076 passed, 10 failed, 1 skipped, 12 deselected** (3071.37 s) |
| `mypy src` strict | 3 errors in 2 files, **none in D04/D05 code** (200 source files) |
| `ruff check` (changed files) | **exit 0** |
| `ruff format --check` (changed files) | **exit 0** |

`ruff check src tests scripts` over the whole repository reports 94 errors; 93
are in `src/xlm/data/acquisition/*`, `src/xlm/data/sources/transport.py` and
`src/xlm/prepare/*`, which are D02 files mid-edit in this branch's baseline. The
one error in `src/xlm/evaluation/inputs.py` was fixed. Formatting was applied to
this branch's own files only; Astra's workspace and files were never formatted.

## Failure attribution — all 10 are pre-existing

Every failure was re-run individually against the baseline commit `0a8ff65` in a
separate git worktree (`.d0405/baseline-tree`, `PYTHONPATH` pointed at that
tree's `src`, import origin verified). **All ten reproduce identically at the
baseline. This branch introduces no test regressions.**

| Test | Failure | Category |
|---|---|---|
| `test_acquisition_fetcher::test_fetcher_ignored_range_handling` | `ProgressCorruptionError: unowned partial file` | D02 mid-edit |
| `test_acquisition_fetcher::test_fetcher_source_drift_412_blocks` | expected `'Source drift detected'`, got `'source/range changed: HTTP 412'` | D02 mid-edit |
| `test_acquisition_fetcher::test_fetcher_inconsistent_content_range_fails` | expected `'Inconsistent Content-Range start'` | D02 mid-edit |
| `test_acquisition_fetcher::test_fetcher_oversized_payload_halts_budget` | expected `'Transferred bytes limit'` | D02 mid-edit |
| `test_acquisition_fetcher::test_fetcher_cache_hits_and_zero_network` | `StorageCapacityManager` has no `transferred_bytes` | D02 mid-edit |
| `test_artifact_identity::test_actual_public_cli_current_and_legacy_artifacts` | `TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'` | console locale |
| `test_cli::test_installed_entrypoint_subprocess` | `TypeError: argument of type 'NoneType' is not iterable` | console locale |
| `test_recipes::test_readme_example_commands_are_implemented` | asserted output contains a `runpy` `RuntimeWarning` | pre-existing |
| `test_frozen_execution::test_relocated_locked_environment_ignores_editable_hooks_and_bytecode` | `ValueError: installed runtime inventory exceeds time bound` | timing-sensitive |
| `test_reports::test_cli_runs_list_empty_and_populated` | `PlanError: legacy/unfrozen plan cannot execute` | pre-existing |

### Console locale

This machine's console codepage is **cp932**. Two existing tests call
`subprocess.run(..., text=True)`, which decodes with the locale encoding; a
non-UTF-8 byte in the child's output raises `UnicodeDecodeError` inside the
reader thread, leaving `stdout`/`stderr` as `None` and producing the `NoneType`
errors above. The fix is one argument per call site:

```python
subprocess.run(..., encoding="utf-8", errors="replace")
```

It was **not applied here** — `test_artifact_identity.py` and `test_cli.py` are
outside this stage's scope. This branch's own CLI tests decode explicitly for
exactly this reason. Flagged for the integration owner.

## Not run

Real official development-input evaluation, the confirmation tier against real
inputs, any final-tier evaluation, GPU evaluation, and the full-platform audit.
See `reports/P23-D04-D05.md` §7 for the exact operator commands.
