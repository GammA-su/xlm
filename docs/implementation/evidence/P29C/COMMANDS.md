# P29C command and validation ledger

All commands ran in `D:\Project\xlm-perf-astra-cleaning-v2`, branch
`perf/astra-cleaning-v2`. Starting production code: `5a43a816505fa374371bc10fdea4eacc64186e6f`.
Final production/harness code: `a60ba1c`. No network, dependency sync/install,
live acquisition, model training, push or merge. No xdist controller was started.

## Environment used throughout

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'D:\Project\xlm-integration-20260922\.venv'
$env:PYTHONPATH = (Join-Path $PWD 'src')
$env:MYPYPATH = $env:PYTHONPATH
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
```

Runtime: Windows 11 build 26200, 16 logical CPUs; Python 3.12.13 (MSC 1944 AMD64),
uv 0.11.6, torch 2.14.0+cpu, tokenizers 0.23.2, pyarrow 25.0.1, psutil 7.2.2,
pytest 9.1.1, xdist 3.8.0, Ruff 0.16.8, mypy 2.3.1. NumPy absent; Torch printed
its expected NumPy warning. CUDA unavailable. Existing lock, Python pin and
CPU/CUDA policy unchanged. Borrowed environment was not synchronized.

## Frozen input and pipeline commands

Each command below exited 0. Output paths were fresh. Fixture generation is
local authored data and a diagnostic tokenizer, not a research fit.

```powershell
uv run --offline --locked --no-sync python scripts/benchmark_token_path.py prepare --output artifacts/perf/p29c-fixture
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/p29c-before-100k --documents 100000 --tokenizer artifacts/perf/p29c-fixture/tokenizer --token-workers 8
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/p29c-final-code-100k --documents 100000 --tokenizer artifacts/perf/p29c-fixture/tokenizer --token-workers 8
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/p29c-final-parallel-100k --documents 100000 --tokenizer artifacts/perf/p29c-fixture/tokenizer --token-workers 8 --workers 8
uv run --offline --locked --no-sync python scripts/verify_cleaning_v2.py --before artifacts/perf/p29c-before-100k --after artifacts/perf/p29c-final-code-100k --pipeline --output artifacts/perf/p29c-exact-code.json
uv run --offline --locked --no-sync python scripts/verify_cleaning_v2.py --before artifacts/perf/p29c-before-100k --after artifacts/perf/p29c-final-parallel-100k --pipeline --output artifacts/perf/p29c-exact-parallel.json
```

The baseline command ran before product edits. Its report records starting HEAD;
final reports record `a60ba1c`. Do not rerun a baseline command at final HEAD and
call it a baseline. No general comparator was weakened. The P29C comparator
explicitly reports changed implementation fingerprints, alongside exact payloads.

## Cleaner command matrix

Every row expands this command; exit 0 for every listed benchmark:

```powershell
uv run --offline --locked --no-sync python scripts/benchmark_cleaning_v2.py --input INPUT --output artifacts/perf/OUTPUT --documents DOCS --workers WORKERS --shard-mib SHARD [EXTRA]
```

`full` input is `artifacts/perf/p29c-before-100k/adapted/documents.jsonl`.
`prefix` is `artifacts/perf/p29c-input-10k.jsonl`, copied as the first 10,000 raw
lines of full input (no parsing/reserialization). `heavy` is
`artifacts/perf/p29c-heavy-input.jsonl`.

| Output | Input | DOCS | WORKERS | SHARD | EXTRA |
| --- | --- | --- | --- | --- | --- |
| p29c-profile-before-10k | full | 10000 | 1 | 4 | --profile |
| p29c-profile-10k | prefix | 10000 | 1 | 4 | --profile |
| p29c-profile-before-100k | full | 100000 | 1 | 4 | --profile |
| p29c-clean-before-10k | prefix | 10000 | 1 | 4 | |
| p29c-before-w1 / w2 / w4 / w6 / w8 | full | 100000 | 1 / 2 / 4 / 6 / 8 respectively | 4 | |
| p29c-clean-after-10k | prefix | 10000 | 1 | 4 | |
| p29c-after-w1 / w2 / w4 / w6 / w8 | full | 100000 | 1 / 2 / 4 / 6 / 8 respectively | 4 | |
| p29c-shard-1 | full | 100000 | 8 | 1 | |
| p29c-shard-16 | full | 100000 | 8 | 16 | |
| p29c-dynamic-w8 | full | 100000 | 8 | 4 | --scheduling dynamic |
| p29c-heavy-static | heavy | 10000 | 4 | 1 | --scheduling static |
| p29c-heavy-dynamic | heavy | 10000 | 4 | 1 | --scheduling dynamic |
| p29c-heavy-repeat-dynamic | heavy | 10000 | 4 | 1 | --scheduling dynamic |
| p29c-heavy-repeat-static | heavy | 10000 | 4 | 1 | --scheduling static |
| p29c-sharded-w1 | prefix | 10000 | 1 | 1 | --output-shard-mib 1 --scheduling dynamic |
| p29c-sharded-w4 | prefix | 10000 | 4 | 4 | --output-shard-mib 1 --scheduling dynamic |
| p29c-profile-after-10k | prefix | 10000 | 1 | 4 | --profile |

`--documents` is a cap; the heavy input contains 128 documents, not 10,000.
The initial profile-before-10k uses all 41 planned ranges, including zero-work
ranges after the cap. It is retained as a diagnostic, excluded from the matched
10k headline. Correct prefix profiles plan five units. Before-run wrapper used
the old API without the newly added `scheduling` keyword; same static lanes,
timers, caps and telemetry. Profiling was completed before product changes.

Heavy fixture recipe used `random.Random(29)` and the unchanged
`benchmark_pipeline.WORDS`. Prose: join 400,000 choices from `WORDS + ['the'] * 30`,
then take 1,048,576 characters. CJK: repeat the 128 characters starting at U+4E00
3,000 times, then take `1,048,576 // 3` characters. Write 128 newline-terminated
`fixture_document(text, index).to_dict()` records with `ensure_ascii=False`,
selecting CJK when `index % 4 == 0`, prose otherwise. Actual file: 134,286,852
bytes. This creates unequal acceptance/work per static lane; CJK is the cheaper
lane in this measurement. All timing jobs ran sequentially. Reverse-order repeat
tests schedule order; both trials are reported, not best-of selection.

## Candidates, long records and stage oracle

Benchmark/oracle commands exited 0. The first evidence collector exited 1 because
it compared the initial partial 10k diagnostic's `is_partial_sample=true` with
the complete prefix's `false`. The collector now keeps that diagnostic separate,
checks its accepted bytes and partial flag, and does not erase the scientific
field. Corrected collection exited 0: 31 reports, 24 complete cleaner comparisons
and two verified manifests. The near-miss harness deliberately terminates the reference
100 KiB and 1 MiB cases at three seconds; those rows are TIMEOUT, not equality passes.

```powershell
uv run --offline --locked --no-sync python scripts/benchmark_cleaning_candidates.py --source artifacts/perf/p29c-input-10k.jsonl --output artifacts/perf/p29c-candidates.json
uv run --offline --locked --no-sync python scripts/benchmark_cleaning_candidates.py --source artifacts/perf/p29c-input-10k.jsonl --output artifacts/perf/p29c-char-candidate.json --chars-only
uv run --offline --locked --no-sync python scripts/benchmark_cleaning_long.py --output artifacts/perf/p29c-long
uv run --offline --locked --no-sync python scripts/benchmark_pii_nearmiss.py
uv run --offline --locked --no-sync python scripts/verify_cleaning_v2.py --source artifacts/perf/p29c-input-10k.jsonl --output artifacts/perf/p29c-stage-oracle.json
uv run --offline --locked --no-sync python docs/implementation/evidence/P29C/collect_results.py
```

Candidate runs use at most 10k authored records and separate tracemalloc passes on
100. Long-record runs: 180 seconds, 3 GiB RSS, 1 MiB output, maximum 1 MiB record;
near-miss: 120 seconds overall, 15 seconds child initialization, three seconds
per filter call, 3 GiB RSS, 1 MiB output. Long/oracle reference loading executes
only trusted source from pinned local Git, never corpus content.

## Focused tests and quality

Commands use the environment prefix above and `uv run --offline --locked --no-sync`.

| Python pytest arguments | Exit / result |
| --- | --- |
| `tests/test_cleaning_v2_exact.py tests/test_cleaning_repetition.py tests/test_cleaning_filters.py tests/test_pii_example_domains.py tests/test_performance_cleaning.py -n 0` | 1: 73 passed, 1 failed, 3.13 s. New fixture attempted to mutate a frozen document; corrected with `dataclasses.replace`. |
| `tests/test_cleaning_v2_exact.py::test_language_frozen_counts_case_and_unicode_boundaries -n 0` | 0: 1 passed, 0.66 s after fixture repair. |
| `tests/test_cleaning_pipeline.py tests/test_cleaning_throughput.py tests/test_cleaning_structured.py -n 0` | 0: 38 passed, 217.80 s. Included four existing authored scale tests; longer than focused-feedback target. |
| `tests/test_cleaning_scheduler_v2.py tests/test_cleaning_throughput.py::test_worker_failure_aborts_without_publication tests/test_cleaning_throughput.py::test_max_docs_matches_reference -n 0` | 0: 6 passed, 12.47 s. Includes actual spawned-worker comparison, bounded-futures logic and missing/duplicate inventory failures. |

Each table row expands `uv run --offline --locked --no-sync python -m pytest ...`.
No retry-until-green: only the exact failed fixture node was rerun after repair.
No full acceptance, live adapter, CUDA or official evaluation tests ran.

Scoped Ruff check/format and `mypy --follow-imports=silent` passed after fixing
zip strictness, unused imports and local variable annotation/name conflicts.
Final implementation scope: `src/xlm/data/cleaning/{language,repetition,pii,sharded}.py`
and the five new benchmark/oracle scripts. Ruff also checked the two new tests.
Mypy checked nine implementation/script files, not the whole repository. The
collector was checked separately: initial Ruff failed on six long lines; initial
mypy failed on missing script import search paths and a list annotation. Wrapped
strings/explicit row typing and adding `scripts` to `MYPYPATH` fixed those errors.
Subsequent collector Ruff/mypy both exited 0. Adding the storage summary later
introduced one more long-string lint error; wrapping that string restored the
scoped Ruff pass. A focused pass is not a full-suite pass.

Exact scoped quality invocations (PowerShell array supplies paths only):

```powershell
$qualityFiles = @(
  'src/xlm/data/cleaning/language.py',
  'src/xlm/data/cleaning/repetition.py',
  'src/xlm/data/cleaning/pii.py',
  'src/xlm/data/cleaning/sharded.py',
  'scripts/benchmark_cleaning_candidates.py',
  'scripts/benchmark_cleaning_long.py',
  'scripts/benchmark_cleaning_v2.py',
  'scripts/benchmark_pii_nearmiss.py',
  'scripts/verify_cleaning_v2.py'
)
uv run --offline --locked --no-sync ruff check $qualityFiles tests/test_cleaning_v2_exact.py tests/test_cleaning_scheduler_v2.py
uv run --offline --locked --no-sync mypy --follow-imports=silent $qualityFiles
$env:MYPYPATH = "$env:PYTHONPATH;$(Join-Path $PWD 'scripts')"
uv run --offline --locked --no-sync ruff check docs/implementation/evidence/P29C/collect_results.py
uv run --offline --locked --no-sync mypy --follow-imports=silent docs/implementation/evidence/P29C/collect_results.py
```

Large artifacts remain ignored under `artifacts/perf/p29c-*`; committed evidence
contains JSON reports, profile text, commands, checksums and this collector only.
Read-only audits included Git status/diffs, source inspection, environment probes
and report inspection. One inspection used the wrong `PERFORMANCE.md` root path;
the actual document is `docs/PERFORMANCE.md`. No benchmark result was inferred
from that failed read.
