# Essential-Web v4.1 Phase-D post-live result review

**PHASE-D RESULT REVIEW PASSED — READY FOR SELECTOR EVIDENCE ANALYSIS**

2026-09-30, HEAD `4ec64b856c2f0c41aa8e7480c276d6d5e57841e8`, branch
`data/mix01-ultrax-6b`. The real Phase-D root is **COMPLETE / COMPLETE**, with
null stop reason. All operator-reported totals independently reconcile. Both
arms satisfy their frozen completion conditions; no selector decision or
human scientific judgment follows from acquisition completion.

Read-only sources:
`G:\Project\xlm-evidence-v4.1\essential-web` (Phase P) and
`G:\Project\xlm-evidence-v4.1\essential-web-phase-d` (Phase D).
No network, Phase-D rerun, new acquisition, G: mutation, scientific reselection,
human review, source implementation edit or push. Selected text was processed
in memory for mechanical length/hash checks only, never printed or copied into
Git. No Parquet re-decoding or unselected document inspection was performed.

[Evidence directory](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RESULT-REVIEW/),
[machine-readable result](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RESULT-REVIEW/review-result.json),
[exact commands and run history](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RESULT-REVIEW/COMMANDS.md),
[analysis preparation](../evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RESULT-REVIEW/ANALYSIS-PREPARATION.md).

## Identity and request reconciliation

Independent canonical JSON self-digests, raw SHA-256s, the repository verifier
and parent verifier agree. The authorized identities are unchanged:

| Binding | Verified value |
|---|---|
| Protocol | `bb2bca6f571c9e02b02536bc518cdc1b61fdf0f56b0c6970eaa257c8d198769f` |
| Freeze | `9c4612fe860ef49c26ff09b25f8fdaf00f0a7015d7eab4fd7cfe28a0e1675228` |
| Plan | `23a26ffc87d27e4c8fa5de86d7ec446c260bfd65442dc5b1dfd2651cd62222b7` |
| Selection | `975ba3dee4af0598e665ea05c69bbe49336c3e3a35fca777a863073190b78474` |
| Source revision | `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d` |
| Selector policy | `f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07` |
| Scientific namespace | `essential-web-evidence-v2.0` |

| Derived from SQLite, receipts and retained bodies | M | T | Total |
|---|---:|---:|---:|
| Exact authorized operations, all COMPLETE | 8 | 47 | 55 |
| Physical attempts | 16 | 94 | 110 |
| REDIRECT / SUCCESS | 8 / 8 | 47 / 47 | 55 / 55 |
| Response-body bytes | 11,701,282 | 180,014,609 | 191,715,891 |
| Retained compressed payload bytes | 11,692,530 | 179,963,169 | 191,655,699 |
| Retained redirect-body bytes | 8,752 | 51,440 | 60,192 |

Every SQLite operation matches the frozen operation ID, order, file, arm,
ordinal, kind and inclusive range. Every attempt has that same range; no
additional range, retry or hidden operation is present. Each chain is one
HTTPS `huggingface.co` 302 followed by one `us.aws.cdn.hf.co` 206, hops 0/1,
try 1. Attempt IDs are exactly 1–110. Origin paths match the frozen revision
and file. Every retained redirect and success body reproduces its recorded
byte length and SHA-256. Success body lengths equal the authorized ranges.
All per-response/per-arm operational byte and attempt caps hold.

`request_receipt.jsonl` equals the entire SQLite attempt table; receipt
operations equal the operation/output tables. SQLite was opened only with
`mode=ro&immutable=1` and `query_only=ON`; integrity and foreign-key checks
pass. No journal/WAL/SHM existed to reconcile. No mutable Store was opened.

## M output

**VERIFIED: 4,096 rows, exactly 512 for each of eight frozen files.** The
ordered identities reproduce every frozen window row; none is missing,
duplicated, reordered or foreign. Every provenance record matches repository,
revision, source file, absolute row, row group, row within group and ordinal.
Top-level fields are exactly `eai_taxonomy`, `quality_signals` and
`_xlm_acquisition`. Each per-file record hash, count, projected chunk count,
footer reference, payload binding and aggregate output hash agrees with the
M manifest and decoded-output table. No policies were evaluated here.

M JSONL: 13,571,797 bytes, SHA-256
`4d268f8d4ac3fcfe6cfea6184c8bcce5b7cb797dc1c627ca83a91371e3070e02`.

## T output, retention and isolation

**VERIFIED: all 118 frozen locators represented exactly once**, in the plan's
order, with matching row/group/ordinal and sealed provenance. Status counts:
117 `full_text_available`, one `unreviewable_full_document_due_to_size`, zero
`unreviewable_missing_or_invalid_text`. Each selected full text strictly
encodes as UTF-8 and reproduces its stored byte count and SHA-256.

- Maximum retained document: **38,893 bytes**, below 65,536.
- Total and unique retained text: **671,351 bytes**, below 8,388,608.
- Distinct retained full-text hashes: **117**; no exact-text deduplication needed.
- Oversized entry: recorded full length **109,974 bytes**; `text=null` and
  `sha256=null`. Its locator/status/length and acquired operations agree with
  sealed provenance. No excerpt or silently truncated text is retained.

The oversized full length is cross-checked between retained records, not
independently re-decoded from compressed source. This is output/result
validation under the reviewed decoder, not a second acquisition or decode run.

The complete root inventory has exactly the expected payloads, redirect bodies,
SQLite and eight exports; no unexpected output or log file exists. Export
field sets, selected identities and sealed provenance contain no extra document
records. No unselected text is present in the audited decoded exports. Raw
compressed payloads necessarily contain other rows, as permitted by the frozen
protocol; they were hash-checked only. External terminal logs were not supplied,
so no claim is made about unprovided files outside the evidence roots.

T JSONL: 720,801 bytes, SHA-256
`83491f1539713d6ecf5a0d19cfd39a5e52a82fa0e12a45acc82906afc1183b73`.
Sealed provenance: 65,766 bytes, SHA-256
`8d786ba8d4887c9b15085adde598edc708d265a7ef43b7a46c04047afb4117c1`.

## Membership, parent, storage and COMPLETE qualification

The original selection manifest is byte-identical to its frozen hash
`8424f9668fef6952a558ead0dae27a705545a7f4a4d302713707d3ec6bb1af27`.
Its full strata/census structure remains bound unchanged; all 118 identities
equal the T plan/output set. M windows equal the adopted sample and use files
disjoint from T's development files. The policy YAML independently reproduces
the frozen canonical digest. No census, ownership, threshold or selection changed.

All **30 Phase-P parent artifacts** still match their frozen lengths/hashes;
the canonical verifier also checks JSON digests, COMPLETE binding and layouts.
Both roots' complete before/after/final inventories preserve every file's
size, mtime and SHA-256, plus directory metadata. Phase P has 86 files and
3,241,704 bytes. Phase D has **119 files and 206,319,966 bytes**, below the
**1,073,741,824-byte cap**, with 867,421,858 bytes of headroom. This is the
actual final whole-root measurement, not simulated occupancy or live peak usage.

All **117 manifest entries** reproduce their bytes and hashes. The manifest's
own self-digest and receipt binding pass; all five decoded-output rows agree
with actual files and receipt bindings. SQLite and the manifest itself are
additionally hash-bound by this review inventory. The final receipt and
manifest file SHA-256s are respectively:

- `7e8c37e183d546cb43c5cc3deae1c8b6267969500e8af025ceeba67c149320e3`
- `686b8aabe2a54ac3630173e0c96d829b47a258c4387aa026f277fdb78f68f95e`

**COMPLETE is justified:** both arms complete, exact M rows/T locators with
terminal statuses, intact parent, required exports and hashes consistent,
and actual root below the cap. COMPLETE means bounded acquisition/decoding
finished; it does not mean 118 reviewable texts or an approved selector.

## Analysis-preparation package

Compact, text-free, **dry custodian preparation** is ready. The M manifest binds
the new rows/windows, exact frozen evaluator blob, policy, comparison contract
and all eight corrected development artifacts at
`F:\Project\xlm-selector-sweeps\essential-web-v2`. That location differs from
the historical G: path, but every artifact and the manifest reproduce the exact
frozen hashes. The historical X: raw development bundle is unavailable here;
its original identity remains bound by the verified corrected sweep manifest.

The preparation explicitly records an input-format bridge: Phase D uses
`_xlm_acquisition.row`; the existing sweep CLI expects `row_index` and legacy
bundle/execution receipts. The next analysis must add a checked derived-input
adapter while reusing the exact frozen evaluator; it must not fabricate old
receipts, edit raw evidence, change metadata or alter policy semantics.

T preparation binds all **118 entries**, with hashes/provenance only, and
preserves the 117-reviewable/one-oversized accounting. It retains the frozen
`ew2-` HMAC namespace, review-order seed 20260928, reviewer-1/reviewer-2 ordering,
rubric and blinding exclusions. Existing matching K/IDs/orders, if held elsewhere
by the custodian, must be reused; none exists in the validated source roots.
No K, review IDs, ordered ID lists or reviewer text package was generated.
The oversized entry remains in the master ledger, never supplied to the helper
as a reviewable null-text document and never silently dropped.

**Not yet a distributable reviewer package.** Materialize it later in a separate
access-controlled external directory. No secret, text, source-to-review-ID map,
selector ownership/category, B-vs-D status or hypothesis condition belongs in
reviewer-facing material or Git. M analysis must be sealed before T unblinding.
No labels or final selector decision were made.

## Commands, evidence and limitations

Windows 11 build 26200; CPython 3.12.13; SQLite 3.53.1; uv 0.12.19.
All Python commands used `uv run --offline --locked --no-sync --extra cpu
--extra eval`. Final real audit: exit **0**, internal wall **0.969 s**, measured
process peak working set **121,655,296 bytes**. These are review-process
measurements, not live acquisition memory or decoder performance.

SQLite timestamps span 60.424430 seconds and the longest recorded physical
attempt is 1.817644 seconds. These are receipt-derived durations, not a new
benchmark. Live peak memory/scratch/free-space history and raw response headers
were not recorded by this review; the inherited header-retention limitation
remains. No post-live read-only audit reconstructs a historical disk peak.

The first two audit invocations exited 1 due to a reviewer schema assumption:
redirect attempts store empty-string errors, whereas successes store null.
The final check requires each exact convention. The M output-binding shape
was also corrected against the serializer before reaching that check. No source
artifact or production assertion changed. After successful reconciliation,
additional field/file allowlists and per-file binding checks were added and
the complete read-only audit rerun successfully. All logs are retained.

Nine authored synthetic negative/control tests for the review parsers and
bindings pass (exit 0, final 0.17 s). Ruff check and format check pass on the
four review-only Python files after formatting/import fixes. Production tests,
full offline acceptance, CUDA and mypy were NOT RUN: production code is unchanged.

| Requirement | Status |
|---|---|
| Exact real plan, ranges, attempts and bytes | VERIFIED |
| M rows/projection/windows and output bindings | VERIFIED (real outputs) |
| T membership, statuses, retained sizes and provenance | VERIFIED (real outputs) |
| Scientific membership/strata/census/policy unchanged | VERIFIED |
| Parent binding and both roots' immutability | VERIFIED |
| Final actual root cap and COMPLETE qualification | VERIFIED |
| Compact M/T preparation manifests | IMPLEMENTED, VERIFIED (dry; text-free) |
| Derived M input adapter; materialized blinded T package | NOT RUN / next stage |
| Human review, selector analysis/decision, new acquisition | NOT RUN |
| Historical live peak resources; external unsupplied logs | NOT MEASURED / NOT VERIFIED |
| New protocol, reselection, production changes | OUT OF SCOPE |

Only new review artifacts/report and a prefix to existing STATUS are changed.
Prior user changes are preserved. No review commit; HEAD unchanged and index empty.

## Exact next operator action

Submit this prompt for the next stage:

> Prepare and run the frozen M selector-evidence analysis using
> ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-RESULT-REVIEW/m-analysis-preparation.json.
> First implement and test a bounded read-only Phase-D input adapter that maps
> the existing absolute row identity to the frozen evaluator's input contract,
> preserving all 4,096 records, metadata, order and original hash bindings.
> Reuse unchanged A/B/C/D normal/strict policies and evaluator semantics; compare
> with the hash-verified development artifacts using protocol section 5, and
> seal the M report before T unblinding. Preserve the prepared 118-entry T set
> and frozen blinding namespace/order for later external package materialization.
> No network, acquisition, evidence-root mutation, human labels, reselection,
> final selector decision or push.

**PHASE-D RESULT REVIEW PASSED — READY FOR SELECTOR EVIDENCE ANALYSIS**
