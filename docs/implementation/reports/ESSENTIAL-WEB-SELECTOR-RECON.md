# Essential-Web selector reconnaissance

Date 2026-09-27. Branch `data/mix01-ultrax-6b`. Starting HEAD `9706bc0`
(clean tree). Agent work was offline only. No network, real-data fetch,
admission, production plan, tokenizer, training, pilot or push, and no
writes under `X:\XLM`. The 3 real certified rows at
`D:\Project\xlm-operator-pilot\adapter-cert-essential-web01` were read locally
to confirm field paths. Nothing was written there.

**Verdict (update): READY FOR ESSENTIAL 8-UNIT RECON FETCH** (see §10–§12).
Original verdict: READY FOR ESSENTIAL LIVE RECON.

## 1. The semantic problem

`EssentialWebAdapter` takes `essential_science`, `essential_practical` or
`essential_prose` as explicit operator configuration and stamps it as
`mix01_component`. It does **not** read the upstream taxonomy to pick
rows. `mix01_views.yaml` records the classifier paths only as description.

As a result, the three calibration units adapt **identical raw rows**
under three labels. They are not three distributions, and calibrating
them now would measure the same data three times. The selection policy
does not exist yet. This task gathers the evidence needed to write it. It
does not change the adapter, weights, quotas, selectors, admissions or
plans.

The old runbook's `data/v1/train/0000N.parquet` paths are wrong for the
pinned tree, which is laid out as `data/crawl=CC-MAIN-YYYY-WW/…`. The
recon workflow never uses the old paths. The calibration driver's
`DefaultFiles` for Essential is left unchanged, because that unit must not
run until selectors exist.

## 2. Design

There are four operator stages, and each one is a separate command.

1. **DISCOVER** runs `scripts/essential_web_recon.py discover --live`.
   - It makes metadata-only tree listings at the exact revision.
   - It uses the allowlisted `HuggingFaceTransport` opener, host
     validation, and a `TransportBudget` of 80 requests, 32 MiB, 30 s per
     request and a 600 s deadline.
   - It follows paginated `Link: rel="next"` responses.
   - It writes `discovery.json`, the frozen manifest with its digest, and
     `candidate_files.txt`.
2. **FOOTER** runs `xlm data sample-blocks --mode window
   --window-policy-version 2`.
   - It reads footers only, through bounded range reads.
   - Its evidence records row groups, the logical fields, the physical
     leaves, and per-leaf compressed and uncompressed bytes.
   - window-v2 refuses a projected list, map or union, or a struct that
     contains one, before any payload moves.
3. **SAMPLE** runs the existing chain: `xlm data plan`, then
   `xlm data fetch`, then `xlm data verify`.
   - Acquisition is window-v2 selected-record acquisition.
   - It uses the pinned revision, seed 20260918 and explicit row ranges.
   - The plan caps records and bytes, and there is no whole-shard
     fallback.
   - The helper contains no downloader.
4. **ANALYZE** runs `scripts/essential_web_recon.py analyze`, fully
   offline.
   - It checks the manifest digest.
   - It refuses records whose locator repository, revision or
     `source_file` is outside the manifest.
   - It writes `analysis.json` and `analysis.md` deterministically: sorted
     keys and no timestamps.

## 3. Crawl stratification

1. Parse each `data/` directory as `crawl=CC-MAIN-YYYY-WW`, then sort by
   `(year, week)`.
   - A malformed or out-of-range directory refuses.
   - A duplicate refuses.
   - A non-directory entry is recorded in `ignored_top_level_files`.
2. Split the N crawls into K contiguous strata of near-equal size, where
   stratum `i` covers `[floor(iN/K), floor((i+1)N/K))`. The default is
   `K = 8` and it is configurable with `--strata`.
3. From each stratum, pick the crawl at index
   `sha256("seed|repo|revision|essential-recon-v1|stratum|i") mod size`.
4. List only the chosen crawl directories.
   - A subdirectory refuses, because that layout has not been reviewed.
   - The `.parquet` files are sorted, and one is picked with
     `sha256("seed|repo|revision|essential-recon-v1|<crawl>|file") mod count`.

The choice never depends on provider order: shuffled listings give an
identical manifest. It is also never "first N".

**Why K = 8.** Eight strata spread over the whole CommonCrawl timeline, so
early and late crawl eras are both represented. That is 8 footers and 8
windows. At 512 rows per window this gives 4,096 rows: enough for class
shares of about 1% (±0.3 percentage points binomial SE at p = 0.01) and
for the main cross-tabs, while staying a diagnostic sample.

## 4. Deterministic identities

- Repository `EssentialAI/essential-web-v1.0`, revision
  `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`. It must be exactly 40
  lowercase hex characters; branch names refuse.
- Seed `20260918`. `source_id` is `essential_web`, and the recon view is
  `selector_recon`. This separate view keeps the recon plan and raw ids
  apart from the `essential_*` calibration ids.
- The manifest `digest` is the SHA-256 of the canonical sorted-key JSON of
  the body without `digest`. It also records:
  - `crawl_dirs_sha256`, and per crawl its `listing_sha256`, `size`,
    `oid` and `lfs_sha256`;
  - the ordering rules;
  - the projection.
- The window start and plan identity come from the existing v2 identity:
  `sha256(seed|source|view|revision|file|row_group|window-v2|start)` and
  the plan's behavioral hash.

## 5. Fields inspected

Real rows at the pinned revision have these top-level columns: `id`
(int), `pid` (str), `text`, `metadata`, `eai_taxonomy`, `quality_signals`
and `line_start_n_end_idx`.

**`eai_taxonomy`.** Each classifier has `primary` and `secondary`, and
each of those has `code` and `label`. The classifiers are:

- `free_decimal_correspondence`, which has `code` plus
  `labels.level_1/2/3` instead of `label`;
- `document_type_v1` and `document_type_v2`;
- `bloom_cognitive_process` and `bloom_knowledge_domain`;
- `extraction_artifacts` and `missing_content`;
- `reasoning_depth`, `technical_correctness` and `education_level`.

**`quality_signals`.**

- `fasttext`: `english`, `dclm`, `fineweb_edu_approx`,
  `eai_general_math`, `eai_open_web_math` and `eai_web_code`.
- `red_pajama_v2`: 30 numeric `ccnet_*` / `rps_doc_*` scalars.

**`metadata`.** `url`, `source_domain`, `snapshot_id`, `warc_info`, and
the `warc_metadata` struct.

All of these are structs of scalars. The only lists are in
`line_start_n_end_idx`.

**Projection.** The projection is derived in code as the certified
`columns_for("essential_web", …)` contract minus `text`:
`eai_taxonomy, quality_signals, id, pid, metadata`. `text` is not
projected, and `line_start_n_end_idx` is not in the contract.

**What analyze reports.**

- Primary-label distributions for **every observed** classifier.
- FDC code prefixes at 1, 2 and 3 digits, plus `level_1` labels.
- Three cross-tabs: FDC digit × `document_type_v2`, FDC digit × bloom
  knowledge domain, and `document_type_v2` × bloom knowledge domain.
- Nearest-rank percentiles for every observed `fasttext` and
  `red_pajama_v2` scalar.
- ok / missing / null / malformed accounting for the adapter-read paths.
- English-threshold shares and shares of `extraction_artifacts` and
  `missing_content`. These are observations only.
- Coverage and per-crawl counts for each candidate probe, pairwise
  intersection and Jaccard, and group union and overlap.

**Candidate probes.** The defaults use only structure observed in real
rows. FDC codes are Dewey-compatible (for example, the real row `746.92`
is Arts / Needlework). The probes are `science.fdc_5xx`,
`science.fdc_5xx_61x`, `practical.fdc_6xx`, `practical.bloom_procedural`,
`prose.fdc_8xx` and `prose.fdc_7xx_8xx_9xx`.

- `document_type_v2` labels are **not** guessed. Once the observed labels
  are in `analysis.md`, refine the probes offline with
  `--candidates file.json`. The grammar is data-only: `group`, `all`, and
  conditions `path` / `in` / `prefix_in` / `gte` / `lte`. Any other key,
  including `approved`, refuses.
- Every candidate carries `status: OBSERVATION_ONLY_NOT_APPROVED`, and
  `approved_selectors` is always `[]`.

## 6. Parquet and nested-schema assumptions

- The JSON of the real rows shows that the projected fields are nested
  structs of scalars. That is supported by window-v2 and needs no change.
  The footer stage checks the real physical schema. If any projected
  field is physically a list or map, sample-blocks exits 1 and writes
  nothing. That is a STOP: report it, and do not broaden v2.
- Unknowns until the footer runs:
  - row-group sizes;
  - per-leaf bytes (`metadata.warc_info` may dominate);
  - the file count per crawl;
  - whether a whole Essential footer fits in 32 MiB over 8 files.
- A `total_byte_size` or ratio refusal on every group of a file skips that
  file, and the evidence records the reason.

## 7. Network and data bounds

| Stage | Bounds |
|---|---|
| Discover | 80 requests, 32 MiB, 30 s per request, 600 s deadline, `huggingface.co` allowlist, 4 MiB per page |
| Footer | `--metadata-bytes 33554432`, `--metadata-requests 160` |
| Window | 16,384 scan rows, 4 MiB stream buffer, batches of 256 rows, 512 records per file |
| Plan | `--max-records 4096`, `--max-bytes 268435456` (256 MiB transfer), default 2 GiB output disk, pilot-capped |
| Analyze | at most 200,000 records and 1 MiB per line |

## 8. Tests

**New file.** `tests/test_essential_web_recon.py` has 14 tests, uses
authored fixtures, and blocks sockets. It covers:

- stratified determinism;
- a seed change;
- a required pinned revision;
- independence from provider order and "not first file";
- refusal of malformed and duplicate crawl ids;
- digest reproducibility and tamper detection;
- a supported Essential-shaped nested projection, with no `text` or list
  leaves;
- fail-closed refusal of a list inside `eai_taxonomy`;
- missing, null and malformed accounting;
- distributions and cross-tabs;
- nearest-rank percentiles;
- candidate overlap and Jaccard, and refusal of `approved`;
- analysis without `text`, and refusal of foreign records;
- deterministic analyze CLI outputs;
- the exact recon `sample-blocks` → `plan` argv over a local
  `data/crawl=…/00001.parquet`.

**Commands run.** All ran from `G:\Project\xlm-data-ultrax` with
`OMP_NUM_THREADS=1`.

- `uv run --offline --locked --no-sync --extra cpu --extra eval python -m
  pytest tests/test_essential_web_recon.py tests/test_parquet_window_nested.py
  -n 0 -q` → **36 passed**, exit 0.
- `ruff check` and `ruff format --check` on both new files → clean.
- `MYPYPATH=src mypy scripts/essential_web_recon.py
  --follow-imports=silent` → no issues.

**Not run.** The fast and full suites, and all live or network tests.

**Also checked.** The 3 real certified rows were read locally: every
accounted path is `ok`, and 10 classifiers are observed.

## 9. Files changed

- `scripts/essential_web_recon.py` (new)
- `tests/test_essential_web_recon.py` (new)
- `docs/implementation/reports/ESSENTIAL-WEB-SELECTOR-RECON.md` (new)
- `docs/implementation/STATUS.md`

## 10a. Original USER commands (superseded by §11)

Run these in PowerShell from `G:\Project\xlm-data-ultrax`, in order:

```powershell
$env:XLM_HOME="X:\XLM\xlm-home"; $env:HF_HOME="X:\XLM\hf-cache"; $env:HF_DATASETS_CACHE="X:\XLM\hf-cache\datasets"
$R="X:\XLM\recon\essential_web"; $Rev="ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"; $F="eai_taxonomy,quality_signals,id,pid,metadata"; $UV=@("run","--offline","--locked","--no-sync","--extra","cpu","--extra","eval")
$env:HF_HUB_OFFLINE="0"; $env:HF_DATASETS_OFFLINE="0"
# A. DISCOVER (network metadata only)
uv @UV python scripts/essential_web_recon.py discover --live --revision $Rev --seed 20260918 --strata 8 --out-dir $R
$Files = (Get-Content "$R\candidate_files.txt") -join ","
# Pin the recon view's probe evidence; the check MUST exit 2 (pinned revision matches) or STOP
uv @UV xlm data probe --catalog manifests/datasets.catalog.yaml --source essential_web --view selector_recon --live --budget-mib 16 --probe-id recon01 --json
uv @UV python scripts/calibration_adopt.py probe --store $env:XLM_HOME --source essential_web --view selector_recon --revision $Rev --repository EssentialAI/essential-web-v1.0; "exit=$LASTEXITCODE"
# B. FOOTER (no payload; refuses lists/maps before any payload)
New-Item -ItemType Directory -Force "$R\footer" | Out-Null
uv @UV xlm data sample-blocks --source essential_web --view selector_recon --catalog manifests/datasets.catalog.yaml --revision $Rev --files $Files --seed 20260918 --mode window --window-policy-version 2 --project-fields $F --block-records 512 --target-records 4096 --max-records 4096 --window-max-scan-rows 16384 --window-buffer-bytes 4194304 --window-batch-rows 256 --metadata-bytes 33554432 --metadata-requests 160 --output "$R\footer\rows.json" --report "$R\footer\rows.evidence.json"
# REVIEW rows.evidence.json (projected_physical_leaves, windows, estimated_transfer_upper_bytes) before continuing
uv @UV xlm data plan --source essential_web --view selector_recon --catalog manifests/datasets.catalog.yaml --files $Files --mode selected_records --row-ranges "$R\footer\rows.json" --project-fields $F --seed 20260918 --attempt 1 --parquet-window-scan-rows 16384 --parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 --parquet-window-policy-version 2 --max-records 4096 --max-bytes 268435456 --pilot-approved --output "$R\plan.json"
# C. BOUNDED FETCH
uv @UV xlm data fetch --plan "$R\plan.json" --output-dir "$R\raw" --scratch-dir "$R\scratch" --pilot-approved
$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"
# VERIFY (offline; not published into the artifact store)
uv @UV xlm data verify --plan "$R\plan.json" --output-dir "$R\raw" --scratch-dir "$R\scratch" --json --no-publish
# D. OFFLINE ANALYZE
uv @UV python scripts/essential_web_recon.py analyze --manifest "$R\discovery.json" --records "$R\raw\selected_records.jsonl" --output-json "$R\analysis.json" --output-md "$R\analysis.md"
```

**STOP conditions.**

- **Discover** refuses on a malformed crawl id, a subdirectory inside a
  crawl, or an exhausted budget.
- **The probe check** is anything other than exit 2. This means upstream
  resolved a revision other than `ce4eccc…`.
- **Sample-blocks** exits 1. That covers a list or map in the projection,
  no eligible group, or an exhausted budget.
- **Fetch or verify** exits non-zero.

On any of these, send the output back. Do not retry with wider bounds.

## 10b. Original STOP point (superseded by §12)

**STOP after `analysis.json` and `analysis.md` exist.** Return both, and
`footer\rows.evidence.json`, for review.

Selector policy is a separate reviewed task: it freezes definitions, then
changes the adapter and view semantics, then runs three text-bearing
calibrations. None of that is authorized here. Do not run the
`essential_*` calibration units in the meantime.

## 10. Real execution findings and execution-v2 (update, same date)

### What ran live

**Discovery succeeded.**

- Digest: `c8d448fafb10b524496e6fd51bcde23f87c19c95b68dffe02f293d96be7530a7`.
- Revision `ce4eccc…3113d`, seed 20260918, 8 strata.
- Crawls chosen: 2014-15, 2015-32, 2016-50, 2018-05, 2019-09, 2021-04,
  2021-49 and 2024-26.

**The generic probe for `selector_recon` hit its body limit.**

- Outcome: `budget_exhausted`, 1 request, 0 body bytes, "response exceeds
  its allocated body limit".
- The repository-API metadata response is larger than the probe's
  per-response snapshot-info ceiling (512 KiB).
- The production probe is **not** changed.
- To bind the revision to plans, the operator made
  `X:\XLM\recon\essential_web\recon_catalog.yaml`, pinned to the revision
  discovery had already found. This is a **recon-only research binding**,
  not production evidence or admission.

**The first design refused at the footer stage.**

- Design: the 5-field projection (`eai_taxonomy, quality_signals, id, pid,
  metadata`) in one 8-file, 4,096-row plan.
- Footer planning estimated about **104 requests per file**.
- `PILOT_MAX_REQUESTS` is 100 **per acquisition plan**, so it refused.
- This is a physical-leaf/request limit, not a transfer-size limit. The
  bound is not raised.

**The reduced 2-field projection passed on all 8 files.**

- Projection: `eai_taxonomy, quality_signals`, with 81 physical leaves.
- Footer-only sampling was run for each file independently, into
  `split\NN`.
- Window v2: scan ≤ 16,384 rows, buffer 4 MiB, batches of 256 rows.

| Unit | Crawl | Rows | Scanned | Transfer ≤ (bytes) | Requests |
|---|---|---|---:|---:|---:|
| 00 | 2014-15 | [55285,55797) | 5888 | 1,637,117 | 85 |
| 01 | 2015-32 | [80616,81128) | 1280 | 1,651,844 | 85 |
| 02 | 2016-50 | [50907,51419) | 1536 | 1,636,909 | 85 |
| 03 | 2018-05 | [64350,64862) | 4864 | 1,646,437 | 85 |
| 04 | 2019-09 | [67774,68286) | 8448 | 1,642,273 | 85 |
| 05 | 2021-04 | [44839,45351) | 5376 | 1,644,318 | 85 |
| 06 | 2021-49 | [16495,17007) | 7168 | 1,658,707 | 85 |
| 07 | 2024-26 | [13163,13675) | 3840 | 1,651,131 | 85 |

Totals: 4,096 retained rows, 38,400 scanned rows, and about 13,168,736 B
of upper-bound transfer.

### The corrected shape: execution-v2

The corrected shape is **8 independent 512-row pilot plans**, one per
file. It is never one 8-file plan.

**`discovery.json` is kept as written.** Its 5-field projection records
the original proposed recon. The new `execution` subcommand writes a
separate `execution.json`, version `essential-recon-execution-v2`. It
never fetches data. It binds:

- `parent_discovery_digest`, which must equal the digest passed in
  `--expect-discovery-digest`;
- the repository, revision and seed (both must be the pinned values);
- the 8 strata, with each unit's stratum and crawl identity;
- `projection = ["eai_taxonomy", "quality_signals"]`;
- 512 records per unit, 4,096 in total;
- the window-v2 policy: scan 16,384, buffer 4,194,304, batch 256;
- `plan_shape` of one independent plan per file;
- `pilot_max_requests_per_plan = 100`, read from code;
- the narrowing rationale (104 → 85 requests, split into 8 plans) and the
  probe limitation.

**Per unit, it records:**

- the file;
- the adopted `row_range`;
- the row group, scan rows, requests and transfer upper bound;
- `rows_sha256` and `evidence_sha256`;
- relative paths: `split/NN/rows.json`, `split/NN/rows.evidence.json`,
  `units/NN/plan.json`, `units/NN/raw` and `units/NN/scratch`.

The manifest has its own SHA-256 digest. Built in memory from the real
artifacts, it is `5065bcf6001ee38b63ca783f20a625af0256f2788ce9a0034edfba34c125bc23`.

**Footer adoption.** Each `split/NN` pair must match exactly:

- source, view, revision and seed;
- `mode` is `window`;
- `selected_files == [file]`;
- the logical projection is the 2 fields;
- `planned_records` is 512;
- `row_ranges` equals `rows.json`, and that equals the window's
  `start_row`/`stop_row`, with `stop - start = 512`;
- the policy is v2 with the same scan, buffer and batch;
- estimated requests ≤ 100.

Anything else refuses, and nothing is rewritten. When `execution.json`
already exists, identical bytes are adopted and anything different
refuses.

**Distinct plan identities.** All units share
`plan_id = plan_essential_web_selector_recon_huggingface`, but each has
its own scratch/journal directory. Their behavioral `plan_hash` values
differ because `selected_files` and `row_ranges` differ. Combine refuses
a duplicate plan hash.

### Combine (offline)

For every unit, in stratum order, combine checks:

**The plan.**

- `load_acquisition_plan` passes, which re-verifies the hash.
- source, view, repository and revision match.
- `mode` is `selected_records`.
- `selected_files == [file]`.
- `row_ranges == {file: range}`.
- `projected_fields == [eai_taxonomy, quality_signals]`.
- `is_pilot` is set.
- The window is v2 with the exact scan, buffer and batch values.
- `max_records ≤ 512` and `max_requests ≤ 100`.
- The hash is not a duplicate of another unit's.

**The journal**, `scratch/journals/<plan_id>.progress.json`.

- It binds the same plan id and hash.
- Its status is `COMPLETED`.
- It tracks only `selected_records.jsonl`, which is completed.
- `records_acquired` is 512.

**The part.**

- The output size and SHA-256 equal the journal's.
- It has exactly 512 lines of strict JSON.
- The fields are exactly the projection plus the locator.
- The locator's `source_file` is the unit's file, and its revision and
  repository match.
- `row_index` lies in `[start, stop)`.
- `(file, row_index)` is unique across all units.

**Refusals** cover: a missing or duplicate part, the wrong file, revision,
plan hash, projection, window version or row range, a count other than
512, corrupt JSONL, a digest that drifted from the journal, and a
duplicate locator.

**Outputs.** Parts are concatenated byte for byte in stratum order into
`raw\selected_records.jsonl`. `bundle.json` records:

- the execution and discovery digests;
- per part: plan hash, byte count and SHA-256;
- 8 parts and 4,096 records;
- the combined SHA-256, and the receipt's own digest.

Existing identical outputs are adopted, and anything different refuses.

### Analyzer

Analysis needs only `eai_taxonomy` and `quality_signals`.

- `id`, `pid` and `metadata.*` are no longer accounted paths.
- Crawl and file identity come from the acquisition locator.
- `text` is never read.
- The distributions, cross-tabs, percentiles, quality observations,
  candidate coverage and overlap are unchanged.
- Nothing is approved.
- `analyze --manifest discovery.json` still works, because the file set is
  identical.

### Tests

`tests/test_essential_web_recon_execution.py` has 23 tests. Plans come
from the real offline `xlm data plan` CLI. Journals and parts are authored
in the fetcher's own models, and sockets are blocked. They cover:

- discovery left unchanged, and the parent digest bound;
- the 2-field projection;
- 8 units, 8 × 512 = 4,096, one plan per unit, and stratum identity;
- a restart-safe execution write;
- 4 incompatible-footer refusals, plus a rows mismatch and a missing
  footer;
- combine byte order and the 4,096 total;
- combine CLI write, adopt and tamper refusal;
- refusals for the wrong plan file, a record from another file, the wrong
  revision, plan hash, projection, window version or row range, a row
  outside the range, a count of 511, a duplicate locator, a corrupt or
  drifted part, and a missing or duplicate part;
- bundle analysis without id, pid, metadata or text, with nothing
  approved.

**Commands run.** Focused run:
`uv run --offline --locked --no-sync --extra cpu --extra eval python -m
pytest tests/test_essential_web_recon.py
tests/test_essential_web_recon_execution.py
tests/test_parquet_window_nested.py -n 0 -q` → **59 passed**, exit 0.
`ruff check`, `ruff format --check` and scoped mypy are clean.

**Not run.** The fast and full suites, and anything live.

## 11. Exact USER commands (current real state)

Run these in PowerShell from `G:\Project\xlm-data-ultrax`. Only the fetch
step uses the network.

```powershell
$env:XLM_HOME="X:\XLM\xlm-home"; $env:HF_HOME="X:\XLM\hf-cache"; $env:HF_DATASETS_CACHE="X:\XLM\hf-cache\datasets"
$R="X:\XLM\recon\essential_web"; $Cat="$R\recon_catalog.yaml"; $F="eai_taxonomy,quality_signals"; $UV=@("run","--offline","--locked","--no-sync","--extra","cpu","--extra","eval")
# 1. execution manifest (offline; adopts the 8 real split\NN footers, refuses any mismatch)
uv @UV python scripts/essential_web_recon.py execution --discovery "$R\discovery.json" --expect-discovery-digest c8d448fafb10b524496e6fd51bcde23f87c19c95b68dffe02f293d96be7530a7 --root $R --output "$R\execution.json"
$E = Get-Content -Raw "$R\execution.json" | ConvertFrom-Json
# 2. eight independent plans (offline; existing plans are kept)
foreach ($u in $E.units) { $p = Join-Path $R $u.paths.plan; if (Test-Path $p) { continue }; New-Item -ItemType Directory -Force (Split-Path $p) | Out-Null; uv @UV xlm data plan --source essential_web --view selector_recon --catalog $Cat --files $u.file --mode selected_records --row-ranges (Join-Path $R $u.paths.rows) --project-fields $F --seed 20260918 --attempt 1 --parquet-window-scan-rows 16384 --parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 --parquet-window-policy-version 2 --max-records 512 --pilot-approved --output $p; if ($LASTEXITCODE -ne 0) { throw "plan $($u.unit) failed" } }
# 3. display identities (8 distinct plan_hash values expected)
foreach ($u in $E.units) { "unit $($u.unit) $($u.crawl)"; uv @UV python scripts/calibration_adopt.py plan-identity --plan (Join-Path $R $u.paths.plan) }
# 4. sequential bounded fetch (network); a completed unit is adopted, never refetched
$env:HF_HUB_OFFLINE="0"; $env:HF_DATASETS_OFFLINE="0"
foreach ($u in $E.units) { $p = Join-Path $R $u.paths.plan; $o = Join-Path $R $u.paths.raw; $s = Join-Path $R $u.paths.scratch; uv @UV python scripts/calibration_adopt.py fetch --plan $p --scratch-dir $s --output-dir $o; if ($LASTEXITCODE -eq 2) { continue } elseif ($LASTEXITCODE -ne 0) { throw "fetch adoption refused $($u.unit)" }; uv @UV xlm data fetch --plan $p --output-dir $o --scratch-dir $s --pilot-approved; if ($LASTEXITCODE -ne 0) { throw "fetch $($u.unit) failed" } }
$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"
# 5. sequential verify (offline, not published)
foreach ($u in $E.units) { uv @UV xlm data verify --plan (Join-Path $R $u.paths.plan) --output-dir (Join-Path $R $u.paths.raw) --scratch-dir (Join-Path $R $u.paths.scratch) --json --no-publish; if ($LASTEXITCODE -ne 0) { throw "verify $($u.unit) failed" } }
# 6. combine (offline)
uv @UV python scripts/essential_web_recon.py combine --execution "$R\execution.json" --root $R --output "$R\raw\selected_records.jsonl" --receipt "$R\bundle.json"
# 7. analyze (offline)
uv @UV python scripts/essential_web_recon.py analyze --manifest "$R\discovery.json" --records "$R\raw\selected_records.jsonl" --output-json "$R\analysis.json" --output-md "$R\analysis.md"
```

**Expected results.**

- Step 1 prints digest `5065bcf6…bc23` and 4,096 records.
- Step 6 prints 4,096 records and writes `bundle.json`.

**STOP conditions.** Stop and send back the output if any of these
happen. Do not widen any bound.

- A refusal (exit 2) from `execution` or `combine`.
- A `throw` from any loop.
- A plan-identity listing that shows repeated plan hashes.

## 12. STOP point

**STOP after `analysis.json` and `analysis.md` exist.** Return them,
together with `bundle.json` and `execution.json`.

- Selector freezing is still a separate, reviewed task.
- The `essential_*` calibration units must not run.

## 13. Limitations

- Window samples are clustered, one contiguous window per file. The
  shares are partial diagnostic observations, not corpus estimates.
- Byte yield is not measured, because `text` is excluded by design.
- The 256 MiB transfer cap is an upper bound. The real per-file transfer
  is only known from the footer evidence (`estimated_transfer_upper_bytes`).
- The discover pagination and HF tree field names (`type`, `path`,
  `size`, `oid`, `lfs.oid`) are exercised only against a fake lister.
  The first live run is the compatibility check.
- Combine is tested against journals and parts authored in the fetcher's
  own Pydantic models, not against a real fetch. The first live 8-unit run
  is the check that it works with real fetch output.
- The 8 units share `plan_id` but not `plan_hash`. The unit-specific
  scratch directories keep their journals apart.
