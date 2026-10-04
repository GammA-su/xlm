# Quality audit performance: commands, exit statuses, environment (2026-10-04)

## Environment

- Linux container, kernel 6.18, **4 logical CPUs**, 15 GiB RAM. Not the operator's
  Ryzen 7 5700X3D (8C/16T, Windows); 8/12/16-worker results here are oversubscribed.
- CPython **3.12.13** (pinned), installed with uv 0.12.23 (`uvx --from 'uv>=0.9'`:
  the preinstalled uv 0.8.17 did not know 3.12.13). The locked dependency set was
  installed with `uv sync --locked --extra eval` (network: PyPI through the agent
  proxy, package installation only). The `cpu` extra (torch, from the PyTorch index)
  could not be downloaded (`tunnel error`); the quality audit does not import torch.
- numpy 2.5.3, psutil 7.2.2, pytest 9.1.1, pytest-xdist 3.8.0, ruff 0.16.8, mypy 2.3.1.
- Test runs set `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`, `TOKENIZERS_PARALLELISM=false`,
  `PYTHONDONTWRITEBYTECODE=1`.

All data is authored: the moved generator of `scripts/quality_audit_benchmark.py`,
the `tests/quality_fixtures.py` corpus and the repository's AUTHORED synthetic C05
flow (`scripts/c05_synthetic_flow.py`, as in the existing `c05_flow` fixture). No
`G:`, no `X:`, no real C05, no real audit, no push.

`$S` is the session scratch directory; `old/` is `git worktree add $S/old c517fe0`.

## Commands

| # | Command | Exit | Evidence |
|---|---|---:|---|
| 1 | profiling scripts in `$S` (cProfile of `process_chunk`, per-section timers inserted into a copy of `analyze`, per-thread parent CPU via psutil, 32 MiB pickling/pipe probe, overlay stub with 100-200k authored rows) | 0 | numbers in the report, section 2 |
| 2 | scratch fuzz: frozen reference vs optimized `analyze`, 31,850 texts | 0 (0 mismatches) | report section 4 |
| 3 | `equiv_inputs.py` (fixture corpus + AUTHORED C05 flow); `equiv_run.py` with `old/src` (4 workers) and with this change at 1/2/4 workers; `equiv_cmp.py` | 0 | `artifact-equivalence.txt` (90/90 IDENTICAL) |
| 4 | `overlay_compare.py $S 200000 old` (old worktree) vs `new` | 0 | `overlay-equivalence.txt` |
| 5 | `exp.py` matrix at 4 workers: chunk 8/16/32/64 MiB, queue 2/3/4x, old vs new scheduler (512 MiB authored) | 0 (one early run of a buggy old-scheduler emulation crashed and was re-run after the fix) | `chunk-queue-scheduler-4workers.jsonl` |
| 6 | `cmp_all.sh`: same 512 MiB authored corpus, old worktree vs this change, workers 1/2/4/8 (`cmp_run.py`) | 0 | `throughput-old-vs-new-512mib.jsonl` |
| 7 | `uv run`-equivalent `.venv/bin/python -m pytest tests/test_quality_performance.py tests/test_quality_{audit,detectors,hardening,final_repairs,acceptance_fixes}.py -n 4 --dist=worksteal --max-worker-restart=0 -m "not serial_exclusive"` | 1: **374 passed, 2 failed** (the two PowerShell-junction tests; no PowerShell on Linux) | `quality-fast.{log,xml}` |
| 8 | the 2 junction tests with the existing `QUALITY-AUDIT-ACCEPTANCE-FIXES/linux_probe_shim.py` (`-p linux_probe_shim`, scratch cwd, `-n 0`) | 0 (2 passed) | `junction-shim.log` |
| 9 | serial selection: same 6 modules, `-n 0 -m serial_exclusive` | 0 (1 passed) | `quality-serial.log` |
| 10 | `ruff format --check` / `ruff check` / `mypy --strict` on the 13 changed Python files | 0 / 0 / 0 | `ruff-*.log`, `mypy-strict.log` |
| 11 | `git diff --check` (new files intent-added; trailing spaces stripped from the captured pytest log/xml first) | 0 | `diff-check.log` |
| 12 | `.venv/bin/python -m xlm.data.quality benchmark --scratch $S/benchfinal --corpus-mib 512 --budget-seconds 300` | 0 (all six worker counts within 300 s; artifacts identical across worker counts; best 4 workers on 4 vCPUs) | `benchmark-cloud-final.{json,stderr.log}` |
| 13 | live demonstrations: `audit ... --workers 4 --progress-interval-seconds 2 --progress-log`, and `status --watch` against a background audit | 0 / 0 | console (formats are covered by tests) |

The new module alone: 43 passed. The new tests include the concurrency proof, the 1/2/4/8/12
identity, the head-of-line test, progress/log/no-leak tests, status read-only and live
discovery, resume/interruption progress and the equivalence oracles.

## NOT RUN

- native Windows (status process discovery, spawn cost, 12/16 workers on 8C/16T);
- the full repository suite (not an acceptance gate; focused selection only);
- any real data, real C05, CUDA or network tests.
