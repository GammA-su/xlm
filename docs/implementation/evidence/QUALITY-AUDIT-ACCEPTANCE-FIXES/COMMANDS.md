# Acceptance fixes (I04, worker-pool normalization, I10): commands and exit statuses (2026-10-04)

## Environment

- Linux container (kernel 6.18, 4 logical CPUs, 15 GiB RAM).
- CPython **3.12.3**, not the pinned 3.12.13, which is not installed in this container.
- uv 0.8.17. The `uv.lock` dependency set was installed with
  `uv sync --locked --python /usr/bin/python3.12` into a scratch environment `$V`.
  The uv cache was empty, so the locked wheels came from PyPI through the agent
  proxy. This was the only network use: package installation. There was no corpus,
  dataset or model download.
- The `cpu`/`eval` extras (torch, lm-eval) were not installed; the quality audit does
  not import them.
- Main tool versions: pytest 9.1.1, pytest-xdist 3.8.0, psutil 7.2.2, pydantic 2.13.5,
  ruff 0.16.8, mypy 2.3.1.
- Tests set `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`, `TOKENIZERS_PARALLELISM=false`
  and `PYTHONDONTWRITEBYTECODE=1`.

All data is authored. There was no real `G:` corpus, no `X:`, no real quality audit,
no real C05, no tokenizer, no training and no push. The C05 overlay probes use the
repository's AUTHORED synthetic C05 flow, the same flow as the existing `c05_flow`
test fixture.

## Frozen probes on Linux: `linux_probe_shim.py`

`QUALITY-AUDIT-INDEPENDENT-96F38F3/test_independent.py` is unchanged; its SHA-256 is
the same as at aa7b580. It assumes Windows in two places:

- `junction()` runs PowerShell;
- two probes read an authored C05 fixture at the literal path
  `F:/qa-tmp-96f38f3/quality-c050/root`.

The pytest plugin `linux_probe_shim.py` supplies these without editing any probe:

- a directory symlink stands in for the junction;
- the authored fixture is rebuilt at that relative path under a scratch working
  directory.

**This is an emulation, not a native Windows run.**

## Directories that could not be used

`QUALITY-AUDIT-RECHECK-1883093/` and `QUALITY-AUDIT-FINAL-RECHECK-AA7B580/` do not
exist in this checkout or in any git ref of the remote. Astra's final targeted I04/I10
edge probes therefore could NOT be run. They were rebuilt from the reported failures
as `tests/test_quality_acceptance_fixes.py`.

## Commands

| # | Command | Exit | Evidence |
|---|---|---:|---|
| 1 | baseline aa7b580 (git worktree), frozen 49 probes, shim, `-n 0` | 1 (48 passed, 1 failed: `test_child_rss_included`, BrokenProcessPool) | reproduction; not kept |
| 2 | fix, frozen 49 probes, shim, `-n 0` (run from a scratch cwd with `--rootdir` = repo) | **0 (49 passed)** | `astra-49-unchanged.{log,xml}` |
| 3 | fix, `test_child_rss_included` alone, 10 repeats | 0 each (10/10 passed) | console |
| 4 | focused: `$V/bin/python -m pytest tests/test_quality_{detectors,audit,hardening,final_repairs,acceptance_fixes}.py tests/test_c06_fast_hardening.py -n 4 --dist=worksteal --max-worker-restart=0 -m "not serial_exclusive"` with the 2 PowerShell-junction tests deselected (run 5 covers them) | 1: 404 passed, 2 failed | `quality-fast.{log,xml}` |
| 5 | the 2 deselected junction tests of `test_quality_hardening.py`, shim, `-n 0` | 0 (2 passed) | `junction-shim.log` |
| 6 | serial: quality files, `-n 0 -m serial_exclusive` | 0 (1 passed) | `quality-serial.log` |
| 7 | `ruff format --check` / `ruff check` / `mypy --strict` (22 files) | 0 / 0 / 0 | `ruff-*.log`, `mypy-strict.log` |
| 8 | `git diff --check` (new files intent-added) | 0 | `diff-check.log` |
| 9 | timing: `scripts/quality_audit_benchmark.py --target-mib 32 --workers 1 4`, 3 alternating rounds fix vs aa7b580 | 0 | `timing-check.json` |
| 10 | artifact equivalence fix vs aa7b580 (8 MiB, same root, workers 1) | n/a | `artifact-equivalence.txt` |
| 11 | mutation checks (temporary, reverted): post-publication gate removed → `after_rename`/withdraw probes 8 failed; pool normalization removed → 6 of 8 pool probes failed | expected failures | console |

### Run 4 per module

| Module | Passed | Failed |
|---|---:|---:|
| `test_quality_acceptance_fixes` (new) | 108 | 0 |
| `test_quality_final_repairs` | 83 | 0 |
| `test_quality_hardening` | 51 | 0 (2 more in run 5) |
| `test_quality_audit` | 40 | 0 |
| `test_quality_detectors` | 49 | 0 |
| `test_c06_fast_hardening` | 73 | **2** |

The quality modules total 331 passed and 0 failed.

The 2 C06 failures are **pre-existing on Linux and unrelated to this change**:

- `test_a1_blocked_workers_are_killed_within_a_bound`
- `test_a1_interrupt_kills_rather_than_drains_workers`

Both assert that the test process has no child processes, and the Linux
`multiprocessing` resource-tracker child is still alive. They fail identically on the
untouched aa7b580 worktree, both with the file alone and within the suite. Windows has
no resource-tracker process.

## NOT RUN

- native Windows;
- the full repository suite;
- the 256 MiB benchmark (scan-path cost is one `max` per chunk; see `timing-check.json`);
- Astra's FINAL-RECHECK-AA7B580 probes (directory absent);
- real data, CUDA and network tests.
