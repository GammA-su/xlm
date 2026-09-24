# P32 performance closeout commands

All commands ran from `G:\Project\xlm-p32-perf-closeout` on
`perf/p32-happy-path-closeout`. No other worktree was modified. The wrapper
`scripts/p32_perf.ps1` checks this branch and sets private environment/cache/home/
temp roots, offline flags, Python paths, native threads=1, and
`TOKENIZERS_PARALLELISM=false`. Its execution command is:

```powershell
uv run --offline --locked --no-sync python @args
```

## Admission and existing environment

```powershell
git branch --show-current
git rev-parse HEAD
git status --short
git worktree list
```

Branch matched; HEAD was `8b1476655241528d69d2797bc681f6aeb7ee12f5`; short status
was empty. All four commands exited 0. No checkout/reset occurred.

The existing `.venv` was copied read-only from `G:\Project\xlm-p32-heavy-crash`
with `robocopy /E /COPY:DAT /DCOPY:DAT /R:0 /W:0 /NFL /NDL /NP`, writing only
this worktree's `.venv`. The local `_editable_impl_xlm.pth` was rebound to this
worktree's `src`. Robocopy's success status was checked using its documented
failure threshold (8); 46,951 files / 1.008 GiB copied, zero failures. No uv
sync, dependency install or Python download was attempted. Environment/package
versions and locked-input SHA values are in `environment.json.gz`.

## Frozen methods and baseline before product edits

```powershell
./scripts/p32_perf.ps1 -c "import review_opus_measure as m; from pathlib import Path; m.OUT=Path('artifacts/p32-perf-closeout'); m.METHODS=m.OUT/'methods'; m.prepare()"
./scripts/p32_perf.ps1 scripts/p32_perf_measure.py whole --label baseline
./scripts/p32_perf.ps1 scripts/p32_perf_measure.py profile --label baseline
./scripts/p32_perf.ps1 scripts/p32_perf_measure.py exact --label baseline
```

All exit 0. `whole` is three repetitions at each of 1/8/16 workers; `profile`
is a separate diagnostic repetition at each count. The original methodology
hashes and API-only adaptations are retained in `methodology.json.gz`.
The whole fixture is RNG seed 31, 16 × 64 MiB, localhost port 47183, 64 KiB
reads. Each frozen point has a 900-second/4-GiB-RSS watchdog plus the bounded
acquisition plan. Output identities/total bytes are asserted, never speed.
Payload generation and output SHA checking are outside the throughput timer.

## Product repair verification and after measurements

```powershell
./scripts/p32_perf.ps1 -m pytest tests/test_p32_publication.py -n 0 -q --basetemp artifacts/p32-perf-closeout/pytest-publication
./scripts/p32_perf.ps1 scripts/p32_perf_runs.py focused
./scripts/p32_perf.ps1 scripts/p32_perf_measure.py exact --label current
./scripts/p32_perf.ps1 scripts/p32_perf_measure.py compare
./scripts/p32_perf.ps1 scripts/p32_perf_measure.py whole --label current
./scripts/p32_perf.ps1 scripts/p32_perf_measure.py profile --label current
```

All exit 0. Publication 33 passed; focused 177 passed / one capability skip /
one performance deselection; exact eight cases match. All nine after throughput
points and three profiles remain, including the severe third-repetition fsync
stalls and the slow w1 profile. No failed product test or benchmark was retried.
The three-run aggregate elapsed time is slower after the change despite the
median happy-path/CPU improvement; this limitation is explicitly reported.

## Process-death matrices and related callers

```powershell
./scripts/p32_perf.ps1 scripts/p32_perf_runs.py crashes
./scripts/p32_perf.ps1 scripts/p32_perf_runs.py related
```

Both exit 0. The first command sequentially invokes:

```powershell
python scripts/review_opus_crashes.py --output artifacts/p32-perf-closeout/crashes-early
python scripts/review_opus_crashes.py --output artifacts/p32-perf-closeout/crashes-mature --mature
python scripts/review_opus_selected_crashes.py --output artifacts/p32-perf-closeout/crashes-selected
python scripts/review_opus_selected_crashes.py --output artifacts/p32-perf-closeout/crashes-parallel --parallel-publication
```

These show the underlying child arguments; execution uses the already active
offline uv environment, never a separate package installer. Exact executable
paths, argv, exit codes, elapsed time and sampled tree RSS are in each `.run.json.gz`.
Matrix controllers exit 0; intentional process deaths exit 73; all 112 restarts
exit 0 (44/44/20/4). Frozen per-child bounds and assertions remain unchanged.
The related selection has 52 passes, including streaming serialization,
selected concurrency, restored-stat journal mutation and early-prefix ownership.
The runner enforces 1,800 seconds / 24 GiB tree RSS per job; no outer worker pool.

## One frozen 100k pipeline

The latest certified artifact tree was copied read-only from
`G:\Project\xlm-opus55-review\artifacts\opus-review\pipeline-100k` to
`artifacts/p32-perf-closeout/reference-100k` using the same bounded-retry
robocopy options. 95 files / 1.616 GiB copied, zero failures; the copy log,
source fixture SHA and reference report SHA are retained.

```powershell
./scripts/p32_perf.ps1 scripts/p32_perf_runs.py pipeline
```

Exit 0. Eligibility first checks focused/related results, eight-case exactness,
all four complete recovery matrices, and absence of replacement `.tmp` files.
The runner passes `--documents 100000 --workers 6 --token-workers 8
--dedup-workers 8 --max-seconds 1800 --acquisition-loopback`, the copied gzip
source fixture and copied existing tokenizer directory. It then invokes the
unmodified frozen comparator on reference/current roots. Exact argv is in
`pipeline.run.json.gz` and `pipeline-exact.run.json.gz`; both children exit 0.
The frozen methodology includes its existing artifact and per-stage bounds.

## Product commit and single final release gate

```powershell
git add src/xlm/data/acquisition/written.py src/xlm/data/acquisition/publication.py src/xlm/data/acquisition/fetcher.py src/xlm/data/acquisition/records.py src/xlm/data/acquisition/selection.py tests/test_p32_publication.py
git diff --cached --check
git commit -m 'perf(acquisition): avoid redundant success-path payload verification'
./scripts/p32_perf.ps1 scripts/p32_perf_runs.py gate
```

Product commit is `ce2bb32f9487eac560ae9086a6c783d995f3614c`; commit/diff checks
exit 0. The gate starts only after focused/related/exact/crash eligibility;
Tier A is its first leg, not a second standalone Tier-A run. It uses the
unchanged six selectors from `scripts/p32_gate.py`: Tier A at 16 workers
worksteal, core at 4 loadgroup, exclusive at 0, heavy at 4 loadgroup, scale at
0, optional at 4 loadgroup. Every parallel leg has `--max-worker-restart=0`.
All use `-q -rs --strict-markers -o faulthandler_timeout=120 --durations=20`,
fresh per-leg basetemp, and `pytest_evidence` collection/outcome recording.
Only one controller runs at a time; no worker restart/retry. The runner rejects
missing results or unequal worker collections, and the collector additionally
rejects missing/failed outcomes and overlapping gate selections.

Final gate outcomes are recorded in `results.json` and `TABLES.md`; raw logs,
node/phase evidence and exact run argv are retained as `.gz` files.

## Static checks and evidence

Changed product/test modules and the three new Python tools use Ruff checks,
Ruff format checks and strict mypy (`--follow-imports=silent`, private cache).
The final nine-path argv is represented below without repeating it three times:

```powershell
$closeoutPython = @(
  'src/xlm/data/acquisition/written.py',
  'src/xlm/data/acquisition/publication.py',
  'src/xlm/data/acquisition/fetcher.py',
  'src/xlm/data/acquisition/records.py',
  'src/xlm/data/acquisition/selection.py',
  'tests/test_p32_publication.py',
  'scripts/p32_perf_measure.py',
  'scripts/p32_perf_runs.py',
  'scripts/p32_perf_evidence.py'
)
./scripts/p32_perf.ps1 -m ruff check @closeoutPython
./scripts/p32_perf.ps1 -m ruff format --check @closeoutPython
./scripts/p32_perf.ps1 -m mypy @closeoutPython --follow-imports=silent --cache-dir artifacts/p32-perf-closeout/mypy-cache
```

All three final checks exit 0 (`static-final.log.gz`, `static-final.exit.gz`).
The wrapper includes the reconstructed frozen methods in `MYPYPATH`; an initial
tooling-only missing-import diagnostic was resolved by that path declaration.
Preliminary formatting/lint issues were corrected, including CRLF normalization;
the saved initial format failure is retained alongside final checks. No product
correctness test was rerun without a change to address a failure.

```powershell
./scripts/p32_perf.ps1 scripts/p32_perf_evidence.py
git diff --check
```

The collector reads existing evidence only, checks the aggregate, and records
the SHA-256 of each uncompressed archived log/JSON in `sha256.json`. It does not
run or retry any test. `results.json` embeds all whole/profile observations,
per-leg outcomes, sampled resources, environment and pipeline comparison.
All fixture outputs remain in the ignored artifact tree; no live data or model
weights are included in commits. Capability skips remain distinct from passes.

No external network, live acquisition, CUDA, research training, dependency
installation, push or merge was run. Next read-only review:
`git show --stat ce2bb32` and the accompanying final report.
