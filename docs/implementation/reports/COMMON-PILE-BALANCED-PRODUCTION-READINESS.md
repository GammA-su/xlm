# Common Pile Balanced production readiness (PARTIAL; stopped at the operator's request)

Worktree `F:\Project\xlm-common-pile`, branch `feat/common-pile-allowlist`, on
top of `3473324`. The session was stopped early because the operator ran out
of quota. This report is the handoff to the next agent. No production run, no
admission, no plan or authorization, no C05, tokenizer or training, no push.

## Done

1. **Operator artifacts verified (Task 1)** with
   `evidence/COMMON-PILE-BALANCED-PRODUCTION-READINESS/verify_operator_artifacts.py`
   (output `operator-artifacts.json`).
   - Allowlist digest `b2bb7c0dc532a6263ca17b78816186fa194c12c09c74614baaeb29c76cdaab04`
     (operator GammA): exactly libretexts, news, oercommons, pressbooks,
     project_gutenberg and public_domain_review; no accepted flags; the
     evidence-matrix sha256 matches the committed matrix.
   - Production inventory digest
     `c0984aa33fb3598a9e724b9518df8cd8a0f2af52b5708ec211fb7fd693df7637`:
     384 files (64 per component), 8,131,797,849 B. It re-derives exactly from
     listing `a32b9b12…5038` plus the allowlist, with seed 20260918 and
     revision `5afc546d…1e7c`.
2. **Real-row certification (Task 2, authorized, network)** with
   `scripts/jsonl_gz_sample.py`, receipt `certification-cert02.receipt.json`,
   digest `f52a0622…cfe1`. The corpus rows are stored only at
   `G:\XLM\calib\common_pile_cert02\real-records.jsonl`.
   - The first 16 rows of `oercommons/oercommons.chunk.49`,
     `pressbooks/pressbooks.chunk.18` and
     `project_gutenberg/project_gutenberg.chunk.43`, read by sequential
     128 KiB `Range` requests from byte 0. No whole file was read.
   - Totals: 16 requests (8 ranges + 8 redirect hops) and 1,048,576 B
     transferred: 131,072 B, 131,072 B and 786,432 B. One extra metadata API
     call confirmed HEAD = `5afc546d…1e7c`.
   - All 48 rows are exactly `{"text": str}`, and the adapter accepted 48/48.
     Decompression amplification: 3.18, 3.14 and 2.70.
   - Gutenberg rows are 67–129 KB of text (median about 128 KB): books appear
     as fixed-size segments, which is relevant to C05 lineage.
   - With the earlier 9 rows (news, libretexts, public_domain_review), all six
     Balanced components show the same `{"text"}` schema in real samples.
3. **Adapter contract v2 (Task 3):** `src/xlm/data/adapters/common_pile_adapters.py`,
   registered in `registry.py` (the frozen `mix01_adapters.py` is untouched).
   - A blank string `text` is a recorded `CommonPileEmptyTextError`.
   - Absent, null or non-string `text` stays a fatal `MissingFieldError`.
   - Valid text is verbatim and byte-identical to v1.
   - Provenance: the upstream component, source file, source row and revision.
   - The adapter code identity changes, so evidence must be renewed (tested).
4. **`.jsonl.gz` production transport (Task 4):**
   - `jsonl_gz.py`: a bounded, fail-closed decoder. It handles multiple gzip
     members, CRC and truncation, trailing garbage, absolute and relative
     expansion bounds, line and row bounds, strict UTF-8, single JSON objects,
     no duplicate keys and no NaN.
   - `source_formats.py`: format by exact suffix; representation
     `verified_source_jsonl_gz` and contract
     `mix01-source-raw-artifact-jsonl-gz-v1`; scratch name `.jsonl.gz.part`.
     Parquet keeps every historical name and byte.
   - `source_local.py`: whole-file serial decode with the same serialization,
     adapter, ledger and summary as Parquet. A row range or row-group
     parallelism is refused.
   - `source_run.py` and `source_benchmark.py`: format-aware identity,
     durable reuse, scratch names, receipts and seal.
   - `source_plan.check_format_modes`: refuses mixed formats; `.jsonl.gz` is
     allowed only in `whole_file_local` or `small_source_direct`, without
     row-group parallelism.
   - **Resume semantics:** download resume is byte-level on the compressed
     file (Range/If-Range from a verified checkpoint, unchanged). Decompression
     never resumes mid-stream; it always runs from byte 0 of the
     SHA-256-verified local file, and a failed decode keeps the download for a
     local retry.
   - Unchanged frozen EW files: `source_parquet.py`, `essential_web_local.py`,
     `columns.py` and the adapter files.
5. **Pre-existing flake fixed.** The intermittent runner-test failure
   `reservation path escapes its budget root` came from Windows non-strict
   `Path.resolve()` keeping a `\\?\` prefix for a vanishing
   `*.state.json.tmp`. The fix, `source_reservations.plain_resolve`, took the
   result from 4/6 failing on base to 6/6 passing.
6. **Calibration tooling (Task 6, code only, untested):**
   - `component_calibration.py`: per-component rates, inventory-weighted
     combined estimate, proposed per-file bounds (max over components x1.35),
     planner layout and measurement file.
   - `scripts/component_calibration.py`: the build/show CLI.
   - `mix01_inventory.py record --measurement` accepts the
     `component_weighted_prefix_samples` basis.
7. **Evidence translation (Task 8, code only, untested):**
   `src/xlm/data/sources/common_pile_evidence.py` turns prefix-sample receipts
   and saved rows into `CertifiedFacts` bound to the allowlist, inventory and
   receipts. It establishes no license.

Tests (focused, `uv run --offline --locked`):
- `tests/test_jsonl_gz.py`: 36 passed.
- `tests/test_source_jsonl_gz_run.py`: 11 passed, run with `-n 8` alongside
  `test_source_repair.py` and `test_source_file_bounds.py`; 6 repeated runs at
  31 passed each.
- Parquet regressions (`test_source_run`, `test_source_repair`,
  `test_source_file_bounds`, `test_ifm_empty_text_recovery`,
  `test_source_rowgroups`, `test_mix01_source_cli`): 104 passed, plus the
  flake, now fixed.

Not run: mypy, a full ruff run over the final state, the related suite, and
any tests for items 6–7.

## NOT done / remaining (handoff)

1. **Tests for the untested modules:** `component_calibration.py`,
   `scripts/component_calibration.py`, `common_pile_evidence.py`, and the
   `mix01_inventory` component basis. Then ruff, format and
   `mypy --strict` on every changed file, and the related suite (allowlist,
   inventory, source_* and mix01_source_cli).
2. **Task 5, source registration (not started in code):**
   - Pin the catalog revision: in `manifests/datasets.catalog.yaml`, the
     `common_pile` entry has `"revision": null`; set it to
     `5afc546db324e7f39f297ba757c9a60547151e7c`. Never edit `snapshots/`.
   - In `scripts/mix01_source.py`, add
     `SourceSpec("common_pile", "common_pile_prose", "common_pile", "common_pile_prose", None)`
     with a component calibration kind and remove the `BLOCKED` entry. Update
     the tests that assert the block:
     `test_component_allowlist::test_common_pile_stays_blocked_for_every_production_command`
     and the `status --source-key common_pile` assertion in
     `test_mix01_source_cli.py`.
   - `layout_of` for the component kind: load and verify
     `<data-root>\calib\common_pile_prose\component-calibration.json` and use
     `component_calibration.layout_of`. Perf comes from `observed_transfer`;
     the adapt rate is `FALLBACK_ADAPT_ROWS_PER_SECOND`.
   - `evaluate_policy`: keep only `planner.JSONL_GZ_MODES` models.
   - `cmd_plan`: refuse until `planner.SOURCE_FILE_BOUNDS[("common_pile", "common_pile_prose")]`
     holds the reviewed bounds from the calibration.
   - `build_bridge`: for the component kind, use
     `common_pile_evidence.translate_component_samples`, with a new
     `--sample-dir` option (repeatable; each holds `sample-receipt.json` and
     `real-records.jsonl`).
3. **Calibration (Task 6) needs operator authorization (network); NOT run.**
   Proposed plan:
   - Sample libretexts, news and public_domain_review (no receipts exist for
     them; the 9-row cert01 has no file identities).
   - Take larger samples for all six components (for example 256 rows of the
     first 2 inventory files of each small component, and 32 rows of 2
     Gutenberg files).
   - Then run `scripts/component_calibration.py build`, then
     `mix01_inventory.py record --source common_pile_prose --measurement …`,
     then `estimate`.
   - Example (do not run without authorization):
     `uv run --offline --locked --extra cpu --extra eval python scripts/jsonl_gz_sample.py --source-key common_pile --data-root G:\XLM --label common-pile-cal01 --rows 256 --authorized-components libretexts,news,public_domain_review,oercommons,pressbooks --target <first 2 inventory files per component> --chunk-bytes 131072 --output-dir G:\XLM\calib\common_pile_cal01`
     plus a separate `--rows 32` run for project_gutenberg.
4. **Task 7, dominance audit (not re-run):** the previous estimate stands.
   Under the current hash-ordered prefix, the first Balanced plan is about 97%
   Gutenberg by bytes (25 files: 6 Gutenberg; libretexts, news, oercommons,
   pressbooks and pdr together about 3%).
   - A per-view split, analogous to the IFM requirement split (an
     instructional core taken whole plus Gutenberg for the remainder), should
     be designed and offered. It was NOT designed in code and NOT recorded.
     Compare A (hash order), B (equal share) and C (prose-balanced); the
     operator chooses.
   - Option B is volume-limited: the core components hold only about 1.24 GB
     estimated canonical in total.
5. **Task 8, evidence/admission is blocked by policy, not only by code:**
   - The repository declares no license, so `ce.build_bridge` and the C04 gate
     (`policy.evaluate_license_review`) refuse.
   - Admission needs a C04 contract amendment defining a per-component license
     basis (the allowlist digest) — an operator decision. Do not write a
     synthetic `declared_license`.
   - It also needs a real `xlm data probe --live` metadata probe in the store.
6. **Task 9:** after items 2–5: `policy freeze`, then `plan`, STOP at PLAN
   DIGEST. Docs to update: the runbook Common Pile section, `CONTRACTS.md`
   (the jsonl.gz raw-artifact contract `mix01-source-raw-artifact-jsonl-gz-v1`
   is in code only; add the amendment text), and `STATUS.md`.

Final state: **COMMON PILE BALANCED SOURCE NEEDS FURTHER WORK**
