# Quality audit Phase A: commands and exit statuses (2026-10-04)

Environment:

- Windows 11 (10.0.26200), 16 logical and 8 physical cores, 72 GiB RAM;
- CPython 3.12.13 via `uv` (offline, locked; `uv sync --offline --locked --extra cpu --extra eval`);
- worktree `F:/Project/xlm-quality-audit`, branch `feat/global-quality-audit`
  (base `40ce62a`).

Every input was an authored fixture or authored synthetic data. There was no
network access, no `X:` access and no real corpus.

Test settings: `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS` and
`NUMEXPR_NUM_THREADS` were set to 1, and `TOKENIZERS_PARALLELISM=false`.

| Command | Exit | Log |
|---|---:|---|
| `uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_quality_detectors.py tests/test_quality_audit.py -n 8 --dist=worksteal --max-worker-restart=0 -p no:cacheprovider -q` | 0 (81 passed) | `pytest-quality.log` |
| `uv run --offline --locked --no-sync ruff format --check <15 files>` | 0 | `ruff-format.log` |
| `uv run --offline --locked --no-sync ruff check <15 files>` | 0 | `ruff-check.log` |
| `uv run --offline --locked --no-sync mypy --strict <15 files>` | 0 | `mypy-strict.log` |
| `git add -N <new paths>; git diff --check; git reset -q -- <new paths>` | 0 | `diff-check.log` |
| `PYTHONPATH=. uv run --offline --locked --no-sync python scripts/quality_audit_benchmark.py --root <scratch>/bench --output <scratch>/bench-result.json --target-mib 256 --workers 1 4 8 16` | 0 | `authored-benchmark.json` |
| aggregation-scale check: 2,035 replayed benchmark units through `report.build_artifacts` (scratch script, peak RSS sampled every 50 ms) | 0 | `aggregation-scale.json` |

The `<15 files>` are:

- `src/xlm/data/quality`
- `tests/test_quality_audit.py`
- `tests/test_quality_detectors.py`
- `tests/quality_fixtures.py`
- `scripts/quality_audit_benchmark.py`

NOT RUN:

- the full offline acceptance suite;
- the serial selection;
- any CUDA, network or real-corpus run;
- `materialize-review` on real text (operator only).
