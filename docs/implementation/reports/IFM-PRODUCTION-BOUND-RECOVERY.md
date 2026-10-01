# IFM production bound recovery (General p01 failure, Planning p01 supersession)

Status: **IFM PRODUCTION BOUNDS READY FOR OPERATOR DIGEST REVIEW.**
Nothing was authorized or run.

- Branch: `fix/ifm-production-bounds`, worktree `F:\Project\xlm-ifm-bounds`.
- Base: `8271505` on `data/mix01-ultrax-6b`; the primary checkout is untouched.
- Operator roots: data `G:\XLM`, scratch `C:\XLM-scratch`, store `G:\XLM\xlm-home`.
- Writes under them: exactly two new write-once plan records,
  `G:\XLM\plans\ifm_general\p02\plan.json` and
  `G:\XLM\plans\ifm_planning\p02\plan.json`.
- Network use: one bounded, metadata-only footer audit (section 3).

Evidence: [`evidence/IFM-PRODUCTION-BOUND-RECOVERY/`](../evidence/IFM-PRODUCTION-BOUND-RECOVERY/).
It contains no corpus text.

## 1. History preserved (General p01 is immutable)

The p01 digest is `b7bb0cbfb6b7f600a48babcb4507e7dd089ae04716bb4501927f5cda55428372`.
It was authorized by GammA at 2026-10-01T19:18:08Z.

| file | bytes | SHA-256 | mtime (UTC) |
|---|---:|---|---|
| plan.json | 6,065 | `fce481dbfa271a4433bbfb923c35a411494dbf47a730faa6cbf54036dce186a0` | 19:16:49.230968 |
| authorization.json | 166 | `a3e55f6f169aa58b1a792d1e6344c39b48d128a925a640bec7127f4f3f7d1686` | 19:18:08.718367 |
| acquisition.plan.json | 2,532 | `22d5ddb9eefa30f2012389daebf34596470a26b6e5f3a9625896805781186de1` | 19:18:08.793427 |
| events.jsonl | 751 | `54e956e55d1a45f59bacfb7569384966b5588a141b8ff2375a208f0f8c917fb7` | 19:18:20.274537 |
| performance-00.json | 3,131 | `d3d8469e71cfe96b9ac82a4cd47467ac16d5bc3d6ea21a84c4b22b73ed217704` | 19:18:20.275546 |

- The failed receipt is `performance-00.json`, digest `e5f6e98e…706f3`.
  - Root failure: f00001 `SourceTransferError` at `source_parquet.py:575 in _bind`.
  - Other failure: f00000, at the same site.
  - Restart: fresh_download 2; resumable_partial, local_processing_retry and
    sealed_skip all 0.
  - Resumable verified bytes: 0. Transfer: 0 B and 0 requests.
- Sealed units: 0. `canonical/ifm_general` holds only an empty `.staging/p01`.
- `acq-raw/ifm_general` and the scratch `ifm_general` are empty.
- Sufficiency is INCOMPLETE with unresolved ranks `{"1": [0, 1]}`.
- Planning p01 `plan.json`: 6,087 B, SHA-256 `89ee0c60…dadaea`. It is the only
  file in its directory, with no units, raw or scratch data.

After all work, all six files are byte- and mtime-identical. So are both
transport policies, General `sufficiency.json`, and the canonical, raw and
scratch trees (`history_and_inventory.json` vs `history_after.json`).

## 2. Selected files against the frozen inventories

| view | rank | file | declared bytes | ×calibration | ×median | ×mean | ×max |
|---|---:|---|---:|---:|---:|---:|---:|
| general | 0 | `general/general_full.chunk0-bdbff8a5c6-00069.parquet` | 2,013,330,256 | 4.9467 | 0.9996 | 1.0021 | 0.9985 |
| general | 1 | `general/general_full.chunk0-bdbff8a5c6-00146.parquet` | 2,014,409,401 | 4.9493 | 1.0002 | 1.0026 | 0.9991 |
| planning | 0 | `planning/planning.chunk0-160f3594ed-00058.parquet` | 2,007,884,297 | 2.8427 | 1.0002 | 1.0016 | 0.9983 |
| planning | 1 | `planning/planning.chunk1-6d580bf230-00295.parquet` | 2,007,493,650 | 2.8422 | 1.0001 | 1.0014 | 0.9981 |

| view | files | total | min | median | mean | max | > p01 max_file |
|---|---:|---:|---:|---:|---:|---:|---:|
| general | 632 | 1,269,807,414,764 | 407,007,573 | 2,014,077,184.5 | 2,009,188,947.4 | 2,016,262,542 | 630 |
| planning | 834 | 1,671,906,970,539 | 706,323,152 | 2,007,390,808 | 2,004,684,617.0 | 2,011,241,379 | 832 |

**The calibration shard was an extreme outlier.** Each view's calibration
shard is the smallest file in its inventory:

- General `…-00315`: 407,007,573 B (percentile 0.16).
- Planning `…-00416`: 706,323,152 B (percentile 0.12).

Each is a chunk's tail shard. The p01 `max_file_bytes` is 2× that size, so it
excluded all but two files of each inventory, including all four selected
ones. (The earlier split report's General total, "12,698,074,147,764", was a
typo for the inventory's 1,269,807,414,764 B.)

## 3. Bounded Parquet footer audit (metadata only)

There were no local copies, so this audit read only the four Parquet footers
(`footer_metadata_fetch.py`):

- Requests: 8 ranged GETs (trailer, then footer, per file) against the pinned
  revision. Each was redirected once (8 redirects, all to `us.aws.cdn.hf.co`),
  so 16 HTTP exchanges in total.
- Bytes: 240,107 B of response bodies. Caps were 4 MiB per file and 16 MiB in
  total.
- Wall time: 6.3 s.
- Safeguards: 30 s timeout, no retries, host allowlist, and every
  Content-Range total equal to the inventory size.

No payload or text-column statistic was read. The fetch used urllib, not the
Hugging Face client. The fetch shell had no HF offline variables set before or
during the fetch. Every later command ran with `HF_HUB_OFFLINE=1` and
`HF_DATASETS_OFFLINE=1`.

| unit | rows | row groups | max / min rows per group | column-chunk compressed | logical (uncompressed) | text uncompressed | footer | max ratio | max `token_count` |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| general f00000 | 329,409 | 80 | 4,188 / 4,045 | 2,013,311,422 | 4,846,739,434 | 4,844,485,958 | 18,830 | 2.505 | 7,863 |
| general f00001 | 328,568 | 80 | 4,188 / 4,043 | 2,014,387,899 | 4,846,452,175 | 4,844,194,575 | 21,498 | 2.511 | 7,819 |
| planning f00000 | 350,701 | 78 | 4,575 / 4,391 | 2,007,778,514 | 4,646,893,383 | 4,644,490,344 | 105,779 | 2.508 | 7,719 |
| planning f00001 | 346,354 | 78 | 4,549 / 4,328 | 2,007,399,646 | 4,646,134,477 | 4,643,743,770 | 94,000 | 2.526 | 7,696 |

Shared format facts:

- Schema: `text: string` and `token_count: int64`, the adapter's exact
  projection.
- Codec: ZSTD, written by parquet-cpp-arrow 25.0.1.
- Largest row group: about 60.6 MB logical.

Raw footers are kept outside git, in the session scratchpad.

## 4. Root cause, and whether it is a planner bug or an estimate miss

**Both.**

- **Planner bug: a missing generic invariant.** The frozen inventory records
  every file's exact size, and that size is bound into the inventory digest.
  Yet `plan_limits` derived `max_file_bytes` only from
  `layout.file_bytes × FILE_BYTES_TOLERANCE`. It never compared that bound
  with the sizes of the files it had just selected. The planner could
  therefore emit, and the operator authorize, a whole-file plan that can only
  fail closed in `_bind` before its first byte. The invariant was missing.
- **Estimate miss.**
  - The calibration file is the inventory minimum, so the 2× tolerance was
    meaningless.
  - The `sampling_plan_version 1` layout divides compressed file bytes by
    logical bytes per row from one atypical calibration group: 698 rows at
    14,651 B per row, against about 6.1 KB per row compressed overall. This is
    the FineWiki pattern.
  - So even the calibration file underestimates rows about 2.4×. Its own group
    16 starts at row 65,484, above p01's 55,560-row bound.
  - Scaling by size alone would give 274,986 rows for General, still below the
    footer's 329,409.

## 5. Every production limit, old → new

The new values are the stored p02 values. "Insufficient" is judged against
footer facts, or against calibration ratios where marked as an estimate.

The canonical JSONL estimate uses the calibration's documents.jsonl/canonical
ratio: 1.0735 for General and 1.0790 for Planning. That gives about 5.20 GB
of output per General file and 5.01 GB per Planning file.

| limit | General p01 → p02 | Planning p01 → p02 | verdict / evidence |
|---|---|---|---|
| max_file_bytes | 814,743,552 → **2,015,363,072** | 1,413,480,448 → **2,008,023,040** | both insufficient: files are 2.007–2.014 GB; new = exact largest size, rounded up to whole MiB |
| max_rows_per_file | 55,560 → **444,703** | 105,642 → **473,447** | both insufficient: footers show 329,409 and 350,701; new = ceil(1.35× footer max) |
| max_canonical_bytes_per_file | 813,225,910 → **6,540,056,044** | 1,411,285,579 → **6,270,061,965** | both insufficient: text is 4.844 / 4.644 GB uncompressed; new = ceil(1.35× text) |
| max_decoded_bytes_per_file | 3,258,974,208 → **8,061,452,288** | 5,653,921,792 → **8,032,092,160** | General insufficient (4.847 GB projected); Planning p01 sufficient (4.647 GB) but follows the anchor; 4× max_file |
| max_durable_bytes_per_file | 2,441,195,372 → **10,065,245,718** | 4,236,051,606 → **10,031,839,224** | both insufficient: need raw + ~5.2 / 5.0 GB (estimate) |
| processing_growth.source_max_bytes | 814,743,552 → **2,015,363,072** | 1,413,480,448 → **2,008,023,040** | both insufficient; equals max_file |
| processing_growth.output_bytes | 1,626,451,820 → **8,049,882,646** | 2,822,571,158 → **8,023,816,184** | both insufficient: ~5.20 / 5.01 GB JSONL (estimate); new holds the canonical ceiling |
| scratch_cap_bytes | 4,888,698,584 → **20,136,799,276** | 8,478,411,052 → **20,069,986,288** | both insufficient; = 2 in-flight × (max_file + processing + state peak) |
| file_deadline_seconds | 2,590 → **6,406.67** | 4,493.33 → **6,383.33** | both insufficient at the 0.3 MiB/s floor (2.01 GB needs ~6,404 s) |
| plan max_transferred_bytes (enforced by the run meter) | 1,221,591,040 → **6,043,992,064** | 2,119,172,096 → **6,024,069,120** | both insufficient: exact transfer is 4,027,739,657 / 4,015,377,947 B |
| plan_deadline_seconds | 14,400 (unchanged) | 14,400 (unchanged) | sufficient at the floor rate: downloads run in parallel (~6,400 s), then processing; FineWiki p02 measured 3,297 rows/process-s, which is not IFM-specific |
| max_record_bytes | 8,388,608 (unchanged) | 8,388,608 (unchanged) | not provable from footers; retained, fail-closed; largest `token_count` is 7,863, and the calibration rows all fit 1 MiB |
| max_parser_bytes | 33,554,432 (unchanged) | 33,554,432 (unchanged) | sufficient: footers are ≤106 KB; whole-file `check_layout` does not gate the 60.6 MB row groups (preflight below) |
| max_ledger_bytes | 536,870,912 (unchanged) | 536,870,912 (unchanged) | sufficient on evidence: 0 rejections in 2,601 calibration rows; the ledger holds only rejections |
| max_decompression_ratio | 15 (unchanged) | 15 (unchanged) | sufficient: max projected column ratio is 2.53 |

Source-specific bounds: `SOURCE_FILE_BOUNDS` gains `ifm-general-file-v1` and
`ifm-planning-file-v1`. Each gives a row and a canonical ceiling with its
footer evidence, using the FineWiki 1.35× rule. The adapters keep `text`
verbatim, so canonical text is bounded by the text column's uncompressed bytes.
The calibration confirms this: 10,216,553 canonical B of 10,226,373 logical
group B, and 25,422,431 of 25,446,776. Observed maxima were not fabricated.
Record maxima stay unknown, because only footers were read.

## 6. The generic fix

All changes are in `source_plan.py`, plus wiring.

- **Selected-size anchor** (`selected_sizing`, rule
  `mix01-selected-size-anchor-v1`).
  - `build_plan` and `build_repair_plan` pass the selected files' exact
    inventory sizes to `plan_limits`.
  - If the largest exceeds the estimate-derived ceiling, that size replaces
    the calibration file size as the per-file sizing anchor.
  - `max_file_bytes` becomes the exact size rounded up to whole MiB. An exact
    size needs no size tolerance.
  - Rows and canonical bytes scale with it under the unchanged tolerances.
  - The decoded, durable, growth, scratch, deadline, transfer and output
    ceilings all follow.
  - `expected` is scaled the same way, and its transfer is the exact sum of
    downloads.
  - The plan records `file_size_anchor` only when the anchor applies.
- **Invariant.** `plan_limits` refuses if any known selected size exceeds
  `max_file_bytes`. `check_selected_file_bounds(record, inventory)` refuses a
  stored plan whose own bound excludes a known selected size. The driver's
  `authorize` calls it, so General p01 and Planning p01 can no longer be
  authorized.
- **Repair.** `UNIT_LIMIT_KEYS` gains `file_deadline_seconds`, a per-file
  limit. Every other required change was already covered:

  | key | already in `UNIT_LIMIT_KEYS` |
  |---|---|
  | max_file_bytes | yes |
  | rows | yes |
  | decoded | yes |
  | canonical | yes |
  | durable | yes |
  | processing_growth | yes (covers source_max and output) |

  `scratch_cap_bytes` and the acquisition limits are plan-wide and depend on
  the file count, so they stay outside the key set. Otherwise any repair with
  fewer files would count as "changed". `plan-repair` now prints them as
  plan-wide changes, and both plans bind them through their digests.
- **Not changed.** The selection count rule, the requirement split (50/50,
  digest `e001aff4…abd7`), and benchmark plans. Benchmarks call `plan_limits`
  without sizes; their files are reserved positions or a named calibration
  file.

Digest preservation: `rebuild_digests.py` rebuilds every stored plan from its
frozen inputs, under base `8271505` and under the new code.

- Every non-IFM plan rebuilds identically under both (base==new).
  - finepdfs p02, finewiki p02, simple_stories, synth and wiki_rewrite
    reproduce their stored digests.
  - finepdfs p01, finewiki p01 and ultrax p01 already did not reproduce
    under base, because they were made under earlier source-specific rules.
    They are equally unaffected.
- Only the IFM p01 rebuilds change, carrying the anchor.
- Both new IFM p02 plans reproduce exactly.

## 7. General p02 (repair; prepared, NOT authorized)

`plan-repair --source-key ifm_general --plan 1` produced the repair; its full
output is in `plan-repair-general.txt`.

- Repairs plan 1 `b7bb0cbf…8372`, binding the failed receipt
  `performance-00.json` `e5f6e98e…706f3` (root f00001 `_bind`).
- Ranks [0, 1]: the same two files. Sealed ranks: none. Retained SHA-256:
  none.
- `next_cursor` 2 (unchanged).
- Changed per-unit limits: max_file_bytes, max_rows_per_file,
  max_decoded_bytes_per_file, max_canonical_bytes_per_file,
  max_durable_bytes_per_file, processing_growth and file_deadline_seconds,
  with the values in section 5.
- Expected: 4,027,739,657 B exact transfer and 4 requests. Calibration-scaled
  estimates are 274,986 rows and 1,006,235,330 tokens. Footers give 657,977
  rows.
- `resume-check --plan 2`: fresh_download 2 (f00000, f00001); everything else
  0. resumable_verified_bytes 0. Worst-case network 4,030,726,144 B.
- Status: p01 authorized, p02 not authorized. Sufficiency is INCOMPLETE with
  unresolved `{"2": [0, 1]}` only: no rank appears twice. Repairs:
  `[{sequence 2, repairs 1, ranks [0,1]}]`. next_cursor 2.

**General p02 digest: `6c0dfe5a3474978684d294cf8b9dffa616d6cf963b3bfdcd3ecaaea89ec79939`.**
The stored file's SHA-256 is `8ce5f1fa…0c47`.

## 8. Planning p01: immutable supersession (new generic mechanism)

The repository had no mechanism for this:

- `plan-repair` requires an authorized plan with failed-run receipts, and
  rightly refuses an unauthorized one.
- `plan` cannot top up over an INCOMPLETE plan.
- `store_plan` is write-once, so p01 could not be rewritten.

So I added the smallest generic mechanism, `plan-supersede --plan N`.

- **Driver preconditions.** N is the latest plan, and its directory holds
  only `plan.json`: no authorization, acquisition plan, events or receipts.
  No units, staging or scratch exist. N is not a repair plan.
- **Planner.** It writes plan N+1 from N's cursor and acquired bytes under
  today's frozen inputs.
  - The lineage binds N's digest.
  - `supersedes` records the changed sections and all changed limits
    (old → new).
  - It refuses if nothing changed. In that case, authorize N instead.
- **Runtime.**
  - `authorize` and `run` refuse N.
  - `resolution` gives N no ranks of its own, and refuses a sealed unit
    under N.
  - `sufficiency` lists `supersessions`.
  - The first-pass seal binds N as `superseded_by` without units or an
    authorization.
- All new keys appear only when a supersession exists, so earlier
  statuses and seals keep their bytes.

`plan-supersede --source-key ifm_planning --plan 1` produced the replacement;
its output is in `plan-supersede-planning.txt`.

- Supersedes `49ecb6b3…127150e63`. Changed sections: `limits`.
- Ranks [0, 2), the same two files. `next_cursor` 2.
- Limits are as in section 5. Planning p01 stays byte-identical, now flagged
  `superseded_by: 2` in status.
- Sufficiency is INCOMPLETE with unresolved `{"2": [0, 1]}` and supersessions
  `[{sequence 2, supersedes 1}]`.
- `resume-check --plan 2`: fresh_download 2.

**Corrected Planning plan (p02) digest: `72778e8c5144033381500dfb6762120df7a0d74813b6641bf509ec6484082141`.**
The stored file's SHA-256 is `2bdd2a06…1c83`.

## 9. Proactive preflight (no plan known to fail is left reviewable)

`preflight_footers.py` runs the runtime's own footer checks on the four real
footers: `discover_layout_local` + `layout_record`, as in `check_layout`. A
virtual file presents each footer at its exact offset; the body is never
read, and any read before the 64 KiB speculative window raises.

It also checks every footer-decidable bound: file bytes, footer ≤ parser,
refused row groups, rows, projected uncompressed ≤ decoded, text ≤ canonical,
and file + text ≤ durable.

- **General p02 and Planning p02: all checks pass for all four files.** No
  row group is refused.
- General p01 fails file, rows, decoded, canonical and durable.
- Planning p01 fails file, rows, canonical and durable.

Output: `preflight-footers.json`.

## 10. Tests and checks

New file `tests/test_ifm_production_bounds.py`. It uses an authored fixture
with the real calibration layout numbers and the real selected sizes, and
contains 10 tests:

- The pre-fix rule reproduces p01 exactly: 814,743,552 / 55,560 for General
  and 1,413,480,448 / 105,642 for Planning. Its violation is refused.
- Selected sizes bound every ceiling consistently (decoded, durable, growth,
  scratch, deadline, transfer, expected), and the footer facts fit.
- Plans whose files fit the estimate keep byte-identical limits, with no
  anchor key.
- The General repair:
  - covers ranks [0, 1] once, with cursor 2 and an unchanged p01 directory;
  - binds its changed limits by identity (a forged change fails the digest);
  - classifies fresh_download=2 with 0 retained bytes;
  - resolves to `{1: [], 2: [0, 1]}`;
  - refuses to run p01.
- A repair under unchanged limits is refused.
- The Planning supersession:
  - leaves p01 byte-identical, with only `plan.json`;
  - binds its digest and lineage, with cursor 2;
  - makes p01's authorize and run refuse;
  - resolves ranks to plan 2, with supersessions listed in sufficiency.
- Supersession refuses run plans, non-latest plans, repairs, left-over
  scratch and identical re-plans.
- End to end on authored Parquet behind a loopback server: unauthorized p01,
  superseding p02, authorize, run, SUFFICIENT, then a seal that binds p01 as
  `superseded_by` with units (2,0) and (2,1).

`test_source_file_bounds.py` now expects the two IFM keys.

Commands (thread variables set to 1, `--basetemp` under `C:/XLM-scratch`):

- `uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_ifm_production_bounds.py -n 0`
  → **10 passed** (exit 0).
- 15 related files with `-n 8 --dist=worksteal --max-worker-restart=0 -m "not serial"`
  → **216 passed** (exit 0). The files: ifm_production_bounds, source_plan,
  source_repair, source_file_bounds, source_record_bound, ifm_requirement_split,
  source_run, mix01_source_cli, source_rowgroups, source_growth,
  source_growth_integration, mix01_inventory, source_archive,
  source_discovery and source_admission. None has `serial` tests.
- `ruff check` and `ruff format --check` on the changed files, `mypy --strict`
  on source_plan and source_run (interpreted run, not blocked this session),
  and `git diff --check`: all exit 0.
- **Not run:** the full suite and its serial selection, live or network
  tests, CUDA tests, and any production run.

## 11. Open limitations and decisions for the operator

- **Large overshoot (operator decision).** The count rule is unchanged and
  still sizes files from the calibration shard's yield, so it selects 2 files
  per view. The footers show about 9.69 GB (General) and 9.29 GB (Planning) of
  text, against 660 MB canonical required per view. That is about 14× each,
  or roughly 2.4B and 2.3B estimated tokens.
  - One file per view would still be about 7×.
  - Changing the count rule changes the frozen planner rule and General p01's
    committed ranks. This was not done here.
  - Run cost, if authorized: about 4.0 GB transfer per view, and up to about
    20 GB of durable G: space per view (check: 2 × 10.07 GB; 674 GB free).
- The record maximum is unknown (footers only). A row above 8 MiB would fail
  closed and need another repair.
- Canonical and output sufficiency rest on the text-uncompressed bound and the
  calibration JSONL ratio. These are estimates; the ceilings enforce fail-closed.
- Expected rows and tokens in the plans still carry the v1 layout's ~2.4×
  under-estimate (the bounds use footers). A whole-file sizing measurement
  from a p02 receipt would correct later top-ups.
- Benchmark plans do not yet take selected sizes. Their files are reserved
  positions or a named calibration file, and still fail closed at `_bind`.
- **The operator must use this branch's code.** The base code does not know
  supersession: it would let Planning p01 be authorized.

## 12. Operator commands (STOP at digest review)

Run from `F:\Project\xlm-ifm-bounds`, or from `data/mix01-ultrax-6b` after
merging `fix/ifm-production-bounds`:

```powershell
$env:XLM_DATA_ROOT='G:\XLM'; $env:XLM_HOME='G:\XLM\xlm-home'; $env:XLM_SCRATCH_ROOT='C:\XLM-scratch'
$env:HF_HUB_OFFLINE='1'; $env:HF_DATASETS_OFFLINE='1'; $env:PYTHONUTF8='1'

# General p02 (repair of failed p01)
Get-Content -LiteralPath 'G:\XLM\plans\ifm_general\p02\plan.json'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py resume-check --source-key ifm_general --plan 2
# STOP: review PLAN DIGEST 6c0dfe5a3474978684d294cf8b9dffa616d6cf963b3bfdcd3ecaaea89ec79939
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py authorize --source-key ifm_general --plan 2 --digest 6c0dfe5a3474978684d294cf8b9dffa616d6cf963b3bfdcd3ecaaea89ec79939 --operator '<operator-name>'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py run --source-key ifm_general --plan 2

# Planning p02 (supersedes unauthorized p01)
Get-Content -LiteralPath 'G:\XLM\plans\ifm_planning\p02\plan.json'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py status --source-key ifm_planning
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py resume-check --source-key ifm_planning --plan 2
# STOP: review PLAN DIGEST 72778e8c5144033381500dfb6762120df7a0d74813b6641bf509ec6484082141
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py authorize --source-key ifm_planning --plan 2 --digest 72778e8c5144033381500dfb6762120df7a0d74813b6641bf509ec6484082141 --operator '<operator-name>'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py run --source-key ifm_planning --plan 2

# After each run: verify, sufficiency, and seal only if SUFFICIENT
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py verify --source-key ifm_general --plan 2
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py sufficiency --source-key ifm_general
```

The `run` lines use the network; do not execute them before authorization.
