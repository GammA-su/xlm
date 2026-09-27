# Parquet window-v2: nested struct projections

Date 2026-09-27. Branch `data/mix01-ultrax-6b`. Starting HEAD `c68b421`
(clean tree). Everything here ran offline. No network, fetch, admission,
tokenizer, training, pilot or push. X:\XLM was only read: the real SYNTH
v1 `plan.json`, `rows.json` and `rows.evidence.json` were copied into
`tests/fixtures/window_v1_synth/` to pin them.

**Verdict: READY FOR WIKI WINDOW FOOTER RETRY.**

## 1. Problem

The Wiki source is `nemotron_specialized` / `Nemotron-Pretraining-Wiki-Rewrite`
@ `9ed3718b5f2ae29074c5e34e64115432b7c4320f`, file
`Nemotron-Pretraining-Wiki-Rewrite/part_000003.parquet`. Its footer, as
observed by the USER, is one row group of 263,542 rows with 5 physical
columns: `total_byte_size` 892,867,087 and compressed 320,096,797.

Both existing sampling modes refuse it, correctly:

- whole-row-group sampling, because the group exceeds 32 MiB;
- window-v1, with `window mode supports flat projected columns only;
  nested: ['metadata']`, because the `wiki_rewrite` projection
  `[text, license, metadata, uuid]` contains the struct `metadata`.

## 2. Arrow logical schema vs Parquet physical leaves

Probed with PyArrow 25.0.1 on authored files:

| API | What it lists | Example |
|---|---|---|
| `ParquetFile.schema_arrow.names` | logical top-level fields | `text, license, metadata, uuid` |
| `ParquetFile.schema.names` | **leaf** names, not paths; duplicates possible (`element`) | `text, license, category, models_used, uuid` |
| `schema.column(i).path` / `row_group.column(i).path_in_schema` | physical leaf paths, one column chunk each | `metadata.category`, `metadata.models_used` |
| `schema.column(i).max_repetition_level` | > 0 for list/map leaves | `lst.list.element` → 1 |

- A struct owns **every** leaf beneath it. Leaves are stored in
  depth-first schema order.
- `iter_batches(columns=["metadata"])` reconstructs the full struct dict.
- The old resolver indexes `schema.names`. That cannot express a struct,
  so the flat assumption "one projected field = one chunk" is false for
  nested data.

## 3. Resolver (`src/xlm/data/acquisition/projection.py`, shared)

`map_fields_to_leaves(fields, leaves)` walks the logical top-level Arrow
fields in order and assigns each one the next `leaf_count(type)` physical
leaves:

- a scalar owns 1 leaf;
- a struct owns the sum of its children's leaves;
- a list owns its element's leaves;
- a map owns its key's and its value's leaves.

It fails closed when:

- the leaf count disagrees with the schema;
- a leaf path is neither `name` nor `name.<child…>`;
- two top-level names are duplicated;
- the leaves are not in dense order;
- the type is a union.

`resolve_projection(mapping, requested)` then:

- de-duplicates the requested fields, keeping request order;
- refuses unknown fields;
- refuses unsupported types, with the reason;
- refuses any physical leaf claimed twice;
- returns the sorted unique leaf indices plus the owning logical field of
  each leaf.

Sampling and fetch both call this one function (through
`parquet_field_leaves`), so they cannot disagree.

**Supported in window-v2:** scalars, and structs of scalars or structs at
any depth.

**Refused with a reason:** lists (including large, fixed-size and view
variants), maps, unions, a struct that contains any of those, and any leaf
with a repetition level above 0. For repeated leaves the number of values
is not the number of rows, so row-based scan and byte accounting would be
wrong. The tests cover structs only; no list or map support is claimed.

## 4. v1 preservation and v2 identity

- **No new model field.** `ParquetWindowDecode` gained none, so a v1 plan's
  `model_dump` and hash are byte-identical.
  - Supported versions are now `(1, 2)`; the default is still 1.
  - The real SYNTH plan `plan_synth_default_huggingface_2353ec572c800576ff82`
    still hashes to `dd248136…8c33`, and its selection hash is `83c2bb27…7caf`.
    Both are pinned by a test against the copied real plan.
- **window-v1 is frozen.** The sampling branch and the fetch branch
  (`_resolve_projection` over leaf names) are unchanged, and v1 still
  refuses nested projections with the exact old message.
- **A fresh v1 report** has exactly the key sets of the real SYNTH evidence
  (top level, per window, and `window_policy`).
- **v2 identity.** `policy_version: 2` is part of the dumped window, so it
  binds:
  - the **behavioral hash**, and with it `plan_id` and authorization;
  - the **sampling evidence** (`window_policy.policy_version`);
  - the **start construction**
    `sha256(seed|source|view|revision|file|row_group|window-v2|start)`, and
    the file order and group choice;
  - **adoption.** The helper always compares `policy_version`; if the
    driver passes no version, it means 1. A v1 request never adopts v2
    evidence or a v2 plan, and a v2 request never adopts v1 ones.
- **Selection hash.** v2 is excluded from it, as v1 was, because records are
  the same logical selection. v1 and v2 plans over the same rows share a
  selection hash but have different behavioral hashes.
- **Production gate.** v2 uses the same pilot ceilings as v1.

## 5. Physical leaf accounting (v2)

- Eligibility, ratio checks, `selected_compressed_bytes` and
  `selected_uncompressed_bytes`, `largest_selected_chunk`, the chosen-window
  estimates and `eligibility_worst_case` are all computed over the resolved
  physical leaves.
- Each leaf is counted once.
- Unprojected leaves do not contribute, including unprojected nested
  structs and lists.
- The per-row-group chunk paths must equal the schema leaf paths; otherwise
  the group is refused.
- The ratio rule is unchanged: per leaf above `ratio_exempt_bytes`, plus the
  aggregate. It now sees struct children, so a highly compressible
  `metadata.models_used` is refused by name.

New v2 evidence fields (additive; v1 reports never carry them):

- top level: `projected_logical_fields` and `projected_physical_leaves`
  (paths);
- per window: `projected_physical_leaves` as
  `[{logical_field, path, compressed_bytes, uncompressed_bytes}]`.

## 6. Fetch and reconstruction

`_parquet_selection_window` branches on version:

- **v1:** unchanged.
- **v2:** `_resolve_logical_projection` resolves the plan's logical
  projection to all backing leaf indices. `check_window_group` (ratio and
  scan bound) runs on those leaves. Arrow is then asked for the **logical**
  fields, so `metadata` comes back as the same dict a full decode gives.

Streaming, buffering, the range bound, early stop, and the transfer,
request, decompression and scanned-row accounting are the existing code.
Only projected leaf chunks are ever requested. A projection the resolver
refuses at fetch raises `RecordLimitError` before any column bytes move,
so a hand-written range cannot bypass it.

## 7. Wiki applicability and adapter non-regression

- The real file's 5 physical columns are exactly `text`, `license`,
  `metadata.category`, `metadata.models_used` and `uuid`, so the full
  schema is projected.
- If `models_used` were actually a list, its leaf would be repeated and v2
  would refuse at SampleBlocks with a reason.
- `WikiRewriteAdapter` is **unchanged**:
  - `text`, `license`, `metadata.category` and `uuid` are required;
  - `models_used` is optional;
  - the wrong category is `RecordRejectedError`;
  - a missing or null category, or a missing struct, is `MissingFieldError`.
- Tests prove that v2 window records are byte-equal to an independent full
  decode of the same rows, and adapt identically to the legacy exact
  (all-columns) fetch.

**Real-retry prediction** (deterministic, computed offline; N = 263,542, D = 16,384):

- rows **[7519, 8519)** of row group 0;
- `expected_scan_rows` **8,704** (3.3%);
- uniform-row estimate about 10.6 MB compressed / 29.5 MB decoded;
- worst admissible window about 19.9 MB / 55.5 MB, plus at most
  5 × 4 MiB of buffer slack.

## 8. Tests (offline, authored; Windows 11, Python 3.12.13, PyArrow 25.0.1)

- `tests/test_parquet_window_nested.py` (new, 22 tests). Fixture: 140,000
  rows in one row group (legacy refuses it), with the extra unprojected
  `provenance: struct<source_hash, stats: struct<tokens>>` and
  `tags: list<string>`. It covers:
  - the PyArrow leaf model and the resolver (struct depth, dedupe, unknown,
    list, map, struct-wrapping-list, count/path/duplicate conflicts);
  - the real SYNTH plan pin and the v1 evidence-shape pin;
  - v1/v2 hash separation;
  - legacy and v1 refusals;
  - v2 evidence leaves and byte-exact sizes against the footer;
  - the versioned deterministic start, recomputed with `hashlib`;
  - seed and selection-hash changes;
  - remote/local mapping parity;
  - a missing struct and a list child (sampling and fetch);
  - a struct-child ratio bomb and an oversized struct-leaf page;
  - struct reconstruction, and no unprojected leaf bytes transferred;
  - adapter equivalence with the legacy exact fetch;
  - adapter edge rows: a struct missing a child, null `models_used`, null
    category, wrong category, category absent;
  - transfer, request and decompression budgets (each matched on its own
    message);
  - the scan bound before any column bytes;
  - retry, completed reuse (zero requests) and attempt renewal;
  - CLI sample→plan with v2.
- `tests/test_operator_driver_window.py` gains 2 tests: Wiki binds v2
  everywhere, and SYNTH carries no version flags. SimpleStories' exact argv
  is unchanged.
- `tests/test_calibration_adopt.py` gains 1 test: v1 and v2 plans and
  evidence never cross-adopt, and a version without window values is
  refused.
- Focused regression over 21 files (window, rowgroup, plan, fetcher,
  bounds, verifier, perf, selection, adoption, driver, inventory, Wiki and
  SYNTH adapter/cert, views, ingest, rejections): **443 passed** in
  parallel plus **3 serial**, 0 skipped. The full suite was NOT RUN.
- **Mutations, 8/8 killed:**
  - ignore a struct child;
  - double-count struct children;
  - flatten `metadata` in the output;
  - include an unprojected field;
  - drop the version from the behavioral hash;
  - let v1 accept nested projections;
  - leave the version out of the start;
  - adoption ignores the version.
- `ruff check` and `ruff format --check` are clean. Scoped mypy shows only
  the pre-existing unused-ignore at `data_cmd.py`'s numpy import, which was
  already present at HEAD.

**Local benchmark (authored, not committed):**

- Wiki-shaped, 263,542 rows, 882 MB logical. The random-letter text
  compresses only 1.6×, which makes it more conservative than the real
  2.8×.
- v2 driver policy:
  - sampled window: 8.8 MB transferred, 8 requests, 4,096 scanned,
    14.0 MB decoded, peak RSS 107 MB;
  - worst admissible window: 33.9 MB transferred, 14 requests, 16,384
    scanned, 55.7 MB decoded, peak RSS 112 MB.
- The estimates (12.8 MB / 11 requests and 37.7 MB / 17 requests) bounded
  the actuals.

## 9. Driver

- Only `nemotron_wiki_rewrite` gains
  `Window = @{ScanRows 16384; BufferBytes 4194304; BatchRows 256; PolicyVersion 2}`.
- Its sample-blocks, plan and both adoption calls carry the same four
  values.
- SYNTH keeps its unchanged v1 argv, and SimpleStories, FinePDF, FineWiki
  and IFM are untouched.

## 10. Exact USER footer-only retry

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File G:\Project\xlm-data-ultrax\scripts\operator_calibrate_remaining.ps1 -Unit nemotron_wiki_rewrite -Stage SampleBlocks
```

This writes `X:\XLM\calib\nemotron_wiki_rewrite\rows.json` and
`rows.evidence.json`. It reads the footer only and creates no plan and no
fetch. On any bound failure it refuses and writes neither file. Expected
evidence fields:

- `mode: "window"`;
- `window_policy.policy_version: 2`, plus `max_window_scan_rows` 16384,
  `stream_buffer_bytes` 4194304, `batch_rows` 256 and
  `ratio_exempt_bytes` 16777216;
- `projected_fields` (sorted);
- `projected_logical_fields: [text, license, metadata, uuid]`;
- `projected_physical_leaves: [license, metadata.category,
  metadata.models_used, text, uuid]`;
- per window: `projected_physical_leaves` with real
  `compressed_bytes`/`uncompressed_bytes` per leaf, `selected_columns`,
  `selected_compressed_bytes`/`selected_uncompressed_bytes`,
  `largest_selected_chunk` and `unselected_compressed_bytes` (expected 0);
- `start_in_group` 7519, `stop_in_group` 8519, `expected_scan_rows` 8704,
  `start_domain_rows` 16384;
- `estimated_transfer_upper_bytes`, `estimated_requests`,
  `eligibility_worst_case` and the bias warnings.

After review, run the same command with `-Stage All`; the finished stages
are adopted.

## 11. Remaining risks

1. The real per-leaf sizes are unknown until the footer retry. A leaf
   larger than 16 MiB decoded with a ratio above 15×, a repeated
   `models_used`, or pilot estimates exceeded will refuse at SampleBlocks,
   before any payload moves.
2. The estimates assume uniform row size; the runtime budgets are the hard
   bounds.
3. **Finding, not fixed:** the legacy non-window *projected* path
   (`_resolve_projection` over leaf names) also cannot express struct
   projections. A small-group `wiki_rewrite` plan with `--adapter-spec`
   would fail at fetch. It is left unchanged to avoid altering legacy
   semantics, and it does not affect window plans.
4. Lists and maps are unsupported by design. A source that needs them
   requires its own reviewed accounting (window-v3).
5. Production Wiki acquisition, one range per file, is still not
   addressed.
