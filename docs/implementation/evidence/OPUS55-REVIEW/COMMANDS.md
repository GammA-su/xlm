# Review commands and evidence

All commands ran in `G:\Project\xlm-opus55-review` on
`review/opus55-product`. `scripts/review_opus.ps1` is the checked-in execution
wrapper. It invokes `uv run --offline --locked --no-sync python`, points the
copied environment/editable source, cache, home and TEMP/TMP at this worktree,
sets offline/download prohibitions, sets OMP/MKL/OPENBLAS/NUMEXPR threads to 1,
and disables tokenizer parallelism. No dependency installation or sync occurs.
The copied interpreter is the already installed CPython 3.12.13. Local authored
HTTP servers are the only network sockets used; no public hosts are contacted.

Initial verification (clean expected branch, green base HEAD; inspected output):

```powershell
git branch --show-current
git rev-parse HEAD
git status --short
git worktree list
```

All four exited 0. The requested six local objects were absent from HEAD and
were applied after reporting the discrepancy:

```powershell
git cherry-pick 7da15f4 b15059d 5bf7bbb 84a04e5 2201589 87791e2
```

Exit 0, no conflicts. Correctness inspection included `git show` for each
commit and reading affected source, AGENTS.md, STATUS, previous P31/P31-SSD
reports and the local Opus report. Initial restored-stat and scan tests failed
as expected (2 failed, exit 1); the first mutation/equivalence selection had
22 passes/5 failures (exit 1). Compressed original logs retain those failures.
Regression source was subsequently split into the five final review test files.

## Correctness gates

Focused command, exit 0, 227 passed / 9 deselected / 221.49 s:

```powershell
& scripts/review_opus.ps1 -m pytest tests/test_acquisition_fetcher.py tests/test_acquisition_bounds.py tests/test_acquisition_plan.py tests/test_acquisition_verifier.py tests/test_acquisition_leases.py tests/test_prepare.py tests/test_prepare_bounds.py tests/test_dedup_small_oracles.py tests/test_dedup_lineage.py tests/test_dedup_engine.py tests/test_dedup_throughput.py tests/test_minhash_arrow_kernel.py tests/test_selected_record_encoding.py tests/test_selected_record_concurrency.py tests/test_hf_range_transport.py tests/test_opus_review_accounting.py tests/test_opus_review_equivalence.py -n 0 -q -m 'not performance and not scale and not network and not operator and not cuda and not environment_setup' --basetemp artifacts/opus-review/pytest-focused
```

Tier A, exit 0, 1600 passed / 1 skipped / 103.50 s:

```powershell
& scripts/review_opus.ps1 -m pytest -q --strict-markers -m 'not serial and not performance and not scale and not cuda and not network and not operator and not optional_dependency and not environment_setup' -n 16 --dist=worksteal --max-worker-restart=0 --durations=20 --basetemp artifacts/opus-review/pytest-tier-a
```

The single controller completed before any other test pool or benchmark began.
Compiler skip diagnosis, exit 0, one skip:

```powershell
& scripts/review_opus.ps1 -m pytest tests/test_cuda_execution.py::test_cpu_compile_eager_parity -n 0 -q -rs --basetemp artifacts/opus-review/pytest-skip-reason
```

Final review regressions after splitting files and adding actual spawned-process
budget stress, exit 0, 36 passed / 25.54 s:

```powershell
& scripts/review_opus.ps1 -m pytest tests/test_opus_review_accounting.py tests/test_opus_review_journal.py tests/test_opus_review_encoding.py tests/test_opus_review_prefix.py tests/test_opus_review_equivalence.py -n 0 -q --basetemp artifacts/opus-review/pytest-review-final
```

Real crash controllers (all controller exits 0; read individual restart exits
in JSON, because publication recovery failures are deliberately reported):

```powershell
& scripts/review_opus.ps1 scripts/review_opus_crashes.py
& scripts/review_opus.ps1 scripts/review_opus_crashes.py --output artifacts/opus-review/crashes-corrected
& scripts/review_opus.ps1 scripts/review_opus_crashes.py --output artifacts/opus-review/crashes-mature --mature
& scripts/review_opus.ps1 scripts/review_opus_selected_crashes.py
```

The first command used original six-commit product and the earlier post-commit
final-settlement hook. The latter two used the corrected product and the
checked-in pre-replace hook. Each killed child exits 73; normal restarts exit 0;
publication refusal exits 1. Raw per-case logs remain in the ignored artifacts.
Selected matrix runs four authored formats/variants × five phases. This is
correctness instrumentation, not throughput evidence.

## Exact references and measurement preparation

Existing `G:\Project\xlm-p31-ssd\.venv` and `artifacts\p31-ssd\reference-100k`
were copied into the review worktree. Only the copied editable `.pth` was
adjusted. No source worktree was modified. Green source was extracted locally:

```powershell
git archive --format=zip --output=artifacts/opus-review/green-src.zip 9765a00 src
Expand-Archive -LiteralPath artifacts/opus-review/green-src.zip -DestinationPath artifacts/opus-review/green
& scripts/review_opus.ps1 scripts/review_opus_measure.py prepare
& scripts/review_opus.ps1 scripts/review_opus_measure.py exact --label green
& scripts/review_opus.ps1 scripts/review_opus_measure.py exact --label corrected
& scripts/review_opus.ps1 scripts/review_opus_measure.py compare
```

All exited 0. `prepare` reconstructs frozen methodology from local git object
`1edacbd`; source hashes and the two explicit adaptations are in methodology.json.
Exact mode uses an authored 40-row corpus and fixed localhost repository identity.
Receipt timestamp is fixed explicitly, not silently omitted.

## Performance commands

Each command below exited 0. Runs were sequential; no other review benchmark
ran concurrently. Whole mode runs 1/2/4/8/16 workers in that order and requires
a fresh destination. Retained files are never silently deleted for a rerun.

```powershell
& scripts/review_opus.ps1 scripts/review_opus_measure.py whole --mib 16 --chunk 65536
& scripts/review_opus.ps1 scripts/review_opus_measure.py whole --mib 16 --chunk 1048576
& scripts/review_opus.ps1 scripts/review_opus_measure.py whole --mib 16 --chunk 8388608
& scripts/review_opus.ps1 scripts/review_opus_measure.py whole --mib 64 --chunk 65536
& scripts/review_opus.ps1 scripts/review_opus_measure.py whole --mib 64 --chunk 8388608
& scripts/review_opus.ps1 scripts/review_opus_measure.py selected
& scripts/review_opus.ps1 scripts/review_opus_measure.py selected --profile
& scripts/review_opus.ps1 artifacts/opus-review/methods/benchmark_pipeline.py --output artifacts/opus-review/pipeline-100k --documents 100000 --workers 6 --token-workers 8 --dedup-workers 8 --max-seconds 1800 --tokenizer artifacts/opus-review/reference-100k/tokenizer --source-fixture artifacts/opus-review/reference-100k/source.jsonl.gz --acquisition-loopback --transfer-chunk-bytes 65536
& scripts/review_opus.ps1 artifacts/opus-review/methods/compare_pipeline.py artifacts/opus-review/reference-100k artifacts/opus-review/pipeline-100k --output artifacts/opus-review/pipeline-exact.json
& scripts/review_opus.ps1 scripts/review_opus_cpu.py --label green
& scripts/review_opus.ps1 scripts/review_opus_cpu.py --label corrected
```

Whole plan caps: requests 4096, retry 0, workers <=16, deadline 600 s, ordinary
transfer budget 256 MiB (1 GiB only for 16×64 MiB control). Per-point watchdog
900 s / 4 GiB RSS. Selected corpus is eight bounded authored files; request
cap 100000, retries 0, deadline 3600 s with the tighter 900 s / 4 GiB watchdog.
The frozen pipeline enforces its 1800 s and artifact limits. CPU diagnostic:
50k rows ×3; MinHash 4 sizes ×3 backends ×5 batches ×10 repetitions.

Whole counters observe the original locks and actual `os.fsync`; they do not
replace synchronization or durability. Selected cProfile is a separate
instrumented run; its slower throughput is not substituted into the main
table. Both sets include existing page cache/OS behavior; no cold-cache claim.
Timings from earlier P31-SSD reports were read, not rerun or relabeled.

## Static checks and packaging

Final commands (exact selected paths are in the committed scripts/diff):

```powershell
$reviewPython = @(git diff --name-only 9765a00 -- '*.py') + @(git ls-files --others --exclude-standard -- '*.py')
$reviewPython = @($reviewPython | Sort-Object -Unique)
& scripts/review_opus.ps1 -m ruff check @reviewPython
& scripts/review_opus.ps1 -m ruff format --check @reviewPython
& scripts/review_opus.ps1 -m mypy src/xlm/data/acquisition/disk.py src/xlm/data/acquisition/fetcher.py src/xlm/data/acquisition/progress.py src/xlm/data/acquisition/records.py src/xlm/data/acquisition/selection.py scripts/review_opus_cpu.py scripts/review_opus_crashes.py scripts/review_opus_measure.py scripts/review_opus_selected_crashes.py scripts/review_opus_evidence.py tests/test_acquisition_leases.py tests/test_opus_review_accounting.py tests/test_opus_review_journal.py tests/test_opus_review_encoding.py tests/test_opus_review_prefix.py tests/test_opus_review_equivalence.py --follow-imports=silent --cache-dir artifacts/opus-review/mypy-cache
& scripts/review_opus.ps1 scripts/review_opus_evidence.py
git diff --check
```

Final Ruff check and format check passed on all 24 changed/new Python files;
mypy passed on the 16 listed modules (all exit 0). Static results and exits are
retained in final-static.log.gz/log-manifest.json. Packaging also normalizes
one pre-existing long line in Opus's CPU benchmark; no benchmark logic changes.
The collector only reads existing measurements, checks exact hashes and writes
compact evidence. Failed preliminary tooling lint/type checks were corrected;
no product test was retried until green without a corresponding fix. Full
six-leg acceptance, live network, CUDA and installation remain NOT RUN.

Next step is the narrow recovery/scratch follow-up specified in the report,
not a repeat of all these performance measurements.
