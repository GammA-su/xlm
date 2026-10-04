# Final repairs (I04/I08/I10/I11): commands and exit statuses (2026-10-04)

Environment: Windows 11, CPython 3.12.13 via `uv` (offline, locked, no sync).
Authored data only. Tests set `OMP/MKL/OPENBLAS/NUMEXPR=1`,
`TOKENIZERS_PARALLELISM=false` and `PYTHONDONTWRITEBYTECODE=1`.
`T` = the quality detector, audit, hardening and final-repair test files.

| Command | Exit | Evidence |
|---|---:|---|
| `pytest docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/test_independent.py -n 0 --basetemp F:/qfin-astra` | 0 (49 passed) | `astra-49-unchanged.{log,xml}` |
| `pytest $T -n 8 --dist=worksteal --max-worker-restart=0 -m "not serial_exclusive" --basetemp F:/qfin-fast` | 0 (225 passed) | `quality-fast.{log,xml}` |
| `pytest $T -n 0 -m serial_exclusive --basetemp F:/qfin-serial` | 0 (1 passed) | `quality-serial.log` |
| `ruff format --check` / `ruff check` / `mypy --strict` (20 files) | 0 / 0 / 0 | `ruff-*.log`, `mypy-strict.log` |
| `git diff --check` (new files intent-added) | 0 | `diff-check.log` |
| `scripts/quality_audit_benchmark.py --root F:/qfin-bench-256 --target-mib 256 --workers 1 2 4 8` | 0 | `authored-benchmark.json` |

The first Astra 49-probe run had **exit 1 (48 passed, 1 failed)**. Its
`free_reserve_bytes=10**18` probe hit the new 1 PiB reserve bound. The bound was
raised to 2^62, and the rerun is recorded above.

`QUALITY-AUDIT-RECHECK-1883093/` is present but empty in this worktree. Its probes
could not be run; equivalent repair-edge probes are in
`tests/test_quality_final_repairs.py`.

NOT RUN:

- the full repository suite;
- real data;
- CUDA and network tests.
