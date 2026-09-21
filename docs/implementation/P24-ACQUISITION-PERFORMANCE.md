# P24 — acquisition-performance measurement (no optimization)

Status: **MEASUREMENT ONLY — IMPLEMENTED / VERIFIED (offline fixtures)** on
`closeout/readiness`. No downloader optimization, no semantic change to make it
faster. No live network was used here. Real 1/2/4/8 worker runs are OPERATOR-RUN.

Tree: `D:\Project\xlm-final-integration` at `22c781b` plus the perf work in this
session. Env: `.venv-final` (CPython 3.12.13). Shell: PowerShell, repo root.

## 1. What was instrumented (existing pipeline only)

No second downloader. The existing `BoundedFetcher` + transport + selected-record
+ Parquet + accounting path is timed observationally with `time.monotonic`:

- `src/xlm/data/acquisition/perf.py` — `PerfTelemetry`, versioned sidecar,
  bounded aggregates + slowest-8, sanitized hostnames only.
- `src/xlm/data/acquisition/fetcher.py` — open/body/accounting/decode/serialize
  spans, request/redirect/retry/cache/worker counters, wall + process CPU,
  sidecar write in `finally` (also on refusal/interruption).
- `src/xlm/data/sources/transport.py` — `SafeRedirectHandler(budget, observer)`
  counts redirects by sanitized hostname; policy unchanged.
- `src/xlm/data/acquisition/selection.py` — JSONL body/decode/scanned/retained,
  Parquet metadata-open + row-group decode, per-source `file_worker`,
  serialize/write timing, cache-hit counting.
- `src/xlm/data/acquisition/records.py` — Parquet footer/metadata vs decode
  split (`metadata` for `ParquetFile` open + `check_row_group`,
  `decode` for batch iteration), observational only.

Captured: total wall, process CPU where practical, requests, transferred
application body bytes, decompressed/decoded bytes (journal truth + telemetry
delta), open/body elapsed, redirect/retry counts + retry-wait time, metadata /
footer time, range-request time (labeled `parquet-header` / `parquet-range` /
`range` in slowest requests), Parquet metadata-open, row-group fetch/decode /
iteration, scanned / retained records (global + per-file), selected-record
serialization + disk/output write, accounting/lock wait (journal spans),
cache hits, configured workers, max observed concurrency + average concurrency,
per-file aggregates (elapsed, bytes, requests, records, body/decode/metadata /
serialize splits), slowest-8 requests + slowest-8 files.

Bytes are application response-body bytes (`TransportBudget` meaning). No
TCP/TLS wire bytes, provider billing bytes, or packet accounting is claimed.

## 2. Research-identity preservation

Timing is nondeterministic and never affects: behavior hash, plan hash,
artifact identity, receipt identity, selected-record bytes, source revision,
experiment identity, checkpoint identity. Identical requests with different
timing produce identical data identities.

- Sidecar: `scratch/performance/<plan_id>.perf.json` (`perf_version: 1`),
  under the attempt-owned scratch tree. Journal, plan, receipt untouched.
- Old journals/plans without telemetry still load; `data performance` falls
  back to journal stats with `telemetry_available: false`.
- No silent journal schema change (`progress.py` schema v2 unchanged).

## 3. Bounds and safety

- Aggregates + slowest-8 only; no per-request/row logs. Files capped at 256 by
  plan validation.
- Never persisted: tokens, Authorization headers, signed query strings,
  presigned CDN query strings, cookies, credentials, response bodies.
- Hosts are sanitized via `_sanitize_host` (lowercased hostname, drops URLs /
  queries / userinfo / ports). Security/SSRF/redirect policy intact.

## 4. Direct CLI — inspect one run (reads telemetry, never refetches)

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data performance --plan plans/<plan_id>.json --json
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data performance --plan plans/<plan_id>.json
```

With a custom scratch layout:

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data performance --plan plans/<plan_id>.json --scratch-dir D:/path/to/scratch --json
```

Human output answers “Where did the time go?” (wall shares per stage +
slowest stage + slowest requests/files) and “Was parallelism used?”
(configured vs max observed vs average concurrency + per-file elapsed).
JSON output is the sidecar document verbatim.

## 5. Direct CLI — compare runs (offline, no fetches launched)

Prefer sidecars (works with any custom layout):

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data performance-compare --perf D:/a/scratch/performance/<planA>.perf.json --perf D:/b/scratch/performance/<planB>.perf.json --json
```

Or via plans (default per-plan scratch layout):

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data performance-compare --plan plans/<planA>.json --plan plans/<planB>.json --json
```

Comparison requires: same source, view, immutable revision, provider,
acquisition mode, selected files, row ranges, byte/record/parser/decompression
limits; all COMPLETED; same cache class. Attempt, plan identity, and
max_workers may differ — that is the comparison. Anything else is refused with
explicit reasons, never silently merged. Mixed cached/uncached, revision or
selection mismatch, failed runs, and materially different ceilings are refused
or separated. Report includes per-run workers, status, wall, transferred MiB,
app MiB/s, decompressed MiB/s, scanned/s, retained/s, requests, requests/s,
open/body/metadata/decode/serialize/accounting shares, max/avg concurrency,
cache class, slowest stage, and observed-fastest COMPLETED config for that
workload only (not a universal recommendation).

Cache classes: `uncached-network` (0 hits), `partial-cache` (some hits but
network bytes flowed), `cache-hit/local` (all files hit or no network bytes
with hits).

## 6. 1 / 2 / 4 / 8 worker benchmark runbook (OPERATOR-RUN, your live calls)

A one-file workload cannot test file-level worker scaling. Choose a
representative bounded multi-file workload with enough independent files to
keep workers busy. Do not invent source files — pick real reviewed files from
your probed source.

Rules for all four attempts:

- Same source, same immutable revision, same selected files, same row ranges /
  workload, same resource limits. Only `max_workers` changes (1, 2, 4, 8).
- Distinct attempt identity via `data plan --attempt N` (attempt 1 is legacy;
  higher attempts bind distinct plan_id/hash/authorization to identical
  behavior). Each attempt gets its own plan file, journal, telemetry, and
  storage roots. Never delete/reuse old journals to replenish budget.
- Fresh explicit operator authorization per attempt (`--pilot-approved` for
  pilot scope, or production admission + authorization hash).
- Same XLM_HOME for default-layout comparison, or record each custom
  `--scratch-dir` / `--output-dir` and compare via `--perf` sidecars.
- Cold (uncached) for throughput comparison: run each attempt once on a fresh
  plan/journal. Do not compare a warm-cache rerun against a cold run as
  network throughput.

Step 1 — plan the same workload four times (example placeholders; use your
real source/view/files/revision):

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data plan --source <SOURCE> --view <VIEW> --files <FILE1,FILE2,FILE3,...> --mode whole_file --attempt 11 --pilot-approved --output plans/<plan>_w1.json
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data plan --source <SOURCE> --view <VIEW> --files <FILE1,FILE2,FILE3,...> --mode whole_file --attempt 12 --pilot-approved --output plans/<plan>_w2.json
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data plan --source <SOURCE> --view <VIEW> --files <FILE1,FILE2,FILE3,...> --mode whole_file --attempt 13 --pilot-approved --output plans/<plan>_w4.json
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data plan --source <SOURCE> --view <VIEW> --files <FILE1,FILE2,FILE3,...> --mode whole_file --attempt 14 --pilot-approved --output plans/<plan>_w8.json
```

Set `max_workers` per plan via `--limits limits_w<N>.json` (or your reviewed
limits file) where `limits_w1.json` has `max_workers: 1`, `w2: 2`, `w4: 4`,
`w8: 8`, all other limits identical. Do not edit a plan JSON by hand to change
workers — rebuild via `data plan` so the hash binds the configuration.

Step 2 — fetch each attempt once (OPERATOR-RUN, live network authorized by
you; each command is a separate explicit authorization):

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data fetch --plan plans/<plan>_w1.json --pilot-approved
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data fetch --plan plans/<plan>_w2.json --pilot-approved
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data fetch --plan plans/<plan>_w4.json --pilot-approved
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data fetch --plan plans/<plan>_w8.json --pilot-approved
```

For `selected_records` mode add `--row-ranges ranges.json` identically to all
four plans.

Step 3 — inspect and compare (offline, no network):

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data performance --plan plans/<plan>_w1.json
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data performance-compare --plan plans/<plan>_w1.json --plan plans/<plan>_w2.json --plan plans/<plan>_w4.json --plan plans/<plan>_w8.json --json
```

Disk/network saturation and provider throttling can make 8 slower than 4 (or
4 slower than 2). That is an expected finding, not a failure. 8 (and 16) are
never mandatory; stop at the observed fastest for your workload and report the
numbers. Do not change defaults based on one workload.

## 7. Future optimization decision tree (documented, NOT implemented)

- If network/request latency dominates: investigate bounded file-level
  concurrency, connection reuse, provider-aware scheduling, and approved
  Hugging Face transfer backends (`hf_xet`, `hf_transfer`).
- If Parquet range/decode dominates: investigate row-group reuse, whole-shard
  acquisition when a large fraction is needed, local Arrow parallel decoding,
  avoiding repeated row-group work.
- If disk dominates: investigate storage location, batching, write
  amplification, temp/output layout.
- If accounting/locks dominate: investigate lower-contention accounting while
  preserving exact durable bounds.
- If later dedup dominates: profile dedup separately before considering GPU
  FAISS.
- If tokenization dominates: profile batched/multiprocess tokenization
  separately.

None of the following were implemented here: hf_xet/hf_transfer integration,
new HTTP client, changed connection concurrency, altered range selection,
larger default workers, Arrow multithreading changes, decode-once cache,
whole-shard policy changes, GPU decoding, FAISS, semantic dedup, cleaning or
tokenization parallelism.

## 8. Known measurement limitations

- Category sums can exceed wall time under multi-worker overlap and (for
  Parquet selection) nested range-fetch timings inside decode spans.
- Redirect time is included in open timing; only the redirect count is
  separately attributed.
- CPU time is `time.process_time` delta for the fetch process; worker-thread
  CPU is included only as the OS attributes it to the process.
- Throughput is application response-body MiB/s, not wire or billing bytes.
- Cache classification is heuristic from hits + transferred bytes + file
  count; mixed layouts should be compared via explicit `--perf` sidecars.
- Old sidecars without `provider`/`cache_class` recompute those fields for
  comparison; mismatched versions refuse.

## 9. Handoff

Implemented + verified offline here: instrumentation, sidecar, both CLI
commands, comparison logic, 20 offline tests, this runbook, decision tree.
OPERATOR-RUN (you): real 1/2/4/8 fetches with your authorization, then
`data performance` + `data performance-compare`. NEXT decision only after your
measurements — do not implement optimizations yet.
