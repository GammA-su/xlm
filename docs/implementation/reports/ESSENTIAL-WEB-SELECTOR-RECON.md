# Essential-Web selector reconnaissance

Date 2026-09-27. Branch `data/mix01-ultrax-6b`. Starting HEAD `9706bc0`
(clean tree). Agent work was offline only. No network, real-data fetch,
admission, production plan, tokenizer, training, pilot or push, and no
writes under `X:\XLM`. The 3 real certified rows at
`D:\Project\xlm-operator-pilot\adapter-cert-essential-web01` were read locally
to confirm field paths. Nothing was written there.

**Verdict: READY FOR ESSENTIAL LIVE RECON.**

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

## 10. Exact USER commands

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

## 11. STOP point

**STOP after `analysis.json` and `analysis.md` exist.** Return both, and
`footer\rows.evidence.json`, for review.

Selector policy is a separate reviewed task: it freezes definitions, then
changes the adapter and view semantics, then runs three text-bearing
calibrations. None of that is authorized here. Do not run the
`essential_*` calibration units in the meantime.

## 12. Limitations

- Window samples are clustered, one contiguous window per file. The
  shares are partial diagnostic observations, not corpus estimates.
- Byte yield is not measured, because `text` is excluded by design.
- The 256 MiB transfer cap is an upper bound. The real per-file transfer
  is only known from the footer evidence (`estimated_transfer_upper_bytes`).
- The discover pagination and HF tree field names (`type`, `path`,
  `size`, `oid`, `lfs.oid`) are exercised only against a fake lister.
  The first live run is the compatibility check.
