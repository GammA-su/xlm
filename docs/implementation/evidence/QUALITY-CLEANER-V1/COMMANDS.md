# Quality cleaner v1 (Phase C): commands, exit statuses, environment (2026-10-04)

Environment: native Windows 11 Pro (10.0.26200), 16 logical CPUs, CPython 3.12
(`.python-version`), the locked `--extra cpu --extra eval` environment via
`uv run --offline --locked`. Base: `c9de734` (operator's frozen v2). Authored fixtures
and synthetic data only. No `G:`, no `X:`, no real canonical file, no production
cleaning, no C05, no tokenizer, no training, no push. Test runs set
`OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1` and `TOKENIZERS_PARALLELISM=false`. A short
`--basetemp` (`C:/t/qc/...`) avoids long Windows temp paths.

`U` = `uv run --offline --locked --extra cpu --extra eval`.

| # | Command | Exit | Evidence |
|---|---|---:|---|
| 1 | `U python -m pytest tests/test_quality_cleaning_production.py -n 0 -q -p no:cacheprovider -rs` | 0: 41 passed, 0 skipped (real Windows junctions) | `production-tests.log` |
| 2 | `U python -m pytest tests/test_quality_{cleaning_production,cleaning,cleaning_v2,performance,audit,detectors,hardening,final_repairs,acceptance_fixes}.py -n 8 --dist=worksteal --max-worker-restart=0 -m "not serial_exclusive" -q -p no:cacheprovider -rs` | 1: **531 passed, 7 skipped, 1 failed**. Skips: POSIX `/proc` fd inspection. Failure: `test_status_discovers_a_running_audit_process_tree` (see 3) | `quality-suites.log` |
| 3 | Row 2's failing node, alone, on the UNMODIFIED base `c9de734` (temporary detached worktree, removed afterwards) | 1: fails identically. It is pre-existing on native Windows (the venv `python.exe` launcher PID differs from the interpreter PID) and not a regression | `base-c9de734-status-pid.log` |
| 4 | `U python -m pytest tests/test_quality_hardening.py -m serial_exclusive -n 0 -q -p no:cacheprovider` | 0: 1 passed | `serial.log` |
| 5 | `U python docs/implementation/evidence/QUALITY-CLEANER-V1/bench_production.py C:/t/qc/bench 768` (bounded synthetic throughput, 16 workers) | 0 | `bench.log` |
| 6 | `U ruff format --check src/xlm/data/quality tests/test_quality_cleaning_production.py tests/production_fixtures.py docs/implementation/evidence/QUALITY-CLEANER-V1/bench_production.py` | 0 | `ruff-format.log` |
| 7 | `U ruff check` (same paths) | 0 | `ruff-check.log` |
| 8 | `U mypy --strict src/xlm/data/quality tests/test_quality_cleaning_production.py tests/production_fixtures.py tests/test_quality_cleaning.py tests/test_quality_cleaning_v2.py docs/implementation/evidence/QUALITY-CLEANER-V1/bench_production.py` | 0 | `mypy-strict.log` |
| 9 | `git diff --cached --check` | 0 | `diff-check.log` |

Bench (row 5): on the same 805 MB synthetic corpus and 16 workers, production ran at
64.6 MB/s end-to-end (clean phase 70.2 MB/s) against 65.8 MB/s for the Phase-B dry
run. Verification with `--compare-sources` ran at 236 MB/s with a warm page cache.
Other sessions may have shared the machine. After the bench run, `bench_production.py` was
only reformatted (ruff) and its `timed` helper re-typed; its behavior is unchanged.

NOT RUN:
- the real `clean-production`, `clean-production-verify` and
  `clean-production-manifest` on `G:` (operator);
- native Linux;
- the full repository acceptance gate (not a milestone acceptance test).
