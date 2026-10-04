# Cleaning policy v2: commands, exit statuses, environment (2026-10-04)

Environment: Linux container (4 vCPU), CPython 3.12.13, the locked `--extra eval`
environment. Base: remote tip `24bb9a1` (operator's v1 freeze from audit-v3). Authored
fixtures only. No `G:`, no `X:`, no real corpus, no C05, no tokenizer, no training, no
push.

| # | Command | Exit | Evidence |
|---|---|---:|---|
| 1 | `.venv/bin/python -m pytest tests/test_quality_cleaning_v2.py -n 0 -q -p no:cacheprovider` | 0 (42 passed) | `cleaning-v2-tests.log` |
| 2 | `.venv/bin/python -m pytest tests/test_quality_cleaning.py -n 0 -q -p no:cacheprovider` (v1 tests, file unchanged) | 0 (80 passed) | `cleaning-v1-tests.log` |
| 3 | `.venv/bin/python -m pytest tests/test_quality_{performance,audit,detectors,hardening,final_repairs,acceptance_fixes}.py -n 2 --dist=worksteal --max-worker-restart=0 -m "not serial_exclusive" -q -p no:cacheprovider` | 1: **374 passed, 2 failed**. The 2 are the known PowerShell-junction tests (no `powershell` on Linux) | `phase-a-suites.log` |
| 4 | the 2 junction tests under `QUALITY-AUDIT-ACCEPTANCE-FIXES/linux_probe_shim.py` | 0 (2 passed) | `junction-shim.log` |
| 5 | v1 reproducibility. The same authored corpus and Phase-A audit go through the v1 freeze and v1 dry run (authored test cuts) twice: once with the previous code (`git worktree` at `24bb9a1`) and once with this change. Then `cmp` | 0 | `v1-reproducibility.txt`. Frozen v1 file IDENTICAL. Dry-run artifacts are IDENTICAL, or identical after normalizing only the code-bound binding digest |
| 6 | `ruff format --check` / `ruff check` / `mypy --strict` on `src/xlm/data/quality` and the three cleaning test files | 0 / 0 / 0 | `ruff-format.log`, `ruff-check.log`, `mypy-strict.log` |
| 7 | `git diff --cached --check` | 0 | `diff-check.log` |

NOT RUN: native Windows; the real v2 freeze, dry run and report against audit-v3
(operator).
