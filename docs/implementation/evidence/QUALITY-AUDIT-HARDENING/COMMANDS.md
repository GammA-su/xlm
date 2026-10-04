# Quality audit hardening: commands and exit statuses (2026-10-04)

Environment:

- Windows 11 (10.0.26200), 16 logical and 8 physical cores;
- CPython 3.12.13 via `uv` (offline, locked; existing CPU/eval environment, no sync);
- worktree `F:/Project/xlm-quality-audit`, branch `feat/global-quality-audit`,
  base `824b23e`.

All inputs were authored fixtures or authored synthetic data. There was no network
access, no real `G:` corpus, no `X:`, no real C05, tokenizer or training.

Every test command set `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS`
and `NUMEXPR_NUM_THREADS` to 1, plus `TOKENIZERS_PARALLELISM=false` and
`PYTHONDONTWRITEBYTECODE=1` (so no bytecode is written into the historical evidence
directory). `T` below stands for
`tests/test_quality_detectors.py tests/test_quality_audit.py tests/test_quality_hardening.py`.

| Command | Exit | Evidence |
|---|---:|---|
| `uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/test_independent.py -n 0 --basetemp F:/qrep-astra -q --tb=short -p no:cacheprovider --junitxml=<this dir>/astra-probes-unchanged.xml` | 0 (49 passed) | `astra-probes-unchanged.{log,xml}` |
| `... python -m pytest $T -n 8 --dist=worksteal --max-worker-restart=0 -m "not serial_exclusive" --basetemp F:/qrep-fast -q --tb=short -p no:cacheprovider --junitxml=<this dir>/quality-fast.xml` | 0 (142 passed) | `quality-fast.{log,xml}` |
| `... python -m pytest $T -n 0 -m serial_exclusive --basetemp F:/qrep-serial -q --tb=short -p no:cacheprovider` | 0 (1 passed) | `quality-serial.log` |
| `uv run --offline --locked --no-sync ruff format --check <F> <this dir>/measure_hardening.py` | 0 (19 files) | `ruff-format.log` |
| `uv run --offline --locked --no-sync ruff check <F> <this dir>/measure_hardening.py` | 0 | `ruff-check.log` |
| `uv run --offline --locked --no-sync mypy --strict <F>` | 0 (18 files) | `mypy-strict.log` |
| `git add -N <new paths>; git diff --check; git reset -q -- <new paths>` | 0 | `diff-check.log` |
| `git diff --exit-code 824b23e -- docs/implementation/reports/QUALITY-AUDIT-INDEPENDENT-96F38F3.md docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/` | 0 | (historical evidence unchanged) |
| `PYTHONPATH=. uv run --offline --locked --no-sync python scripts/quality_audit_benchmark.py --root F:/qrep-bench-256b --output <this dir>/authored-benchmark.json --target-mib 256 --workers 1 2 4 8` | 0 | `authored-benchmark.json` |
| `uv run --offline --locked --no-sync python <this dir>/measure_hardening.py` | 0 | `measurements.json`, `kernel-profile.txt` |

The `<F>` files are:

- `src/xlm/data/quality`
- `tests/test_quality_audit.py`
- `tests/test_quality_detectors.py`
- `tests/test_quality_hardening.py`
- `tests/quality_fixtures.py`
- `scripts/quality_audit_benchmark.py`

Failed or superseded attempts:

- **First benchmark (`--root F:/qrep-bench-256`): exit 1.** After the 1-worker run
  the script read `scan.peak_process_tree_rss_bytes`, which the first hardening
  draft had moved into the receipt only. The field was restored to the scan result,
  and the benchmark was rerun from a new root. The incomplete root remains on disk.
- **First Astra probe attempt: collection error, non-zero exit (code not captured).**
  `tests/test_quality_audit.py`
  still imported the removed `materialize_review`. The tests were updated and the
  probe file itself was not touched.
- **First `ruff check` of the evidence script: exit 1.** One line was too long; the
  string literal was split without changing its value, and the check was recaptured.
- **Scratch cleanup:** deleting a `--basetemp` root with `rm -rf` was refused by a
  safety check. It was not worked around; pytest clears an existing basetemp itself.

NOT RUN:

- the full repository suite;
- CUDA, network or real-corpus runs;
- real `materialize-review`;
- aggregation-scale and RSS re-measurement at 2,035 units after hardening.
