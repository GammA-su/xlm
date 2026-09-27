# Essential-Web evidence-v2 frozen protocol

Version **essential-web-evidence-v2.0**, frozen 2026-09-27. Verdict:
**READY TO IMPLEMENT EVIDENCE V2**. This freezes a scientific protocol,
not a selector or an acquisition authorization. No new evidence was acquired.
This document is normative; its exact bytes and the inventory receipt are
bound by `../evidence/ESSENTIAL-WEB-EVIDENCE-V2/freeze.json`. Any behavioral
change requires a new protocol version, digest, rationale, and authorization.

## 1. Scope, evidence, and identity

Read the corrected nine v2 artifacts directly from
`G:\Project\xlm-selector-sweeps\essential-web-v2`, the scientific sweep
review, recon/policy reports, `CONTRACTS.md`, and existing acquisition and
adapter code. Source checkout: `dabf8e7c6a2838062a4d427d35a489ea7e694482`.
Preserve earlier user edits and reports. No source, selector, dependency,
tokenizer, training, admission, X: write, network, text inspection, or push.

| Binding | Frozen value |
|---|---|
| Repository | `EssentialAI/essential-web-v1.0` |
| Revision | `ce4eccc7e9604667b6d7f32cb6274b8b41f3113d` |
| Development records | 4096 |
| Combined metadata SHA-256 | `42e07c350d849c608ef202348971e3f7ea48aa80775d413a39dd3373628c58e1` |
| Development execution digest | `5065bcf6001ee38b63ca783f20a625af0256f2788ce9a0034edfba34c125bc23` |
| Development bundle digest | `ac1c13b656d9dc3bc506726bfe3f1db925444efb899733052496be0425c58eb3` |
| Development discovery digest | `c8d448fafb10b524496e6fd51bcde23f87c19c95b68dffe02f293d96be7530a7` |
| Policy spec version | `essential-web-selector-sweep-v1` |
| Policy canonical digest | `f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07` |
| Corrected sweep tool/report version | 2 / 2 |
| Corrected sweep manifest canonical digest | `e13c9c98efdf07fcc7c1375c4c8b62d918d77aa39c158171aa090c8695ac4087` |
| Actual sweep manifest FILE SHA-256 | `0d550a2d871f737c8bc46f82a8de38fd817b50bb1bffee46f2c3c07a129e26bc` |

The request's manifest file hash omitted one `b` and has 63 characters:
`0d550a2d871f737c8bc46f82a8de38fd817b50bb1ffee46f2c3c07a129e26bc`.
The table records the directly measured 64-character value. The canonical
digest and all eight artifact byte lengths/hashes agree with the manifest.
This is an input transcription correction, not a change to a sweep artifact.
All eight assignment vectors equal the request. No v1 replay or new selector
audit is claimed here. Reporting fixes are accepted; no implementation
question is reopened. The publisher word count is not a token estimate.

## 2. Canonical construction and freeze stages

Define `C(x)` as UTF-8 bytes of Python 3.12
`json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
allow_nan=False)`, without BOM or trailing newline. Reject duplicate object
keys, non-finite numbers, invalid Unicode and unknown fields. `H(x)` is the
lowercase SHA-256 hex digest of `C(x)`. New selection keys use arrays with
explicit field order, JSON integers, and exact case-sensitive POSIX paths;
no path, Unicode, newline, or case normalization. No Python `hash()` or PRNG
library-dependent shuffle. New receipts self-digest the object excluding
only the top-level `digest`; byte hashes separately bind serialized files.
Existing receipt/plan hashes retain their native versioned conventions.

Freeze stages, all recorded with parent digests and timestamps:

1. This protocol plus complete path-inventory commitment and eight selected
   files, before inspecting any new footer (DONE offline).
2. Implement and synthetic-test the bounded mechanisms; generate the exact
   development text locator selection offline; freeze it before any text.
3. Obtain separate authorization for named-file footer planning, if needed;
   freeze eight metadata windows and exact physical plans before data fetch.
4. Obtain execution authorization bound to those plan hashes, limits, and
   protocol digest. No permission in this task covers stages 3 or 4.

Unknown physical costs remain unknown until stage 3; a cap is not a measured
estimate. Insufficient cost evidence means refusal, never assumed zero.

## 3. Arm M: files and complete inventory

Metadata seed: **20260927**. It is new relative to 20260918, selected before
new file contents/footers; it also matches the earlier review's proposed
new-window seed. Do not reroll it. Retain the eight exact development crawls,
not a new selection of eight from the 101 original crawl directories.

Offline discovery is possible here: for each crawl with N files, enumerate
`data/<crawl>/train-{i:05d}-of-{N:05d}.parquet` for integer `0 <= i < N`.
The canonical hash of each sorted full list exactly reproduces its existing
discovery `listing_sha256`. Thus this is hash-verified reconstruction of
the original complete path inventory, not an invented list inferred solely
from a naming convention. No new listing request or footer was made.

Exclude **all eight development files** from every candidate set. Eligible
means a listed Parquet path in the fixed crawl, not a file passing a content,
size, label, science, footer, or availability test. Rank each candidate by:

```
H(["essential-web-evidence-v2.0", "metadata-file", 20260927,
   "EssentialAI/essential-web-v1.0", revision, crawl, source_file])
```

Choose the lowest digest, breaking hash ties by ascending UTF-8 path bytes.
Exactly one file per crawl, without replacement. An unavailable selected
file is a STOP, not permission to choose the next rank. Path inventory
membership is bound to the immutable revision; sizes and object IDs for new
files have NOT been established and must be obtained in authorized planning.

| Crawl (`crawl=CC-MAIN-` prefix) | N | Excluded development basename | Frozen new basename |
|---|---:|---|---|
| 2014-15 | 2772 | train-01787-of-02772.parquet | train-01860-of-02772.parquet |
| 2015-32 | 1920 | train-01486-of-01920.parquet | train-01682-of-01920.parquet |
| 2016-50 | 3132 | train-00454-of-03132.parquet | train-02156-of-03132.parquet |
| 2018-05 | 3429 | train-01826-of-03429.parquet | train-03378-of-03429.parquet |
| 2019-09 | 2577 | train-01825-of-02577.parquet | train-00153-of-02577.parquet |
| 2021-04 | 3315 | train-00297-of-03315.parquet | train-00179-of-03315.parquet |
| 2021-49 | 2895 | train-02221-of-02895.parquet | train-00408-of-02895.parquet |
| 2024-26 | 3168 | train-02037-of-03168.parquet | train-01127-of-03168.parquet |

Full paths prepend `data/crawl=CC-MAIN-<crawl>/`. The complete **23,200-path
eligible** inventory digest is
`d2b3eac5dca002336a97654564a13c93e8e4696e6a1624a6fdf3c012fd0927d5`.
Its canonical object is `{repository, revision, strata}`, with `strata` in
table order, each object `{crawl, files}`, and `files` the sorted eligible
full paths. The compact, losslessly reconstructible
[inventory receipt](../evidence/ESSENTIAL-WEB-EVIDENCE-V2/inventory-freeze.json)
contains all templates, ranges, original and eligible hashes, exclusions,
selected paths and ranking hashes. Its self-digest is
`466ece976bfadfbd024c7fb66ff0076f1d86f36ce7d362331d85f1fee36c018f`.
Future implementation expands and verifies it offline; any discrepancy
stops. It must not silently refresh the inventory against a moving source.

## 4. Arm M: exact windows and limits

Reuse `plan_sample_windows` window-v2 semantics in
`src/xlm/data/acquisition/sampling.py` and `ParquetWindowDecode` in
`plan.py`. Source ID `essential_web`, view ID `selector_recon`, pinned
revision above, seed 20260927, one explicit file per invocation. Each of
eight independent plans has block/target/max records **512**. Never form
one 4096-record acquisition plan. Projection is exactly, in this order,
`["eai_taxonomy", "quality_signals"]`; provenance locators are generated
by acquisition, not additional upstream columns. No text/id/pid/metadata.

For each selected file, consider physical row groups in ascending index.
The unchanged v2 eligibility check rejects unsupported projections, bad
chunk/schema bindings, empty groups, ratio violations, and worst-domain
pilot work exceeding the existing ceilings. Record every group and refusal.
Do not add a quality test or try different files. For a group of N rows,
`D=N` if `N<=16384`; otherwise `D=floor(16384/256)*256=16384`.
The v2 index function is `I(parts,m)=int(SHA256(UTF8("|".join(
["20260927", *parts]))).hexdigest(),16) % m`.

Select eligible group number `I([source,view,revision,file,"window-v2",
"group"], number_of_eligible_groups)`. For that group's physical index g,
let K=min(512,D), and choose
`s=I([source,view,revision,file,str(g),"window-v2","start"],D-K+1)`.
The absolute half-open window is `[group_start+s, group_start+s+K)`.
If K is not 512, refuse the file/arm: do not rerank groups to fill it.
Decoded scan rows are `min(N,ceil((s+512)/256)*256)`. Single-file ordering
rotation is immaterial. Freeze both absolute and group-relative indices.

| Resource | Exact ceiling / setting |
|---|---|
| Plans and output rows | 8 separate plans, 512 each, 4096 total |
| Window policy | version 2; scan 16384/file, 131072/arm; buffer 4194304 bytes; batch 256 rows; threads disabled |
| Ratio | 15; v2 small-chunk/aggregate exemption 16777216 bytes, unchanged |
| Network inventory | 0 requests; use verified offline reconstruction |
| Footer planning | 80 requests total, at most 10/file; 33554432 bytes total, at most 4194304/file |
| Data execution | at most 100 requests/plan, 800 total; pilot ceiling remains 100 |
| Whole arm requests | 880 including footer, HEAD, redirects, retry attempts and failures |
| Whole arm response bodies | 268435456 bytes; footer portion above, data at most 29360128/file (28 MiB), 234881024 total |
| Response body | at most 4194304 bytes; split valid ranges, never accept whole-shard 200 responses |
| Decompressed work | at most 67108864 bytes/file, 536870912/arm, including footer parsing, decoded/discarded rows and repeated work |
| Record/parser | metadata JSON record <=1048576 bytes; parser <=33554432 bytes |
| Process tree memory | <=268435456 resident bytes, external supervisor; decoder allocations must have conservative preflight reservations |
| Disk | scratch <=469762048; final <=67108864; combined <=536870912 bytes at every instant, including caches, partials, manifests, duplicate copies and expansion |
| Time | <=1800 seconds/arm, <=600/plan, <=30/request; the earliest deadline wins |
| Retry | at most 2 retries/request, delays 1 then 2 seconds, only transport timeout/reset or HTTP 429/502/503/504; all attempts count within unchanged caps |
| Concurrency | one local process queue, one active file, one network worker |

M's 64 MiB final cap includes parts, combined bundle, inventory, receipts,
sweep results, and comparison report. The existing sweep's own 64 MiB
output cap does not confer a second allowance. Use the tighter remaining
arm cap. Byte units are binary. No automatic stage/resume budget reset.

Both arms allow only HTTPS `huggingface.co` and
`cas-bridge.xethub.hf.co`, port 443. Initial resource URLs must be generated
from the repository/revision/frozen path; CDN URLs only validated redirects
from those resources. At most 3 redirect hops per logical request, each
charged. Reject arbitrary user/corpus URLs, other hosts (including broader
default transport allowlists), userinfo, IP literals, downgrade and changed
resource identity. Signed query credentials stay out of logs. No license
acceptance, remote code, access-term workaround, OCR, or fallback files.

Before fetch, bind footers/schema, file length and stable remote identity,
projected chunks, windows and cost calculations in plans. Distinguish
v2 proportional prefix estimates from hard bounds: they do not prove
maximum page/dictionary/decoder cost. Require conservative page/chunk and
memory reservations plus runtime meters. Refuse if a safe upper bound is
unavailable or above a cap; do not loosen it or silently resample.

Stop on digest/revision/inventory drift, no eligible group, non-512 window,
schema mismatch, missing field, malformed locator, changed ETag/length,
bad content range, unexpected response, count/conservation failure, cap
exhaustion, timeout or resource-accounting gap. Mark incomplete units and
the entire intended 4096-row replicate INCOMPLETE; preserve bounded failure
receipts. Partial outputs are descriptive only, never a successful replicate.
Malformed classifier values within valid acquired rows remain counted
rejections under the unchanged selector, not replacements or fetch retries.

Different immutable files give disjoint source locators and remove reuse
of the original file/window. They do NOT establish iid documents, unique
content, independent domains, temporal causation, corpus weighting, or
independence from common source curation. One scan-prefix-restricted,
contiguous window per crawl/file remains clustered and nonuniform.

## 5. Frozen-policy comparisons

Apply the exact frozen policy JSON/YAML digest in section 1, A/B/C/D at
normal/strict, with corrected tool version 2. No threshold, taxonomy,
precedence, validity, or sensitivity-grid changes. Bind evaluator code
hash; changes that could affect semantics require a new reviewed lineage.
Evaluate M and seal its outputs before unblinding T or proposing redesign.

Predeclared replication observations are the following tables, for the
development sample and M separately, plus matched-crawl differences and
equal-crawl aggregate differences. Counts use denominator 512/crawl and
4096/arm; component shares use selected-component denominators. Zero
denominators are undefined, not zero percent. Report absolute counts too.

| Comparison | Frozen analysis |
|---|---|
| B science | Normal and strict totals, each crawl, zero cells, cells <20, retention per 512; compare to development 29/20 totals |
| S5/S61 | Counts, shares of science, overlap, FDC prefixes/primary labels and genres under B and D, both tiers; distinguish disjoint final rows from predicate overlaps |
| B versus A | Full row transition matrix; S61 increments, A-prose/unassigned origins, added assignments versus mere component moves |
| B versus C | Shared science; practical/prose to unassigned; explicit/conditional genres and FDC domains lost |
| B versus D | Gate/assignment increments, retained B membership, D-only component counts and artifact-label distribution; verify strict identity |
| GN/GS/GD | Fixed-order validity/English/artifact/missing/correctness/type waterfalls, marginal and sole failures, failure intersections; language-only, missing-only and joint normal-to-strict losses |
| Practical | Tutorial/Documentation/FAQ/Support and Procedural Q&A/Knowledge/Blog branches, FDC domain, correctness and missing-figure composition |
| Prose | News/Blog/Knowledge/Creative/Nonfiction counts and conditional shares, FDC/knowledge composition, concentration |
| Artifact behavior | No Artifacts versus Irrelevant Content, other rejected labels; B/D additions by genre, English band, missing content, correctness; no semantic inference |
| Temporal | Each fixed crawl, matched-replicate change, min/max and spreads; inherited >=20-cell, 10 percentage-point, twofold and S61>0.5 flags only as diagnostics |

Mechanical invariants (conservation, strict subset, B/C science identity,
B preservation in D, B-strict=D-strict) are implementation checks, not
empirical evidence for a scientifically superior policy. Predesignated M
comparisons are fresh replication observations for this policy, not a
formal hypothesis-test success or final benchmark. Word-count summaries,
all sensitivity cells, finer cross-tabs and pooled 8192-row summaries are
descriptive. Preserve separate replicate labels in any pooled display.
No binomial iid CIs, eight-window bootstrap confidence claim, significance
claims, multiple-testing winner, token-supply extrapolation, or volume
optimization. Unexpected analyses must be labeled exploratory.

## 6. Arm T: exact metadata-only selection

This protocol freezes the rule; the actual locator manifest is a required
offline implementation output, NOT produced or claimed complete here.
Verify the 4096-row bundle/execution/payload identities first. A physical
source row is `(repository, revision, source_file, source_row)`, where
`source_row` is the zero-based absolute Parquet row index in the acquisition
locator, never the combined JSONL line number. Reject duplicate input
locators or inconsistent locator metadata. Use the frozen evaluator.

Denote final component by F(policy,tier,row). Define B-c as
`F(B,normal,row)==c`; D-only-c as `F(D,normal,row)==c` and
`F(B,normal,row)!=c`. Assert D-only rows were B-rejected (as required by
this frozen policy); any B-assigned component switch is an integrity STOP.
Strict survivor means `F(B,strict,row)==c`; strict loss means B-normal-c
and `F(B,strict,row)!=c`. Do not equate strict loss with language loss:
missing-content gating also changes.

Process these exact stratum names in this precedence order:

| Name | Eligibility / selection | Maximum |
|---|---|---:|
| `B_science_census` | Every B-normal science row, assert exactly 29 | 29 |
| `D_only_science_census` | Every D-normal-only science row, assert exactly 25 | 25 |
| `B_practical_survivor` | One B practical strict survivor per crawl | 8 |
| `B_practical_loss` | One B practical strict loss per crawl | 8 |
| `D_only_practical` | Two D-only practical per crawl | 16 |
| `B_prose_survivor` | One B prose strict survivor per crawl | 8 |
| `B_prose_loss` | One B prose strict loss per crawl | 8 |
| `D_only_prose` | Two D-only prose per crawl | 16 |

For non-census cells rank ascending by:

```
H(["essential-web-evidence-v2.0", "text-select", 20260927,
   stratum_name, crawl, repository, revision, source_file, source_row])
```

Tie-break with `(UTF8(source_file), integer source_row)` ascending.
Process crawls in section 3 order within each stratum. Keep the first
requested count among previously unused eligible identities. Record all
candidate counts, ranks, eligibility, conflicts and selected identities.
Census rows are sorted by that locator order, never sampled.

The final unique set is <=118, with the two duplicate-free exact censuses
comprising 54. These strata should be disjoint under final-component
precedence, but ownership is explicitly first eligible stratum in the
table; remove already-owned locators before ranking a later cell. For
census overlap or counts other than 29/25, STOP as an identity/policy error,
rather than silently reducing a census. For other depleted/empty cells,
select all remaining candidates and record `requested`, `eligible`,
`conflicts`, `selected`, `shortfall`; never borrow across crawl/stratum,
repeat a locator, or change ranking. Assert selected<=118 and unique.

Only source-row identity is available for selection; metadata projection
does not establish semantic document deduplication across different rows.
Do not claim 118 unique underlying articles. During later authorized text
acquisition, exact equal UTF-8 text may share one review item (byte equality
confirmed after hash match); retain all aliases and their strata, choosing
the representative by table precedence, crawl order, then locator order.
Never backfill duplicate-content aliases. Keep both locator-level counts
and distinct reviewed-text counts. Near duplicates/reposted articles are
not certified absent. This post-acquisition consolidation changes only
review multiplicity, never the frozen selection or acquisition membership.

## 7. Arm T: text field and bounded acquisition

Verified offline repository/source-contract field: top-level **`text`**,
a string preserved verbatim. Evidence: `EssentialWebAdapter` pinned-source
schema docstring and `contract(text_field="text")` in
`src/xlm/data/adapters/mix01_adapters.py`; its explicit string validation;
`src/xlm/data/adapters/columns.py`; recon report section 5's recorded source
schema. These establish the field name without reading document contents.
Existing live-test source is corroboration of the contract, not a live test
run in this session. Actual new physical schema must still pass footer
verification; failure is BLOCKED, with no guessed alternate field.

Project only `["text"]` plus acquisition-generated locator metadata.
Join already-owned classification metadata offline through exact locators.
No adapter invocation requiring six upstream columns and no new score/id/
URL download. Operate only on section 3's eight **excluded development**
files and the existing development row groups/ranges bound by execution.
Every requested row must belong to the frozen T selection and its original
window. No new M file contributes to T.

Group selected locators by file and row group; sort rows ascending. Decode
each required group's text chunk once, to the batch covering the greatest
selected row. Membership-filter before retention/serialization: gaps and
preceding rows can be physically decoded, but are never emitted, displayed,
logged, or semantically inspected. Preserve exact row indices. Stop after
the final required batch. All selected rows in a development file originate
in one known window/group. Reuse range validation, transport, deadlines,
leases, reservations, journal and verifier from existing XLM machinery.

Existing `parquet_window_records` emits a contiguous interval; it does NOT
yet implement the required sparse selected-row retention contract. Future
implementation needs a small explicit locator-membership layer before
serialization/retention and physical accounting tests. Do not pass a hull
range to the ordinary writer and pretend its extra documents are selected.
No acquisition implementation is added by this protocol.

| Resource | Exact Arm T ceiling / setting |
|---|---|
| Selected source rows | <=118; requested membership immutable, no substitution |
| Full retained document | <=65536 UTF-8 bytes, inclusive; no normalization or cleanup |
| Retained unique text | <=8388608 bytes (118*65536=7733248 fits) |
| Final artifacts | <=16777216 bytes, including text, packages, aliases, receipts, mapping, both label sets, disagreement/adjudication/summary; shared text stored once |
| Requests | <=800 whole arm and <=100/file including planning footers, HEAD, redirects, failed attempts and retries; counters shared across stages |
| Transfer | <=134217728 whole arm; <=16777216/file; within that, footer <=2097152/file (16777216 total), data <=14680064/file |
| Response body / range buffer | <=4194304 bytes each |
| Decompressed work | <=67108864/file, <=536870912/arm; count all unselected scanned text, dictionaries, repeated decode and parser expansion |
| Scanning | <=16384 rows/file, <=131072/arm, batches 256, no hidden rescan |
| Ratio | <=15; existing v2 exemption <=16777216 bytes; hard decoded limits always apply |
| Parser / process tree memory | <=33554432 parser bytes; <=268435456 resident bytes with supervised enforcement and safe decode reservations |
| Disk | scratch <=520093696; final <=16777216; combined <=536870912 including caches, partials, package copies, logs and extraction expansion |
| Time / retries / hosts | <=1800 seconds/arm, <=600/file, <=30/request; 2 retries, 1/2-second delays; exact section 4 hosts, revision and redirect restrictions |
| Concurrency | one file and one worker at a time |

Footer preflight must inventory text chunk offsets, compressed and
uncompressed lengths, codec, group sizes, dictionary/page requirements,
range/request counts and worst decoder workspace. If page-size limits or
safe memory bounds cannot be enforced with the existing reader, add tested
bounded enforcement or refuse; post-hoc `batch.nbytes` alone does not prove
a decompression/allocation cap. Conservative whole projected-chunk bounds
are acceptable even if they force refusal. Record estimated and actual
physical work separately. These caps may make a selected file infeasible;
that is a reported limitation, not permission to expand transfer or select
another file. Retries and process resumes consume the original arm budget.

Each selected locator receives exactly one acquisition status:
`full_text_available`, `unreviewable_full_document_due_to_size`,
`unreviewable_missing_or_invalid_text`, or `acquisition_incomplete` with
explicit reason. Empty/null/non-string/invalid UTF-8 is not coercible text.
If full UTF-8 length exceeds 65536, retain no review excerpt and assign the
size status; record full byte length only if actually determined safely.
If a parser/memory/cost limit prevents knowing the full length, use
`acquisition_incomplete`, never invent a size or hash. Do not replace the row.
Full-document rubric labels are unavailable for non-full statuses; these
stay visible in denominators. Global cap or binding failure stops the arm;
bounded completed items remain marked as partial evidence. A clean receipt
can account for all locators including oversized rows, but cannot claim
118 completed full-document reviews.

## 8. Frozen human review rubric

All dimensions are categorical, with the anchors below. `uncertain` is
allowed on every judgment and requires a short reason. It means insufficient
evidence/expertise, not a midpoint. No text is available for unavailable
rows: record `not_reviewed` in all 18 fields, not substantive judgments.
`not_applicable` is allowed only where expressly listed. Review the entire
unaltered available document. Brief reasons reference paragraph/character
offsets; do not put sensitive rejected passages into public reports.

| # / dimension | Exhaustive substantive levels and anchors (plus `uncertain`) |
|---|---|
| 1 English predominance | `predominant`: >50% of natural-language words appear English; `not_predominant`: <=50%. Exclude code, formulas, URLs, numbers and proper-name-only spans; if no assessable natural language, use uncertain. Estimate as human judgment, not fastText. |
| 2 English fluency | `fluent`: English passages readable throughout, at most isolated local errors; `impaired_but_readable`: recurrent errors require rereading but central meaning recoverable; `unreadable`: central meaning cannot be reliably recovered. `not_applicable` only when no English passage exists. Judge linguistic fluency separately from missing-context/extraction labels. |
| 3 Boilerplate/irrelevant fraction | Estimated fraction of visible non-whitespace characters that is navigation, ads, templated clutter, duplication or unrelated inserted material: `zero`=0%; `low`=(0,10%]; `moderate`=(10,25%]; `high`=(25,50%]; `dominant`=(50,100%]. Disagreement with the topic/opinion is not irrelevance. |
| 4 Boilerplate severity | `none`: absent; `peripheral`: present but main content reads continuously; `intrusive_separable`: interrupts reading, useful passage boundaries still identifiable; `obscuring`: useful content cannot be reliably separated. A low fraction can still be intrusive. |
| 5 Standalone completeness | `complete`: stated main purpose fulfilled with necessary textual context; `minor_gaps`: local omissions but central purpose still usable; `incomplete`: missing essential premise, result, answer, ending or instructions prevents use. |
| 6 Missing image/figure dependency | `none`: none required for central meaning; `helpful_only`: absent visual would aid but text suffices; `essential_missing`: central explanation/instruction cannot be interpreted without absent image/figure. Unknown existence/dependence => uncertain. |
| 7 External-link dependency | `none`: no external material required; `optional_support`: citations/further reading optional to use text; `essential_external`: following a link is needed for central content/steps/answer. Do not follow links during review. |
| 8 Actual genre | One dominant-purpose label: `academic` (research report/scholarly argument), `explanatory` (topic exposition), `technical_reference` (lookup/specification/API reference), `instruction` (procedure/tutorial), `faq` (curated question-answer list), `support` (troubleshooting/support response), `discussion_qa` (forum question and replies), `news` (event reporting), `personal_commentary` (personal narrative/opinion/blog), `creative` (fiction/poetry/drama), `nonfiction_narrative` (connected factual narrative/essay), `promotional` (sales/advertisement), `listing_structured` (index/catalog/table without sustained exposition), `other`, `mixed`. Choose mixed when no purpose dominates; other requires explanation. Format alone does not determine genre. |
| 9 Essential component fit | `science`: substantive scientific explanation, evidence or reasoning; `practical`: usable instruction, problem-solving or reference; `prose`: sustained coherent narrative/exposition; `none_mixed`: none fits, or multiple fit without a dominant purpose (required subreason `none` or `mixed`). Judge content, not upstream labels. Scientific step-by-step material with primarily instructional purpose can fit practical. |
| 10 Promotion/commercial | `none`: no promotional purpose; `incidental`: attribution/product mention supports independent substance; `substantial`: persuasion/sales mixed with independently useful substance; `dominant`: central purpose advertising, sales, conversion or affiliate promotion. Product-specific documentation is not automatically promotion. |
| 11 Science substance | `none`: no scientific explanatory claims; `mention_only`: terminology or assertions without meaningful explanation/evidence; `substantive_explanation`: meaningful mechanisms, concepts or scientific relationships explained; `substantive_evidence_reasoning`: methods/data, explicit inferential reasoning or evidence-based comparison is developed. This scale measures substance, not correctness; technical vocabulary alone earns no high label. |
| 12 Apparent factual/technical errors | `none_detected`: within declared expertise no apparent error found, not certification; `minor_local`: localized issue does not overturn the central explanation/action; `material`: error undermines central claim, mechanism, answer or action; `not_applicable`: no checkable factual/technical claims (e.g. pure fiction). Suspected error outside expertise => uncertain with suspicion noted. |
| 13 Checkability/verifiability | `no_checkable_claims`: no factual/technical propositions; `checkable_from_document`: central claims can be checked against supplied derivation/data or explicit internal evidence; `external_check_identifiable`: meaningful external check has identifiable source, method or sufficiently precise claim; `insufficient_basis`: claims too vague or sources/methods too unspecified for meaningful checking. Checkability is not a truth judgment; no browsing. |
| 14 Practical actionability | `not_applicable`: no instructional/reference/problem-solving purpose; `usable`: sufficient steps, conditions or reference to perform stated task with expected prerequisite knowledge; `partially_usable`: some usable information but essential step/condition unresolved; `unusable`: stated task cannot be carried out from text. State assumed prerequisites. |
| 15 Q&A answer | Apply also to FAQs/support with a question-answer purpose. `not_applicable`: no question-answer purpose; `usable_answer`: at least one responsive answer adequately addresses the main question; `partial_answer`: responsive but essential issue unresolved; `no_usable_answer`: question only, irrelevant replies, or substantively unusable answer. For multi-question documents, judge the main purpose and note unanswered subordinate questions. |
| 16 Prose coherence | `coherent`: sustained understandable narrative/exposition; `local_breaks`: localized discontinuities but overall progression recoverable; `incoherent`: central progression cannot be recovered; `not_applicable`: primarily reference/code/table/short Q&A with no sustained-prose purpose. |
| 17 Fragmentation/extraction | `none_visible`: no visible defect; `minor`: local duplication/formatting break, central meaning intact; `major_recoverable`: repeated fragmentation/interleaving but central passage recoverable; `severe`: missing/interleaved/garbled content prevents reliable recovery. Mark defect types separately: truncation, duplication, interleaving, encoding, markup, broken table/code, other. Multiple types allowed. |
| 18 Overall disposition | `acceptable_as_is`: serves a science/practical/prose purpose as supplied, predominant readable English, essential content usable, no detected material error or destructive extraction/dependency; `requires_separately_specified_repair`: useful core exists but a specific bounded repair is necessary, describe it (no repair performed); `reject`: no suitable core or central defect cannot be repaired from supplied text; `uncertain`: expertise/evidence/disagreement prevents disposition. No arithmetic sum and no forced acceptance from label averages. |

Reviewer forms also collect reviewer ID, rubric version, review ID, UTC
submission time, relevant expertise, confidence (`sufficient` or
`limited` with reason), dimension notes, and a disposition rationale.
Inconsistent form choices are returned for clarification, never silently
corrected. A claim of acceptable_as_is with known essential gaps or material
error requires correction or uncertainty. Calibration uses authored
synthetic examples only, before real labeling, without modifying anchors.

## 9. Blinding, order and two reviewers

Selection seed 20260927 is separate from frozen **review-order seed
20260928**. Custodian generates a fresh 32-byte cryptographically random
secret K before labeling, stores K only in the sealed provenance area and
publishes its SHA-256 commitment in the review-package manifest. K is not
another sampling seed; it protects opaque IDs from enumeration. Do not put
K, locators or the selection manifest into reviewer-accessible files.

For locator L=`[repository,revision,source_file,source_row]`, review ID is
`"ew2-" + HMAC-SHA256(K,C([protocol_version,"review-id",L])).hexdigest()`.
Use the full digest; collision => STOP. Exact-text aliases use their frozen
representative's ID and sealed alias list. For reviewer r=`reviewer-1` or
`reviewer-2`, order ascending by
`H([protocol_version,"review-order",20260928,r,review_id])`, tie by review ID.
Record actual ordered ID lists and hashes. This pseudorandom ordering is
reproducible from sealed K and the manifest; do not expose a stratum-sorted
list as an alternative presentation. No reroll for a preferred order.

The reviewer package contains only opaque ID, complete plain text, rubric
and that reviewer's blank forms. It excludes policy A/B/C/D, crawl, FDC
codes/labels, artifact/correctness classifiers, English scores, source-row
stratum, locators, filenames, URLs from metadata, acquisition order and
per-stratum counts. Use a separate access-controlled sealed mapping
`review_id -> locator(s)`, key commitment, ranks and acquisition receipt.
Escaped offline text rendering, no active links, scripts, remote assets or
embedded metadata. Do not remove dates or identities naturally present in
the document text: report unavoidable content-level inference as a
blinding limitation. Reviewers must not have access to custodian reports.

Two independent human reviewers label every full-text item, without
discussion or access to the other's labels, then seal first-pass submissions
and digests. Both must have suitable domain expertise for scientific-content
judgments; because origin strata are hidden, assign qualified reviewers to
the package or independently route text-identified specialist topics through
opaque IDs. No policy/crawl metadata is revealed for routing. If expertise
is insufficient, use uncertain and seek an appropriate adjudicator; do not
impute correctness. No model label substitutes for either human reviewer.

Mechanically summarize all categorical dimensions: per-reviewer counts,
cross-tabs, exact agreement numerator/denominator and unavailable/uncertain
counts; item-by-dimension disagreements; defect-type set agreement. Do not
average ordinal codes or treat uncertain as agreement with a substantive
label. No reliability claim based on a pooled convenience sample or tiny
cells. Acquisition status, bytes, exact-text aliases and form completeness
are mechanical; factual validity, component fit, genre, repair feasibility,
and disposition are human judgments. All substantive disagreements on any
dimension go to adjudication, not only the overall label.

An independent third adjudicator first records an unaided blind assessment
of disputed dimensions, then sees the two anonymized rationales, still
without selection metadata. Use a science-domain expert for scientific
disputes. Record final label, rationale, supporting text offsets and whether
resolved or unresolved. A reviewer's uncertainty triggers adjudication for
science substance/errors/checkability, component fit and disposition even
if both chose uncertain. No majority rule; unresolved => uncertain. Preserve
both original labels and all revisions. After adjudication is sealed,
custodian joins strata and reports both raw and adjudicated results,
selected/retrieved/reviewable/reviewed denominators and every shortfall.
Two mutually agreeing labels remain agreement, not independent truth proof.

## 10. Development versus confirmation

The existing 4096 rows influenced policy design. They are development
evidence, and every T text review from them is also development evidence.
M provides fresh file/window evidence for the exact frozen policy; it is
still eight clusters in previously selected crawls, not sealed final data.
Neither arm alone validates usable corpus-scale token availability.

Run frozen-policy M evaluation and seal its report before joining T labels
to selection metadata. If T motivates policy changes, record the information
timeline, proposed change and all exposure. M supports only the original
policy's replication. Re-evaluating a redesigned policy on M is descriptive
development even if some labels were not inspected earlier. Freeze new
semantics first, then acquire a separately authorized, preregistered new
file/window metadata replicate and new blinded text from previously
unreviewed independent files/clusters. Exclude development and M files.
Needed sample sizes/coverage are a new protocol, not an adaptive extension
of this one. Taxonomy redesign also needs fresh coverage of suspected
false-negative regions; reviewing selected science alone cannot estimate
science recall. Repair redesign needs a frozen transform and separate
before/after quality evidence. Never call reused examples final confirmation.

## 11. Predefined decision matrix

Rules are qualitative evidence requirements, not scores or policy-ranking
optimization. Multiple outcomes can coexist; unresolved integrity/resource
failures first block interpretation. Review every crawl/stratum, counts,
missingness and disagreements, not just a combined fraction. No automatic
final selector freeze follows from this protocol.

| Outcome | Evidence pattern and next action |
|---|---|
| A Continue toward selector-v1 freeze | Complete, valid M broadly reproduces the predeclared composition/attrition mechanisms; T's adjudicated content supports intended component meaning and as-is usefulness, including scientific substance, with no unresolved central defect. Remaining sparse/uncertain cells must be disclosed and resolved through fresh confirmation. Continue planning confirmation; do not declare a selector frozen. |
| B Another metadata replicate | Science remains sparse/zero in crawl cells, apparent availability/composition changes are concentrated in particular windows, M and development differ without a demonstrated semantic failure, or incomplete acquisition prevents the comparison. Freeze a new disjoint-file protocol before observing further metadata; no rerun-until-green or post-hoc science enrichment. |
| C Policy redesign | Adjudicated text shows a repeatable central mismatch between assigned component and actual useful content, or gates/predicates retain unsuitable material/remove desired material for a scientifically documented reason. Describe the concrete failure, counterexamples and changed rule; refreeze and obtain fresh evidence. Volume increase alone never triggers promotion. |
| D Artifact-handling redesign | D-only material contains recurring intrusive boilerplate/extraction defects, or useful cores consistently require repair; alternatively the Irrelevant Content label consistently masks usable material. Freeze a selective artifact/repair hypothesis with uncertainty, not blanket admission or blanket label rejection. No automatic cleaner and no D adoption for doubling retention. |
| E Science-taxonomy redesign | S5/S61 text does not represent substantive science, S61 concentrates in promotional/unsupported health content, or defensible science coverage is systematically mismatched. Require suitable expertise. Sparse counts alone imply B, not proof that taxonomy is wrong; selected-row review cannot measure missed-science recall. |
| F Reconsider strict language gate | Blind review finds usable predominant/fluent English among strict-loss practical rows and post-unblinding cause tables implicate language (not missing figures alone), with corroborating attrition/composition across replicates. Freeze a language-specific hypothesis and fresh evaluation; do not lower the threshold merely to recover volume. If losses are unreadable or essential-context deficient, evidence supports retaining caution. |

Unreviewable documents, unresolved scientific uncertainty, or within-crawl
differences without semantic evidence are reasons to defer, not count as
acceptance/rejection votes. Negative findings may block A without proving
a corpus-wide defect. No arbitrary minimum-quality percentage or maximum
retained-volume winner is introduced.

## 12. Implementation outputs and integrity

All manifests bind protocol/freeze digest, parent digests, schema version,
code hashes, `.python-version`, `uv.lock`, environment, limits, stage status,
and exact commands/exit statuses. Every JSON receipt has a canonical digest;
each artifact also has byte length and file SHA-256. JSONL logical digest
is over the canonically ordered record array, additionally binding raw-byte
hash; ordered arrays must declare their ordering. Non-JSON files use a
canonically digested descriptor `{relative_path, bytes, sha256, media_type}`.
Final artifact-tree manifest excludes itself to avoid a hash cycle.
Sealed artifacts get digests in public receipts but remain access-restricted.
Missing artifacts/statuses fail aggregate success; a skip is never a pass.

M must produce: expanded discovery/inventory receipt and selected-file
manifest (reproducing this freeze); eight footer evidences and window
manifests; execution manifest; eight separate pilot plans; eight verified
outputs/receipts; combined bundle and digest; corrected sweep-v2's nine
outputs; replicate comparison report. Also record incomplete attempts and
actual requests, bytes, decoded/scanned rows, runtime, disk high water,
measured memory methodology and endpoints/peaks accurately labeled.

T must produce: frozen review-selection manifest with all candidate-cell
counts/ranks/conflicts/shortfalls; selected locator list; file/group cost
evidence and acquisition plans; verified text receipt with every selected
locator's status; blinded review package/order manifests; sealed locator
and alias mapping/key commitment; reviewer-1 and reviewer-2 labels;
disagreement report; adjudication artifact; final summary with original
and adjudicated labels, denominators, missingness and scope limits.

New artifacts go only into a fresh named G: experiment root specified and
bound by future plans. No X: writes or shared-cache spill. Existing data is
read-only. Atomic writes, bounded staging and single ownership are required.
Do not treat this document or a data-only recipe as executable YAML.

## 13. Muse handoff (exact next prompt)

> Implement only `essential-web-evidence-v2.0` from
> `docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md` and its
> `docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2/freeze.json` binding.
> Read AGENTS.md, CONTRACTS.md, STATUS.md and the corrected selector-sweep-v2
> report first. Verify protocol, inventory, source revision and policy
> digests. Keep the eight frozen new files and seed 20260927, unchanged
> window-v2 identities and policy digest
> f4357f61f434d5105d823266153dac6372e122f12767187631e8796b4899dd07.
> Reuse existing bounded XLM acquisition infrastructure; implement the
> minimal sparse exact-locator text retention, shared arm-budget enforcement,
> inventory/selection/plan receipts, blind package/forms and reporting needed
> by this protocol. Do not change selectors, production adapters/admission,
> pilot ceiling 100, or dependency defaults. Use authored synthetic offline
> fixtures and focused regressions for deterministic selection/ties,
> original-file exclusion, 512-row refusal, sparse membership, request and
> redirect accounting, decompression/allocation refusal, disk including
> partials/cache, retry/resume ceilings, oversized full-document handling,
> shortfalls/deduplication, digest drift and blinding leaks. Run one focused
> pytest controller with explicit worker count, single nodes -n 0; set
> OMP_NUM_THREADS/MKL_NUM_THREADS/OPENBLAS_NUM_THREADS/NUMEXPR_NUM_THREADS=1
> and TOKENIZERS_PARALLELISM=false. Use uv offline/locked/no-sync and the
> existing pinned Python 3.12.13 environment; record its actual CPU/CUDA
> package state. Do not run full acceptance, live or CUDA tests by default.
> You may read the existing metadata-only development bundle on X: to
> generate and seal the deterministic <=118 locator selection in G:.
> Produce a concrete offline implementation report, requirement ledger,
> commands/statuses, all available dry plans and remaining unknown physical
> costs. No network, fetch, footer network inspection, corpus text inspection,
> X: writes, tokenizer, training, production admission, push, or actual human
> labeling is authorized. Stop after offline implementation and focused
> verification; present exact hash-bound footer-planning steps for separate
> authorization. Never invent executable-ready plans before costs are known.

## 14. Session evidence, ledger and stop point

This is the task-specific implementation report (the AGENTS.md `PXX.md`
report convention), not a new numbered research milestone. Normative user
documentation and next prompt are this document; no executable recipe is
needed. The inventory JSON is a data-only freeze artifact, not a tool.

| Requirement | Status |
|---|---|
| Scientific protocol, caps, rubric, decisions and handoff | IMPLEMENTED as documentation |
| Nine corrected sweep artifacts, policy identity, development receipt/payload hashes | VERIFIED offline against existing real metadata |
| Complete path inventory and eight new file choices before footer inspection | VERIFIED offline by all eight original listing hashes |
| Upstream field name `text` | VERIFIED from offline pinned-source repository contract; physical text schema not newly fetched |
| T exact locator manifest, new window indices and physical plans | NOT RUN; future implementation/planning outputs |
| New metadata/text acquisition and two-reviewer labeling | NOT RUN; not authorized |
| New physical feasibility and final selector-v1 freeze | BLOCKED on future authorized evidence; not a protocol-design blocker |
| Acquisition tool implementation, selector changes, training/admission | OUT OF SCOPE this task |
| Unit/fast/full/live/CUDA test suites | NOT RUN; documentation-only task |

The audit command and exact results are in
[COMMANDS.md](../evidence/ESSENTIAL-WEB-EVIDENCE-V2/COMMANDS.md) and
[verification.json](../evidence/ESSENTIAL-WEB-EVIDENCE-V2/verification.json).
Environment: Windows 11 build 26200, PowerShell, Python 3.12.13 via
`uv run --offline --locked --no-sync --extra cpu --extra eval python -B -`.
The command did not synchronize/install dependencies or import Torch;
the extra flags do not prove the installed Torch is a CPU build.
Primary audit: exit 0; 864832 sweep bytes read, 15135064 metadata bytes
hashed, 0.243022 seconds, endpoint RSS 31244288 bytes (not peak).
No live datasets or synthetic acquisition tests were run. Physical
acquisition performance, text size distribution and reviewer agreement
remain unmeasured. No documentation commit made in this session.

Stop here: **protocol and offline file-selection freeze only**.
**READY TO IMPLEMENT EVIDENCE V2** is not READY TO ACQUIRE, selector approval,
or production admission. Next action is the exact Muse prompt in section 13.
