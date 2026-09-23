# Offline performance measurements

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
1/2/4/8 cleaning workers and a 1–1,800 second deadline (default 600 seconds).
It generates six deterministic source-like shapes at runtime. They exercise a
generic JSONL adapter, not the six publishers' adapters. It deliberately omits
dedup, uses a 256-document/1 MiB fixture BPE fit, and consumes at most 16 loader
steps without any model update. Packing is measured as a separate consumer of
the same mmap token shard; the harness does not claim a new packed-file format.
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
