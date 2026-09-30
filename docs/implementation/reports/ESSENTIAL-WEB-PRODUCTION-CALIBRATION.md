# Essential-Web production calibration — sealed (2026-09-30)

**CALIBRATION SEALED.** The live 16,384-row production calibration was recomputed
offline from its executed artifacts. Every expected value reproduced. Seal digest
`149f3eb48e047e6545314dcb40cc01fe2b0e8853898a83a2060f0e8c9032e2a1`.

No network, fetch, selector change, quota change or push. No document text is in
Git: the evidence holds hashes, counts and sizes only. Exact commands and exit
statuses are in
[COMMANDS.md](../evidence/ESSENTIAL-WEB-PRODUCTION-CALIBRATION/COMMANDS.md).

Starting HEAD `b2e13840b92483a7483c157cf5dec536d2d44c8d`, branch
`data/mix01-ultrax-6b`. `c643f2e`, `5fb37c9` and `b2e1384` are ancestors (each
`merge-base --is-ancestor` exit 0). The pre-existing dirty state (modified
`STATUS.md`, seven untracked review paths, three untracked basetemp directories)
is preserved and excluded from the commits.

## What the seal verified

Source: `G:\XLM\calib\essential-web-production\calibration` (real, live evidence).

| Check | Result |
|---|---|
| `measurement.json` recomputed from plans, journals, raw records and adaptation outputs | identical |
| Eight executed plans against the eight frozen plans | same behavioral hash |
| Eight raw files against journal digests and the published raw artifacts | identical |
| 24 adaptation summaries against their documents and rejection ledgers | bound |
| Current adapter code re-run offline on the eight raw files, three views | byte-identical documents and ledgers, 24 of 24 |
| Calibration freeze digest `a6cab8cd…89b0` | reproduces |
| Stored probe evidence and operator decision, three views, through the C04 gate | admitted |

The re-run matters because the adapter changed at `5fb37c9`: the code at this
HEAD (`mix01_adapters.py` SHA-256 `3651ff2a…7ef6`) produces exactly the
calibration outputs.

Bound identities, all in
[calibration-seal.json](../evidence/ESSENTIAL-WEB-PRODUCTION-CALIBRATION/calibration-seal.json):

- `measurement.json` SHA-256
  `75b0b95997701e33ba60cd070eaa8e6f1bc7ea9a4e6ad3e802bed1baeb1963cb` (11,093 bytes).
- Per unit: plan hash, executed and frozen plan file hashes, selection hash,
  authorization, journal hash, source ETag and length, raw SHA-256 and size,
  published raw artifact content hash and receipt hash.
- Per unit and view: adaptation summary, documents and rejection-ledger hashes,
  accepted and rejected counts, rejection counts by code, canonical bytes.
- Adapter `essential_web_bnormal` with the SHA-256 of four code files.
- Selector `essential-web-b-normal`, condition B-normal, freeze
  `essential-web-selector-fasttrack-v1`, freeze digest `c6f32a65…0a0c`, policy
  `f4357f61…dd07`, evaluator `5a63e785…bf9c`.
- Source `EssentialAI/essential-web-v1.0` at
  `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d`.
- Admission: decision artifacts `admission_essential_web_<view>.attempt02`
  (contract `c04-benchmark-risk-v2`, `suspect_with_mitigation`), their record
  hashes, probe evidence artifacts and fingerprints.

## Measured facts

Counted directly in the artifacts.

| Quantity | Value |
|---|---:|
| Crawls / files / input rows | 8 / 8 / 16,384 (2,048 per file) |
| Physical response-body bytes | 110,070,226 |
| Decompressed bytes | 94,613,662 |
| Requests | 848 (106 per file) |
| Raw selected-record bytes | 149,979,195 |
| Largest raw record | 540,293 bytes |
| Journal wall seconds, including restart downtime | 301.275739 |
| Malformed rows | 8 |
| Selector: science / practical / prose | 101 / 434 / 1,495 |
| Selector: rejected / unassigned | 14,214 / 140 |
| Retained documents / canonical bytes | 2,030 / 10,462,526 |

| Component | Documents | Canonical bytes | Characters |
|---|---:|---:|---:|
| essential_science | 101 | 671,387 | 667,124 |
| essential_practical | 434 | 1,900,850 | 1,885,481 |
| essential_prose | 1,495 | 7,890,289 | 7,799,962 |

Process peak memory per fetch was 137.8–146.3 MB (performance sidecars, one
worker). Canonical document files total 14,418,258 bytes and rejection ledgers
26,318,331 bytes.

## Deterministic arithmetic

Exact fractions are in
[calibration-yields.json](../evidence/ESSENTIAL-WEB-PRODUCTION-CALIBRATION/calibration-yields.json).
Estimated tokens are canonical bytes divided by an **assumed** 4 bytes per token
(3 and 5 as the range). They are not training-token counts.

| Per component | science | practical | prose |
|---|---:|---:|---:|
| Documents per input row | 0.0061646 | 0.0264893 | 0.0912476 |
| Canonical bytes per input row | 40.978 | 116.019 | 481.585 |
| Estimated tokens per input row (4 B/token) | **10.244553** | 29.004669 | 120.396255 |
| — at 3 / 5 B/token | 13.659 / 8.196 | 38.673 / 23.204 | 160.528 / 96.317 |
| Canonical bytes per document | 6,647.4 | 4,379.8 | 5,277.8 |
| Estimated tokens per document | 1,661.85 | 1,094.96 | 1,319.45 |
| Input rows per retained document | 162.218 | 37.751 | 10.959 |
| Prefix transfer bytes per estimated token | 655.778 | 231.623 | 55.800 |

Shared per input row: 6,718.153 transfer bytes, 5,774.760 decompressed bytes,
0.018388 seconds. These three are **prefix-window** costs, not full-file costs.

Malformed rate: 8 / 16,384 = 1 / 2,048 = 0.0488%.

## Extrapolations and assumptions

Everything below multiplies the pooled calibration yield. None of it is measured.

Rows each component needs alone for its frozen first-pass target, at 4 B/token:

| Component | First-pass target | Required input rows |
|---|---:|---:|
| essential_science | 660,000,000 | **64,424,483** |
| essential_practical | 660,000,000 | 22,754,957 |
| essential_prose | 330,000,000 | 2,740,950 |

Science is the bottleneck. Scanning 64,424,483 rows would be expected to yield
about 1.869 B estimated practical tokens (2.83 times its target) and 7.756 B
estimated prose tokens (23.5 times). The quotas are unchanged by this.

Assumptions carried by these numbers: 4 bytes per token; yield linear in scanned
rows; the eight prefix windows represent the 23,200 inventory files.

## Crawl dispersion

The calibration is eight clusters: one crawl, one file, one 2,048-row prefix
each. The rows are not independent, so no confidence interval is reported.
[crawl-yields.json](../evidence/ESSENTIAL-WEB-PRODUCTION-CALIBRATION/crawl-yields.json)
holds the table.

| Crawl | Sci docs | Sci bytes | Sci est. tok/row | Prac docs | Prac tok/row | Prose docs | Prose tok/row | Malformed | Transfer bytes | Seconds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2014-15 | 6 | 41,582 | 5.076 | 27 | 19.09 | 152 | 137.99 | 0 | 13,705,877 | 34.42 |
| 2015-32 | 9 | 47,531 | 5.802 | 34 | 23.78 | 162 | 92.60 | 0 | 13,667,949 | 37.80 |
| 2016-50 | 11 | 75,927 | 9.268 | 50 | 20.53 | 162 | 99.88 | 0 | 13,650,333 | 36.73 |
| 2018-05 | 10 | 60,515 | 7.387 | 58 | 33.70 | 198 | 85.38 | 4 | 13,792,025 | 37.15 |
| 2019-09 | 3 | 90,392 | 11.034 | 61 | 27.96 | 200 | 128.03 | 2 | 13,775,155 | 38.74 |
| 2021-04 | 16 | 120,154 | 14.667 | 61 | 29.01 | 199 | 118.67 | 2 | 13,806,866 | 36.24 |
| 2021-49 | 26 | 153,928 | 18.790 | 83 | 43.39 | 213 | 110.41 | 0 | 13,857,049 | 40.20 |
| 2024-26 | 20 | 81,358 | 9.931 | 60 | 34.58 | 209 | 190.22 | 0 | 13,814,972 | 39.99 |

Every crawl made 106 requests. Spread, largest over smallest:

- science documents 3 to 26 (8.7 times); science estimated tokens per row 5.08
  to 18.79 (3.7 times);
- practical tokens per row 19.09 to 43.39 (2.3 times); prose 85.38 to 190.22
  (2.2 times);
- transfer 13.65 to 13.86 MB (1.015 times).

Science yield depends on document length as much as on document count: 2019-09
has three science documents and the third-highest science bytes.

Leaving one crawl out at a time moves the pooled science requirement from
64,424,483 rows to between 60,093,267 and 73,140,172 (−6.7% to +13.5%). This is
a sensitivity description, not a bound.

## Malformed rows

8 of 16,384, in three crawls (2018-05: 4, 2019-09: 2, 2021-04: 2). The frozen
policy stops a pass above 1% after 100 rows, or on the third malformed row
before that. Each file pass had at most 4 of 2,048 (0.195%). No pass stopped.

Reason codes (counts only; the ledgers hold no record text):

| Reason code | Rows |
|---|---:|
| `essential_web_selector_value:unknown_label:k` | 5 |
| `essential_web_selector_value:invalid_fdc_syntax` | 2 |
| both | 1 |

`unknown_label:k` is a knowledge-domain label outside the frozen label set; it
occurs in 6 of the 8 rows. `invalid_fdc_syntax` is the known non-decimal
classification code. Both are evaluator validity failures that fail closed by
design. The renderer incompatibility repaired at `5fb37c9` does not recur. The
threshold is unchanged.

## Requirement ledger

| Requirement | Status |
|---|---|
| Recompute and seal the calibration from real artifacts | VERIFIED |
| Bind measurement, plans, raw, adaptation, selector, adapter, admission, freeze, revision | VERIFIED |
| Exact component yields | VERIFIED (arithmetic) |
| Per-crawl dispersion without an interval claim | VERIFIED |
| Malformed rate and reason counts | VERIFIED |
| Full-file cost, capacity and campaign | see [bulk plan](ESSENTIAL-WEB-BULK-ACQUISITION-PLAN.md) |
| Exact training-token counts | OUT OF SCOPE (tokenizer not frozen) |
| Full and fast repository test selections, CUDA | NOT RUN |

Limits: eight clustered prefix windows; peak memory measured only on prefix
windows; elapsed time includes restart downtime.

## Next

Re-verify at any time, offline, from the repository root:

```powershell
. .\scripts\operator_storage.ps1
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/essential_web_calibration_seal.py --root G:/XLM/calib/essential-web-production/calibration --freeze docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS --xlm-home G:/XLM/xlm-home --work-dir G:/XLM/temp/ew-seal-work --output-dir docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-CALIBRATION --verify
```

Then the [bulk acquisition plan](ESSENTIAL-WEB-BULK-ACQUISITION-PLAN.md).
