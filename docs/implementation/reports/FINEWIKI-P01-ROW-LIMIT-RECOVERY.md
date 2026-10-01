# FineWiki p01 row-limit recovery (2026-10-01)

Verdict: **FINEWIKI P01 RECOVERY READY FOR OPERATOR DIGEST REVIEW.**

Repair plan **p02** is prepared and is **not authorized**. Its digest is
`319d6bf3dbb7ef085b637c27ea75a89d9d1a97f6b1337153cfebebb1844a305b`.

p01, its authorization, its failed-run receipt, its scratch partials and the
retained source are unchanged. This session did not authorize or run p02, did
not seal, and ran no C05, tokenizer, training or push. No corpus text was
printed or stored.

**Network:** one bounded footer-metadata read of `000_00014.parquet`
(section 2). Everything else was offline.

## 1. p01 state (immutable)

- p01 digest: `fd8c67df3638059dd58b908317fe368c69a6c4b5c96e14139372bc63eabeabfb`.
  It was authorized by GammA at 2026-10-01T17:31:57Z.
- `performance-00.json` (the failed-run receipt) has digest `dc3d3585…f8426`.
- Events:
  - at 34 s, `f00001` failed: `SourceAdaptError` at `source_local.py:119 check_layout`;
  - `f00000` was cancelled;
  - restart counts: sealed_skip 0, local_processing_retry 1, resumable_partial 1,
    local_complete_reuse 0, fresh_download 0.

The p01 directory files have these SHA-256 values. Their mtimes (19:31–19:32)
were not touched by this session.

| File | SHA-256 |
| --- | --- |
| `plan.json` | `bdc353f4…ce54` |
| `authorization.json` | `d6d0838c…e073` |
| `acquisition.plan.json` | `9c08455a…7c5b` |
| `events.jsonl` | `5116be07…4395` |
| `performance-00.json` | `2bc66c31…846b` |

| Unit | Rank | File | Declared size | Local bytes | Status |
| --- | --- | --- | --- | --- | --- |
| f00000 | 0 | `data/enwiki/000_00014.parquet` | 2,538,032,319 B | `C:\XLM-scratch\finewiki\p01\f00000.parquet.part`: 1,720,713,216 B on disk; **1,677,721,600 B verified** (prefix SHA-256 `35aa8a08…af54` re-checked by `resume-check`); charged 1,744,830,464 B | `resumable_partial` |
| f00001 | 1 | `data/enwiki/000_00011.parquet` | 2,498,000,006 B | Durable `G:\XLM\acq-raw\finewiki\source\data\enwiki\000_00011.parquet` + identity sidecar. Complete; SHA-256 `c64481777c4ad518969680bdb3f27e8b1c9e782fa831709ee5fc30beb3aa622d`, re-hashed offline and equal to `x-linked-etag`. A complete scratch copy also remains in `p01\f00001.parquet.part` | `local_processing_retry` |

## 2. True row counts

| File | Rows | Row groups | Largest group (rows) | Sum of `total_byte_size` | Footer | Source of evidence |
| --- | --- | --- | --- | --- | --- | --- |
| 000_00014 | **446,535** | 447 | 1,000 | 7,439,794,805 | 2,899,995 B | Bounded footer read |
| 000_00011 | **417,809** | 418 | 1,000 | 7,426,183,990 | 2,636,331 B | Local footer |

The 000_00014 footer was not available locally, because the partial holds only
a prefix. It was read with
[`footer_metadata_fetch.py`](../evidence/FINEWIKI-P01-ROW-LIMIT-RECOVERY/footer_metadata_fetch.py):

- tail Range requests only, with a 4 MiB byte cap and a 30 s timeout;
- exact-host allowlist: `huggingface.co`, `cas-bridge.xethub.hf.co` and
  `us.aws.cdn.hf.co` (the last is already allowlisted in `evidence_v4/v41.py`);
- **two ranged 206 responses, 2,900,003 payload bytes** (8-byte trailer plus
  footer), each behind one redirect.

A first attempt that lacked the CDN host was refused at the redirect, with
**0 body bytes**. Total HTTP exchanges were 5: 3 to `huggingface.co` and 2 to
the CDN. No document payload was fetched, and HF offline variables were never
unset for the repository commands.

## 3. Root cause: an estimate error, not a new planner defect

`max_rows_per_file = ceil(rows_per_file × ROWS_TOLERANCE 2.0) = 2 × 81,270 = 162,540`.

Here `rows_per_file = file_bytes / whole_bytes_per_row`, which is
2,417,467,623 / 29,745.797 = 81,270. The divisor comes from the 1000-row
calibration block, `000_00013` rows 2000–3000, with `group_bytes =
29,745,797`.

That calibration uses `sampling_plan_version 1`. Its `compressed_bytes` field
is the row group's **logical (uncompressed) `total_byte_size`**. The code
already documents this at `transport_policy.py`: "v1 labels the logical
total_byte_size as compressed_bytes… a measured whole-file sizing supersedes".
The estimate therefore divides compressed file bytes by uncompressed bytes per
row, and two errors stack:

- **Units.** Whole files are about 2.97× compressed (7.43 GB logical over
  2.50 GB file), so the estimate is low by roughly that factor.
- **Sampling.** The calibrated group is dense: 29.7 KB of logical bytes per
  row, against about 17 KB per row averaged over both production files. The
  file's own first group is 9.1 MB per 1000 rows.

The counted rows are 417,809 (2.57×) and 446,535 (2.75×) of the bound. A 2×
tolerance could not absorb this.

This is a **sizing estimate error** from a known, documented v1 historical
label. FineWiki has no whole-file sizing measurement to supersede it. The
planner arithmetic is correct, and the bound failed closed before decoding any
row, as designed.

## 4. Other ceilings, checked by an exact offline scan of 000_00011

The FinePDFs scan script was reused unchanged as
[`scan_source.py`](../evidence/FINEWIKI-P01-ROW-LIMIT-RECOVERY/scan_source.py).
It uses the certified projection (`title`, `text`, `in_language`), the exact
`located_record`, and the real adapter. Output is in
[`scan_f00001.json`](../evidence/FINEWIKI-P01-ROW-LIMIT-RECOVERY/scan_f00001.json).
The scan took 79 s with a 318 MB RSS peak.

- 417,809 rows; **all accepted**, 0 rejected.
- **Canonical text: 2,733,162,301 B**, above p01's 2,055,733,915.
- **Largest record: 13,566,858 B** (row 58004). Six rows exceed 8 MiB, and
  all of them are accepted documents.
- `documents.jsonl`: 3,042,433,120 B. Decoded: 2,747,915,976 B. Largest
  decoded group: 30,333,907 B.

**Rows is not the only insufficient bound.** After the row check, p01 would
also have failed on `max_record_bytes` and `max_canonical_bytes_per_file`.

| Ceiling | p01 | Observed / estimated | Verdict |
| --- | --- | --- | --- |
| max_rows_per_file | 162,540 | 446,535 | **change** |
| max_record_bytes | 8,388,608 | 13,566,858 (00011). 00014 is unscanned | **change** |
| max_canonical_bytes_per_file | 2,055,733,915 | 2,733,162,301 (00011). About 2.69 GB for 00014 (text-column ratio) | **change** |
| max_file_bytes | 4,834,983,936 | 2,538,032,319 | ok (1.9×) |
| processing_growth.output_bytes | 4,111,467,830 | 3,042,433,120 (00011). About 3.02 GB for 00014 | ok (1.35×) |
| max_durable_bytes_per_file | 8,946,451,766 | 5,540,433,126 (00011) | ok |
| max_decoded_bytes_per_file | 19,339,935,744 | 2,747,915,976 | ok |
| max_parser_bytes (Thrift) | 33,554,432 | footers 2.6–2.9 MB | ok |
| max_ledger_bytes | 536,870,912 | 0 rejections | ok |
| decompression ratio | 15 | about 2.9 per file; 00011 passed `check_layout` | ok |
| scratch cap / min free | 17,899,211,372 / 32 GiB | 2.5 GB + 3.0 GB per unit; C: has 785 GB free | ok |
| file / plan deadline | 15,370 s / 14,400 s | about 0.86 GB left to download | ok |

## 5. New bounds (FineWiki `en` only)

The rule is the existing FinePDFs record rule: **at least 1.35× the largest
observed value**.

| Bound | Value | Evidence basis |
| --- | --- | --- |
| `max_rows_per_file` | `ceil(446,535 × 1.35)` = **602,823** | Both footers |
| `max_canonical_bytes_per_file` | `ceil(2,733,162,301 × 1.35)` = **3,689,769,107** | 00011 exact scan |
| `max_record_bytes` | **18 MiB** = 18,874,368: the smallest whole MiB of at least 1.35 × 13,566,858 (1.39×) | 00011 exact scan |

All other p02 limits equal p01's: durable, growth, scratch, file, parser and
deadlines.

Other inventory files are 2.47–2.54 GB, the same size class. A shard at the
higher observed density (1.76e-4 rows per byte) would need to be 3.4 GB to
exceed 602,823 rows. A top-up shard is not guaranteed to fit, though. Row and
record bounds fail closed before any publication, and the generic repair would
then apply again.

Unchanged: the generic bounds (8 MiB record, `ROWS_TOLERANCE 2.0`), UltraX,
FinePDFs (48 MiB), SYNTH, Wiki Rewrite, SimpleStories and Essential-Web. The
tables carry only `("finewiki", "en")` entries, and a test pins this.

## 6. Code changes

- **`max_rows_per_file`, `max_record_bytes` and `max_canonical_bytes_per_file`
  were already in `UNIT_LIMIT_KEYS`**, so the repair identity mechanism was used
  unchanged.
- `source_plan.py`:
  - a `SOURCE_RECORD_BYTES` entry `finewiki-record-v1`;
  - a new generic table `SOURCE_FILE_BOUNDS`, with evidence, that replaces only
    the row and canonical ceilings. A `file_bounds_basis` policy key is added
    only when an entry applies, so all other plans keep their digests.
- `source_run.py` (generic fix): a repair plan's scratch label is `p02`, so
  before this fix p01's verified f00000 partial was invisible to it. p02 would
  have classified f00000 as `fresh_download` and fetched all 2.54 GB again. Now:
  - `inherited_scratch` lets `classify` see the predecessor's same-key
    download, read-only, after checking file name and revision;
  - `adopt_inherited_scratch` moves it into the repair's scratch at run start
    with atomic same-volume `os.replace`, partial before state. The transport
    then re-verifies prefix, URL and revision as it does for any resume;
  - units that already have a retained durable source are never adopted.

## 7. p02 (prepared, NOT authorized)

| Field | Value |
| --- | --- |
| Digest | `319d6bf3dbb7ef085b637c27ea75a89d9d1a97f6b1337153cfebebb1844a305b` |
| Repairs | p01 `fd8c67df…eabfb`, sealed ranks `[]` |
| Ranks | 0 = `000_00014.parquet` (f00000), 1 = `000_00011.parquet` (f00001) |
| Changed limits | rows 162,540 → 602,823; record 8,388,608 → 18,874,368; canonical 2,055,733,915 → 3,689,769,107 |
| Retained | `000_00011.parquet` bound to SHA-256 `c6448177…622d` (no download) |
| `resume-check` | f00001 `local_processing_retry`, f00000 `resumable_partial`; verified 1,677,721,600 B; **known = worst-case network 860,310,719 B** |
| Planner `expected` (layout model, not a measurement) | 2,417,467,623 B transfer, 2 requests |
| Ceilings | max_records 1,205,646; max_transferred 7,253,000,192 B; max_requests 32; max_file 4,834,983,936; durable 8,946,451,766 per file; output 4,111,467,830; scratch cap 17,899,211,372; min free 34,359,738,368; deadline 14,400 s |
| next_cursor | 2 (unchanged) |

`sufficiency` reports `INCOMPLETE` with unresolved ranks `{"2": [0, 1]}`. The
seal refuses until both ranks seal under p02, and `run --plan 1` is refused
because p01 has been repaired.

The modeled transfer counts f00000 again as a whole file, but the runner
resumes from the verified prefix. f00000 must download, so p02 must run
**without** `--offline`.

## 8. Tests and checks

New file: `tests/test_source_file_bounds.py`, 4 tests. They cover:

- the FineWiki-only tables, with the generic, UltraX and FinePDFs bounds
  unchanged;
- only the row and canonical keys change; the new bound gives a new plan digest
  and hash; removing it reproduces the old identity byte-identically;
- a zero-sealed repair covers every rank and keeps the cursor; unchanged limits
  are refused;
- end to end on authored Parquet behind a loopback server:
  - the row-limit failure reproduces under the old bound;
  - p01's history is byte-identical afterwards;
  - sufficiency is `INCOMPLETE` and the seal is refused;
  - a top-up at cursor 2 is refused;
  - p02 covers ranks [0, 1];
  - p02 classifies the retained source as `local_processing_retry` and the
    partial as `resumable_partial` before it runs;
  - `run --plan 1` is refused;
  - the retained file gets zero hits;
  - the resumed file's ranges all start at the verified offset;
  - each rank is sealed once, as (2,0) and (2,1), with cursor 2.

Commands and results (`--basetemp` under `C:/xt`, thread variables set to 1):

- `uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_source_file_bounds.py -n 0`
  → **4 passed**.
- Related files: file_bounds, repair, record_bound, plan, run and growth, with
  `-n 4 --dist=worksteal -m "not serial"` → **94 passed**.
  - Two earlier `-n 16` runs each had one failure, in a different non-repair
    `test_source_run` test each time. One failure was `ScratchCapError:
    reservation path escapes its budget root`.
  - Both tests pass alone with `-n 0`. The new code returns early for
    non-repair plans.
  - This is recorded as an **open parallel-load flake. It is not counted as a
    pass.**
- `ruff check`, `ruff format --check` (changed files), `mypy --strict`
  (source_plan, source_run) and `git diff --check`: all pass.
- **Not run:** the full suite, serial selection, network and CUDA tests, and
  any p02 run.

## 9. Operator commands (STOP at digest review)

```powershell
$env:XLM_DATA_ROOT='G:\XLM'; $env:XLM_HOME='G:\XLM\xlm-home'; $env:XLM_SCRATCH_ROOT='C:\XLM-scratch'
$env:HF_HUB_OFFLINE='1'; $env:HF_DATASETS_OFFLINE='1'; $env:PYTHONUTF8='1'
Get-Content -LiteralPath 'G:\XLM\plans\finewiki\p02\plan.json'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py resume-check --source-key finewiki --plan 2
# STOP: review PLAN DIGEST 319d6bf3dbb7ef085b637c27ea75a89d9d1a97f6b1337153cfebebb1844a305b
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py authorize --source-key finewiki --plan 2 --digest 319d6bf3dbb7ef085b637c27ea75a89d9d1a97f6b1337153cfebebb1844a305b --operator '<operator-name>'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py run --source-key finewiki --plan 2
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py verify --source-key finewiki --plan 2
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py sufficiency --source-key finewiki
# Only if SUFFICIENT:
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py seal --source-key finewiki
```

## Open limitations

- The record maximum of 000_00014 is unknown, because only its footer was
  read. A row above 18 MiB would fail closed and be repaired again.
- The FineWiki layout still carries the v1 logical-bytes label. A whole-file
  sizing measurement from p02's receipts would correct planning for later
  top-ups.
- A parallel-load flake in `test_source_run` was observed at `-n 16`.
