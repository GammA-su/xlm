# Essential-Web v4.1 M selector-evidence analysis

**M SELECTOR EVIDENCE SEALED — READY FOR T BLINDED PACKAGE**

2026-09-30, branch `data/mix01-ultrax-6b`, starting HEAD
`c69a184ee86606d3a4960b689d3958617c9c09f3`. The frozen A/B/C/D
normal/strict policies were run, unchanged, on the verified 4,096-row
Phase-D M replicate and compared with the hash-verified frozen development
sweep. This is evidence reporting only: no policy is ranked, no threshold is
tuned, no selector decision is made.

M seal digest:
`afc972ccf570a5587bf7bd3b6b8d1d728a09f2230c49f1b0a19c1c74cda0b9bc`

No network, acquisition, Phase-P/Phase-D rerun, G: mutation, Arm-T text
access, human review, unblinding, reselection or push.

[Evidence directory](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS/),
[seal](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS/m_seal.json),
[full generated comparison tables](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS/m_comparison.md),
[machine-readable comparison](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS/m_comparison.json),
[eight-condition results](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS/m_sweep_results.json),
[exact commands](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-M-ANALYSIS/COMMANDS.md).

## Inputs verified

| Binding | Verified value |
|---|---|
| M source | `G:\Project\xlm-evidence-v4.1\essential-web-phase-d\m_selected_metadata.jsonl`, 13,571,797 bytes, 4,096 rows (512 × 8 files) |
| M source SHA-256 | `4d268f8d4ac3fcfe6cfea6184c8bcce5b7cb797dc1c627ca83a91371e3070e02` |
| M acquisition manifest SHA-256 | `9923e434b42188b978b0c3b3b976abd17c5b09f66f0d1e9bfad779aaf55aadb9` |
| Phase-D receipt SHA-256 | `7e8c37e183d546cb43c5cc3deae1c8b6267969500e8af025ceeba67c149320e3` |
| Preparation manifest digest | `925ed40b6709519863eb6c0069386da5a0f9ae3f649beb63205f9b0694a0a246` |
| Scientific namespace | `essential-web-evidence-v2.0` |
| Selection digest | `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474` |
| Source revision | `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d` |
| Selector policy digest | `f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07` |
| Frozen evaluator | `scripts/essential_web_selector_sweep.py`, SHA-256 `5a63e78560ea9e12b0ec03b5e7e63204b5c75554bee02192c12bb4ab0853bf9c`, tool version 2, report schema 2; file unmodified in Git |
| Policy spec file | `recipes/selectors/essential_web_selector_sweep_v1.yaml`, SHA-256 `c27a0aae8f63a1d9f80fdeb693a14d877b68298d386a3fc8679bbab34fa2a088` |
| Development sweep | `F:\Project\xlm-selector-sweeps\essential-web-v2`, manifest digest `e13c9c98efdf07fcc7c1375c4c8b62d918d77aa39c158171aa090c8695ac4087`; all eight artifacts and the manifest match their frozen hashes |

The development replicate was **not re-evaluated**: its raw bundle is
unavailable, so its numbers come from the hash-verified frozen artifacts.
Its eight-condition counts equal the reference counts quoted in the task.
The M-run policy spec serializes byte-identically to the development
sweep's `policy_spec.json`.

## Adapter

`src/xlm/data/evidence_v2/m_input_adapter.py`, version 1, code SHA-256
`fdf21328933840bcc3be4e7b40f43c49ad58d9d212147d8b17a77e6bbe6c66ab`.

The frozen evaluator reads the absolute Parquet row from
`_xlm_acquisition.row_index` and takes it to lie inside the file's row
range. The adapter therefore writes a separate derived view in which each
record's locator gains `row_index` (equal to the existing `row`) and the
marker `analysis_input_kind: derived_analysis_input`. Everything else is
kept: `eai_taxonomy`, `quality_signals`, repository, revision, source file,
row, row group, row-within-group and ordinal.

- Metadata is provably unchanged: each source line must round-trip to its
  exact bytes under the canonical serializer, and an ordered metadata digest
  computed from the source equals the one recomputed from the derived file.
- The evaluator is entered through its own `run_sweep`. The historical
  `load_binding` step is not used, because it checks a legacy bundle and
  execution receipt that Phase D never produced. The adapter supplies only
  the facts `run_sweep` reads (crawl, row range, repository, revision,
  payload hash and size). No bundle digest, execution digest, plan hash,
  acquisition ID or transport history is invented; a test confirms the
  frozen `load_binding` refuses the derived view.
- It refuses, writing nothing, on a count other than 4,096, a duplicate or
  missing row identity, order drift, a foreign row, any source/manifest/
  receipt hash mismatch, an incomplete receipt, or an unsupported shape
  (extra fields, non-canonical JSON, a pre-existing `row_index`).

| Derived input | Value |
|---|---|
| Path (outside Git and outside G:) | `F:\Project\xlm-selector-sweeps\essential-web-v4.1-m-analysis\m_derived_analysis_input.jsonl` |
| Bytes / rows | 13,838,037 / 4,096 |
| SHA-256 | `839e1296940154e70e91cc15c1059a7708d4f1833da329ebb6146739ef625676` |
| Row-identity digest | `3d164fa9fe5988e6743f4659a75e5a5f1c39143a9163b47d6cd96590fa059711` |

## Eight-condition counts on the M replicate

All eight conditions sum to 4,096; every crawl sums to 512; per-crawl totals
reconcile; a second pass over the rows with the evaluator's own kernels
reproduces every count. Precedence science, practical, prose is confirmed
row by row, so no row holds more than one final component.

| Condition | Science | Practical | Prose | Unassigned | Rejected |
|---|---:|---:|---:|---:|---:|
| A-normal | 17 | 117 | 374 | 50 | 3538 |
| A-strict | 7 | 68 | 344 | 38 | 3639 |
| B-normal | 24 | 117 | 372 | 45 | 3538 |
| B-strict | 11 | 68 | 342 | 36 | 3639 |
| C-normal | 24 | 46 | 127 | 361 | 3538 |
| C-strict | 11 | 30 | 111 | 305 | 3639 |
| D-normal | 56 | 257 | 717 | 118 | 2948 |
| D-strict | 11 | 68 | 342 | 36 | 3639 |

Mechanical invariants all hold (B-strict = D-strict on 4,096/4,096 rows,
strict ⊆ normal, B/C science identity, B preserved in D). These are
implementation checks, not evidence for any policy. M has no invalid row;
development had one (an invalid FDC code).

## Per-crawl counts on M (of 512)

B-normal and D-normal shown here; all eight conditions, with development
side by side, are in the generated tables.

| Condition / component | 2014-15 | 2015-32 | 2016-50 | 2018-05 | 2019-09 | 2021-04 | 2021-49 | 2024-26 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B-normal science | 5 | 0 | 4 | 1 | 1 | 3 | 5 | 5 |
| B-normal practical | 4 | 10 | 10 | 16 | 18 | 19 | 20 | 20 |
| B-normal prose | 39 | 35 | 31 | 49 | 47 | 63 | 65 | 43 |
| B-normal unassigned | 4 | 5 | 4 | 1 | 7 | 12 | 5 | 7 |
| B-normal rejected | 460 | 462 | 463 | 445 | 439 | 415 | 417 | 437 |
| B-strict science | 2 | 0 | 2 | 1 | 1 | 2 | 1 | 2 |
| B-strict practical | 3 | 7 | 3 | 9 | 11 | 9 | 12 | 14 |
| B-strict prose | 39 | 30 | 26 | 45 | 42 | 59 | 61 | 40 |
| D-normal science | 7 | 2 | 9 | 3 | 7 | 7 | 9 | 12 |
| D-normal practical | 16 | 40 | 48 | 26 | 33 | 30 | 33 | 31 |
| D-normal prose | 98 | 102 | 68 | 87 | 88 | 97 | 100 | 77 |
| D-normal rejected | 366 | 346 | 369 | 390 | 374 | 361 | 361 | 381 |

## Development versus M

Both replicates cover the same eight crawls from disjoint files. Counts are
development → M.

| Condition | Science | Practical | Prose | Unassigned | Rejected |
|---|---:|---:|---:|---:|---:|
| A-normal | 19 → 17 | 108 → 117 | 377 → 374 | 40 → 50 | 3552 → 3538 |
| A-strict | 12 → 7 | 62 → 68 | 334 → 344 | 28 → 38 | 3660 → 3639 |
| B-normal | 29 → 24 | 108 → 117 | 371 → 372 | 36 → 45 | 3552 → 3538 |
| B-strict | 20 → 11 | 62 → 68 | 329 → 342 | 25 → 36 | 3660 → 3639 |
| C-normal | 29 → 24 | 41 → 46 | 120 → 127 | 354 → 361 | 3552 → 3538 |
| C-strict | 20 → 11 | 22 → 30 | 102 → 111 | 292 → 305 | 3660 → 3639 |
| D-normal | 54 → 56 | 279 → 257 | 719 → 717 | 110 → 118 | 2934 → 2948 |
| D-strict | 20 → 11 | 62 → 68 | 329 → 342 | 25 → 36 | 3660 → 3639 |

- **Aggregate shares moved little.** Every aggregate component share is
  within 0.54 percentage points of development (largest: D-normal practical,
  6.81% → 6.27%). Rejected share: 86.72% → 86.38% normal, 89.36% → 88.84%
  strict, 71.63% → 71.97% D-normal.
- **Per-crawl cells move more than the aggregates.** The largest matched-crawl
  change is 5.86 points of 512 (D-normal rejected, 2018-05: 360 → 390); among
  assigned components it is 4.49 (D-normal practical, 2014-15: 39 → 16),
  4.30 (D-normal prose, 2018-05: 109 → 87) and 3.71 (B-strict prose,
  2014-15: 20 → 39).
- **Rejected and unassigned.** Rejected fell by 14 rows (normal) and 21
  (strict) and rose by 14 under D-normal. Unassigned rose in every condition
  (by 7 to 13 rows).

### Strict-versus-normal attrition (share of normal rows kept at strict)

| Policy | Component | Development | M |
|---|---|---:|---:|
| A | science | 12/19 = 63.2% | 7/17 = 41.2% |
| A | practical | 62/108 = 57.4% | 68/117 = 58.1% |
| A | prose | 334/377 = 88.6% | 344/374 = 92.0% |
| B | science | 20/29 = 69.0% | 11/24 = 45.8% |
| B | practical | 62/108 = 57.4% | 68/117 = 58.1% |
| B | prose | 329/371 = 88.7% | 342/372 = 91.9% |
| C | science | 20/29 = 69.0% | 11/24 = 45.8% |
| C | practical | 22/41 = 53.7% | 30/46 = 65.2% |
| C | prose | 102/120 = 85.0% | 111/127 = 87.4% |
| D | science | 20/54 = 37.0% | 11/56 = 19.6% |
| D | practical | 62/279 = 22.2% | 68/257 = 26.5% |
| D | prose | 329/719 = 45.8% | 342/717 = 47.7% |

Selected-total retention is 80.9% → 82.1% for B and 39.1% → 40.9% for D.
On M, B's normal-to-strict losses split by strict-gate reason as: science 10
English-only, 1 missing-content-only, 2 both; practical 41 / 5 / 3; prose
17 / 12 / 1. Strict loss is therefore not language loss alone. The same
split is unavailable for development (raw bundle absent).

### A, B, C, D descriptive differences (second minus first)

| Pair / tier | Replicate | Science | Practical | Prose | Unassigned | Rejected |
|---|---|---:|---:|---:|---:|---:|
| A → B normal | development | +10 | 0 | −6 | −4 | 0 |
| A → B normal | M | +7 | 0 | −2 | −5 | 0 |
| A → B strict | development | +8 | 0 | −5 | −3 | 0 |
| A → B strict | M | +4 | 0 | −2 | −2 | 0 |
| B → C normal | development | 0 | −67 | −251 | +318 | 0 |
| B → C normal | M | 0 | −71 | −245 | +316 | 0 |
| B → C strict | development | 0 | −40 | −227 | +267 | 0 |
| B → C strict | M | 0 | −38 | −231 | +269 | 0 |
| B → D normal | development | +25 | +171 | +348 | +74 | −618 |
| B → D normal | M | +32 | +140 | +345 | +73 | −590 |
| B → D strict | both | 0 | 0 | 0 | 0 | 0 |

- **A versus B.** The S61 rule adds 7 science rows on M (10 in development):
  2 moved from A-prose and 5 from A-unassigned. Nothing is removed.
- **B versus C.** Science is identical. On M, 71 practical and 245 prose rows
  become unassigned (67 and 251 in development). The 71 practical rows are 50
  explicit-genre and 21 Procedural-conditional; 32 of them sit in FDC 0xx.
- **B versus D.** On M, D-normal admits 590 rows that B rejects, all carrying
  the artifact label "Irrelevant Content": 32 science, 140 practical, 345
  prose, 73 unassigned (618 rows in development: 25 / 171 / 348 / 74). Every
  B-assigned row keeps its component under D. B-strict and D-strict are
  identical.

### Science, practical and prose stability

- **Science is sparse and the least stable.** B-normal 29 → 24, B-strict
  20 → 11. Every B science crawl cell is below 20 in both replicates, with
  one zero cell each (2016-50 in development, 2015-32 on M). The S61 share of
  B-normal science is 10/29 (34.5%) → 7/24 (29.2%); under D-normal it is
  22/54 → 24/56. No S61 > 0.5 flag fires in either replicate.
- **Practical.** B-normal 108 → 117, B-strict 62 → 68; six of eight crawl
  cells stay below 20 in both replicates. Explicit-branch share 73.1% → 70.1%.
  D-normal practical fell 279 → 257, concentrated in 2014-15 (39 → 16).
- **Prose.** B-normal 371 → 372, B-strict 329 → 342; per-crawl range 26–63 →
  31–65. The inherited twofold per-crawl retention note fires for B prose in
  both replicates (spread 2.42 → 2.10).

## Gate and composition drift

Sequential waterfall, rows surviving each stage (development → M):

| Gate (conditions) | Valid | English | Artifacts | Missing | Correctness | Doctype |
|---|---:|---:|---:|---:|---:|---:|
| GN (A/B/C normal) | 4095 → 4096 | 3125 → 3125 | 1439 → 1474 | 1351 → 1367 | 1348 → 1366 | 544 → 558 |
| GS (all strict) | 4095 → 4096 | 1911 → 1835 | 925 → 948 | 838 → 853 | 836 → 852 | 436 → 457 |
| GD (D normal) | 4095 → 4096 | 3125 → 3125 | 3107 → 3108 | 2600 → 2595 | 2590 → 2587 | 1162 → 1148 |

- **Dominant failure reasons on M (marginal, over 4,096 valid rows).** Under
  GN: doctype 2,557, artifacts 2,258, English 971, missing content 659,
  correctness 15. The most common failure sets are doctype alone (808),
  artifacts + doctype (636) and artifacts alone (600). Under GS the English
  failures rise to 2,261 and missing-content to 1,641. Under GD artifact
  failures fall to 36 and doctype alone accounts for 1,439.
- **Stage retention is close to development.** The largest changes in
  conditional stage retention are at strict: English −1.87 points, artifacts
  +3.26 points. The same inherited per-crawl spread flags fire in both
  replicates (artifacts and doctype under GN; artifacts, doctype and English
  under GS; doctype under GD).
- **Input composition is close.** Doctype shares move by at most 0.73 points
  (Personal Blog 6.57% → 7.30%); artifact labels by at most 0.27; knowledge
  labels by at most 0.90 (Procedural 10.52% → 9.62%); missing-content by at
  most 1.49 ("No missing content" 61.43% → 59.94%); correctness by at most
  1.64; FDC first digit by at most 1.75 (3xx 20.78% → 22.53%). English median
  0.892 → 0.887. Median metadata word count 320 → 310 (publisher words, not
  tokens).
- **Prose genre mix shifted more than the input did.** Within B-normal prose,
  News Article fell 205/371 (55.3%) → 174/372 (46.8%) and Personal Blog rose
  102/371 (27.5%) → 129/372 (34.7%). At strict the shift is about 10 points
  each way (57.8% → 47.7%, 25.8% → 36.0%). Under D-normal it is smaller
  (53.1% → 50.8%, 27.1% → 29.0%).

## Instability and warnings

1. Science counts are small. B-strict science fell from 20 to 11 and strict
   retention of science fell from 69% to 46%; with cells this small, and
   clustered windows, this is an observation, not an estimate of a rate.
2. Prose totals are stable but their genre composition is not: the News /
   Personal Blog balance moved by 7 to 10 points while the input doctype mix
   moved by less than 1 point.
3. D-normal practical is the largest aggregate move (−22 rows), driven by one
   crawl window.
4. Single-crawl cells move by up to about 4 to 6 points between replicates,
   so per-crawl comparisons carry much more noise than the aggregates.
5. Development figures cannot be re-derived here: the raw development bundle
   is absent, so row transition matrices, gate failure sets and the
   strict-loss split exist for M only.
6. The protocol's Arm-T census assertions (29 B-normal science, 25 D-only
   science) describe the development sample. M has 24 and 32. These are
   different replicates; nothing in T selection was recomputed.

## Statistical reporting

Descriptive only. Both replicates are one contiguous 512-row window per
crawl and file: clustered, not iid. Protocol section 5 forbids binomial iid
confidence intervals, eight-window bootstrap confidence claims and
significance claims, so none is reported and no hypothesis test was added.
Uncertainty is shown as per-crawl ranges, spreads and matched-crawl
differences. Shares on a zero denominator are reported as undefined. No
threshold was examined for tuning; the sensitivity cells are the inherited
diagnostic grid only.

## Seal

`m_seal.json` binds the M source, M manifest and Phase-D receipt hashes, the
preparation manifest, the adapter version and code hash, the derived input
hash and row-identity and metadata digests, the evaluator and policy hashes,
the analysis code hashes, the development manifest and all eight development
artifacts, every result file (14 files, via `artifact_manifest.json`), the
exact run command and the environment. Changing any of these changes the
digest. `verify` recomputes the whole package from the bound inputs and
matched it byte for byte, twice.

The seal holds no Arm-T text, label, review ID or mapping, and the code
refuses any `t_*` or `sealed/` path. One disclosure: the adapter parses
`phase_d_receipt.json` to confirm arm M is COMPLETE and that the M hashes
match; that receipt also lists Arm-T output hashes and status counts. No T
file was opened and no T content was read.

After this seal the M analysis must not change in response to T review. The
`run` command refuses to overwrite an existing seal. `COMMANDS.md`, the logs
and this report sit outside the seal and cite it.

## Requirement ledger

| Requirement | Status |
|---|---|
| Read-only adapter, 4,096 rows, order, identity, metadata preserved | IMPLEMENTED, VERIFIED (real M output and synthetic tests) |
| Adapter refusals (count, duplicate, missing, order, hash, shape) | IMPLEMENTED, VERIFIED (synthetic tests) |
| No invented legacy receipts | VERIFIED (synthetic test; frozen loader refuses the derived view) |
| Frozen evaluator and policy unchanged | VERIFIED (hashes; 60 frozen-evaluator regressions pass) |
| Adapter path gives the same results as the legacy input contract | VERIFIED (synthetic fixture, byte-identical evaluator payloads) |
| Frozen development artifacts | VERIFIED by hash; re-evaluation BLOCKED (raw bundle absent) |
| Eight conditions, per-crawl counts, conservation, no overlap | VERIFIED (real M output) |
| Development-versus-M comparison, protocol section 5 tables | IMPLEMENTED, VERIFIED for M; development row-level tables NOT AVAILABLE |
| Confidence intervals / hypothesis tests | OUT OF SCOPE (forbidden by the frozen contract) |
| Sealed M package and seal verification | IMPLEMENTED, VERIFIED (real run; verify exit 0 twice) |
| Both G: roots and development directory unchanged | VERIFIED (stat inventories identical before/after) |
| Ruff check, ruff format | VERIFIED |
| Strict mypy | VERIFIED with the interpreted runner; compiled mypy BLOCKED by application control |
| Fast and full offline selections, CUDA, network tests | NOT RUN |
| Measured process peak memory | NOT MEASURED (one endpoint RSS sample: 98,328,576 bytes) |
| T blinded package, human review, unblinding, selector decision | NOT RUN / next stage |

Measured resources for the real run: 4.937 s wall, of which 2.703 s in the
evaluator; 1,284,049 bytes of sealed outputs in Git; 13,838,037 bytes of
derived input outside Git.

## Exact next operator action

Submit this prompt for the next stage:

> Materialize the blinded Arm-T reviewer package from
> ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RESULT-REVIEW/t-analysis-preparation.json
> in an access-controlled directory outside Git and outside both G: roots.
> First confirm the M seal with
> `scripts/essential_web_m_analysis.py verify` (digest
> `afc972ccf570a5587bf7bd3b6b8d1d728a09f2230c49f1b0a19c1c74cda0b9bc`) and do
> not alter any M artifact. Reuse an existing matching custodian key if one
> is held, otherwise generate one 32-byte key in a restricted location; keep
> the frozen `ew2-` HMAC IDs, namespace `essential-web-evidence-v2.0`, order
> seed 20260928, separate reviewer-1/reviewer-2 orders and rubric
> `essential-web-evidence-v2.0-rubric-1`. Supply the 117 reviewable texts to
> the package builder; keep the oversized entry in the 118-entry master
> ledger with no text. Reviewer material carries opaque IDs, escaped full
> texts, the rubric and blank forms only. No network, acquisition, labels,
> unblinding, M changes, selector decision or push.

**M SELECTOR EVIDENCE SEALED — READY FOR T BLINDED PACKAGE**
