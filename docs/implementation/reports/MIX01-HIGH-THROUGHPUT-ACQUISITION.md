# Mix-01 high-throughput source acquisition + UltraX admission bridge (2026-10-01, offline)

**Verdict: READY FOR ULTRAX PERFORMANCE BENCHMARK.** The already-certified
UltraX evidence now becomes admission evidence without any re-probe. The
reporting bug that showed Essential-Web unadmitted is fixed (production gating
never shared it). A deterministic production planner, a versioned transport
policy and a reusable whole-file acquisition engine exist and are tested on
authored loopback fixtures. Nothing ran against the network; nothing was
published, admitted, planned or authorized in the operator store.

Scope kept: no network, download, acquisition, re-probe, C05, tokenizer,
tokenization, pilot, training, mixture, quota or selector change; no push.
Essential-Web code and data are untouched.

## 1. Repository state

| Item | Value |
|---|---|
| Branch | `data/mix01-ultrax-6b` |
| HEAD at start | `ae14155933c4fe1bf66fe9c72f2308ca1210d626` |
| Pre-existing user work (preserved, not staged) | `docs/implementation/STATUS.md` 101-line hunk; untracked `.bf/`, `.br0/`, `.bscope/`, `.bt-c04/`, `.bt-fdc/`, `docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-REVIEW/`, `docs/implementation/evidence/ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW/`, five untracked review reports |
| Environment | Windows 11, Python 3.12.13, `uv run --offline --locked --no-sync --extra cpu --extra eval`; C: 796.1 GiB free, G: 745.7 GiB free, 16 logical CPUs |

## 2. The UltraX evidence chain (all re-read and re-hashed today)

| Artifact | Identity |
|---|---|
| Repository / revision / view | `openbmb/UltraX-Preview` @ `a88527587389fd4ab352e9ad1273f4c0a234d8df`, config `UltraX-Ultra-FineWeb`, split `train` (catalog and `mix01_views.yaml` pins agree) |
| Dedicated schema-probe receipt | `D:\Project\xlm-operator-ultrax\adapter-cert-ultrax01\probe_receipt.json`, 4,581 B, SHA-256 `c67e9152c371bd9d8a462eb801eccc11e45cb58349980f7735778f64323349d4`; probe `ultrax-schema-probe-v1`, observed 2026-09-27T09:09:58Z; schema evidence `streaming_features` (datasets Features of the pinned Parquet Arrow schema); 5 fields, all `Value('string')`; license `apache-2.0` from the live pinned resolution; alias `openbmb/UltraX` inaccessible |
| Saved real rows | `real-records.jsonl`, 131,854 B, 30 rows, SHA-256 `493077d2dba14d7955312c5d06c916cae9395593d621596d02a88ae320aa3298` |
| Adapter certification | adapter `ultrax_ultrafineweb` (`UltraXUltraFineWebAdapter`); operator live test `tests/test_ultrax_live_certification.py` (5 tests, passing today with the real D: evidence); current adapter code `mix01_adapters.py` `3651ff2a…7ef6`, `columns.py` `3e594f75…db1` (LF-normalized SHA-256) |
| Calibration plan | `G:\XLM\calib\ultrax_plan.json`, SHA-256 `729b34ea…c99c`, plan hash `f13b93a5…ac02d`, part-0039 rows [90950, 91950) |
| Calibration journal | SHA-256 `ea263d85…299d`; COMPLETED; real validator for part-0039: strong ETag `"f5ae473f…d92e"`, length 1,055,787,705 |
| Calibration records / summary | `selected_records.jsonl` `e73c3d97…a89` (1,000 rows); `adaptation_summary.json` `8fcb42a9…87f9`: 994 accepted, 6 `RecordRejectedError`, documents `cd7c157a…5037` |
| Frozen inventory | `G:\XLM\inventories\ultrax.inventory.json`, file SHA-256 `ad407c72…1e1d`, inventory digest `cb42e2738c9b2f74aae98dd557758a638fec7dbb799f3c5b2b0c7e256900634f`, 104 files, seed 20260918, sizes null |
| Headroom estimate | `G:\XLM\calib\headroom_estimate.json` `cd815f14…634d`: requirement 5,280,000,000 canonical B (1.32 B est. tokens), safety 1.15, base transfer 5,252,218,497 B |
| Store probe record | `probe_evidence/probe_ultrax_ultrafineweb_UltraX-Ultra-FineWeb` (attempt 1): `partial`, `real_observed`, `adapter_contract_unassigned`, no schema, no fingerprint, `apache-2.0`; file SHA-256 `f35e581b…dd4a` |
| Store admission record | none |

## 3. Why the real UltraX evidence was not admissible (structural)

`resolve_verified_production_admission(plan, store)` loads the latest
`ProbeEvidenceRecord` for `(source_id, view_id)` and runs
`AdmissionGate.evaluate`. The gate stops at step 3 unless
`evidence.outcome == ProbeOutcome.ACCESSIBLE`, then requires
`immutable_revision`, a non-null `verified_schema: ViewSchema`, and a decision
whose `probe_fingerprint` equals `evidence.probe_fingerprint`.

The only store record was produced by `xlm data probe --live`, which builds
`SourceProber(..., extractor_contract=None)`. With no contract,
`SourceProber.probe` never inspects a schema: it appends
`adapter_contract_unassigned`, so `outcome = PARTIAL`, `verified_schema = None`
and `probe_fingerprint = None` (the fingerprint is computed only when a schema
exists). Even with a contract it reads the schema only from
`snapshot.card_data["schema"]`, which `HuggingFaceTransport` fills from Hub
`cardData`; that never holds a `ViewSchema`.

The dedicated probe held every needed fact, but in a different type system and
outside the store: a JSON receipt in datasets `Value('dtype')` spelling (no
`ViewSchema`, no nullability, no file inventory, no fingerprint, no digest), a
sample on D:, and an adapter certification that existed only as a pytest run.
Nothing translated it into a `ProbeEvidenceRecord` or bound it to the adapter
and revision, so `data admit` would have recorded a decision the gate rejects.

## 4. Admission-evidence bridge (`c04-certified-evidence-bridge-v1`)

`src/xlm/data/sources/certified_evidence.py`, CLI `scripts/mix01_source.py evidence show|publish|verify`.

- **Pin** (`resolve_pin`): source, view, revision and adapter from the catalog
  and `mix01_views.yaml`; they must agree; the view must belong to exactly one
  component; Essential-Web and denied repositories refuse.
- **Translators** (data-only dispatch, no dynamic code):
  `ultrax-schema-probe-v1` (receipt + sample; every row statistic the probe
  recorded is recomputed from the sample: uid digest, length stats, empty count,
  labels, presence) and `xlm-calibration-fetch-v1` (plan hash, COMPLETED journal,
  records SHA-256 equal to the journal's, every locator bound to plan, revision,
  file and ETag). The earlier real metadata probe supplies and cross-checks the
  declared license.
- **Schema**: `ViewSchema` with a named basis (`hf_features_of_pinned_parquet_arrow_schema`
  or `observed_projected_rows_of_real_fetch`); `nullable=true` is recorded as "no
  non-null guarantee observed".
- **Adapter re-certification**: the registered adapter runs on every real row;
  a missing/mistyped field refuses; output must be bound and deterministic; a
  recorded documents digest must be reproduced byte for byte.
- **Fingerprint**: `compute_probe_fingerprint` over provider, repository,
  revision, view, schema and the real file identities from fetch journals.
- **Publication**: next probe attempt with `probe_evidence.json` +
  `certified_bridge_receipt.json` (self-digested); idempotent; earlier attempts
  kept. `verify_current` refuses changed pin, adapter code, or inputs that no
  longer reproduce the receipt. Authored/synthetic evidence and inputs inside
  the checkout are refused. A decision binds the receipt digest
  (`reviews_sha256.certified_evidence`).

**UltraX dry run on the real evidence (nothing written):** bridge receipt digest
`b874f47e8d9a2a58c809a80f34a905353e591d71bd66c7a4645acc1c56c3ecf9`, probe
fingerprint `ca72a0e89cc8e37fd66a5650978d13920665ee12042e9ed5ed62b6e92dc841f8`;
probe sample 30/30 accepted; calibration 994 accepted / 6 rejected,
**reproducing the recorded documents digest `cd7c157a…5037`**; observed file
part-0039 1,055,787,705 B. Preview: `docs/implementation/evidence/MIX01-HIGH-THROUGHPUT/ultrax-bridge-receipt.preview.json`.

**UltraX needs no new schema probe.** Every admission field is present in the
existing immutable evidence; the only quantity not literally recorded
(nullability) is derived honestly from the datasets Features semantics and
labeled as such.

**Reuse:** the same command bridges every other Mix-01 source from its real
calibration fetch (dry run, all reproduce their recorded documents digests):
FinePDFs `283df718…`, SYNTH `c749c999…` (806/194), Wiki-Rewrite `c6eb1f46…`,
FineWiki `f36584a5…`, IFM general `1330da81…`, IFM planning `fb1a2b96…`,
SimpleStories `66f0c419…` (`bridge-dry-run.json`). Each keeps its own pin,
adapter, schema and reviews.

## 5. Admission reporting bug

Root cause: `xlm data audit` and `xlm data mix01-status` both called
`CatalogAuditor.audit_source(candidate)` with the default `view_id="default"`.
Admission is stored per `(source, view)`; Essential-Web is admitted for
`essential_science|practical|prose`, never `default`, so the lookup found no
evidence and reported `unadmitted`. `mix01_status` also keyed admission by
`source_id`, which cannot describe one Essential view.

Production gating did **not** share the bug: `resolve_verified_production_admission`
always used `plan.source_id` and `plan.view_id` (test `test_gating_is_per_view`).

Fix (reporting only; no record mutated): `stored_view_ids` discovers views from
store manifests; `audit_all` evaluates every stored view through the full gate
and reports per-view status (`admitted` when a view is admitted);
`evaluate_stored_view` is the shared per-view evaluation; `mix01-status` judges
each component on every view it needs (`component_admission_views`: observed
configs, else the component id) via `ComponentAdmission`. Real store today:
`audit` shows `essential_web [ADMITTED] essential_practical=admitted,
essential_prose=admitted, essential_science=admitted, selector_recon=unadmitted`;
`mix01-status` shows the three Essential views READY (3/12).

## 6. Benchmark-risk semantics (`c04-benchmark-risk-v3`)

Before: non-Essential decisions were admitted only with `clean`; a mitigated
non-Essential decision under v2 only needed a benchmark review hash and *any*
mitigation (it could reuse the Essential-scoped one). Decision: large
web-derived corpora cannot honestly be called clean, so the mitigated state is
extended, minimally: contract `c04-benchmark-risk-v3`, mitigation
`mix01_contamination_mitigation()` (same mechanism `xlm.data.exclusion`, same
BLiMP/ARC-Easy/HellaSwag/PIQA, scope `eventually_frozen_mix01_canonical_training_pool`),
four review hashes, approved provenance, resource contract. The model enforces
version↔scope pairing. Essential decisions stay exactly on v2 (the sealed
Essential first pass re-verifies). C05 remains required before tokenizer
fitting, training and any benchmark claim; no exclusion requirement was relaxed.

## 7. Transport policy (`mix01-transport-policy-v1`) and decisions

Modes (`TransportMode`): `whole_file_local`, `row_group_local`, `range_selected`,
`small_source_direct`; the frozen mode is bound into every plan. Selection:
smallest modeled wall time among modes that fit the request/scratch/durable
ceilings **and that a production planner executes**; within 10% prefer fewer
requests; the fastest modeled mode is always reported. No fixed amplification
threshold (the Essential `WHOLE_FILE_PREFERENCE_RATIO` 1.35 is not reused).
Model inputs: one stream per file in waves; Essential-Web endpoint rates (1
stream 22.7 MB/s; aggregate floor = production end-to-end 36 MB/s; 947
rows/process-s); each source's calibration request latency; each source's
measured `xlm data adapt` rate (UltraX: none retained, the slowest measured
5,814/s is used). Modeled numbers (36 MB/s aggregate), re-derived today:

| Source | Whole-file B / requests / s | Selected-range B / requests / s | Amplification | Selected | Fastest modeled |
|---|---|---|---|---|---|
| UltraX | 12,669,452,460 / 24 / 494 | 3,597,392,267 / 3,208 / 464 | 3.53 | whole_file_local | range (not executable), 6% faster |
| FinePDFs | 5,542,042,276 / 4 / 429 | 2,529,955,258 / 1,337 / 324 | 2.19 | whole_file_local | range (not executable) |
| SYNTH | 6,130,027,267 / 26 / 334 | 3,680,595,261 / 1,053 (calculated) / 257 | 1.74 | whole_file_local | range (not executable) |
| Wiki-Rewrite | 1,406,396,385 / 2 / 1,034 | 446,286,292 / 206 / 120 | 3.18 | whole_file_local | range (not executable) |
| FineWiki | 4,834,935,246 / 4 / 220 | 326,686,740 / 371 / 83 | 15.04 | whole_file_local | range (not executable) |
| SimpleStories | 712,976,922 / 6 / 205 | 383,715,379 / 10,071 / 867 | 1.88 | whole_file_local | whole_file_local (small-source-direct 1.66 GB / 231 s) |
| IFM general / planning | 814,015,146 / 4 / 52; 1,412,646,304 / 4 / 95 | 317,625,339 / 81 / 30; 327,900,253 / 36 / 23 | 2.56; 4.31 | refused | the 50/50 split is an assumption, not a decision |

UltraX at the measured 8-stream 142.7 MB/s: whole-file 236 s versus range 464 s.

**Corrections to the earlier figures.** UltraX "2.37×" compared whole-group
bytes with *transferred* bytes per row that included a 1,056,037 B footer read
amortized over only 1,000 rows; per-row projected bytes are 2,270 B, so whole
versus selected is **3.53×** and selected transfer is **3.60 GB** (the 5.25 GB
figure is the estimate's transfer basis with the same footer overhead and the
1.15 safety). Range requests: **3,208** (1,580 row groups × 2 coalesced ranges
measured, + 12 × 4 metadata/redirect requests), within the earlier 1,580–9,500
band. FinePDFs: **2.19×** (not 1.59×) for the same reason.

**Decisions:** UltraX `whole_file_local` (also the fastest at ≥ ~70 MB/s
aggregate); bounded benchmark before freezing a measured policy. FinePDFs:
`whole_file_local` executable, range modeled faster: benchmark. SYNTH:
NEEDS_MEASUREMENT (range request count is a calculation): benchmark.
Wiki-Rewrite, FineWiki: range is much faster but has no production planner;
whole-file is executable and bounded (17 min / 4.8 GB). IFM: blocked on the
per-view requirement decision. SimpleStories: `whole_file_local` (range would
need 10,071 requests). Common Pile: blocked.

## 8. UltraX production planner (`mix01-source-planner-v1`)

`src/xlm/data/acquisition/source_plan.py`, CLI `plan`. Inputs: quota file
(cross-checked with the estimate; no renormalization), estimate, calibration
layout (one measured file + one measured row group + calibration yield),
frozen inventory (order keys and digest recomputed), frozen policy, current
store admission, predecessor plan + its sealed accounting. Output: write-once
self-digested plan; the digest is what is authorized. Always production scope.
Preview from the real artifacts (`ultrax-plan.preview.json`, placeholder
admission, not authorizable):

| Item | Value |
|---|---|
| Selection | frozen-order ranks **[0, 12)**, 12 whole files (part-0048, 0039, 0084, 0065, 0060, 0023, 0040, 0083, 0095, 0016, 0068, 0042); next top-up cursor 12 |
| Benchmark-reserved ranks | 102, 103 (part-0064, part-0006): never planned |
| Expected | 12,669,452,460 B transfer, 24 requests, 1,610,436 rows, 6,192,818,907 canonical B ≈ 1,548,204,726 est. tokens (target 1.32 B; 5.28 GB) |
| Transfer ceiling | 19,004,391,424 B (1.5 × expected, MiB-rounded) |
| Request ceiling | 192 (16 per file) |
| Per-file ceilings | 2,111,832,064 B (2 × measured), 268,406 rows |
| Scratch ceiling | 25,341,984,768 B on C: (+34,359,738,368 B kept free) |
| Durable ceiling | 50,113,260,408 B on G: (raw + canonical, upper bound; expected ≈ 21 GB) |
| Concurrency | 8 download streams, 12 processes (maxima 16/16) |
| Deadlines | 6,713 s per file, 14,400 s per run |

Top-up: when `sufficiency` says TOP_UP after a complete plan, `plan` again
mints plan 2 at the cursor with `previous_plan_digest` and the previous
accounting digest (hash chain). A spent plan is never edited; ordering never
restarts; an incomplete plan refuses a top-up.

## 9. Reusable high-throughput engine

`source_run.py` (run, resume, seal, verify, account, sufficiency, first-pass
seal), `source_local.py` (generic single-adapter local adaptation worker),
`source_dashboard.py`, `source_benchmark.py`. The Essential-Web primitives that
are source-agnostic are reused unchanged by import (`download_source`,
`ScratchBudget`, `TransferMeter`, `promote_source`, `load_durable_source`,
`selected_payloads`, `run_pipeline`, progress snapshots, ledger codec, rate/ETA
helpers); the frozen Essential modules were not edited because the campaign
binds their bytes.

- Download: one resumable stream per file, `Range`/`If-Range` from the last
  fsynced checkpoint, strong-ETag binding, `X-Linked-ETag` independent SHA-256,
  revision check, bounded retries with `Retry-After`; concurrency from the plan
  (default 8, ≤ 16), never more streams than files.
- Processing: worker processes (default 12, ≤ 16), pipelined with downloads
  (bounded in-flight files, scratch byte cap counting partials).
- Durable: verified upstream Parquet retained under `G:\XLM\acq-raw\<key>\source`;
  canonical units published atomically after the receipt is sealed; per-unit
  canonical and durable ceilings enforced; a pre-run durable-space guard.
- Failure: first failure is the root; cooperative cancellations are reported
  apart; partials, complete downloads and sealed units are preserved.
- Restart classes: `sealed_skip`, `local_processing_retry`,
  `local_complete_reuse`, `resumable_partial`, `fresh_download` (`resume-check`).
- Dashboard: units, transfer, current and 60-s recent MB/s, requests, retries,
  rows/s, documents/rejections, yield, target progress, elapsed, ETA, scratch
  and durable used/free, active units, failures; metadata only, names escaped.
- Performance receipts: `pNN/performance-XX.json` per run (also on failure):
  source, revision, plan, mode, bytes, requests, retries, wall, MB/s, rows,
  rows/s, CPU rows/process-s, canonical bytes, yield, concurrency, limits.
- Raw contract: `mix01-source-raw-artifact-v1` (CONTRACTS.md).

## 10. Benchmarks

Whole-file: `benchmark plan|authorize|run` on the reserved tail (UltraX:
part-0064, part-0006; 2 files, 2 streams) or a named calibration file; outputs
stay on scratch and are deleted after the receipt. Range: pilot
`sample-blocks`/`plan --pilot-approved`/`fetch`/`adapt` on the same files
(2 files, 2 workers), normalized by `benchmark record-range`. Both are then
frozen with `policy freeze --basis measured`. Commands: §14.

## 11. UltraX license/provenance review input (no legal determination)

From `review show` (preview: `ultrax-review-input.json`):

- Known: repository metadata declares `apache-2.0` (live pinned resolution and
  the Hub metadata probe agree); the probe's own caveat: "UltraX is derived from
  source corpora and users must check applicable source-dataset licenses";
  the repository exposes configs `UltraX-AICC`, `UltraX-FineWeb`,
  `UltraX-FineWeb-ProX-Doc`, `UltraX-RedPajama-V2`, `UltraX-Ultra-FineWeb`;
  all 30 sampled rows carry `source = "Ultra-FineWeb"`; rows carry
  `raw_content` (pre-refinement upstream text, never used) and
  `processed_functions` (refinement operations).
- Not established: per-document license/terms of the underlying web pages;
  cleared underlying rights (never assumed); exact lineage beyond the card and
  row labels (Ultra-FineWeb is reported as FineWeb/FineWeb-Edu-derived; C04
  denies only the direct FineWeb repositories and requires lineage review);
  benchmark contamination until C05.
- The declared `apache-2.0` is a repository-level declaration; the review files
  must not (and cannot) claim all underlying text is Apache-2.0.
- Decision fields: `--license-decision approve_research_pretraining|reject`,
  `--provenance-decision approved|rejected`, `--benchmark-risk
  suspect_with_mitigation`, `--operator`, `--rationale`.
- Registry note text for UltraX still says the revision/license are null; it
  is stale documentation (the pins are set) and was not edited.

## 12. Tests and static checks

New: `tests/test_certified_evidence.py` (20), `tests/test_mix01_admission.py`
(8), `tests/test_source_plan.py` (10), `tests/test_source_run.py` (13),
`tests/test_mix01_source_cli.py` (1) on authored fixtures
(`tests/mix01_source_fixtures.py`) and an authored loopback HTTP endpoint.
They cover bridge conversion, revision/schema/adapter/view/authored-fixture
refusals, immutable idempotent publication, operator-store resolution, audit and
mix01-status reporting, per-view gating, planner and inventory-prefix
determinism, top-up cursor, plan identity, mode binding, byte/request
estimates, resumable partial, local-complete reuse, sealed skip, processing
retry, failure cancellation and root-failure reporting, bounded concurrency,
scratch and durable caps, performance receipts, dashboard, benchmark,
sufficiency, top-up and seal, the multiprocess worker path, and the full
offline CLI path.

Exact runs (offline; `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`,
`TOKENIZERS_PARALLELISM=false`, `XLM_ULTRAX_CERT_DIR=D:/Project/xlm-operator-ultrax/adapter-cert-ultrax01`;
prefix `uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest`):

| Command | Result |
|---|---|
| the five new files, `-n 0 --basetemp=.bt-mx` | 52 passed (exit 0) |
| related selection of 38 files (all `tests/test_essential_web_*.py`, `test_source_admission`, `test_mix01_views`, `test_mix01_inventory`, `test_mix01_quotas_6b`, `test_data_catalog`, `test_acquisition_plan`, `test_acquisition_bounds`, the three UltraX files, `test_production_ingest`, `test_source_discovery`, `test_mixture_planning`, `test_calibration_adopt`, the five new files), `-n 12 --dist=worksteal --max-worker-restart=0 -m "not serial and not optional_dependency" --basetemp=.bt-par` | 867 passed, **1 failed** (exit 1): `test_essential_web_t_package.py::test_package_root_must_be_outside_git_and_the_evidence_roots` refuses a temp root inside the git checkout, which `--basetemp=.bt-par` is |
| that node alone, `-n 0` with the default temp root | 1 passed (exit 0) |
| same selection, serial half `-n 0 -m "serial or optional_dependency" --basetemp=.bt-ser` | 3 passed, 868 deselected (exit 0) |

0 skipped (the UltraX live-certification nodes ran against the real D: evidence).
Not run: the full acceptance suite, CUDA, network and live tests.

Static: `ruff check` and `ruff format --check` pass on all changed/new files.
`mypy --strict`: **NOT RUN** — Windows application control blocked both the
compiled mypy `.pyd` and the interpreted runner's `librt.internal` DLL on every
attempt in this session. A manual strict-typing review fixed two hazards
(`Final` literal constants for pydantic `Literal` fields; an explicitly typed
limits dict).

Real-store read-only checks: `essential_web_pool_seal.py verify --skip-content`
→ `seal verified`, digest `a77c78f7…7516` unchanged; path/size/mtime snapshot of
`G:\XLM\plans` and `G:\XLM\xlm-home` (282 files) identical before and after
every check.

## 13. Files changed

New: `src/xlm/data/sources/certified_evidence.py`,
`src/xlm/data/sources/mix01_admission.py`,
`src/xlm/data/acquisition/transport_policy.py`, `source_plan.py`,
`source_local.py`, `source_dashboard.py`, `source_run.py`,
`source_benchmark.py`, `scripts/mix01_source.py`, five test files + fixtures,
`docs/runbooks/mix01-source-acquisition.md`, this report,
`docs/implementation/evidence/MIX01-HIGH-THROUGHPUT/` (preview script and
read-only previews). Changed: `src/xlm/data/sources/admission.py`,
`src/xlm/data/sources/mix01.py`, `src/xlm/cli/data_cmd.py`, `CONTRACTS.md`
(three C04 amendments), `docs/implementation/STATUS.md` (new entry only).

## 14. UltraX operator runbook (one PowerShell line per command)

Values in `<...>` come from the preceding command's output. Commands marked
NETWORK are the only ones that reach the network.

```powershell
Set-Location F:\Project\xlm-data-ultrax
git rev-parse HEAD
git status --short
. .\scripts\operator_storage.ps1
$env:UV_OFFLINE='1'; $env:HF_HUB_OFFLINE='1'; $env:HF_DATASETS_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'
Get-PSDrive -Name C,G | Select-Object Name,@{n='FreeGiB';e={[math]::Round($_.Free/1GB,1)}}
$env:XLM_ULTRAX_CERT_DIR='D:\Project\xlm-operator-ultrax\adapter-cert-ultrax01'; uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_ultrax_live_certification.py -n 0 -q
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py evidence show --source-key ultrax --probe-dir D:\Project\xlm-operator-ultrax\adapter-cert-ultrax01
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py evidence publish --source-key ultrax --probe-dir D:\Project\xlm-operator-ultrax\adapter-cert-ultrax01
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py evidence verify --source-key ultrax --probe-dir D:\Project\xlm-operator-ultrax\adapter-cert-ultrax01
uv run --offline --locked --no-sync --extra cpu --extra eval xlm data probe --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py review show --source-key ultrax
```

Expected: 5 passed; `BRIDGE RECEIPT DIGEST b874f47e…c3ecf9`; probe `Outcome: accessible`,
`Fingerprint: ca72a0e8…41f8`. **STOP — the operator makes the license,
provenance and benchmark-risk decision from the printed facts.**

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py review record --source-key ultrax --review-dir G:\XLM\reviews\ultrax --operator "<OPERATOR_NAME>" --license-decision approve_research_pretraining --provenance-decision approved --benchmark-risk suspect_with_mitigation --rationale "<OPERATOR_RATIONALE>"
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py admit --source-key ultrax --review-dir G:\XLM\reviews\ultrax
uv run --offline --locked --no-sync --extra cpu --extra eval xlm data audit --source ultrax_ultrafineweb
uv run --offline --locked --no-sync --extra cpu --extra eval xlm data mix01-status
New-Item -ItemType Directory -Force C:\XLM-scratch\ultrax-verify | Out-Null
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py freeze --source ultrax_ultrafineweb --repo openbmb/UltraX-Preview --revision a88527587389fd4ab352e9ad1273f4c0a234d8df --seed 20260918 --files G:\XLM\inventories\ultrax_candidates.txt --output C:\XLM-scratch\ultrax-verify\ultrax.inventory.json
(Get-FileHash C:\XLM-scratch\ultrax-verify\ultrax.inventory.json).Hash -eq (Get-FileHash G:\XLM\inventories\ultrax.inventory.json).Hash
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py policy model --source-key ultrax
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark plan --source-key ultrax --label b1
```

**STOP — USER MUST REVIEW BENCHMARK DIGEST BEFORE AUTHORIZATION** (2 files,
≈ 2.1 GB, 4 requests expected).

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark authorize --source-key ultrax --label b1 --digest <BENCHMARK_DIGEST_FROM_PREVIOUS_COMMAND> --operator "<OPERATOR_NAME>"
$env:HF_HUB_OFFLINE='0'; $env:HF_DATASETS_OFFLINE='0'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark run --source-key ultrax --label b1
uv run --offline --locked --no-sync --extra cpu --extra eval xlm data sample-blocks --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --revision a88527587389fd4ab352e9ad1273f4c0a234d8df --files data/UltraX-Ultra-FineWeb/UltraX-Ultra-FineWeb-en-part-0064-of-0104.parquet,data/UltraX-Ultra-FineWeb/UltraX-Ultra-FineWeb-en-part-0006-of-0104.parquet --seed 20260918 --mode rowgroup --target-records 20000 --output G:\XLM\plans\ultrax\benchmarks\b1\range-rows.json --report G:\XLM\plans\ultrax\benchmarks\b1\range-rows.evidence.json
$env:HF_HUB_OFFLINE='1'; $env:HF_DATASETS_OFFLINE='1'
uv run --offline --locked --no-sync --extra cpu --extra eval xlm data plan --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --catalog manifests/datasets.catalog.yaml --files data/UltraX-Ultra-FineWeb/UltraX-Ultra-FineWeb-en-part-0064-of-0104.parquet,data/UltraX-Ultra-FineWeb/UltraX-Ultra-FineWeb-en-part-0006-of-0104.parquet --mode selected_records --row-ranges G:\XLM\plans\ultrax\benchmarks\b1\range-rows.json --adapter-spec ultrax_ultrafineweb --seed 20260918 --attempt 1 --pilot-approved --output G:\XLM\plans\ultrax\benchmarks\b1\range-plan.json
$env:HF_HUB_OFFLINE='0'; $env:HF_DATASETS_OFFLINE='0'
uv run --offline --locked --no-sync --extra cpu --extra eval xlm data fetch --plan G:\XLM\plans\ultrax\benchmarks\b1\range-plan.json --output-dir C:\XLM-scratch\ultrax\bench-range\raw --scratch-dir C:\XLM-scratch\ultrax\bench-range\scratch --pilot-approved
$env:HF_HUB_OFFLINE='1'; $env:HF_DATASETS_OFFLINE='1'
uv run --offline --locked --no-sync --extra cpu --extra eval xlm data adapt --plan G:\XLM\plans\ultrax\benchmarks\b1\range-plan.json --adapter ultrax_ultrafineweb --input C:\XLM-scratch\ultrax\bench-range\raw\selected_records.jsonl --output-dir C:\XLM-scratch\ultrax\bench-range\canonical --on-reject record --max-input-bytes 134217728 | Out-File -Encoding utf8 -FilePath G:\XLM\plans\ultrax\benchmarks\b1\range-adapt.log
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark record-range --source-key ultrax --label b1 --range-plan G:\XLM\plans\ultrax\benchmarks\b1\range-plan.json --range-perf C:\XLM-scratch\ultrax\bench-range\scratch\performance\<RANGE_PLAN_ID>.perf.json --range-journal C:\XLM-scratch\ultrax\bench-range\scratch\journals\<RANGE_PLAN_ID>.progress.json --range-documents C:\XLM-scratch\ultrax\bench-range\canonical\documents.jsonl --adapt-log G:\XLM\plans\ultrax\benchmarks\b1\range-adapt.log
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py policy freeze --source-key ultrax --basis measured --whole-receipt G:\XLM\plans\ultrax\benchmarks\b1\performance-00.json --range-receipt G:\XLM\plans\ultrax\benchmarks\b1\range-receipt.json
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py plan --source-key ultrax
```

`<RANGE_PLAN_ID>` is the `plan_id` printed by `xlm data plan`. If the measured
policy prints a range mode as fastest, it is reported only; `whole_file_local`
stays the executable selection (§7). `plan` prints the transport mode, ranks,
expected bytes/requests, every ceiling and `PLAN DIGEST`.

**STOP — USER MUST REVIEW PLAN DIGEST BEFORE AUTHORIZATION.**

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py authorize --source-key ultrax --plan 1 --digest <PLAN_DIGEST_FROM_PREVIOUS_COMMAND> --operator "<OPERATOR_NAME>"
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py resume-check --source-key ultrax --plan 1
$env:HF_HUB_OFFLINE='0'; $env:HF_DATASETS_OFFLINE='0'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py run --source-key ultrax --plan 1
$env:HF_HUB_OFFLINE='1'; $env:HF_DATASETS_OFFLINE='1'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py status --source-key ultrax
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py verify --source-key ultrax --plan 1
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py sufficiency --source-key ultrax
```

- Resume after an interruption: `resume-check --plan 1`, then the same three
  `run` lines again (the plan resumes; never re-plan to reset it).
- Adapt/canonicalize: part of `run` in `whole_file_local` (pipelined);
  `verify` re-hashes every canonical unit, ledger, summary and raw file.
- If `sufficiency` says `TOP_UP`: `plan --source-key ultrax` again (plan 2 at
  cursor 12), **STOP for its digest**, then `authorize --plan 2 --digest <PLAN_2_DIGEST>`,
  `run --plan 2` (with the HF lines), `verify --plan 2`, `sufficiency`.
- When `SUFFICIENT`:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py seal --source-key ultrax
uv run --offline --locked --no-sync --extra cpu --extra eval xlm data mix01-status
```

## 15. Other sources: next steps

Already complete for every source below: pinned revision, registered adapter
with authored tests, real calibration fetch, real metadata probe (license),
offline bridge dry run. Pending for all: `evidence publish`, operator review,
`admit`, frozen inventory (none exists: the operator needs the repository file
list at the pinned revision for `scripts/mix01_inventory.py freeze`; listing it
is a network step), measured policy, plan, authorization, run.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py evidence show --source-key finepdfs
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py evidence publish --source-key finepdfs
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py review show --source-key finepdfs
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py review record --source-key finepdfs --review-dir G:\XLM\reviews\finepdfs --operator "<OPERATOR_NAME>" --license-decision <approve_research_pretraining_OR_reject> --provenance-decision <approved_OR_rejected> --benchmark-risk suspect_with_mitigation --rationale "<OPERATOR_RATIONALE>"
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py admit --source-key finepdfs --review-dir G:\XLM\reviews\finepdfs
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py policy model --source-key finepdfs
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py benchmark plan --source-key finepdfs --label b1 --file data/eng_Latn/train/000_00083.parquet
```

FinePDFs benchmark: one calibration file (2,771,021,138 B); after the digest
STOP: `benchmark authorize --source-key finepdfs --label b1 --digest <DIGEST> --operator "<OPERATOR_NAME>"`,
then (NETWORK) `benchmark run --source-key finepdfs --label b1`; the range half
uses `xlm data sample-blocks --source finepdfs_edu --view eng_Latn --revision 9cfabe2127faca99b3d5c4dc6d1fcb397399ebde --files data/eng_Latn/train/000_00083.parquet --seed 20260918 --mode rowgroup --target-records 20000 ...`,
`xlm data plan ... --adapter-spec finepdfs_en --pilot-approved`, `fetch`,
`adapt | Out-File -Encoding utf8`, `benchmark record-range`, exactly as for
UltraX with `finepdfs`/`finepdfs_edu`/`eng_Latn` paths. Plan/run require a
frozen FinePDFs inventory first.

Correction (2026-10-01):

- The real b1 run failed closed. A 9,064,396-byte row exceeded the generic 8 MiB `max_record_bytes`.
- FinePDFs `eng_Latn` now has an explicit, digest-bound 32 MiB record bound. The largest row in the scanned file is 24,828,818 B.
- A row above the bound still fails the unit closed.
- b1 stays as failed history. Plan and authorize a new **b2**, then run `benchmark adopt --source-key finepdfs --label b2 --donor b1` (OFFLINE). That reuses the verified retained 2.77 GB file with no redownload. After that, run `benchmark run --label b2`.
- See `FINEPDFS-RECORD-BOUND.md`.

The UltraX range-half `adapt` needs `--max-input-bytes 134217728`. The default
64 MiB cap is below its 96,365,880-byte 20,000-row `selected_records.jsonl`.
Use the same flag for a FinePDFs range half of similar size.

SYNTH (`--source-key synth`): the same first six commands; benchmark
`benchmark plan --source-key synth --label b1 --file synth_001.parquet`; the range
half is window mode as calibrated: `xlm data sample-blocks --source synth --view default --revision 0d6813a2966662c39f22f0b9af28a0c1c9f7a437 --files synth_001.parquet --seed 20260918 --mode window --adapter-spec synth_en --window-max-scan-rows 16384 --window-buffer-bytes 4194304 --window-batch-rows 256 --target-records 1000 --output <ROWS> --report <REPORT>`
and `xlm data plan ... --parquet-window-scan-rows 16384 --parquet-window-buffer-bytes 4194304 --parquet-window-batch-rows 256 --pilot-approved`.
Status: NEEDS_MEASUREMENT.

Wiki-Rewrite (`wiki_rewrite`), FineWiki (`finewiki`), SimpleStories
(`simple_stories`): the same first six commands with their key; no benchmark
is required for a decision (minutes either way); after a frozen inventory:
`policy freeze --source-key <KEY>` (modeled), `plan --source-key <KEY>`, STOP,
`authorize`, `run`, `verify`, `sufficiency`, `seal`. SimpleStories has 7 train
files; its inventory still needs the operator's file list.

IFM general / planning (`ifm_general`, `ifm_planning`): `evidence show|publish`,
`review show|record`, `admit` per view work now; `policy`/`plan` refuse until
the operator records an explicit per-view requirement split (the component's
5% is not split by this milestone).

Common Pile: blocked; `mix01_source.py` refuses the key. Allowed before the
blocker clears: nothing beyond the existing registry and certification notes
(component allowlist, per-component license/provenance and source review must
be decided first; its 5% weight is unchanged and nothing substitutes for it).

## 16. Requirement ledger

| Requirement | Status |
|---|---|
| UltraX evidence chain recovered and bound | VERIFIED (re-hashed; bridge dry run on real files) |
| Structural incompatibility explained | VERIFIED (code paths in §3) |
| No re-probe; bridge from immutable evidence | IMPLEMENTED, VERIFIED (real dry run; 20 tests) |
| Reusable for other sources | IMPLEMENTED, VERIFIED (7 real dry runs) |
| Refusals (revision, adapter, schema, source, view, incomplete, authored) | IMPLEMENTED, VERIFIED |
| Audit/mix01-status bug; gating unaffected | IMPLEMENTED, VERIFIED (real store + tests) |
| Mitigated benchmark risk for non-Essential sources (v3) | IMPLEMENTED, VERIFIED; Essential v2 unchanged |
| UltraX deterministic planner, top-up, lineage | IMPLEMENTED, VERIFIED (authored + real preview) |
| Transport modes, versioned policy, executability | IMPLEMENTED, VERIFIED |
| High-throughput engine (resume, caps, root failure, receipts, dashboard, pipelining) | IMPLEMENTED, VERIFIED on loopback fixtures; NOT RUN live |
| Bounded benchmark commands (UltraX, FinePDFs, SYNTH) | IMPLEMENTED; benchmarks NOT RUN |
| Range-mode production planner | NOT IMPLEMENTED (reported fastest only; future milestone) |
| UltraX review input | IMPLEMENTED (facts); decision PENDING (operator) |
| UltraX admission, benchmark, plan, authorization, acquisition | NOT RUN (operator) |
| Essential-Web regression and seal | VERIFIED (tests + real `verify --skip-content`) |
| mypy --strict | NOT RUN (blocked by Windows application control) |
| Full acceptance suite | NOT RUN (focused + related selection only) |
| C05, tokenizer, training, network | NOT RUN / OUT OF SCOPE |

## 17. Next prompt

> Run the UltraX operator runbook in this report through `review show`, record
> the operator's decisions and `admit`; then run the bounded UltraX benchmark
> (both halves), `record-range`, `policy freeze --basis measured`, and `plan`.
> Stop at the printed PLAN DIGEST for review. In parallel, decide whether a
> range-mode production planner is worth building from the measured receipts.
