# Mix-01 transition after the Essential-Web first pass (2026-10-01, offline)

**Verdict: MIX-01 ACQUISITION TRANSITION STILL BLOCKED.** The Essential-Web
first pass is complete and is now sealed as one immutable input. The next
source by readiness is UltraX. UltraX cannot start production acquisition with
the current code: no tool can produce the schema-verified store probe evidence
that C04 admission requires. The same gap blocks every other remaining source.

Scope of this task: no network, download, live probe, acquisition, C05,
tokenizer, tokenization, shard building, pilot, training, mixture, quota or
selector change; no push. Essential-Web data was not moved, deleted or
rewritten. The only store write is one new write-once seal file.

## 1. Repository state

| Item | Value |
|---|---|
| Branch | `data/mix01-ultrax-6b` |
| HEAD at start | `9024cfa4812402775b90ea69866a7abf467d5b71` (the last known Essential-Web fix) |
| Pre-existing dirty work, preserved and not staged | `docs/implementation/STATUS.md` (101-line user hunk); untracked `.bf/`, `.br0/`, `.bscope/`, `.bt-c04/`, `.bt-fdc/`, `docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-REVIEW/`, `docs/implementation/evidence/ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW/`, and five untracked review reports (`ESSENTIAL-WEB-EVIDENCE-V3.0-REVALIDATION.md`, `ESSENTIAL-WEB-EVIDENCE-V4.1-REVIEW.md`, `ESSENTIAL-WEB-SELECTOR-POLICY-REVIEW.md`, `ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW-AUDIT.md`, `ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW.md`) |
| Environment | Windows 11, Python 3.12 via `uv run --offline --locked --no-sync --extra cpu --extra eval`; `G:` Samsung 870 EVO SATA SSD (measured ~538 MB/s sequential read during the seal) |

## 2. Authoritative Essential-Web first-pass verification

Every value below was read from the operator store, not from console output.
The seal build (§4) recomputes all of them from the 576 receipts and re-hashes
every byte those receipts bind.

| Fact | Value | Authoritative source |
|---|---|---|
| Source | `EssentialAI/essential-web-v1.0` @ `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d` | campaign `binding`; every receipt `source` |
| Selector | B-normal, `essential-web-selector-fasttrack-v1`, policy `f4357f61…dd07`, freeze `c6f32a65…0a0c` | campaign `binding.selector` |
| Campaign | `8e42ba31b0ef9fcbae1079cebf88d2449b88a9b37ec52b68c7a408eb2546bb8c` (`essential_web_fast_campaign` v1) | `docs/implementation/evidence/ESSENTIAL-WEB-FAST-TRANSPORT/campaign.json` |
| Inventory | 23,200 files, digest `4bbd5517…63d8`, seed 20260930 | campaign `inventory`, re-frozen by the loader |
| Completed batches | **18** (b0000–b0017), a contiguous prefix; no b0018/b0019 directory exists | `G:\XLM\plans\ew-fast\b00NN\batch.json` + `authorization.json` |
| Sealed files | **576** receipts, 576 retained Parquet files, 576 identity sidecars | `G:\XLM\canonical\ew-fast\bNNNN\fNNNNN\receipt.json`, `G:\XLM\acq-raw\ew-fast\source` |
| Rows scanned | **47,979,123** (22,423 malformed, 0.0467%) | receipts, recomputed |
| Raw bytes retained | 150,721,177,161 | receipts, re-hashed |
| Durable footprint | 198,415,949,458 bytes | `cumulative.json` (receipt-derived) |
| Runner end state | `safe_stop`, outcome `FIRST_PASS_COMPLETE`, "all first-pass targets are sufficient" after Batch 17 | `G:\XLM\plans\ew-fast\auto\campaign-runner.jsonl` (last event) |
| Admissions | three views admitted at `attempt02`: `c04-benchmark-risk-v2`, `suspect_with_mitigation`, adapter `essential_web_bnormal`, B-normal selector binding; gate re-evaluated `admitted` | `G:\XLM\xlm-home\admission_decision\*.attempt02` |
| Recovery amendment | f00026 (rank 26, batch 0), record bound 11,494,172 B, digest `51c09f0b…24db` | `ESSENTIAL-WEB-BATCH0-RECOVERY/recovery.json` |
| Code-compatibility chain | scope fix `94ab7cdb…fab6`, Windows fix `1a63736c…c0d9`, malformed whole-pass fix `9c9b618b…7f18` | `essential_web_recovery.COMPATIBILITY` |

Per view (estimated tokens = canonical bytes / 4, the campaign's frozen
`mix01_inventory` method; **not** exact XLM tokens):

| View | Documents | Canonical bytes | Estimated tokens | First-pass target | Final quota | Availability / first-pass target | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| essential_science | 315,173 | 2,692,218,118 | 673,054,530 | 660,000,000 | 600,000,000 | 1.020 | SUFFICIENT |
| essential_practical | 1,278,649 | 6,962,804,542 | 1,740,701,136 | 660,000,000 | 600,000,000 | 2.637 | SUFFICIENT |
| essential_prose | 4,239,644 | 20,071,278,364 | 5,017,819,591 | 330,000,000 | 300,000,000 | 15.206 | SUFFICIENT |

The campaign never needed batches 18 or 19. `-MaxBatches 20` was only the
envelope ceiling. The runner stopped at the first boundary where every target
was met, which was after Batch 17.

**Not run:** C05 (no dedup, exclusion or split artifact exists anywhere under
`G:\XLM`); tokenizer (`G:\XLM\tokfit` and `G:\XLM\frozen` are empty; xlm-home
holds only probe, admission, discovery and raw-dataset artifacts); training
(no run, checkpoint or pool artifact). `campaign-status.json` states
`training_permitted: false`.

**Disagreements found:** none in the Essential-Web facts. One reporting tool
disagrees with the store: `xlm data mix01-status` reports every view,
including the three admitted Essential views, as `not_live_verified` /
`unadmitted`. It audits catalog-level candidate flags, while production fetch
uses the per-view store admission (`resolve_verified_production_admission`).
That is a pre-existing reporting limitation; it was not changed here.

## 3. The repository's canonical-pool freeze contract

The repository has three freeze mechanisms. None of them fits a post-acquisition,
pre-C05 component pool:

| Mechanism | What it binds | Why it cannot be used now |
|---|---|---|
| `xlm.data.pools.freeze.PoolFreeze` (`xlm data split`) | dedup config identity + split policy + split membership | needs a C05 `DedupResult` and a split assignment, which exist only after global C05 |
| `xlm.data.pools.manifest.FrozenPoolManifest` (`xlm data pool build`) | requires `dedup_policy_identity`, `split_policy_identity`, `cleaning_policy_identity`, license receipts | same; it also loads every document into memory (`list(_read_canonical_input(...))`), which is impossible at 30 GB |
| `xlm.data.pools.regime` (`xlm data freeze`) | pool + tokenizer + diagnostic corpus | needs the tokenizer |

The Essential-Web campaign already seals each file unit with a self-digested
receipt. However:

- `cumulative.json` and `campaign-status.json` are summaries rewritten by
  every `account`/`status` call. They carry no digest.
- No record binds the completed first pass as one input that C05, tokenizer
  fitting and exact counting can name.
- `campaign-status.json` itself names the next step:
  `freeze canonical pool -> C05 exclusion receipt -> tokenizer -> exact count
  -> deterministic top-up of deficient components`. The campaign's C05
  obligation says C05 "runs on the frozen canonical pool".

**Conclusion:** an additional, additive first-pass seal is required. It is the
smallest native form: it reuses the campaign's own verified loader
(`essential_web_fast.load_campaign`, which re-checks source, selector, adapter
code, inventory, quotas, catalog and the code-compatibility chain), its
receipt loader and accounting (`campaign_state`, `fast.cumulative`), the
admission gate (`AdmissionGate.evaluate`) and the campaign's own write-once
JSON writer. It is explicitly not a `PoolFreeze` or `FrozenPoolManifest`.

## 4. The Essential-Web first-pass seal

| Item | Value |
|---|---|
| Store artifact | `G:\XLM\plans\ew-fast\first-pass-seal.json` (625,158 bytes, write-once) |
| Repository copy | `docs/implementation/evidence/ESSENTIAL-WEB-FIRST-PASS-SEAL/first-pass-seal.json` (byte-identical) |
| **Seal digest** | **`a77c78f7636695ef0ab241c176e07cc9bcfd1815907a13f55dc2f6f7c28c7516`** |
| Kind / stage | `essential_web_first_pass_pool_seal` v1 / `first_pass_canonical_availability` |
| Membership digests | science `fef2e068f10e006c4da00f693011d260436b9f4bd39d0e7152fa29d0c6aec56a`, practical `2a4a622183a8bf353573251d231b38a05f1c08111127998bdfa2ef21fb2bbabc`, prose `5005351857cba9c1cd11b74d05e8ed75e235c0ea34579f5b85de8d0d1690327d` |
| Code | `src/xlm/data/sources/essential_web_pool_seal.py` (pure), `scripts/essential_web_pool_seal.py` (`build`, `verify`) |
| Build | exit 0 in 353 s; 5,760 files re-hashed (576 raw Parquet + 5,184 canonical files), 198,412,506,830 sized bytes; 8 hashing threads |

**What the seal binds** (directly, or transitively through receipt digests):

- source repository, exact revision, adapter id, canonicalization path, B-normal
  selector (id, condition, policy digest, freeze digest, evaluator SHA-256);
- campaign digest, kind and version, superseded range campaign, inventory
  (path, digest, seed, order rule), batch membership rule, roots, quotas file
  SHA-256 and quota id, adapter-code and transport-code SHA-256 maps,
  calibration freeze and seal digests, raw contract `essential-web-raw-artifact-v2`,
  and the campaign's C05 obligation;
- the recovery amendment, the three ordered code-compatibility records and the
  running code identity;
- per view: the stored probe evidence and admission decision (artifact id and
  file SHA-256), probe fingerprint, contract version, benchmark-risk value and
  the re-evaluated gate status;
- all 18 batches: batch-record digest, membership digest, plan id, plan hash,
  authorization digest and, for batch 0, the recovery authorization digest;
- all 576 units in inventory order: rank, batch, source file, receipt digest,
  raw bytes and raw SHA-256, rows, malformed rows, per-view documents,
  canonical bytes and `documents_sha256`. The receipt digest in turn binds the
  plan, authorization, source identity (ETag, length, linked ETag, Xet hash,
  expected and local SHA-256), selected-record stream hash, rejection ledgers,
  adaptation summaries and transfer accounting;
- per-view membership: an ordered digest over
  `rank|file|documents_sha256|documents|canonical_bytes`, which binds each
  document file and therefore each document id and text;
- totals, the token method, and the first-pass sufficiency per view.

**Verification policy** (fixed, so a rebuild is byte-identical):

- every receipt is checked against its own digest and campaign;
- accounting is recomputed with the campaign's duplicate, order and plan checks;
- every raw Parquet and every documents file, compressed ledger and adaptation
  summary is checked for size and full-content SHA-256;
- every identity sidecar must equal its receipt's source record;
- there must be no staging residue, no unit without a receipt, and no stray or
  missing file in a unit;
- the build refuses an incomplete batch, a batch that is planned but never
  authorized, and an insufficient view.

**What the seal records as pending:** C05 (global; see §5); the tokenizer; the
exact XLM-token count; a deterministic top-up, which may still be required after
exact counting; the final 6B freeze; `training_permitted: false`. It also
lists what it is not: final training exposure, a C05-deduplicated or screened
pool, a tokenizer-fit corpus, an exact count, or the final 6B freeze.

**Why the practical/prose oversupply is preserved.** The seal records
availability, not exposure. `exposure_policy` states that mixture weights and
quotas did not change, that nothing was down-selected, and that final exposure
is chosen later by the C07 exact-token quota scheduler under the frozen
tokenizer and the unchanged Mix-01 weights. The extra text is headroom:

- C05 will drop documents (cross-source duplicates and benchmark hits);
- exact token counts will differ from the 4-bytes-per-token estimate;
- science carries only a 2% margin over its first-pass target.

Discarding documents now would decide exposure before the tokenizer and C05
exist. The mix still draws 10% practical and 5% prose.

**Store impact (proved):** a path/size/mtime snapshot of all 7,940 durable files
under `plans`, `canonical`, `acq-raw`, `xlm-home`, `calib`, `inventories`,
`frozen`, `tokfit` and `recon` before and after the build differs in exactly one
line: the new `plans/ew-fast/first-pass-seal.json`. The 9 files under
`C:\XLM-scratch` are unchanged. `verify --skip-content` on the real store
rebuilds the identical seal.

## 5. C05 ordering: global, deferred

This conclusion comes from the implementation, not only from `CONTRACTS.md`.

- **Deduplication is global.** `xlm.data.dedup.engine` is documented and built
  to "deduplicate across all selected source families before splits". Its
  frozen survivor rule, `clusters.select_survivor` v1, picks the largest
  canonical byte count, then the smallest `source_id`, then the smallest
  `doc_id`. When UltraX (a Common-Crawl-derived web corpus) arrives, a longer
  UltraX copy of a page that Essential-Web also holds would displace the
  Essential survivor. Survivors, clusters and lineage groups change, and
  `pools.freeze.detect_bridge_duplicates` would then require refreezing any
  split made earlier.
- **Benchmark exclusion is not component-invariant.**
  `BenchmarkExclusionMatcher.scan` first calls `suppress_common_spans`, which
  removes indexed spans that occur in more than `max_corpus_span_frequency`
  documents of the scanned corpus. The drop set therefore depends on what else
  is in the corpus. The receipt binds `compute_corpus_input_digest` over all
  submitted document ids. `verify_benchmark_claim` requires that digest and the
  kept-membership digest to equal the frozen training pool's. An
  Essential-only receipt could never certify the Mix-01 pool.
- **No component-level precursor is required.** No code or contract requires
  one, and none would survive the later global run.

**Decision: C05 is intentionally deferred until every Mix-01 component has
first-pass canonical availability.** It then runs once over the combined
first-pass pool, again after any top-up, and before the train/validation split
and tokenizer fit.

**Open limitation:** `BenchmarkExclusionMatcher.scan` materializes the whole
corpus (`doc_list = list(documents)`) and does a per-document substring loop
over every indexed span. It is not production-scalable as written. The
sharded dedup path (`xlm.data.dedup.sharded.run_sharded_dedup`) exists for
dedup, but exclusion has no sharded equivalent yet. This is a C05
implementation task for later, not for this one.

## 6. Tokenizer and training

Tokenizer: NOT TRAINED; no fit corpus has been frozen (`G:\XLM\tokfit` is
empty). Exact XLM-token counts: NOT AVAILABLE. Training: NOT PERMITTED
(`training_permitted: false` in the campaign status and in the seal).

## 7. Remaining Mix-01 sources: production readiness

Probe state is from `G:\XLM\xlm-home\probe_evidence`. Calibration is from
`G:\XLM\calib\calibration.json` and `calibration_quota.json`. Estimates are from
`G:\XLM\calib\headroom_estimate.json`; this task re-derived them offline and
all sources are identical. "Transfer" is `required_transferred_bytes.base`
(low/high assume 3/5 bytes per token).

| Component | Repository @ pinned revision | View / config | Adapter | Schema certification | Store probe | Admission | Declared license | Provenance / benchmark review | Frozen inventory | Calibration (sampled → accepted) | First-pass need: canonical / transfer | Acquired |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ultrax_ultrafineweb (20%) | `openbmb/UltraX-Preview` @ `a8852758…d8df` | `UltraX-Ultra-FineWeb` | `ultrax_ultrafineweb` | live-certified on 30 real pinned rows (operator check re-run today: 16 passed, 0 skipped); registry flag `live_verified: false` | `partial`, `adapter_contract_unassigned`, no schema, no fingerprint | none | apache-2.0 + derived-corpora caveat (Ultra-FineWeb lineage) | pending / not reviewed | **yes**: 104 files, digest `cb42e273…634f`, sizes null (reproduced byte-identically today) | 1,000 → 994 (6 `remove_all`) | 5,280,000,000 / 5,252,218,497 (3.94–6.57 GB) | calibration only (1,000 records) |
| finepdfs_en (15%) | `HuggingFaceFW/finepdfs-edu` @ `9cfabe21…ebde` | `eng_Latn` | `finepdfs_en` | authored fixtures + 1,000 real calibration rows (0 rejected); registry `false` | `partial` | none | odc-by | pending / not reviewed | no | 1,000 → 1,000 | 3,960,000,000 / 2,954,379,963 | calibration only |
| synth_en_explanations (15%) | `PleIAs/SYNTH` @ `0d6813a2…a437` | `default` | `synth_en` | 3 real rows certified; registry `true` | `partial` | none | cc-by-4.0 (row `seed_license`) | pending / not reviewed | no (500 files known) | 1,000 → 806 (window) | 3,960,000,000 / 6,031,472,571 (retained-share basis, §9) | calibration only |
| nemotron_wiki_rewrite (8%) | `nvidia/Nemotron-Pretraining-Specialized-v1` @ `9ed3718b…320f` | `Nemotron-Pretraining-Wiki-Rewrite` | `wiki_rewrite` | 3 real rows; registry `true` | `partial` | none | cc-by-4.0 (row licenses kept) | pending / not reviewed | no | 1,000 → 1,000 (window-v2, 16,384 scanned) | 2,112,000,000 / 519,294,125 (retained-share basis) | calibration only |
| finewiki_en (5%) | `HuggingFaceFW/finewiki` @ `8bd13e72…aeb9` | `en` | `finewiki_en` | fixtures + 1,000 real calibration rows; registry `false` | `partial` | none | cc-by-sa-4.0 (ShareAlike) | pending / not reviewed | no | 1,000 → 1,000 | 1,320,000,000 / 636,069,559 | calibration only |
| ifm_behaviors_general_planning (5%): **two units** | `IFM/Pretrain-Behaviors` @ `3345e13d…91f5` | `general`, `planning` | `ifm_general`, `ifm_planning` | fixtures + real calibration rows; registry `false` | two `partial` records | none | apache-2.0 | pending / not reviewed | no | general 698 → 698, planning 1,903 → 1,903; combined entry 2,601 | 1,320,000,000 / 655,314,194 combined (50/50 split ASSUMED) | calibration only |
| common_pile_prose (5%) | `common-pile/comma_v0.1_training_dataset` @ `5afc546d…1e7c` | top-level component directories | `common_pile` | 9 real rows from 3 of 31 components | **none** | none | **NULL** | **BLOCKED** | no | **none** (`NEEDS_CALIBRATION`) | unknown | none |
| simple_stories (2%) | `SimpleStories/SimpleStories` @ `e63b8adc…0628` | `default` | `simple_stories` | 6 real rows; registry `true` | `partial` | none | mit | pending / not reviewed | no (7 train files) | 1,000 → 1,000 | 528,000,000 / 1,159,928,411 | calibration only |

The C05 obligation is the same for all eight: global dedup plus benchmark
exclusion over the frozen Mix-01 training pool (§5). No non-Essential source
has an admission record, so none carries its own mitigation binding.
Sufficiency is `TOP_UP` for all eight: nothing beyond calibration samples has
been acquired, and no canonical output exists outside `G:\XLM\calib`.

**Blockers shared by every remaining source**

- **I1 (implementation): no route to admissible probe evidence.**
  `xlm data probe --live` constructs `SourceProber` without an
  `extractor_contract`. `HuggingFaceTransport.get_snapshot_info` supplies the
  raw Hub `cardData`, which never holds a `ViewSchema`. So the generic probe
  can only emit `partial` / `adapter_contract_unassigned`. `AdmissionGate`
  requires `accessible`, a verified schema and a matching fingerprint, so
  `xlm data admit` would record a decision the gate rejects, and production
  `xlm data fetch` refuses. Essential-Web needed its own bootstrap
  (`essential_web_bootstrap`), whose transport is hard-pinned to the
  Essential repository and whose plan depends on a retained real footer.
- **I2 (implementation): no production planner.** Nothing derives, from a frozen
  inventory, real footers and the calibration yield, the ordered file prefix,
  the row ranges for whole files and the `AcquisitionLimits` JSON
  (bytes, records, requests, deadline, output disk). Hand-typed limits would
  be fabricated values. `sample-blocks --mode rowgroup` exists but was designed
  and tested for 1,000-record samples.
- **O1 (operator/policy): no review has been recorded** for any source's
  license, provenance or benchmark risk. For non-Essential sources, the gate
  admits only `benchmark_risk=clean`. `data admit` cannot record the mitigated
  form (contract version, review hashes, C05 mitigation binding). Under C04
  risk v2, `clean` is a legacy value: it states admission eligibility, never
  zero contamination.
- **Inventories:** only UltraX has a frozen inventory. The other sources need
  a reviewed file list at the pinned revision first.

**Source-specific blockers**

- UltraX: FineWeb lineage review. Ultra-FineWeb derives from FineWeb and
  FineWeb-Edu; C04 bans only the direct FineWeb repositories and requires
  lineage review.
- FinePDFs, FineWiki, IFM: formal adapter-certification review of the real
  calibration rows (registry `live_verified: false`).
- SYNTH, Wiki-Rewrite: whole-row-group windows at production scale are
  unmeasured (§9).
- IFM: the 50/50 general/planning split is an assumption.
- Common Pile: §10.

## 8. UltraX current state

| Required item | Present? | Evidence |
|---|---|---|
| Exact revision freeze | YES | `a88527587389fd4ab352e9ad1273f4c0a234d8df` in `mix01_views.yaml`, the catalog and every UltraX artifact |
| Real probe evidence | PARTIAL | Track-A schema-probe receipt + 30 real rows at `D:\Project\xlm-operator-ultrax\adapter-cert-ultrax01` (real, but not store evidence); store record `probe_ultrax_ultrafineweb_UltraX-Ultra-FineWeb` is `partial` without schema or fingerprint |
| Tested adapter | YES | `tests/test_ultrax_ultrafineweb.py` (authored) and `tests/test_ultrax_live_certification.py` against the real rows: 16 passed, 0 skipped (with `test_ultrax_probe_compat.py`) |
| Admission | NO | no `admission_decision` record; not possible until I1 is resolved |
| Inventory | YES | `G:\XLM\inventories\ultrax.inventory.json`, 104 files, digest `cb42e2738c9b2f74aae98dd557758a638fec7dbb799f3c5b2b0c7e256900634f`; per-file sizes null |
| Calibration | YES | plan `f13b93a5…ac02d`, part-0039 rows [90950, 91950), 1,000 → 994, 3,326,258 B transferred, 3,845,430 canonical B (1.156 canonical per transferred) |
| Headroom estimate | YES | 5,252,218,497 B base transfer (4.89 GiB), 5.28 GB canonical |
| Production plan tooling | NO | I2 |

**UltraX remaining blockers:** I1, I2, O1 (license and derived-corpus
provenance, FineWeb lineage, benchmark-risk value), plus a user policy decision
on the benchmark-risk treatment of web text for non-Essential sources (§12).
Stale registry text: the UltraX notes in `mix01_views.yaml` still say the
revision is null, although it is pinned. That is documentation only and was not
changed.

## 9. Transport decision (UltraX, FinePDFs, SYNTH)

All inputs are measured artifacts: calibration journals (`source_validators`
whole-file lengths), `rows.evidence.json` footers, and calibration
transfer and request counts. Each extrapolation rests on one measured file.

**UltraX: KEEP_EXISTING_BOUNDED_FETCH.**
- Measurements: 104 files; `part-0039` is 1,055,787,705 B. One 1,000-row group
  is 7,867,072 B, and the projected read (`uid`, `cleaned_content`, `source`,
  `processed_functions`) of the same 1,000 rows transferred 3,326,258 B in 6
  requests, including footer discovery.
- Whole-file cost: 2.37× the projected bytes, because it also moves
  `raw_content`, which the contract says is never training input.
- Requirement: 5,252,218,497 B projected ≈ 1,579,000 rows ≈ 1,580 row groups
  ≈ 12 files.
- Comparison: whole-file ≈ 12.7 GB in about 24 requests, versus range ≈ 5.25 GB
  in 1,580–9,500 requests.
- No UltraX local-Parquet adapter exists. The C04 raw-artifact amendment
  applies to `essential_web` only. The existing path already canonicalizes
  (`selected_records.jsonl` → `data adapt`).
- Retaining the upstream Parquet would store 2.4× the bytes, mostly unused
  `raw_content`.
- Whole files would need new code and a contract amendment to save requests,
  at more than double the transfer.

**FinePDFs: KEEP_EXISTING_BOUNDED_FETCH.**
- Measurements: `000_00083` is 2,771,021,138 B. One 1,000-row group is
  10,640,678 B; the projected read transferred 6,677,528 B in 7 requests.
- Whole-file cost: 1.59× the projected bytes.
- Requirement: 2,954,379,963 B ≈ 442,000 rows ≈ 443 groups ≈ 1.7 files.
- Comparison: whole-file ≈ 5.5 GB in 2 files, versus range ≈ 2.95 GB in
  443–3,100 requests.
- Two whole files would also narrow the sampling frame to two shards of a PDF
  corpus, which is a selection change, not a transport change.
- No local path exists, and no inventory exists yet.

**SYNTH: NEEDS_MEASUREMENT.**
- Measurements: 500 files; `synth_001` is 471,540,559 B, one row group of
  154,674 rows. The 11 projected columns are 273,494,708 B (58%);
  198,035,245 B are unprojected.
- Requirement: at the calibrated 2,290 canonical bytes per sampled record,
  3.96 GB canonical ≈ 1.73M rows ≈ 12 files.
- Comparison: projected whole-group windows ≈ 3.28 GB versus whole files
  ≈ 5.66 GB (1.72×). Window streaming is estimated at about 66 requests per
  file (4 MiB buffers), about 800 in total, versus about 24 whole-file
  requests. That request figure is a calculation, not a measurement.
- The headroom estimate's 6.03 GB uses the `calibration_window_retained_share`
  basis. The calibration window read 3,033 B per scanned row, against the
  footer's 1,768 projected B per row. Production-scale whole-group windows have
  never been measured, so neither path can be compared with evidence yet.

The small sources (Wiki-Rewrite, FineWiki, IFM, SimpleStories) keep the
existing bounded fetch; no new transport is justified.

## 10. Common Pile

- Intended upstream components: the `common_pile_prose` component of
  `common-pile/comma_v0.1_training_dataset` @ `5afc546d…1e7c`. The upstream
  component is the top-level source directory of each file path; the dataset
  has about 31 components.
- Licenses: dataset-level license metadata is NULL. Per-component licenses are
  not recorded in the repository. Only news, libretexts and
  public_domain_review rows (9 real rows) were ever certified.
- Allowlist: none exists. The registry states that "final Mix-01 component
  allowlist, weights, and per-component licensing/admission remain unresolved
  policy decisions".
- Resolved? No. There is no store probe evidence and no calibration.
- Production admission possible? No.

**COMMON PILE STAYS BLOCKED.** Its 5% weight is unchanged. Nothing was
substituted and Mix-01 was not renormalized.

## 11. Next source by readiness

**UltraX.** It is the only remaining source with a frozen inventory. It also
has a pinned revision, an adapter live-certified on real rows, a calibration,
an estimate and a declared license. Every other source shares I1, I2 and O1
and additionally lacks an inventory. No source is materially closer. UltraX
cannot begin production acquisition without new code (I1, I2).

**Smallest implementation milestone that unblocks it** (not started here):

1. A bounded footer-schema admission probe for pinned non-Essential Parquet
   sources: one Hub API call at the pinned SHA (revision, license, gated flag),
   plus the magic, trailer and footer ranges of one frozen-inventory file with
   ETag and length checks. It builds `ViewSchema` via
   `arrow_schema_to_view_schema`, checks the registered adapter contract and
   computes the fingerprint with `compute_probe_fingerprint`. It publishes an
   `accessible` `ProbeEvidenceRecord` at the next attempt with
   `save_probe_evidence`.
2. A production planner that reads the frozen inventory and real footers and
   emits the ordered file prefix, whole-file row ranges and an
   `AcquisitionLimits` JSON sized from the estimate, and is split into bounded
   batches.

Both run offline except item 1's live call, and both need tests on authored
fixtures.

## 12. Operator runbook

Network stays OFF throughout: Part A has no network command, and Part B's
network steps do not exist yet. Every command is one line; run them in order
in PowerShell from the checkout.

**Part A: executable now (offline state and readiness verification)**

```powershell
Set-Location F:\Project\xlm-data-ultrax
git rev-parse HEAD
git branch --show-current
git status --short
. .\scripts\operator_storage.ps1
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:UV_OFFLINE='1'
Get-PSDrive -Name C,G | Select-Object Name,@{n='FreeGiB';e={[math]::Round($_.Free/1GB,1)}}
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/essential_web_pool_seal.py verify
uv run --offline --locked --no-sync --extra cpu --extra eval xlm mixture preset-validate --preset recipes/mixtures/mix01.yaml --views recipes/mixtures/mix01_views.yaml
uv run --offline --locked --no-sync --extra cpu --extra eval xlm data probe --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb
New-Item -ItemType Directory -Force C:\XLM-scratch\ultrax-verify
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py freeze --source ultrax_ultrafineweb --repo openbmb/UltraX-Preview --revision a88527587389fd4ab352e9ad1273f4c0a234d8df --seed 20260918 --files G:\XLM\inventories\ultrax_candidates.txt --output C:\XLM-scratch\ultrax-verify\ultrax.inventory.json
(Get-FileHash C:\XLM-scratch\ultrax-verify\ultrax.inventory.json).Hash -eq (Get-FileHash G:\XLM\inventories\ultrax.inventory.json).Hash
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py estimate --quotas recipes/mixtures/mix01_quotas_6b.yaml --calibration G:\XLM\calib\calibration_quota.json --output C:\XLM-scratch\ultrax-verify\headroom_estimate.json
$env:XLM_ULTRAX_CERT_DIR='D:\Project\xlm-operator-ultrax\adapter-cert-ultrax01'
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_ultrax_live_certification.py -n 0 -q
```

What each check must show:

- `verify` prints `seal verified (FULL)` with digest `a77c78f7…7516`; it takes
  about 6 minutes and is read-only.
- The offline probe prints `Outcome: partial` and `Fingerprint: None`. That
  proves I1.
- The inventory comparison prints `True`.
- The estimate's `ultrax_ultrafineweb.required_transferred_bytes.base` is
  5252218497.
- The certification test prints 16 passed with no skip.

**HARD STOP: UltraX PRODUCTION ADMISSION IS NOT POSSIBLE WITH THE CURRENT
CODE (I1, I2).** Do not run `xlm data admit` for UltraX: it would record a
decision that the gate rejects.

**Part B: after the §11 milestone lands (NOT EXECUTABLE NOW).**

Steps 1 and 3 name tools that do not exist yet; their commands will come with
the milestone. The other commands are the existing CLI. `<...>` values come
from the preceding command's output, never from memory.

1. (new tool; NETWORK) Precede it with `$env:HF_HUB_OFFLINE='0'` and
   `$env:HF_DATASETS_OFFLINE='0'`, run the bounded UltraX footer-schema probe,
   then restore `$env:HF_HUB_OFFLINE='1'` and `$env:HF_DATASETS_OFFLINE='1'`.
2. Review the license, derived-corpus provenance, FineWeb lineage and benchmark
   risk. Then admit, only if the review passes:
   `uv run --offline --locked --no-sync --extra cpu --extra eval xlm data admit --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --adapter ultrax_ultrafineweb --notes "<OPERATOR_REVIEW_NOTES>" --decision approve --license-review approved --benchmark-risk clean`
   (`clean` is the only value the current gate admits for this source; see §7 O1).
3. (new tool; offline) Run the production planner. It writes
   `<ULTRAX_ROWS_JSON>`, `<ULTRAX_LIMITS_JSON>` and `<ORDERED_PREFIX_CSV>`
   under `G:\XLM\plans\ultrax`.
4. `uv run --offline --locked --no-sync --extra cpu --extra eval xlm data plan --source ultrax_ultrafineweb --view UltraX-Ultra-FineWeb --files <ORDERED_PREFIX_CSV> --mode selected_records --row-ranges <ULTRAX_ROWS_JSON> --adapter-spec ultrax_ultrafineweb --seed 20260918 --attempt 1 --limits <ULTRAX_LIMITS_JSON> --output G:\XLM\plans\ultrax\ultrax_prod.json`
5. `uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/calibration_adopt.py plan-identity --plan G:\XLM\plans\ultrax\ultrax_prod.json`
6. **STOP — USER MUST REVIEW PLAN DIGEST BEFORE AUTHORIZATION**
7. Authorization: rerun the identical step-4 command with
   `--authorization-hash <DIGEST_FROM_PREVIOUS_COMMAND>` appended.
8. `$env:HF_HUB_OFFLINE='0'`
9. `$env:HF_DATASETS_OFFLINE='0'`
10. `uv run --offline --locked --no-sync --extra cpu --extra eval xlm data fetch --plan G:\XLM\plans\ultrax\ultrax_prod.json --output-dir G:\XLM\acq-raw\ultrax --scratch-dir C:\XLM-scratch\ultrax`
11. `$env:HF_HUB_OFFLINE='1'`
12. `$env:HF_DATASETS_OFFLINE='1'`
13. Status: `uv run --offline --locked --no-sync --extra cpu --extra eval xlm data status --plan G:\XLM\plans\ultrax\ultrax_prod.json --scratch-dir C:\XLM-scratch\ultrax`
14. Resume, if interrupted: repeat steps 8–12 unchanged. The journal resumes
    the same plan; never mint a replacement plan.
15. `uv run --offline --locked --no-sync --extra cpu --extra eval xlm data verify --plan G:\XLM\plans\ultrax\ultrax_prod.json --output-dir G:\XLM\acq-raw\ultrax --scratch-dir C:\XLM-scratch\ultrax --json`
16. `uv run --offline --locked --no-sync --extra cpu --extra eval xlm data adapt --plan G:\XLM\plans\ultrax\ultrax_prod.json --adapter ultrax_ultrafineweb --input G:\XLM\acq-raw\ultrax\selected_records.jsonl --output-dir G:\XLM\canonical\ultrax --on-reject record`
17. `uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py canonical-bytes --input G:\XLM\canonical\ultrax\documents.jsonl`
18. Sufficiency: `uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py sufficiency --estimate G:\XLM\calib\headroom_estimate.json --acquired <ACQUIRED_JSON_FROM_MILESTONE_TOOL> --output G:\XLM\plans\ultrax\sufficiency.json`
19. If `TOP_UP`: plan the next inventory files under a new plan with the same
    seed and revision, and repeat steps 4–18.
20. Final first-pass source seal: a generic source seal is part of the
    milestone. The Essential seal is Essential-specific.

The `xlm data` fetch path (steps 10 and 13–15) needs `UV_OFFLINE=1` only for
dependency resolution. It stays on through `--offline`; only the HF variables
change for network access.

**Exact command to continue after user authorization:** step 7, then step 10
(with steps 8–9 before and 11–12 after). Neither exists in executable form
until the milestone lands.

## 13. Implementation, tests and files

**Implementation:**

- the additive first-pass seal (§4);
- `tests/test_essential_web_pool_seal.py`: 11 tests. They cover:
  - an end-to-end build on the authored fast world, with a store-immutability
    check, a deterministic rebuild, `verify`, and the repository copy;
  - refusals for an insufficient pass, a planned-but-unauthorized batch and an
    authorized-but-unrun batch;
  - tampered documents, ledger, raw and sidecar files, and a stray file, in
    both full and `--skip-content` modes;
  - a forged seal, an unsupported redigested seal, and a refused overwrite;
  - membership order and binding;
  - contiguity refusals;
  - the committed real seal's headline numbers.

No existing file was changed, and no frozen adapter, transport, driver or
selector file was touched. `essential_web_fast.load_campaign` still loads
through the unchanged compatibility chain.

**Tests run** (offline, locked environment):

| Command | Result |
|---|---|
| `pytest tests/test_essential_web_pool_seal.py -n 0 --basetemp=.bt-seal` (before the real seal) | 10 passed, 1 skipped (committed-seal test; seal not yet built) |
| same, after the real seal | 11 passed, 0 skipped |
| `pytest tests/test_ultrax_live_certification.py tests/test_ultrax_probe_compat.py -n 0` (with the real D: evidence) | 16 passed |
| related selection, parallel half: 30 files, `-n 16 --dist=worksteal --max-worker-restart=0 -m "not serial and not optional_dependency" --basetemp=.bt-par` | 702 passed, **2 failed** (34 s): `test_acquisition_bounds.py::test_production_fetch_with_verified_admission` and `::test_public_plan_fetch_status_verify_prepare`, both `[Errno 2]` on a ~270-character journal `.tmp` path under `.bt-par\popen-gwNN\...` (known Windows path-length issue, unrelated to this change) |
| the same 2 nodes, `-n 0 --basetemp=.bt` | 2 passed |
| related selection, serial half `-n 0 -m "serial or optional_dependency" --basetemp=.bt` | 3 passed, 704 deselected |

The selection's 30 files are the new seal tests; Essential-Web fast,
campaign-runner, recovery, recovery-scope, Windows, malformed, bulk,
calibration, readiness, bootstrap and production-selector tests; pool
freeze/splits/views and CLI pool freeze; exclusion benchmark and receipt;
Mix-01 quotas, views and inventory; mixture planning; source admission and
discovery; acquisition plan and bounds; calibration adoption; and the three
UltraX test files. 0 skipped.
| `ruff check`, `ruff format --check` on the three new files | pass (re-run on the final bytes) |
| `mypy --strict` on the three new files | pass: "Success: no issues found in 3 source files" (compiled mypy, run after the last code edit). A later re-check before commit could not start: Windows application control blocked both the compiled `.pyd` and the interpreted runner's `librt` DLL. The code bytes did not change between the passing run and the commit. |
| `xlm mixture preset-validate` (mix01) | valid, weight sum exactly 1, 11 components |
| `mix01_inventory.py freeze` (UltraX) / `estimate` | byte-identical inventory / identical estimate for every source |

Not run: the full acceptance suite, CUDA, network and live tests, a full
`verify` after the build (the build itself re-hashed everything, and
`verify --skip-content` reproduced the seal), and any tokenizer or training
test.

**Files changed:**

- new `src/xlm/data/sources/essential_web_pool_seal.py`;
- new `scripts/essential_web_pool_seal.py`;
- new `tests/test_essential_web_pool_seal.py`;
- new `docs/implementation/evidence/ESSENTIAL-WEB-FIRST-PASS-SEAL/first-pass-seal.json`;
- new report (this file);
- a prepended `docs/implementation/STATUS.md` entry.

## 14. Requirement ledger

| Requirement | Status |
|---|---|
| Authoritative Essential-Web state from artifacts | VERIFIED |
| Native freeze mechanism audit | VERIFIED (none applies pre-C05) |
| Additive first-pass seal binding campaign, batches, units, content, membership, selector, adapter, amendments, admissions | IMPLEMENTED, VERIFIED (authored world + real store, 5,760 files re-hashed) |
| Oversupply preserved; no weight, quota, selector or membership change | VERIFIED |
| Historical receipts and outputs unmodified | VERIFIED (7,940-file snapshot) |
| C05 global and deferred, from code | VERIFIED |
| Remaining-source audit | VERIFIED (store, calibration, registry) |
| UltraX adapter live certification | VERIFIED (16 passed on real rows) |
| UltraX production admission | BLOCKED (I1, O1) |
| UltraX production planning | BLOCKED (I2) |
| Transport decisions | IMPLEMENTED (calculation from measured sizes); SYNTH NOT RUN (needs measurement) |
| Common Pile | BLOCKED (license, allowlist) |
| C05, tokenizer, training, network, acquisition | NOT RUN / OUT OF SCOPE |

## 15. Next prompt

> Implement the smallest UltraX admission and production-planning milestone
> (§11): (1) a bounded, pinned footer-schema admission probe for non-Essential
> Mix-01 Parquet sources that publishes `accessible`, schema-verified,
> fingerprinted store evidence, and (2) an offline production planner that
> derives the ordered inventory prefix, whole-file row ranges and
> `AcquisitionLimits` from the frozen UltraX inventory, real footers and the
> calibration, in bounded batches, plus a generic first-pass source seal.
> Offline tests on authored fixtures only; no network, no admission, no fetch.
> Record the user's decision on the benchmark-risk treatment of
> FineWeb-lineage web text before any `data admit`.
