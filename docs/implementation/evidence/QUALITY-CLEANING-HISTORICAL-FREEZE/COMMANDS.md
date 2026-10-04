# Phase-B historical freeze compatibility: commands and exit statuses (2026-10-04)

Environment: Linux container (4 vCPU), CPython 3.12.13, the locked `--extra eval`
environment of the Phase-B evidence. Base `8c3cd32`. Authored fixtures only. No `G:`,
no `X:`, no real data, no C05, no tokenizer, no training, no push.

| # | Command | Exit | Evidence |
|---|---|---:|---|
| 1 | `.venv/bin/python -m pytest tests/test_quality_cleaning.py -n 0 -q -p no:cacheprovider` | 0 (80 passed) | `cleaning-tests.log` |
| 2 | `.venv/bin/python -m pytest tests/test_quality_{performance,audit,detectors,hardening,final_repairs,acceptance_fixes}.py -n 2 --dist=worksteal --max-worker-restart=0 -m "not serial_exclusive" -q -p no:cacheprovider` (the Phase-A receipt suites) | 1: **374 passed, 2 failed**. The 2 are the PowerShell-junction tests (`FileNotFoundError: 'powershell'` on Linux), unchanged | `phase-a-receipt-suites.log` |
| 3 | the 2 junction tests with `QUALITY-AUDIT-ACCEPTANCE-FIXES/linux_probe_shim.py` | 0 (2 passed) | `junction-shim.log` |
| 4 | cross-version run: Phase-A `audit` from a `git worktree` at `382ab90` (32 MiB chunks), then `clean-freeze-policy` at `8c3cd32` (refuses, reproducing the operator error) and with this fix (freezes); the current `receipt.load_receipt` (still refuses); then current `clean-dry-run` + `clean-report` | see log | `cross-version-382ab90.log` |
| 5 | `ruff format --check` / `ruff check` / `mypy --strict` on `src/xlm/data/quality tests/test_quality_cleaning.py tests/cleaning_fixtures.py` | 0 / 0 / 0 | `ruff-format.log`, `ruff-check.log`, `mypy-strict.log` |
| 6 | `git diff --cached --check` | 0 | `diff-check.log` |

NOT RUN: native Windows; the real freeze against `G:/XLM/quality/audit-v3` (operator).
