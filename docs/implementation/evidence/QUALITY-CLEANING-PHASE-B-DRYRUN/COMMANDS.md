# Quality cleaning Phase B dry run: commands, exit statuses, environment (2026-10-04)

## Environment

- Linux container, kernel 6.18, 4 logical CPUs, 15 GiB RAM (not the operator's
  Ryzen 7 5700X3D / Windows machine).
- CPython 3.12.13, installed with `uvx --from 'uv>=0.9' uv python install 3.12.13`
  (the preinstalled uv 0.8.17 does not know 3.12.13). Dependencies:
  `uvx --from 'uv>=0.9' uv sync --locked --extra eval` (PyPI through the agent proxy,
  package installation only). The `cpu` extra (torch) was not installed; the quality
  package does not import torch.
- Test runs set `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`, `TOKENIZERS_PARALLELISM=false`,
  `PYTHONDONTWRITEBYTECODE=1`.
- Base `51e0098bd24d6509191cc13576b8db5dc4b1a435` (perf/quality-audit-speedup), branch
  `feat/quality-phase-b-dryrun`.

All data is authored: `tests/cleaning_fixtures.py`, `tests/quality_fixtures.py`, the
benchmark's authored corpus generator (`xlm.data.quality.bench.build_corpus`) and the
repository's AUTHORED synthetic C05 flow (`scripts/c05_synthetic_flow.py`, as in the
existing `c05_flow` fixture). No `G:`, no `X:`, no real or production data, no real C05,
no tokenizer fit, no training, no cleaned corpus, no push.

## Commands

| # | Command | Exit | Evidence |
|---|---|---:|---|
| 1 | `.venv/bin/python -m pytest tests/test_quality_cleaning.py -n 0 -q -p no:cacheprovider` | 0 (55 passed) | `cleaning-tests.log` |
| 2 | `.venv/bin/python -m pytest tests/test_quality_{cleaning,performance,audit,detectors,hardening,final_repairs,acceptance_fixes}.py -n 2 --dist=worksteal --max-worker-restart=0 -m "not serial_exclusive" -q -p no:cacheprovider` | 1: **429 passed, 2 failed**. The 2 failures are the PowerShell-junction tests: `FileNotFoundError: 'powershell'` on Linux, identical before this change | `quality-fast.log` |
| 3 | the 2 junction tests with the existing `QUALITY-AUDIT-ACCEPTANCE-FIXES/linux_probe_shim.py` (`-p linux_probe_shim`, scratch cwd, `-n 0`) | 0 (2 passed) | `junction-shim.log` |
| 4 | serial selection: the same 7 modules, `-n 0 -m serial_exclusive` | 0 (1 passed) | `quality-serial.log` |
| 5 | `ruff format --check` / `ruff check` / `mypy --strict` on `src/xlm/data/quality tests/test_quality_cleaning.py tests/cleaning_fixtures.py` | 0 / 0 / 0 | `ruff-format.log`, `ruff-check.log`, `mypy-strict.log` |
| 6 | `.venv/bin/python docs/implementation/evidence/QUALITY-CLEANING-PHASE-B-DRYRUN/throughput_compare.py $S/bench 128` (3 runs; first line before the bounded-sampler change) | 0 | `throughput-audit-vs-dryrun-128mib.jsonl` |
| 7 | live demonstration: `python -m xlm.data.quality clean-dry-run ... --workers 2 --progress-interval-seconds 0.2 --progress-log $S/p.log`, then `clean-materialize-review ... --operator-confirm --outcomes DROP` on the authored fixture corpus (scratch only) | 0 / 0 | console; formats are covered by tests |
| 8 | `git diff --check` (staged) | 0 | `diff-check.log` |

`$S` is the session scratch directory.

## NOT RUN

- native Windows: process spawn cost, 12/16 workers on 8C/16T, junction tests
  without the shim;
- the real freeze, the real dry run, `clean-report` and review materialization on
  production data (operator only);
- the full repository suite (not an acceptance gate; focused selection only);
- any real data, real C05, CUDA or network test.
