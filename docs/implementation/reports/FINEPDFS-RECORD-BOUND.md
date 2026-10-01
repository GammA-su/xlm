# FinePDFs whole-file record bound (2026-10-01, offline)

Verdict: **FINEPDFS BENCHMARK REQUIRES NEW AUTHORIZATION** (b2). The retained
b1 download is reusable offline. It will not be downloaded again.

## Root cause

The real whole-file benchmark `finepdfs/b1` (digest `b458e83d…0e90a`, plan
hash `c0d17a71…5542`) failed in `source_parquet.selected_payloads` with
`RecordLimitError: row=11729 encoded_bytes=9064396 limit=8388608`. The 8 MiB
`max_record_bytes` is a generic planner constant (`source_plan.MAX_RECORD_BYTES`).
It was applied to every source by `plan_limits`. It is a per-record
serialization/parser-memory safety cap, not a quality policy. It is in the
benchmark/plan `limits` (self-digest), in `AcquisitionLimits` (plan hash, which
is what the authorization binds), in the performance receipt `settings`, and
in the worker `PROCESS_LIMIT_KEYS`. So it was already bound into identity.

The 1,000-row calibration could not expose the problem. Only 3 of 220,407 rows
(0.0014%) exceed 8 MiB.

## Retained download (C:\XLM-scratch\finepdfs\bench-b1)

- `f00000.parquet.part`: 2,771,021,138 B. Its SHA-256 was re-hashed offline today: `4eeb58bc769a194f472100ed4acd1d960e35c669caecd96fd4ce6425fabca38d`.
- `f00000.state.json`: `complete: true`, `verified_bytes` = length. It records `repo_commit` `9cfabe21…`. The repository-declared `linked_etag` SHA-256 equals the local hash, and the xet ETag is `db3520d8…`.
- Failure receipt: `G:\XLM\plans\finepdfs\benchmarks\b1\performance-00.json` (digest `2853e0a6…`, status failed, root failure `RecordLimitError` at f00000). `events.jsonl` is also kept. Nothing was deleted or changed.

## Offline structural scan (projected rows, no text emitted)

Script: this session's scratchpad `scan_finepdfs.py`. It encodes each row with
the certified projection (9 columns) via `located_record`.

| Measure | Value |
| --- | --- |
| rows | 220,407 |
| max encoded row | 24,828,818 B (23.68 MiB), row 22782 |
| p50 / p90 / p99 / p99.9 / p99.99 | 6,622 / 46,777 / 297,726 / 1,212,340 / 4,039,691 B |
| > 4 / 8 / 12 / 16 / 24 / 32 MiB | 20 / 3 / 2 / 1 / 0 / 0 |
| > 8 MiB rows | 11729 (9,064,396), 79885 (13,796,094), 22782 (24,828,818) |

Text is ≥ 97% of every row above 4 MiB. **All 20 rows above 4 MiB are
`extractor = rolmOCR`** with `is_truncated = true` (token_count 0.93M–7.15M).
The certified FinePDFs treatment already rejects these rows in the adapter.

## Decision

- **Bound:** a source-specific `SOURCE_RECORD_BYTES[("finepdfs_edu","eng_Latn")] = 32 MiB`, with an evidence string.
  - It equals the existing parser ceiling (`max_record_bytes ≤ max_parser_bytes` is enforced), so no other ceiling changes.
  - It gives about 1.35× headroom over the observed maximum.
  - 16 MiB was refused: row 22782 is larger. 24 MiB was refused: it leaves 1.4% headroom on a single file.
  - Every other source, including UltraX, keeps 8 MiB. Generic policies are byte-identical, and only an overridden source gets `max_record_bytes_basis`.
- **Oversize semantics (contract A):** a row above 32 MiB still fails its unit closed with `RecordLimitError`.
  - It is not a silent skip.
  - It is not a recorded rejection. The bound fires before the adapter, and the current contracts have no pre-adapter rejection ledger.
- **Resource effect:** the worst case is one ≤ 32 MiB record plus its payload in a worker. The decoded-byte, canonical, durable, scratch and RSS ceilings are unchanged.

## Identity and reuse

The changed bound changes the benchmark digest and the plan hash. A test shows
that b1's authorization digest cannot authorize the new record. b1 stays as
history. A new **b2** must be planned and explicitly authorized.

New offline step `benchmark adopt --label b2 --donor b1`. It requires all of the following:

- an authorized b2;
- the same provider, repository, revision, source and view;
- the same file path;
- a complete donor state with the identical canonical URL and `repo_commit`;
- a repository-declared SHA-256 equal to the recorded one, plus the plan's expected digest when present.

It then hard-links the bytes (the donor keeps its file), re-hashes the link,
writes a zero-charge state, and writes `adoption.json`. `benchmark run` then sees
a complete verified state and takes the existing cache-hit path
(`local_complete_reuse`), with no transfer.

## Tests and checks

Exit status is 0 unless stated.

- `uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_source_record_bound.py -n 0`: 12 passed.
- `... pytest tests/test_source_record_bound.py tests/test_source_plan.py tests/test_source_run.py tests/test_mix01_source_cli.py tests/test_source_admission.py -n 16 --dist=worksteal --max-worker-restart=0`: 46 passed.
- `ruff check` and `ruff format --check` on the changed files: passed.
- `mypy --strict`: NOT RUN (Windows application control blocked the DLL).
- Real-store regression: `mix01_source.py verify --source-key ultrax --plan 1 --skip-content` verified 12/12, plan digest `454395fb…45fb` unchanged; `minted_from_record(p01)` reproduces; seal `efdb99db…2f1f` untouched.
- Not run: the full suite, network tests and the real b2 run.

## Next commands

All of these are OFFLINE except the last one:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark plan --source-key finepdfs --label b2 --file data/eng_Latn/train/000_00083.parquet
# STOP: user reviews the printed BENCHMARK DIGEST (expect limits.max_record_bytes = 33554432)
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark authorize --source-key finepdfs --label b2 --digest <B2_DIGEST> --operator "<OPERATOR_NAME>"
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark adopt --source-key finepdfs --label b2 --donor b1
# NETWORK-classified CLI step; with the adopted state it transfers 0 bytes (cache hit)
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark run --source-key finepdfs --label b2
```
