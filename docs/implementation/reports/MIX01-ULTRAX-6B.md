# Mix-01 UltraX migration + 6B acquisition plan (operator runbook)

Branch: `data/mix01-ultrax-6b`. Starting commit: `d7942ba7cfa1c15f8c9eefe8770c58b655208d84`
(Astra-certified receipt-disabled 32M pilot-readiness code).

Agent boundary (enforced): inspect code, implement the adapter, change
recipes/config, add authored fixtures/tests, create bounded schema-probe
tooling, run synthetic/offline tests, write exact acquisition commands for
the USER. NO live acquisition, NO downloads, NO corpus preparation, NO
tokenizer training, NO shard building, NO pilot, NO training by this agent.
No network from this agent. The USER performs all real network acquisition
and preparation.

## 1. Old -> new Mix-01 diff

Active preset `recipes/mixtures/mix01.yaml` (`mix01`, identity `d670bd3a...`
-> `6e1e4e9a...`):

| Component | Old weight | New weight |
|---|---|---|
| essential_science | 0.10 | 0.10 |
| essential_practical | 0.10 | 0.10 |
| essential_prose | 0.05 | 0.05 |
| nemotron_organic_high | 0.15 | REMOVED |
| nemotron_organic_medium_high | 0.05 | REMOVED |
| ultrax_ultrafineweb | — | 0.20 |
| finepdfs_en | 0.15 | 0.15 |
| synth_en_explanations | 0.15 | 0.15 |
| nemotron_wiki_rewrite | 0.08 | 0.08 (KEPT, different source) |
| finewiki_en | 0.05 | 0.05 |
| ifm_behaviors_general_planning | 0.05 | 0.05 |
| common_pile_prose | 0.05 | 0.05 |
| simple_stories | 0.02 | 0.02 |

Exact weight sum required and verified: `1.0` (exact `Fraction`, never
renormalized). Source count: **12 -> 11** logical components. Registry
`recipes/mixtures/mix01_views.yaml`: `mix01_views_v1` (13 views incl. M4-only
`txt360_web`) -> `mix01_views_v2` (12 views: 11 active + M4-only
`txt360_web`); the two `nemotron_cc21` views are replaced by one
`ultrax_ultrafineweb` view with `observed_revision: null` until the operator
probe freezes an exact SHA. Treatment presets `m1_less_synth`,
`m2_more_synth`, `m5_more_practical`, `mix01_no_ifm` replace the same 20%
with `ultrax_ultrafineweb: 0.2`; `m3_more_pdfs` replaces 10% with
`ultrax_ultrafineweb: 0.1`; `m4_txt360_web` now diffs `ultrax_ultrafineweb`
-> `txt360_web` (verified: removed `{"ultrax_ultrafineweb": 0.2}`, added
`{"txt360_web": 0.2}`). Pilot draft
`recipes/experiments/draft_science_v1_pilot_32m.yaml` and
`src/xlm/experiments/science_pilot.py::M0_COMPONENTS` move to the same 11
components; model/optimizer/LR/runtime/checkpoint/evaluation/M5 order
semantics/statistics/receipt-disabled policy are UNCHANGED. No stale plan
hash remains valid (preset identities change: `mix01` `d670bd3a...` ->
`6e1e4e9a...`).

## 2. Files where old Nemotron organic was ACTIVE (updated here)

- `recipes/mixtures/mix01.yaml`
- `recipes/mixtures/m1_less_synth.yaml`
- `recipes/mixtures/m2_more_synth.yaml`
- `recipes/mixtures/m3_more_pdfs.yaml` (0.075/0.025 -> ultrax 0.10; description updated)
- `recipes/mixtures/m5_more_practical.yaml`
- `recipes/mixtures/mix01_no_ifm.yaml`
- `recipes/mixtures/m4_txt360_web.yaml` (description only)
- `recipes/mixtures/mix01_views.yaml` (views replaced; registry v1 -> v2)
- `recipes/experiments/draft_science_v1_pilot_32m.yaml` (mixture_components)
- `src/xlm/experiments/science_pilot.py` (M0_COMPONENTS + count comment)
- `recipes/science_comparisons/draft_science_v1_mixture_m0_m1.yaml` (M0/M1 arms)
- `tests/test_mix01_views.py`, `tests/test_config.py`, `tests/test_recipes.py`,
  `tests/test_source_doc_ids.py`, `tests/test_production_ingest.py`,
  `tests/test_source_admission.py`, `tests/test_data_catalog.py`
- `DATA_CATALOG.md` (initial-views bullet), `SOURCE_NOTES.md` (UltraX note),
  `manifests/datasets.catalog.yaml` (added candidate 21 `ultrax_ultrafineweb`)

## 3. Historical occurrences DELIBERATELY left untouched

- `docs/implementation/reports/P32*`, `P33`, `P34*`, `P35*`, `P13`, `P01`,
  all `docs/implementation/evidence/**`, `docs/implementation/STATUS.md`
  historical sections (only appended, §11 below)
- `snapshots/baseline_50m_mix01/**` (immutable frozen captures)
- `prompts/13_real_dataset_views_and_mix01.md` (prompt history)
- `fixtures/mixture/views/nemotron_organic.jsonl` (historical adapter fixture)
- `src/xlm/data/adapters/mix01_adapters.py::NemotronOrganicAdapter` (kept;
  removal from Mix-01 is not erasure of history)
- `src/xlm/data/adapters/columns.py` nemotron contracts (kept)
- `src/xlm/data/sources/policy.py` docstring example, `tests/test_p26_streaming.py`
  and `tests/test_source_admission.py` nemotron-shaped examples (not Mix-01)

## 4. New Mix-01 components/weights + source count

`essential_science 0.10, essential_practical 0.10, essential_prose 0.05,
ultrax_ultrafineweb 0.20, finepdfs_en 0.15, synth_en_explanations 0.15,
nemotron_wiki_rewrite 0.08, finewiki_en 0.05,
ifm_behaviors_general_planning 0.05, common_pile_prose 0.05,
simple_stories 0.02`. Sum exactly `1.0`. **11 components.**

## 5. UltraX repository/config chosen for the probe

Probe target: `openbmb/UltraX-Preview`, config `UltraX-Ultra-FineWeb`.
The README alias `openbmb/UltraX` MUST be resolved explicitly by the probe
(`scripts/ultrax_schema_probe.py --probe-aliases` checks both; zero or two
accessible aliases is a refusal, never a silent choice; an explicit `--repo`
overrides only when that repo is accessible). The frozen source definition
MUST pin the exact 40-hex commit SHA the probe returns; `main`/`latest`/empty
are refused by both the probe and `scripts/ultrax_freeze_revision.py`.

## 6. Adapter mapping (`ultrax_ultrafineweb`)

Class `UltraXUltraFineWebAdapter` (`ADAPTER_ID = SOURCE_ID =
"ultrax_ultrafineweb"`, registered in `ADAPTERS_BY_ID`; column contract
`("uid", "cleaned_content", "source", "processed_functions")`, no
`raw_content` projection):

- text = `cleaned_content` verbatim; `raw_content` is never read and never
  copied into canonical output;
- stable source identity = `uid` (preserved in `source_metadata`; canonical
  `doc_id` stays the centralized `ultrax_ultrafineweb:v1:<file-key>:<row>`
  scheme so IDs are stable before uid uniqueness is proven);
- `source` (row label) preserved as `upstream_source`; config
  `UltraX-Ultra-FineWeb`, `mix01_component`, language/license provenance
  recorded; `language = "en"`, `document_kind = "prose"`,
  `license_reference = "unknown"` until the probe receipt declares a license;
- optional metadata: `processed_functions` verbatim when present (empty
  string kept; non-string refused).

## 7. Empty-cleaned-content behavior + schema validation + revision pin

- `cleaned_content` absent/null -> `MissingFieldError` (refuse).
- `cleaned_content` wrong type -> `MissingFieldError` (refuse, never coerce).
- `cleaned_content` empty/whitespace -> `RecordRejectedError` (counted
  `remove_all` drop with explicit reason; `raw_content` NEVER used as
  fallback; `--on-reject record` counts it with reason).
- `uid`/`source` must be non-empty strings (never coerced; no assumed format
  beyond what the probe proves). `processed_functions` must be a string when
  present. Extra fields are ignored; required-field absence fails closed.
- Revision pin: `observed_revision: null` until
  `scripts/ultrax_freeze_revision.py --receipt <probe-receipt>` atomically
  pins the exact 40-hex SHA into `mix01_views.yaml` AND
  `datasets.catalog.yaml`, then re-validates with the strict loaders. Any
  `main`/`latest`/short/non-hex value is refused with exit 1.

## 8. Deterministic file-selection policy

NOT "the first N parquet files". Candidate files are ordered by the
repository hash chain `SHA-256(seed | repository | revision | file)` (same
construction as `xlm.data.acquisition.sampling._det_index`; seed default
`20260918`, the Mix-01 `source_seed`): `scripts/ultrax_order_files.py`
emits that order; the operator takes a prefix until enough source material
exists and later tops up with the NEXT files in the SAME order, without
changing already admitted `(file, row_ranges, selection_hash)` identities.
Row ranges inside files come from `xlm data sample-blocks --seed ...`
(dense row-group-aligned blocks with explicit bias warning), bound into
`xlm data plan --seed ...` (`selection_seed`, `explicit_file_list`). All
acquisition is resumable/idempotent via the bounded fetcher + progress
journal (re-run the identical `fetch`; never delete the journal, never mint
a replacement plan to reset spent allowance).

## 9. 6B / 6.6B / 32M quota tables (`recipes/mixtures/mix01_quotas_6b.yaml`)

FINAL = 6,000,000,000 exact XLM valid tokens (weight * 6B; knowable only
after the 32,768-tokenizer is frozen):

| Component | FINAL |
|---|---:|
| essential_science | 600,000,000 |
| essential_practical | 600,000,000 |
| essential_prose | 300,000,000 |
| ultrax_ultrafineweb | 1,200,000,000 |
| finepdfs_en | 900,000,000 |
| synth_en_explanations | 900,000,000 |
| nemotron_wiki_rewrite | 480,000,000 |
| finewiki_en | 300,000,000 |
| ifm_behaviors_general_planning | 300,000,000 |
| common_pile_prose | 300,000,000 |
| simple_stories | 120,000,000 |

FIRST-PASS headroom = 110% estimated usable (~6.6B total; NOT exact XLM
tokens): 660 / 660 / 330 / 1320 / 990 / 990 / 528 / 330 / 330 / 330 /
132M. Workflow: acquire first-pass -> canonicalize/clean -> freeze
tokenizer-fit input -> train/freeze 32,768 tokenizer (USER, later) ->
tokenize/count exact XLM tokens -> top up ONLY deficient sources -> freeze
final 6B. Never silently renormalize a deficient source.

32M pilot view (weight * 32M; separately identified exposure view, never the
6B payload): 3,200,000 / 3,200,000 / 1,600,000 / 6,400,000 / 4,800,000 /
4,800,000 / 2,560,000 / 1,600,000 / 1,600,000 / 1,600,000 / 640,000 = total
32,000,000.

## 10. Pilot/full-pool identity relationship + 2 GiB handling

Freeze/acquire the larger canonical pool, then derive a SEPARATELY
IDENTIFIED 32M pilot token-shard/exposure view and bind THAT small view to
the pilot, preserving lineage to the same source/pool artifacts. The 6B
uint16 payload alone (~12 GB before indexes) MUST NOT bind the pilot. The
frozen-input cap is UNCHANGED (2 GiB per shard, 2 GiB aggregate;
`src/xlm/data/input_limits.py`, `src/xlm/training/inputs.py` untouched).
The full baseline needs a separately reviewed input-cap decision before
binding multi-billion-token shards. The cap is NOT raised here.

## 11. Source revision / acquisition / resume / storage policy

- Revision policy: exact 40-hex SHA everywhere (`observed_revision`,
  catalog `revision`, plan `revision`, `source_revision`); `main`/`latest`
  refused at probe, freeze, plan and pilot-binding time.
- Acquisition: deterministic hash order (§8), exact revision pin, resumable
  idempotent fetch, bounded bytes/records/requests/disk (pilot defaults
  256 MiB / 25k records / 2 GiB output; production plans carry explicit
  operator limits + authorization hash), provenance per source file
  (`selected_files`, `row_ranges`, `selection_hash`), incremental stop/top-up
  without identity change.
- Resume: re-run the IDENTICAL `xlm data fetch --plan <same> --output-dir
  <same> --scratch-dir <same>`; the journal resumes spent budgets intact.
  Never delete the journal/scratch, never overwrite plans/artifacts, never
  re-plan to replenish allowance.
- Storage: NO hard-coded roots in product code (`XLM_HOME` + declared pilot
  storage roots). Operator inspects drives FIRST, places `XLM_HOME` on the
  drive with headroom, and keeps the code worktree and raw-data root on
  independent roots when needed. Observed bytes are reported separately from
  target tokens at every stage (`status`, `verify`, `adapt` summary,
  `input_bytes` preflight). Measured 2026-09-26 (GiB free): C 71.5, D 95.0,
  E 78.5, F 19.6, G 75.2; worktree `G:\Project\xlm-data-ultrax`.

## 12. Test + static results (offline, no network, no real data)

Env: worktree `G:\Project\xlm-data-ultrax`, branch `data/mix01-ultrax-6b`,
base `d7942ba`, Python 3.12.13 (`uv sync --offline --locked --extra cpu
--extra eval`, exit 0), `HF_HUB_OFFLINE=1/HF_DATASETS_OFFLINE=1/
TRANSFORMERS_OFFLINE=1/UV_OFFLINE=1`.

- `tests/test_ultrax_ultrafineweb.py + tests/test_mix01_quotas_6b.py`: 21 passed.
- `tests/test_mix01_views.py + test_config.py + test_recipes.py + test_source_doc_ids.py`: 71 passed.
- `test_production_ingest.py::test_adapter_column_contracts + test_ultrax_live_certification.py`: 2 passed, 3 skipped (live evidence absent; skip, never pass).
- Pilot draft: `test_p35_science_pilot.py::test_draft_states_the_exact_p35_pilot + ::test_checked_in_draft_is_visibly_nonexecutable`: 2 passed.
- Registry/admission/planning: `test_acquisition_plan + test_mixture_planning + test_source_discovery + test_source_admission + test_pool_views + test_data_catalog`: 103 passed.
- CLI: `xlm mixture preset-validate` (mix01, 11 components, exit 0),
  `preset-diff mix01->m4` (ultrax -0.20 / txt360 +0.20, exit 0),
  `ultrax_order_files` deterministic order (exit 0),
  `ultrax_freeze_revision` accept-40-hex (exit 0) / refuse-`main` (exit 1).
- Static: `ruff check` clean; `ruff format` clean; scoped `mypy`
  (`mix01_adapters.py`, `columns.py`, `science_pilot.py`) clean.
- NOT RUN: full suite, serial selection, CUDA tests, network/probe tests,
  real acquisition/preparation, tokenizer training, pilot, training.

Requirement ledger: adapter/fixtures/registry/presets/pilot/quota/probe-tooling/runbook
IMPLEMENTED + VERIFIED (focused offline); live probe evidence, exact UltraX
revision SHA, production admission (license/provenance approval), full 6B
acquisition/preparation, tokenizer freeze, 32M pilot view binding, pilot
BLOCKED (operator work); historical compatibility OUT OF SCOPE for rewrite
(reports/snapshots/prompts untouched); no fake numbers/hashes/benchmarks.

## 13. EXACT USER operator runbook (one-line PowerShell, in order)

`<HOME>` = operator data root (e.g. `D:\Project\xlm-operator-ultrax`).
`<SHA>` = the exact 40-hex SHA from the probe receipt. `<N>` = file prefix
size for the current acquisition round. Network stays OFF except the marked
probe/fetch/sample-blocks calls, which enable `HF_*_OFFLINE=0` for that call
only (the repository's existing mechanism from `P23-OPERATOR-PILOT` /
`scripts/operator_pilot.ps1`; `UV_OFFLINE=1` stays on throughout).

`Set-Location G:\Project\xlm-data-ultrax`
`git rev-parse HEAD`
`git branch --show-current`
`Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{n='FreeGiB';e={[math]::Round($_.Free/1GB,1)}}`
`$env:XLM_HOME="<HOME>"`
`"$env:HF_HUB_OFFLINE/$env:HF_DATASETS_OFFLINE/$env:TRANSFORMERS_OFFLINE/$env:UV_OFFLINE"`
`uv sync --offline --locked --extra cpu --extra eval`
`uv run --offline --locked --extra cpu xlm data sources --catalog manifests/datasets.catalog.yaml`
`uv run --offline --locked --extra cpu xlm data audit --catalog manifests/datasets.catalog.yaml`
`uv run --offline --locked --extra cpu xlm mixture preset-validate --preset recipes/mixtures/mix01.yaml --views recipes/mixtures/mix01_views.yaml`
`uv run --offline --locked --extra cpu xlm data mix01-status --preset recipes/mixtures/mix01.yaml`
`$env:HF_HUB_OFFLINE="0"; $env:HF_DATASETS_OFFLINE="0"`
`uv run --locked --extra cpu --extra eval python scripts/ultrax_schema_probe.py --probe-aliases --config UltraX-Ultra-FineWeb --split train --max-rows 30 --timeout-seconds 300 --output <HOME>/adapter-cert-ultrax01/probe_receipt.json --save-sample <HOME>/adapter-cert-ultrax01/real-records.jsonl`
`Get-Content <HOME>/adapter-cert-ultrax01/probe_receipt.json`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"`
`uv run --offline --locked --extra cpu python scripts/ultrax_freeze_revision.py --receipt <HOME>/adapter-cert-ultrax01/probe_receipt.json --views recipes/mixtures/mix01_views.yaml --catalog manifests/datasets.catalog.yaml`
`uv run --offline --locked --extra cpu xlm mixture preset-validate --preset recipes/mixtures/mix01.yaml --views recipes/mixtures/mix01_views.yaml`
`$env:HF_HUB_OFFLINE="0"; $env:HF_DATASETS_OFFLINE="0"`
`uv run --locked --extra cpu xlm data probe --catalog manifests/datasets.catalog.yaml --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --live --budget-mib 16 --probe-id ultrax01 --json`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"`
`uv run --offline --locked --extra cpu python scripts/ultrax_order_files.py --repo openbmb/UltraX-Preview --revision <SHA> --seed 20260918 --files <HOME>/ultrax_candidates.txt --count <N> --output <HOME>/ultrax_ordered.json`
`$env:HF_HUB_OFFLINE="0"; $env:HF_DATASETS_OFFLINE="0"`
`uv run --locked --extra cpu xlm data sample-blocks --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --files <ORDERED_SUBSET_CSV> --seed 20260918 --mode rowgroup --target-records 1000 --output <HOME>/ultrax_rows.json`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"`
`uv run --offline --locked --extra cpu xlm data plan --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --catalog manifests/datasets.catalog.yaml --files <ORDERED_SUBSET_CSV> --mode selected_records --row-ranges <HOME>/ultrax_rows.json --adapter-spec ultrax_ultrafineweb --seed 20260918 --pilot-approved --output <HOME>/ultrax_plan.json`
`$env:HF_HUB_OFFLINE="0"; $env:HF_DATASETS_OFFLINE="0"`
`uv run --locked --extra cpu xlm data fetch --plan <HOME>/ultrax_plan.json --output-dir <HOME>/acquisition/ultrax/raw --scratch-dir <HOME>/acquisition/ultrax/scratch --pilot-approved`
`uv run --locked --extra cpu xlm data status --plan <HOME>/ultrax_plan.json --scratch-dir <HOME>/acquisition/ultrax/scratch`
`uv run --locked --extra cpu xlm data fetch --plan <HOME>/ultrax_plan.json --output-dir <HOME>/acquisition/ultrax/raw --scratch-dir <HOME>/acquisition/ultrax/scratch --pilot-approved`
`uv run --locked --extra cpu xlm data verify --plan <HOME>/ultrax_plan.json --output-dir <HOME>/acquisition/ultrax/raw --scratch-dir <HOME>/acquisition/ultrax/scratch --json`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"`
`uv run --offline --locked --extra cpu xlm data adapt --plan <HOME>/ultrax_plan.json --adapter ultrax_ultrafineweb --input <HOME>/acquisition/ultrax/raw/selected_records.jsonl --output-dir <HOME>/canonical/ultrax --on-reject record`
`uv run --offline --locked --extra cpu xlm data mix01-status --preset recipes/mixtures/mix01.yaml`
`uv run --offline --locked --extra cpu xlm mixture preset-diff --base recipes/mixtures/mix01.yaml --variant recipes/mixtures/m4_txt360_web.yaml`

Repeat the probe -> order -> sample-blocks -> plan -> fetch -> status ->
verify -> adapt block per remaining source (essential_science,
essential_practical, essential_prose, finepdfs_en, synth_en_explanations,
nemotron_wiki_rewrite, finewiki_en, ifm_behaviors_general_planning,
common_pile_prose, simple_stories) with that source's `--source`,
`--view`/`--adapter`/`--adapter-spec` and quota from §9, then top up ONLY
deficient sources with the NEXT files in the same hash order (new
`sample-blocks`/`plan` with identical seed/revision, never edited in place).

FORBIDDEN until the tokenizer is frozen AND a reviewed Mix-01 prepare config
exists: `xlm tokenizer train`, `xlm data tokenize`, `xlm data freeze` for 6B
shards, `xlm mixture plan --budget-targets 6000000000`,
`xlm prepare --config <mix01> --authorize`, `xlm experiment plan/submit`
(pilot launch), `xlm train`, `xlm resume`, and any production-scale fetch
past pilot caps without a recorded admission (`xlm data admit` with tested
adapter + license approved + benchmark review) and plan-bound authorization.

## 14. Remaining blockers before real acquisition

1. Operator probe receipt (exact SHA, config, schema, license) — NOT RUN.
2. Revision freeze into `mix01_views.yaml` + catalog — NOT RUN (command ready).
3. UltraX license/provenance review + explicit admission — PENDING.
4. Per-source file lists + first-pass byte budgets + XLM_HOME placement — operator decision.
5. Production-scale (past pilot caps) authorization bound to plan hashes — NOT RUN.
6. Tokenizer freeze (32,768), 6B tokenization/counting, deficit top-up — LATER USER task.
7. 32M pilot view derivation/binding + full-baseline input-cap review — LATER.
8. Full offline acceptance gate (`fast` + `serial`) — NOT RUN (focused only).

Verdict: **READY FOR OPERATOR ULTRAX PROBE** (code + authored evidence done;
no live step executed by this agent).

## 15. Track A repair: probe/certification path (2026-09-27, datasets 5.0.1)

The real bounded probe (30 rows, `openbmb/UltraX-Preview` @
`a88527587389fd4ab352e9ad1273f4c0a234d8df`, config `UltraX-Ultra-FineWeb`,
split `train`, license `apache-2.0`, `Ultra-FineWeb` 30/30) exposed five
probe-path defects; all are fixed in-tree (commit on
`data/mix01-ultrax-6b`, no push):

1. **datasets 5.0.1 API**: the operator's manual removal of
   `trust_remote_code` from `get_dataset_config_names` /
   `load_dataset_builder` / `load_dataset` is retained permanently (UltraX
   is parquet-backed; no remote code). Regression:
   `tests/test_ultrax_probe_compat.py` stubs the 5.0.1 signatures and
   fails on any reintroduced kwarg.
2. **Revision race**: every post-resolution datasets call now takes
   `revision=revision_sha` (configs, features, streaming sample), so the
   receipt can never claim revision A while reading HEAD. Proven by
   stub-call assertions in the same compat test.
3. **Cert locator**: the operator's provisional
   `hf-stream://{repo}@{rev}/{config}/{split}` is kept as the deterministic
   truthful virtual locator (`cert_source_file()`; no parquet filename is
   fabricated); `_cert_source_row` stays separate and live certification
   asserts `_cert_revision == receipt revision`.
4. **UTF-8 BOM**: the manual PowerShell rewrite left a BOM + CRLF in the
   operator sample. The reader is NOT weakened: BOM input fails closed
   with an explicit regeneration message. The probe writer emits UTF-8, no
   BOM, LF (asserted byte-level in the compat test).
5. **Schema discovery under pinning (datasets 5.0.1)**: `Parquet._info`
   only echoes card-declared `config.features`, so
   `load_dataset_builder(..., revision=SHA).info.features` is empty BY
   DESIGN for this card-less parquet config. The probe now falls through
   builder metadata → pinned streaming Arrow-schema `.features` → types
   observed from the bounded rows, recording
   `schema_evidence: {source, rows}` honestly and requiring the five
   string fields on BOTH the selected evidence and every sampled row
   (ragged/mistyped rows fail). Covered by scenario tests A–H.
5. **Live-cert contract**: `tests/test_ultrax_live_certification.py` now
   checks pin + repository/config/schema + per-row locator + revision
   equality + verbatim `cleaned_content` + identity/determinism + no-raw
   fallback + missing-field refusal, plus an offline authored-fixture test
   of the same logic. UID rule UNCHANGED (`uid` = non-empty string; the
   observed 32-hex shape is receipt diagnostic only).

Frozen state preserved: `openbmb/UltraX-Preview` @ `a88527…d8df`,
`UltraX-Ultra-FineWeb`, license `apache-2.0` in both `mix01_views.yaml`
and the catalog (surgical 3-line pin; formatting restored). The freeze
script is now line-level and idempotent (same SHA = byte-identical no-op;
different SHA / `main` = refusal, exit 1).

Operator re-probe (ONE bounded command; network enabled for this call
only, `UV_OFFLINE=1` stays on):

`$env:HF_HUB_OFFLINE="0"; $env:HF_DATASETS_OFFLINE="0"`
`uv run --locked --extra cpu --extra eval python scripts/ultrax_schema_probe.py --probe-aliases --config UltraX-Ultra-FineWeb --split train --max-rows 30 --timeout-seconds 300 --output D:\Project\xlm-operator-ultrax\adapter-cert-ultrax01\probe_receipt.json --save-sample D:\Project\xlm-operator-ultrax\adapter-cert-ultrax01\real-records.jsonl`
`$env:HF_HUB_OFFLINE="1"; $env:HF_DATASETS_OFFLINE="1"`

Live certification after the probe (offline; `XLM_ULTRAX_CERT_DIR` defaults
to the path above):

`uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_ultrax_live_certification.py -n 0`

Evidence state at repair time: clean 30-row sample present
(`adapter-cert-ultrax01/real-records.jsonl`, LF/no-BOM — 4/5 live nodes
already pass verbatim/identity checks against it); the on-disk receipt
predates the `schema_evidence` format, so the receipt gate refuses it
until the re-probe below regenerates it. No operator file was written by
the agent.

Verdict after repair: **READY FOR OPERATOR RE-PROBE**.

## 16. Track A closeout: successful probe + live certification (2026-09-27)

The operator reran the pinned 30-row probe and then
`tests/test_ultrax_live_certification.py`: **ALL 5 PASSED, 0 failed,
0 skipped** (verified in-tree against the regenerated evidence; no agent
network involved).

Final real source evidence
(`D:\Project\xlm-operator-ultrax\adapter-cert-ultrax01\`,
`probe_receipt.json` 4,581 bytes + `real-records.jsonl` 131,854 bytes,
both 2026-09-27):

- repository: `openbmb/UltraX-Preview` (alias `openbmb/UltraX` recorded
  inaccessible; never selected)
- revision: `a88527587389fd4ab352e9ad1273f4c0a234d8df` (frozen, unchanged)
- config: `UltraX-Ultra-FineWeb`, verified
- schema_match: true, evidence source `streaming_features` (pinned parquet
  Arrow-schema footer — the declared-metadata level working as designed)
- fields: uid, raw_content, cleaned_content, processed_functions, source —
  all `Value('string')`
- declared_license: `apache-2.0` (+ derived-corpora caveat recorded)
- rows_sampled: 30

Runbook fix in this closeout: the probe `--output` now writes
`<HOME>/adapter-cert-ultrax01/probe_receipt.json` next to the sample, so
receipt and rows are produced atomically by the one probe command with no
manual `Copy-Item`. Adapter/source semantics and the frozen SHA are
untouched.

**Track A source certification is CLOSED.** Remaining Track B+ work
(production admission, 6B acquisition, tokenizer, pilot) is out of scope
for this closeout.
