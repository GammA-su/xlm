# Offline performance measurements

The [P32 recovery closeout](implementation/reports/P32-RECOVERY.md) adds durable
publication intent and bounded, locked journal-orphan cleanup. All required
crash matrices now recover automatically (44/44 early, 44/44 mature, 20/20
selected), and all eight successful acquisitions remain exact against the
corrected review candidate. Scratch admission now includes journal, diagnostic
and replacement bytes from the first write. The report states the complete
logical-byte contract. Its initial offline gate hit a native Windows worker
crash during runtime-inventory fixture teardown. The
[P32 diagnostic repair](implementation/reports/P32-HEAVY-CRASH.md) identifies
the unsafe native timeout dumper and records the repaired acceptance results.
That repair changes test infrastructure only; the acquisition cost below is
unchanged.

This correction has a substantial measured throughput cost. On the same G:
16×64 MiB authored fixture, workers 1/8/16 changed from **143.581 / 227.321 /
226.137 MB/s** to **91.263 / 125.916 / 29.326 MB/s**. The last point includes
a large fsync stall and is retained. Full verification reads and control-file
inspection occur under the journal lock; journal writes remain batched
(315 versus 331), rather than returning to per-read persistence. These single
observations are not a throughput ceiling or a performance win. Integrating
the correctness fix entails this recorded cost; no further optimization was
part of this task. See the [complete table](implementation/evidence/P32-RECOVERY/TABLES.md).

The earlier [independent Opus review](implementation/reports/OPUS55-REVIEW.md) measured
corrected durable leases on G: SATA SSD: up to 234.212 MB/s for 16×64 MiB
whole files, 8.42–9.06 MB/s selected Parquet, and 109.834 s for the frozen 100k
pipeline with exact comparator success. These are authored local measurements,
with full worker tables and slow fsync observations retained. Larger reads add
no established independent benefit after leases; retain ordinary 64 KiB reads.
The original six commits require the four review corrections plus the P32
recovery corrections above; the historical review measurements alone do not
approve preprocessing freeze or replace final acceptance. NumPy remains the first MinHash backend; Arrow is the exact fallback for
eligible sets when NumPy is unavailable. No dependency install is needed for
the declared Arrow fallback. See the report for exact cherry-pick conditions
and historical recovery findings, now addressed in the closeout report.

P29C adds exact cleaner improvements and an explicit bounded dynamic scheduling
option. [P29C results](implementation/reports/P29C.md) record 24.4% less 100k
single-worker cleaning time on the frozen authored fixture. The same-setting
pipeline is 144.719 s; explicitly enabling eight cleaning workers gives 111.688 s.
These are local diagnostics, not live-source throughput claims.

After setting the environment below, a bounded cleaner review uses the existing
canonical prefix from P29C and a fresh destination:

```powershell
uv run --offline --locked --no-sync python scripts/benchmark_cleaning_v2.py --input artifacts/perf/p29c-input-10k.jsonl --output artifacts/perf/my-clean-10k --documents 10000 --workers 1
```

If the ignored prefix is absent, first generate a local frozen 10k pipeline using
the P29B fixture commands below, then pass its `adapted/documents.jsonl` as input.
Never substitute a live corpus silently. For 100k, explicitly set
`--documents 100000 --workers 6 --shard-mib 4`. Six workers measured 21.271 s
at 1,542.5 MiB tree RSS; eight measured 20.256 s at 1,982.1 MiB. The benchmark's
Torch imports contribute to these RSS/startup costs. Default worker counts and
static scheduling are unchanged.

`--scheduling dynamic` opts into at most two queued whole-unit tasks per worker;
it helped an uneven 128-document fixture but was slower on the mixed 100k case.
The product Python API is `run_sharded_clean(..., scheduling="dynamic")`; there
is no hidden tuning or new cleaning CLI default. Source order and publication
verification remain fixed. One and sixteen MiB shards were slower than four MiB
on this fixture. Cleaner benchmark caps: 900 seconds, 3 GiB process-tree RSS,
512 MiB input, 2 GiB output/scratch per run. Sampled caps are guardrails, not OS
reservations; retain space for all runs, including failed outputs.

For a full P29C frozen comparison, use the existing pipeline command with
`--token-workers 8 --workers 8`. P29C's `scripts/verify_cleaning_v2.py` compares
full payloads while reporting the expected implementation-fingerprint difference.
The general comparator still treats those hashes as identity; do not silently
erase provenance to make it pass. [Exact commands and validation exits](
implementation/evidence/P29C/COMMANDS.md) include same-worker controls and limits.

P29B extends this workflow to the frozen 32,768-entry vocabulary regime. See
[P29B results](implementation/reports/P29B.md) for exactness, worker scaling,
memory costs and the limits of these authored fixtures. No production tokenizer
or research model is fitted by these examples.

With the environment variables below set, the next bounded command sequence is:

```powershell
uv run --offline --locked --no-sync python scripts/benchmark_token_path.py prepare --output artifacts/perf/my-32k-fixture
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/my-32k-10k --documents 10000 --tokenizer artifacts/perf/my-32k-fixture/tokenizer
```

Use `--reference-p29` with a separate fresh pipeline output directory for a
comparison against pinned commit `44c19b8`. For a bounded 100k experiment, add
`--token-workers 8`; worker preparation and final canonical assembly are included
in stage time. This raises the pipeline's disk allowance to 3 GiB including
scratch (RSS remains 3 GiB), with transient disk sampling during parallel stages.
The ordinary pipeline remains capped at 2 GiB. Keep enough free storage for each
retained run; caps are per run, not an aggregate retention quota.

The explicit product path consumes a verified P27A shard directory containing
one source and the already selected split. It does not perform split selection:

```powershell
uv run --offline --locked --no-sync python -m xlm.data.parallel_tokens --input artifacts/perf/p29b-sharded --tokenizer artifacts/perf/p29b-fixture/tokenizer --output artifacts/perf/my-token-shard --source-id authored_mix --shard-id fixture --pool-hash p02_local_pool --workers 8 --max-documents 100000 --max-input-bytes 268435456 --max-output-bytes 2147483648 --max-record-bytes 8388608 --max-seconds 900 --max-rss-bytes 3221225472
```

These names refer to authored diagnostics, not an approved training corpus.
Outputs are immutable; use a fresh destination after an interrupted publication.
`--batch-size 128` enables bounded native batches. To measure the lower-memory
one-process alternative, explicitly set `TOKENIZERS_PARALLELISM=true` and
`RAYON_NUM_THREADS=8`, then use `--workers 1 --batch-size 128`. Avoid multiplying
large process and native thread counts; the product changes no global settings.
Restore `TOKENIZERS_PARALLELISM=false` for the normal fixture comparisons.

For the loader, `MixtureBatcher(..., max_open_shards=8)` opts into verified persistent
maps. Use it as a context manager or call `close()`; maps also close on eviction.
The default is zero (ordinary reads), which was competitive in this environment.

```powershell
uv run --offline --locked --no-sync python scripts/benchmark_loader_cache.py --shard artifacts/perf/my-token-shard --output artifacts/perf/my-loader.json --mixture-steps 50
uv run --offline --locked --no-sync python scripts/benchmark_h2d.py --mib 8 --iterations 100
```

The H2D command requires an already installed functional CUDA build and reports
exit 77 / NOT RUN otherwise. It measures synthetic copies only, never training or
compute overlap. It installs nothing. Existing CPU/CUDA installation policy still
applies; these results do not authorize a persistent GPU job.

P29's [report](implementation/reports/P29.md) maps the pipeline, records measured
limits, and separates authored fixtures from production evidence. No production
admission, network access, or training authorization follows from these results.

Use an **already installed** Python 3.12.13 environment matching `uv.lock`. The
CPU/CUDA installation policy remains in [the Windows runbook](runbooks/windows.md).
This workflow installs nothing; `--no-sync` is intentional. The examples below
assume the checkout's existing environment; when borrowing an existing environment,
set `UV_PROJECT_ENVIRONMENT` to its absolute path and leave `--no-sync` enabled.

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
uv run --offline --locked --no-sync python scripts/benchmark_pipeline.py --output artifacts/perf/my-10k --documents 10000 --workers 1
uv run --offline --locked --no-sync python scripts/benchmark_hotspots.py --output artifacts/perf/my-hotspots --tokenizer artifacts/perf/my-10k/tokenizer
uv run --offline --locked --no-sync python scripts/benchmark_auxiliary.py --output artifacts/perf/my-auxiliary
uv run --offline --locked --no-sync python scripts/compare_pipeline.py artifacts/perf/before-10k artifacts/perf/my-10k --output artifacts/perf/my-comparison.json
```

Output directories must be fresh. The pipeline accepts 100–100,000 documents,
1/2/4/6/8 cleaning workers and a 1–1,800 second deadline (default 600 seconds).
It generates six deterministic source-like shapes at runtime. They exercise a
generic JSONL adapter, not the six publishers' adapters. It omits dedup unless
`--dedup-workers` is passed (0 by default = skip; 1/2/4/8 run P28 exact/lexical
dedup between cleaning and split with per-phase telemetry), uses a 256-document/
1 MiB fixture BPE fit, and consumes at most 16 loader steps without any model
update. Packing is measured as a separate consumer of the same mmap token shard;
the harness does not claim a new packed-file format.
`--profile` adds a cProfile file; do not compare profiled and unprofiled timings.

The harness watches process-tree RSS (3 GiB cap, sampled every 100 ms), checks
artifact bytes at stage boundaries (2 GiB cap), and limits fixture rows to 8 KiB.
Time/RSS breaches terminate the benchmark and its own children without publishing
a success report. These are application guardrails, not OS storage reservations:
disk use can grow between checks. The fixed fixture generator and 100k ceiling
bound the workload; measured 100k artifacts were about 1.21 GiB. Keep failed-run
artifacts in the named output directory for inspection. The hotspot harness's
million-document case measures serialization only and stores no million-row corpus.

Each successful run publishes `report.json` atomically and prints a human table.
Reports include wall time, parent CPU time, process-tree sampled RSS, parent OS I/O,
parent fsync count and retained artifact bytes. Parent counters exclude child work;
OS I/O bytes include cache effects and are not physical-disk traffic. Discovery of
children is sampled once per second. Short-lived peaks may be missed.

The comparator verifies full bytes/hashes for canonical, tokenizer and token
artifacts, split membership, loader batches, packing target counts, cleaning metrics
and quarantine decisions. Only explicit timestamps and elapsed-time fields are
excluded. A timing regression is reported; a changed scientific output raises an
error. There are no absolute timing gates in CI.

The hotspot benchmark loads four explicitly named modules from pinned commit
`2a82dfd8c2bc3e0d183d9c339ae0cfe8d073784f` as local reference implementations. It
executes trusted repository code, never corpus instructions or configurable YAML.
The serialization shortcut is an experiment only: public `to_dict()` still returns
the original detached recursive representation.

For this 10k fixture, use one cleaning worker: process startup outweighed parallel
work at two and four workers. Re-measure larger real workloads under their own
authorization before selecting worker counts. The production defaults, precision,
source selection, cleaning thresholds, packing policy and artifact formats are
unchanged. Existing direct CLI commands remain the normal product interfaces.

Next bounded command: run the first command above in a fresh output directory and
compare its report with the committed [P29 evidence](implementation/evidence/P29/).
