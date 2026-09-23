# P29B command and validation ledger

All commands run in `D:\Project\xlm-perf-astra-global`. No network or installs.
Common PowerShell prefix (used for every Python/quality command):

```powershell
$env:UV_PROJECT_ENVIRONMENT='D:\Project\xlm-integration-20260922\.venv'
$env:PYTHONPATH=(Join-Path $PWD 'src')
$env:MYPYPATH=$env:PYTHONPATH
$env:PYTHONDONTWRITEBYTECODE='1'
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
```

The listings below abbreviate only this shared prefix. Commands are sequential,
not concurrent timing jobs. `--no-sync` uses the existing environment read-only.

## Focused implementation validation

Each line uses `uv run --offline --locked --no-sync python -m pytest`, with `-n 0`.
Test files are under `tests/`. The selections overlap and must not be summed.

| Selection (filenames) | Exit / result |
|---|---|
| `test_token_map_cache.py test_token_publication.py test_tokenizer_fit_stream.py test_parallel_tokens.py test_token_shards.py test_performance_tokenization.py` | 0; 43 passed, 4.39 s |
| `test_token_map_cache.py test_tokenizers.py test_tokenizer_regime.py test_pool_freeze_regime.py test_mixture_stream.py test_trainer_data.py test_training_resolution.py test_trainer_mixture.py test_packing_scheduling.py` | 0; 182 passed, 31.39 s |
| `test_sampling_trace.py test_token_batches.py test_parallel_tokens.py test_token_map_cache.py test_performance_tokenization.py test_mixture_stream.py test_trainer_mixture.py test_packing_scheduling.py` | 0; 94 passed, 22.77 s |
| `test_packing_lookup.py test_packing_scheduling.py` | 0; 31 passed, 0.47 s |
| `test_parallel_tokens.py` after final RSS guard | 0; 8 passed, 3.13 s |

All are authored offline fixtures. Full suite, live-source tests, CUDA tests and
release acceptance were NOT RUN. The CPU torch NumPy-unavailable warning is real;
no test was relabeled as a CUDA pass.

Final scoped quality commands:

```powershell
$paths=@(git diff 44c19b8 --name-only -- '*.py')
uv run --offline --locked --no-sync ruff format --check @paths
uv run --offline --locked --no-sync ruff check @paths
$typed=@($paths | Where-Object { $_ -notlike 'tests/*' })
uv run --offline --locked --no-sync mypy --follow-imports=silent @typed
git -c core.whitespace=cr-at-eol diff --check
```

Ruff and scoped mypy passed (24 Python files / 16 typed source and script files).
Earlier style/import errors were corrected. Default transitive mypy encounters
missing NumPy in unchanged training components/checkpoint modules; no dependency
was installed and no unrelated code changed to hide it. An initial script check
also lacked local MYPYPATH and inspected the installed package; the explicit
prefix above fixes discovery. The final claim is scoped, not repository-wide.

## Vocabulary, probes and worker matrix

```powershell
uv run --offline --locked --no-sync python scripts/benchmark_token_path.py prepare --output artifacts/perf/p29b-fixture
uv run --offline --locked --no-sync python scripts/benchmark_token_path.py probe --output artifacts/perf/p29b-probe-before --source artifacts/perf/p29b-before-100k/split/documents.jsonl --tokenizer artifacts/perf/p29b-fixture/tokenizer --documents 10000
uv run --offline --locked --no-sync python scripts/benchmark_token_path.py shard --output artifacts/perf/p29b-sharded --source artifacts/perf/p29b-before-100k/split/documents.jsonl --documents 100000
foreach ($w in @(1,2,4,8)) {
  uv run --offline --locked --no-sync python scripts/benchmark_token_path.py scaling --output "artifacts/perf/p29b-scale-w$w" --source artifacts/perf/p29b-sharded --tokenizer artifacts/perf/p29b-fixture/tokenizer --workers $w
}
foreach ($w in @(1,8)) {
  uv run --offline --locked --no-sync python scripts/benchmark_token_path.py scaling --output "artifacts/perf/p29b-batch-w$w" --source artifacts/perf/p29b-sharded --tokenizer artifacts/perf/p29b-fixture/tokenizer --workers $w --batch 128
}
$env:TOKENIZERS_PARALLELISM='true'
$env:RAYON_NUM_THREADS='8'
uv run --offline --locked --no-sync python scripts/benchmark_token_path.py scaling --output artifacts/perf/p29b-batch-w1-t8 --source artifacts/perf/p29b-sharded --tokenizer artifacts/perf/p29b-fixture/tokenizer --workers 1 --batch 128
$env:RAYON_NUM_THREADS='2'
uv run --offline --locked --no-sync python scripts/benchmark_token_path.py scaling --output artifacts/perf/p29b-batch-w4-t2 --source artifacts/perf/p29b-sharded --tokenizer artifacts/perf/p29b-fixture/tokenizer --workers 4 --batch 128
$env:TOKENIZERS_PARALLELISM='false'
uv run --offline --locked --no-sync python scripts/benchmark_token_path.py probe --output artifacts/perf/p29b-probe-100k --source artifacts/perf/p29b-before-100k/split/documents.jsonl --tokenizer artifacts/perf/p29b-fixture/tokenizer --documents 100000
$env:TOKENIZERS_PARALLELISM='true'
$env:RAYON_NUM_THREADS='8'
uv run --offline --locked --no-sync python scripts/benchmark_token_path.py direct --output artifacts/perf/p29b-direct-batch-100k --source artifacts/perf/p29b-before-100k/split/documents.jsonl --tokenizer artifacts/perf/p29b-fixture/tokenizer --documents 100000 --batch 128
$env:TOKENIZERS_PARALLELISM='false'
```

All completed with exit 0. The matrix used a prototype fixed-lane process-pool
harness; final product uses explicitly owned spawn processes to guarantee one
model initialization per process even for small lanes. Matrix JSON records CPU
and initialization per lane; process startup/assembly are separate costs. Final
pipeline exercises the product implementation. Thread environment metadata was
added after early matrix runs; the commands above document those settings.

## Matched final pipelines

Frozen source/harness commit `060f3d9`. All four commands exit 0. Fresh output
directories, same tokenizer, one cleaning worker. `--reference-p29` changes
only the explicitly pinned BPE/writer/packer modules. The 100-document reference
smoke (`p29b-reference-smoke`, otherwise identical args) also exited 0.

```powershell
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/p29b-matched-before-10k --documents 10000 --tokenizer artifacts/perf/p29b-fixture/tokenizer --reference-p29
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/p29b-matched-after-10k --documents 10000 --tokenizer artifacts/perf/p29b-fixture/tokenizer
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/p29b-matched-before-100k --documents 100000 --tokenizer artifacts/perf/p29b-fixture/tokenizer --reference-p29
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/p29b-matched-after-100k --documents 100000 --tokenizer artifacts/perf/p29b-fixture/tokenizer --token-workers 8
foreach ($size in @('10k','100k')) {
  uv run --offline --locked --no-sync python scripts/compare_pipeline.py "artifacts/perf/p29b-matched-before-$size" "artifacts/perf/p29b-matched-after-$size" --output "artifacts/perf/p29b-matched-compare-$size.json"
}
```

Earlier pipeline command shapes were identical without `--reference-p29`, using
`p29b-before-{10k,100k}` before edits, `p29b-after-{10k,100k}` during exploration,
and `p29b-final-{10k,100k}` before the monitor correction. All exited 0; comparison
JSONs also exited 0. They are retained as diagnostic evidence. In the latter
100k run, process discovery/disk monitoring was unnecessarily active in serial
stages, confounding wall time. The final harness limits it to parallel stages;
matched runs supersede those timings. The 10k `after` trial included the rejected
span experiment. These are not extra independent replications of final code.

## Loader, fit and exactness

```powershell
uv run --offline --locked --no-sync python scripts/benchmark_loader_cache.py --shard artifacts/perf/p29b-before-100k/tokens --output artifacts/perf/p29b-cache.json
uv run --offline --locked --no-sync python scripts/benchmark_loader_cache.py --shard artifacts/perf/p29b-before-100k/tokens --output artifacts/perf/p29b-mixture-before-profile.json --mixture-steps 20 --profile
uv run --offline --locked --no-sync python scripts/benchmark_loader_cache.py --shard artifacts/perf/p29b-before-100k/tokens --output artifacts/perf/p29b-mixture-before.json --mixture-steps 50
uv run --offline --locked --no-sync python scripts/benchmark_loader_cache.py --shard artifacts/perf/p29b-before-100k/tokens --output artifacts/perf/p29b-mixture-after.json --mixture-steps 50
uv run --offline --locked --no-sync python scripts/benchmark_loader_cache.py --shard artifacts/perf/p29b-before-100k/tokens --output artifacts/perf/p29b-mixture-final.json --mixture-steps 50
uv run --offline --locked --no-sync python scripts/benchmark_token_oracles.py --tokenizer artifacts/perf/p29b-fixture/tokenizer --source artifacts/perf/p29b-before-100k/split/documents.jsonl --output artifacts/perf/p29b-oracles-final.json
uv run --offline --locked --no-sync python scripts/benchmark_packing_path.py --shard artifacts/perf/p29b-before-100k/tokens --output artifacts/perf/p29b-packing-final.json
uv run --offline --locked --no-sync python scripts/benchmark_token_path.py prepare --output artifacts/perf/p29b-fixture-final
uv run --offline --locked --no-sync python scripts/benchmark_h2d.py --mib 8 --iterations 100
```

Before/after commands were run at the indicated implementation points. Final
packing script loads the pinned original packer; the initial experiment used a
local candidate function which is now removed. The initial oracles command used
`p29b-oracles.json` and measured the since-rejected prefix-sum candidate. All
completed CPU experiments exit 0. CUDA transfer exits **77 / NOT RUN**.
The earlier `--mixture-steps 500` run was manually stopped, exit 1, no success
report; the final CLI now rejects counts above 100. There was no retry-until-green
or dropped correctness assertion.

Final full-file SHA comparisons cover all four token files in every worker/batch
matrix shard, direct native-batch output versus scalar reference, both integrated
pipelines and the rebuilt 32,768-entry tokenizer JSON/fingerprint. Hashes and
assertion counts are in `exactness.json`; comparison commands exit 0. Profile
text is extracted with Python `pstats.Stats(...).sort_stats('tottime')`, without
running the benchmark again. No network tooling is involved.

```powershell
uv run --offline --locked --no-sync python docs/implementation/evidence/P29B/collect_results.py
```

Collector exit 0: seven variants, 180 files each, direct-batch/final-fit equality,
and every full mixture digest equal. Its own Ruff format/check also passed after
wrapping long report strings. `quality.json` records the final four scoped
quality exits as zero. The collector copies reports, not corpora or tokenizer
weights, and inventories retained bytes without deleting anything.

## Retention

Resource and retained-byte inventory are in RESULTS/environment JSON. Job-local
temporary input/worker shards are automatically cleaned by their own lifetime.
Retained diagnostics and prior P29 evidence were not deleted. P29's earlier
cleanup was rejected by automatic approval review as "blocked by policy"; this
session did not retry it or use another deletion mechanism. Per-run limits do
not imply an aggregate cap across all retained runs.
