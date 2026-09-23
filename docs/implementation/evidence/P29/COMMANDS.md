# P29 command evidence

Working directory for every edit, benchmark, test and commit:
`D:\Project\xlm-perf-astra-global`. All commands below use existing installed
dependencies; no `uv sync`, install, network, push or merge was performed.

## Environment prefix

```powershell
$env:UV_PROJECT_ENVIRONMENT='D:\Project\xlm-integration-20260922\.venv'
$env:PYTHONPATH=(Join-Path $PWD 'src')
$env:PYTHONDONTWRITEBYTECODE='1'
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
```

`uv --version`: exit 0, 0.11.6. Python/import probe: exit 0, Python 3.12.13,
torch 2.14.0+cpu, tokenizers 0.23.2, Arrow 25.0.1, 16 CPUs, imports from this
worktree. Torch reports NumPy absent. No CUDA or evaluation-extra installation.

## Benchmark commands

Each command below exited **0**. Generated corpus/binary/profile files remain
untracked under `artifacts/perf/`; small report JSON files were copied into this
evidence directory. All runs are authored fixtures, not live compatibility tests.

```powershell
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/before-10k --documents 10000
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/before-profile --documents 1000 --profile
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/before-100k --documents 100000
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/after-10k --documents 10000
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/after-100k --documents 100000
uv run --offline --locked --no-sync python scripts/benchmark_hotspots.py --output artifacts/perf/hotspots --tokenizer artifacts/perf/before-10k/tokenizer
uv run --offline --locked --no-sync python scripts/benchmark_auxiliary.py --output artifacts/perf/auxiliary
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/after-10k-w2 --documents 10000 --workers 2
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/after-10k-w4 --documents 10000 --workers 4
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/after-profile --documents 1000 --profile
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/final-100k --documents 100000
uv run --offline --locked --no-sync python scripts/benchmark_hotspots.py --output artifacts/perf/final-hotspots --tokenizer artifacts/perf/before-10k/tokenizer
uv run --offline --locked --no-sync python scripts/compare_pipeline.py artifacts/perf/before-10k artifacts/perf/after-10k --output artifacts/perf/comparison-10k.json
uv run --offline --locked --no-sync python scripts/compare_pipeline.py artifacts/perf/before-100k artifacts/perf/after-100k --output artifacts/perf/comparison-100k.json
uv run --offline --locked --no-sync python scripts/compare_pipeline.py artifacts/perf/before-100k artifacts/perf/final-100k --output artifacts/perf/comparison-final-100k.json
uv run --offline --locked --no-sync python scripts/compare_pipeline.py artifacts/perf/after-10k artifacts/perf/after-10k-w2 --output artifacts/perf/comparison-w2.json
uv run --offline --locked --no-sync python scripts/compare_pipeline.py artifacts/perf/after-10k artifacts/perf/after-10k-w4 --output artifacts/perf/comparison-w4.json
```

Before baselines imported the original product modules before any edits. The
100k baseline process was already running with those modules loaded before edits;
it used one in-process cleaning worker. The after runs use the optimized modules.
`final-100k` additionally covers the final added-token compatibility guard.
The hotspot tool loads exactly four old modules from the starting commit as its
reference; the old token writer uses the same optimized tokenizer as its comparator
to isolate binary-writer cost. The batch experiment is no longer a product override;
the final script reproduces it explicitly in tooling, with effective batch cap 128.

Initial `baseline-profile` (1000, `--profile`) and `baseline-10k` (10000) commands
also exited 0, but their process-tree sampler imposed excessive Windows overhead.
They are intentionally excluded from reported speedups. Output is retained locally.
Profiles were read using `pstats.Stats(...).strip_dirs().sort_stats('tottime')`.

## Focused tests

```powershell
uv run --offline --locked --no-sync python -m pytest tests/test_performance_cleaning.py tests/test_performance_tokenization.py tests/test_performance_splits.py tests/test_tokenizers.py tests/test_token_shards.py tests/test_pool_splits.py tests/test_pool_freeze_regime.py tests/test_cleaning_throughput.py tests/test_cleaning_filters.py tests/test_cleaning_repetition.py tests/test_pii_example_domains.py tests/test_likelihood.py tests/test_packing_scheduling.py tests/test_mixture_stream.py -n 4 --dist=worksteal --max-worker-restart=0 -m 'not serial and not network and not cuda and not operator' --basetemp=artifacts/test-temp/regressions -q
```

Exit **0**: **229 passed**, 4 NumPy-absence warnings, 220.41 s. No skips reported.
This selection includes the existing P27B slow 128 MiB and 100k cleaning cases;
their runtime explains the longer focused run. It is not the full offline suite.

```powershell
uv run --offline --locked --no-sync python -m pytest tests/test_cleaning_throughput.py -m serial -n 0 --basetemp=artifacts/test-temp/serial -q
```

Exit **0**: **2 passed, 26 deselected**, 6.15 s. These are the relevant quarantine
ceiling and worker-failure publication checks. Deselected cases were covered in
the preceding selected regression group; this is not a full serial acceptance run.

Intermediate focused test corrections were not concealed:

- Initial `python -m pytest tests/test_performance_exact.py -n 0 -q`: exit **1**,
  15 failed / 3 passed. The new fake-tokenizer fixture omitted `super().__init__`,
  and a cleaning equality check included observational stage duration. Corrected
  fixture initialization and excluded only `duration_ms`/`elapsed_seconds`.
- Same file with `--basetemp=artifacts/test-temp/exact`: exit **1**, 3 passed / 16
  setup errors because the parent directory did not exist. Created the parent.
- Same file with `--basetemp=artifacts/test-temp/exact-fixed`: exit **0**, 19 passed
  in 2.14 s. The file was then split into three scoped test modules for the commits,
  and the added-token compatibility regression brought the new total to 20 cases.
- No retries without a concrete correction, weakened scientific assertions, xfails,
  or hidden skip-to-pass conversions. No whole-repository acceptance selection.

## Scoped quality checks

Final format/check/type commands use these exact file arguments:

```powershell
$scope = @(
 'src/xlm/data/cleaning/features.py', 'src/xlm/data/tokens.py',
 'src/xlm/data/pools/splits.py', 'src/xlm/tokenizers/bpe.py',
 'scripts/benchmark_pipeline.py', 'scripts/benchmark_hotspots.py',
 'scripts/benchmark_auxiliary.py', 'scripts/compare_pipeline.py',
 'tests/test_performance_cleaning.py', 'tests/test_performance_tokenization.py',
 'tests/test_performance_splits.py'
)
uv run --offline --locked --no-sync python -m ruff format --check @scope
uv run --offline --locked --no-sync python -m ruff check @scope
uv run --offline --locked --no-sync python -m mypy @scope
git -c core.whitespace=cr-at-eol diff --check
```

Final command output/exit status is recorded in `quality.json`. Intermediate
Ruff reported syntax/line-length/import issues in new benchmark/tests; corrected
before final checks. Intermediate mypy found one unused helper calling
`iter_shard_records` without its manifest (helper removed), then comparator
variable-name reuse and lambda inference errors (renamed streams, used `partial`).
Final strict checks cover all 11 changed/new Python files. An initial plain
`git diff --check` flagged retained CRLF endings in `tokens.py`; using Git's
`cr-at-eol` whitespace setting preserves that file's existing line endings rather
than rewriting the whole module.

Before each commit, `git status --short`, scoped/staged diff inspection,
`git diff --cached --stat`, `git diff --cached --name-only`, and
`git -c core.whitespace=cr-at-eol diff --cached --check` checked ownership/scope.
No P28-owned implementation path was staged.

## Not run

Closeout cleanup: a PowerShell `Remove-Item -LiteralPath ... -Recurse -Force`
operation over an explicit list of this session's generated `artifacts/perf/`
directories and `artifacts/test-temp`, with resolved absolute path checks against
the worktree artifact root, was rejected before execution by automatic approval
review: “blocked by policy.” There is no process exit code. All files remain;
no alternative deletion mechanism was attempted. Small evidence files were copied
separately afterward using a successful non-destructive command.

Full offline acceptance/release gate; live source pilots; actual HTTP benchmarks;
real tokenizer fitting; full optimizer checkpoint timing; CUDA transfer/training;
official task evaluation; full 1M-row pipeline; production Mix-01 campaign; P28
dedup/FAISS benchmarking. Absence of these runs is not a pass.
