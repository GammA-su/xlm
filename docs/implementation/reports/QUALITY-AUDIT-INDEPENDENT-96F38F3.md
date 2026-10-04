# Independent acceptance audit of Global Quality Audit Phase A

2026-10-04. **QUALITY AUDIT NOT SAFE FOR REAL RUN.**

The requested first production run is blocked. This is an audit of commit
`96f38f311cccaa8a157fb8b3f4f4bb5ed0601d50`, not a repair. No implementation,
dependency, existing test or benchmark script was changed. No commit or push.
Only audit evidence and documentation were added/updated.

Evidence: [commands and results](../evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/COMMANDS.md),
[49 independent probes](../evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/test_independent.py),
[64-case detector matrix](../evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/detector-matrix.json),
[scaling measurements](../evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/scaling.json),
[benchmark](../evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/benchmark.json), and
[worker-kernel profile](../evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/kernel-profile.txt).

**Scope deviation:** the initial existing-test command included the `c05_flow`
fixture, whose implementation invokes an authored synthetic C05 pipeline. It
reached the first overlay test before I interrupted it. That was contrary to the
request's prohibition on running C05; it is not counted as a completed test run.
Subsequent baseline tests excluded all four overlay tests. Additional overlay
checks consumed only the already-created public authored proof, plan, trust,
completion, membership and authored source files. They did not invoke C05 again,
read matcher/index/private-ledger/group-array contents, or use benchmark examples.
No network, G: access, X: access, protected mount, real p0002 text, real review
materialization, tokenizer fit or training occurred.

## 1. HEAD and cleanliness

Initial `git status --short` was empty. Branch: `feat/global-quality-audit`.
HEAD matched exactly. The three commits were `96f38f3`, `40ce62a`, `03242cf`;
the supplied base was the immediate parent. Final changes are audit docs/evidence
only; implementation remains byte-identical to HEAD. Python 3.12.13, Windows 11
10.0.26200, uv 0.12.19, NumPy 2.5.3, psutil 7.2.2; imports were verified to come
from `F:/Project/xlm-quality-audit/src`. Existing offline locked CPU/eval environment,
no sync/install. `pyproject.toml`, `uv.lock`, `.python-version` and CPU/CUDA policy
were preserved; CUDA is out of scope.

## 2. Read-only verdict — BLOCKED

Normal source scans use `open("rb")`; report regenerates artifacts in memory and
compares them without writing. The overlay reads public metadata/membership.
There is no cleaner call, corpus-filter writer, or recursive deletion in this
package. Ordinary corpus overlap and an output-root Windows junction were refused.

However, two authored reproductions violate the required guarantee:

- An external input manifest named `quality-audit.json` placed in the output
  directory was overwritten by the aggregate artifact. `_check_output` checks
  corpus directories, not the input manifest or all auxiliary input paths.
- A pre-existing `output/units` junction into a source directory passed those
  checks. Startup deleted an authored retained `*.unit.zz.tmp` sentinel inside
  that source directory, then could write units there. Resolving only the output
  root does not protect child paths or staging files.

See `runner.py:92`, `runner.py:160`, `scan.py:538`, and atomic writing at
`evidence_v2/canonical.py:124`. Input-path protection must include resolved final
and temporary destinations, nested reparse points and explicit ownership.

## 3. Source integrity — fresh-read checks VERIFIED; full guarantee BLOCKED

`scan.file_tasks` hashes the exact byte buffers passed to workers and compares
size and SHA before yielding the final task. `commit_unit` checks row and canonical
text-byte totals. Fresh same-size mutations, truncation, extension, insertion,
deletion, a midstream change, malformed JSON, wrong row counts/declared bytes and
oversized rows were refused without committing the affected file. Canonical bytes
mean UTF-8 text bytes; physical JSONL bytes are separately counted.

There is no stable-source check across hash completion and publication. A mutation
in the last worker task after the parent had hashed the file still produced a
unit and receipt; `commit_unit` records the *post-mutation* stat without verifying
that stat against the opened/scanned identity. The bytes measured were the frozen
bytes, but the resulting completed audit no longer described the current source.

Additionally, `json.loads` at `scan.py:249` accepts duplicate keys and `NaN`.
Both independently authored, correctly file-hashed noncanonical inputs completed.
Canonical parsing must reject these, rather than silently select the last `text`.

## 4. Resume — BLOCKED

The unit self-digest covers its file record (including frozen SHA) and audit-binding
digest. **That binds the old result, not today's source bytes.** `load_unit`
(`scan.py:561`) checks only current size and mtime. A valid JSON mutation preserving
length, followed by restoring the original nanosecond mtime, reused a stale unit
and published COMPLETE. `report` also verified that stale completed audit.

| Change | Observed behavior |
|---|---|
| Source size/mtime changes | Refused |
| Source bytes change, same size/restored mtime | Reused; blocker |
| Detector code identity | Refused (identity injection, no implementation edit) |
| Detector policy identity | Refused |
| Manifest digest/content | Refused |
| Worker count | Reused; intended operational equivalence |
| Output ceiling, RSS ceiling, deadline | Reused; neither old nor new envelope is recorded/bound |
| C05 proof/completion identity | Binding contains these; stale completion refused in public-overlay probe |

Rehash reused source files against frozen identities, or establish a separately
verified immutable snapshot mechanism. Restorable mtime is not sufficient.
Operational equivalence need not change scientific results, but the effective
resource envelope must be independently recorded and bound.

## 5. Partial runs — interruption behavior VERIFIED; receipt semantics BLOCKED

Injected interruptions mid-file, between files, during aggregation and before
receipt publication left no final receipt. `report` refused each; receipt staging
was not mistaken for completion. Normal atomic units are distinct from `.tmp`.
These are injected exceptions, not an OS power-loss/fsync durability certification.

`verify_report` does not validate receipt kind/status/phase/actions. Changing a
valid receipt's status to `INCOMPLETE` still returned `verified: true`. Late
deadline failures also permit COMPLETE (§20). Some aggregate files are labelled
COMPLETE before the receipt exists: consumers must require a correctly validated
receipt, not read an individual aggregate as a completed run.

## 6. Worker determinism — VERIFIED on bounded fixtures

Existing workers 1/2/4/8 test passed with many 4 KiB chunks. All ten aggregate
artifacts, including review, candidates and result digest, were byte-identical.
Independent delayed jobs completed in reverse order but were consumed in input
order. Reversing committed-unit aggregation also preserved every artifact on the
ordinary fixture. The 32 MiB benchmark independently matched workers 1 and 8.
Receipt execution fields intentionally differ (workers, duration, scanned/resumed
counts, rate, measured RSS); these are excluded from result digest.

This does not establish universal merge-order invariance: capped first-seen
language-value dictionaries are order-dependent above 256 distinct values, and
numeric score-bin merging crashes (§23, I05). A forced random completion schedule
was not separately run; ordered consumption and a forced reverse schedule were.

## 7. Accounting and byte impact — VERIFIED on authored populations, with defects

Exact global/component/allocation/source document and canonical-byte conservation
passed. Per-flag, class and Boolean-intersection counts/bytes were bounded by
their own populations; percentages reproduced `100 * affected / population`.
Metric applicable + not-applicable populations conserved documents. Existing
histogram/joint/merge tests passed. Counts overlap by design and are not summed as
a unique affected population. C05 kept + removed exactly reproduced whole-input
documents, text bytes and JSONL bytes in the consumed public fixture.

Important flags expose documents, text bytes and corresponding population
percentages. Histogram bins expose counts/bytes, with denominators in their
population summaries; not every histogram cell carries an explicit percentage.
Not-applicable histograms track documents but do not publish separate NA byte
counts. Quantiles use applicable documents, not byte-weighted populations.
Candidate saturated-ratio impacts and the meaning of `boilerplate_lines` are
incorrect (§23, I09/I12), despite ordinary accounting conservation.

## 8. Historical C05 overlay — authentication partly VERIFIED; identity BLOCKED

Trace: `overlay.load_overlay` -> `fitfast.open_streamed` -> proof/plan validation,
detached-root guard, trusted signed completion verification -> hash the exact
membership bytes being parsed. No matcher, protected index, group arrays or
private decision ledger is needed. The protected guard would probe its configured
root on a real protected plan; no protected plan was exercised here.

Whole-input statistics with/without the authored overlay were identical. Kept
counts matched the public bitmap and removed counts reconciled. Stale manifest
and stale completion proofs refused. Membership hash, ordering, row bounds and
duplicate-locator checks are present in source; the initial interrupted run is
not claimed as full tampered-membership coverage.

The bitmap keeps only `(file,row)` and discards document identity/content fields.
An independently re-signed *authored* public completion containing a wrong kept
`doc_id`, but valid file/row and membership SHA, was accepted. This requires an
inconsistent trusted producer/signing key; it is not an unsigned-signature bypass.
Nevertheless, it fails the requested identity-mismatch refusal. Verify kept-row
identity against the canonical row while scanning, without opening private C05 data.

## 9. Detector policy and every numerical measurement cut

No production KEEP/DROP/TRANSFORM is executed. Candidate YAMLs are safe-loaded
data, PROPOSAL_ONLY, `executable: false`, every action null; no candidate-policy
reader applies them. A missing action currently cannot default to DROP because
there is no executor. Source scopes and protected-class proposals are visible.
This is **IMPLEMENTED**, not approval of any future cleaner or policy consumer.

The following are measurement/classification/sampling definitions, never cleaning
rules. Exact regexes, character sets, all metric edges and policy identity inputs
are retained in [policy-definitions.json](../evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/policy-definitions.json).

| Definition | Exact cutoff/rule |
|---|---|
| Empty / whitespace | 0 code points; otherwise all characters satisfy `isspace` |
| Small-document flags | characters strictly <16, <32, <64, <128, <256 (overlapping) |
| Large-document flags | text bytes strictly >64 KiB, >256 KiB, >1 MiB, >4 MiB |
| Count-presence outputs | count >=1 |
| OCR ratio presence | bin >=1, actually ratio >=0.001; label incorrectly says `gt_0_001` |
| Same-character runs | only lengths >=8 are measured; shorter runs report 0 |
| Published run buckets | >=8, >=16, >=32, >=64, >=128, >=256 |
| Duplicate-line eligibility | whitespace-collapsed length >=16 characters |
| Duplicate paragraphs | blank-line separated, whitespace-collapsed length >=64 |
| Header candidates | eligible lines 16–100 characters inclusive, repeated >=3 times |
| Repeated bytes | excess normalized line/paragraph occurrences divided by original text bytes; clamped at 1 |
| Fragment lines | stripped nonempty length <40 |
| Anchor-like lines | length <=30, fewer than 3 spaces, no final `. ! ? : ;` |
| Table-like lines | >=2 pipes and starts/ends with pipe, or >=2 tabs |
| Code-like | >=3 nonempty lines, code-shaped line fraction >=0.30 |
| Markup-like class | full-HTML structure OR known-tag character fraction >=0.10; takes precedence over code |
| Full HTML flag | any DOCTYPE, HTML opener, or both HEAD and BODY openers |
| Light / ambiguous markup | >=1 known tag/entity without full structure; generic-tag count > known-tag count |
| XML/script/style/comment/entity flags | presence, no document-level density threshold |
| Math/table class | table fraction >=0.30 OR fixed math-symbol fraction >=0.03 OR >=5 LaTeX commands/1000 chars OR digit fraction >=0.30 |
| Prose class | alpha fraction >=0.60 AND (>=5 whitespace words OR >=32 chars), after earlier classes |
| Other/empty | fallback / zero characters or whitespace only; six classes total |
| Boilerplate phrases | only lines <=160 chars, category presence/hits; menu lines <=40 chars |
| Cookie phrase gap | up to 60 characters between paired trigger words |
| HTML tag grammar | generic names 1–32 chars; up to 256 non-angle/non-newline attribute chars; known h1–h6 |
| Entity grammar | named entity 2–32 letters/digits; decimal 1–7 digits; hex 1–6 digits |
| URL grammar | `http(s)://` or `www.` + 1–2048 permitted suffix chars |
| Page-number grammar | 1–4 decimal digits OR 1–7 Roman-numeral letters; optional `page`/`p.`, total, or dash decoration |
| Hyphen/soft line breaks | letter-hyphen-newline-letter; lowercase/comma/semicolon end continued by lowercase |
| LaTeX command | backslash + >=2 ASCII letters |
| N-grams | 5 and 10 whitespace-word units; require >=5/10 words; prefix capped at 100,000 words |
| Compression | eligible >=256 UTF-8 bytes; first 1 MiB, zlib level 1, ratio clamped to 1 |
| Truncated-analysis flag | >100,000 words or >1 MiB compression input; full word splitting still occurs |
| Control-any | >=1 NUL, forbidden C0/DEL (except LF/TAB), C1 or U+FFFD |
| Other Unicode signals | independent counts for zero-width, ZWJ/ZWNJ, BOM, bidi, private-use, noncharacters, surrogates; mojibake regex hits |
| Non-ASCII | code point >=U+0080; this is not a corruption flag |
| Count histogram | exact below 16, then 8 subdivisions per power-of-two octave |
| Ratio histogram | 0.001 bins on [0,1), exact 1; published at 0.005 width; real metrics floored to count bins |
| Coarse count edges | 1,2,4,8,16,32,64,128,256,1024,4096 |
| Coarse size edges | 16,64,256,1024,4096,16384,65536,262144,1048576,4194304 |
| Coarse ratio edges | .001,.01,.05,.1,.2,.3,.5,.7,.9,1 |
| Compression edges | .1,.2,.25,.3,.35,.4,.5,.6,.8,1 |
| Quantiles | p50,p75,p90,p95,p99,p99.9, nearest cumulative document rank |
| Candidate bands | conservative .999, moderate .99, aggressive .95; inverse tails .001,.01,.05; beyond quantile bin |
| Review | 4 entries per component/metric/coarse-bin; BLAKE2b-64 seeded locator rank, XOR stratum salt |
| Review roles | extreme populated suspicious/control bins; moderate candidate cut bin for near-threshold |
| Language confidence | clamp histogram to [0,1]; constant-1 iff every doc in bin 1000 |
| Language assessment | row-level evidence fraction >=.99: nearly all; >0: partial; else inherited only |
| Language low score | bins below 500, meaning score <.5 |
| Language cardinality | strings truncated at 200 chars, first 256 values then `<other>` (defective for numeric bins) |

Operational bounds, not detector cuts: fixed 32 MiB chunks; workers 1/2/4/8/16;
at most 2×workers futures; 10,000 input files; configured document-row maximum
up to 256 MiB (proposed run 64 MiB); audit RSS at most 16 GiB; deadline at most
168 hours; review manifest 256 MiB; review at most 5,000 entries and 200,000 excerpt
chars each. The normal proposal uses workers 8/RSS 12 GiB/reserve 8 GiB/output
4 GiB/document 64 MiB/deadline 12 h. These settings are not fully enforced (§20).

## 10. HTML false positives — PARTIAL, repair required

HTML documents gave full structure; nested tags gave light markup; entities gave
light markup; XML declarations were separate/ambiguous. C++/Rust generics and
`<unk>`/`<eos>`/angle-bracket prose were ambiguous, not full HTML. Inequalities,
Markdown and shell redirects were not markup. These are appropriate diagnostics.
But fenced XML containing `<!DOCTYPE note>` was labelled `markup_full_html` and
`markup_like`. An HTML source-code example is likewise not protected by fencing.
Distinguish actual HTML DOCTYPE and code/example context, or explicitly classify
these signals as ambiguous; do not describe them as certain full HTML garbage.

## 11. Repetition — numerical signals useful; complexity BLOCKED

Character/punctuation/whitespace spam, paragraph and phrase loops all registered
their expected run/duplicate/ngram signals. Separators, indentation, ASCII/table
art, generated code, refrains and repeated legal headings also can register real
repetition: no cleaner is invoked. Short lines under 16 chars are excluded from
duplicate-line statistics; short runs under 8 are intentionally invisible.

`detectors._runs` loops over distinct characters and searches the entire document
for each eligible character: O(length × distinct characters), quadratic while
distinct characters grow with length. Three timing trials per size:

| Distinct characters, each repeated 8 times | UTF-8 bytes | Best run-detector seconds |
|---:|---:|---:|
| 1,000 | 24,000 | 0.07025 |
| 2,000 | 48,000 | 0.28203 |
| 4,000 | 96,000 | 1.13431 |
| 8,000 | 192,000 | 4.59629 |

Doubling input cost about 4×. No 64 MiB document was allocated. Replace this with
one pass over adjacent character changes, preserving exact outputs. N-gram work
is prefix-bounded, but token/line splitting and normalization create full-document
allocations. Non-ASCII `np.unique` also sorts (not strictly linear). Per-worker
character/regex caches grow with observed code points and have no explicit eviction.

## 12. Unicode/encoding — VERIFIED on authored controls

French, German, Japanese, CJK, Arabic, Hebrew, emoji, combining marks and mathematical
Unicode did not produce mojibake/control garbage. Emoji ZWJ is counted independently
with direction `none`. Actual NUL/C0/C1/FFFD, zero-width, embedded BOM, bidi,
private-use and UTF-8-as-Latin-1/cp1252 samples produced separate numerical signals.
Non-English language is not equated with corruption. Surrogates refuse in the
source worker because valid UTF-8 scalar encoding fails. This is heuristic
coverage, not a universal encoding detector certification.

## 13. OCR/PDF — VERIFIED as measurements only

Scientific prose, equations, references, page numbers and legitimate hyphenated
words were measured without rejection. Broken one-character lines and repeated
headers/pages produce fragmentation/repetition signals; hyphen-newline damage and
replacement glyphs are separately visible. A legitimate page number also triggers
page-number measurement; reference abbreviations/Roman-like words can false flag.
No OCR processing, dehyphenation, line joining or universal PDF rejection exists.

## 14. Code/math/table protections — IMPLEMENTED, limited recall

Python/C++/Rust/JSON and generated code were `code_like`; equations and Markdown
tables were `math_table_like`. Numeric/symbol metrics remain measured and class
intersections/scope proposals enable interpretation; classes do not suppress flags.
The shell fixture was `other`, CSV-like prose was `prose_like`, bibliography was
`other`, and a short indented function was `other` because of the three-line minimum.
These are descriptive heuristics, not reliable protection masks for Phase B.
Human review must cover these misses; no current definitive-garbage decision exists.

## 15. Boilerplate — useful signals; semantic counting defect

Cookie/privacy/terms, account/navigation, subscriptions and copyright examples were
detected; URL farms have URL density; SEO loops have repetition signals. Ordinary
prose mentioning cookies/privacy/subscription was not flagged. A historical sentence
mentioning a newsletter was flagged: the phrase detector is context-insensitive.

`boilerplate_lines` counts regex matches/categories, not unique matching lines:
one line mentioning privacy, terms and newsletter produced 4. Its description and
candidate thresholds therefore misstate the unit. Count distinct lines or rename
and version this as phrase/category hits. Phrase overlap itself is acceptable if
labelled; reporting it as a line census is not.

## 16. Review sampling — VERIFIED with binding caveat

Seeded keyed BLAKE2b over `path + NUL + row`, then a metric/bin salt, selects
bottom four per stratum. Ordered worker results, reverse unit aggregation, chunking
and workers 1/2/4/8 preserved review bytes. Roles distinguish strong/control/near
bins; a single populated bin has no meaningful separate positive/control role.
No `text` or snippet field is emitted. Selection is over fixed locator identities;
permuting corpus rows changes those identities and is not an invariance claim.

Rank itself does not include the frozen manifest digest. Receipt artifact hashes
and audit bindings can associate it with the manifest, if actually verified. The
materialization consumer fails to verify that association (§17). Review `doc_id`
also needs a constrained identifier schema if arbitrary snippets/secrets must be
impossible, rather than merely absent from normal fixtures.

## 17. Materialization security — BLOCKED

CLI operator confirmation is required, direct destinations inside the checkout or
source root refuse, traversal outside the listed files refuses, and HTML escaping
passed. Filenames are fixed (`review.jsonl`, `review.html`, `README.txt`).

However, `cli._materialize` checks only receipt status then trusts receipt fields
and an unchecked review JSONL. A modified review manifest was accepted without
matching its recorded SHA. A fabricated minimal COMPLETE receipt plus an unaudited
authored JSONL locator copied text from an unrelated file. Same-size/restored-mtime
source mutation also copied changed text. There is no whole-file or selected-row
content hash check, no check that row number agrees with offset, and no explicit
protected-root/X destination guard. No X path was exercised.

Verify the receipt/binding and review-artifact hash against a trusted audit identity,
then verify current frozen source SHA and exact selected row identity/content.
Reject protected/aliased destinations. Apply bounded reads and supervision before
loading/decoding; current receipt reads are unbounded and the 256 MiB review cap
does not bound decoded Python object expansion or combined output memory/disk.
The 5,000 × 200,000-char maximum can itself entail gigabytes of duplicate JSON/HTML
output accumulated in memory, with no RSS, disk-reserve or deadline envelope.

## 18. Output privacy — normal text exclusion VERIFIED; guarantee BLOCKED

Normal aggregate/candidate/summary/review fixture canaries were absent, including
decompressed units. No benchmark text is intentionally exported. Nevertheless
`Population.add_language` copies arbitrary source-derived strings from language,
provenance, document-kind, split, label values and unknown language-like metadata
keys. An authored sensitive prose canary in `source_metadata.language_provenance`
appeared verbatim in `quality-language.json` and units. Truncation to 200 chars is
not sanitization. Validate categorical vocabularies/map unknown values to bounded
opaque categories; constrain review doc IDs and other identifiers. A statement
`content_free: true` is not proof when unconstrained metadata strings are copied.

## 19. Receipt integrity — BLOCKED

Present: manifest semantic digest and file SHA; code/dependency/policy identity;
data root; chunk/line limits; optional proof/plan/completion/membership identities;
ordered source file SHA/bytes/rows/stat; artifact hashes and deterministic result
digest. Binding reaches canonical-byte totals through the manifest and unit record.
Receipt is written last in the normal path.

Missing: bound effective RSS/reserve/output/deadline envelope, strict receipt schema
and completion-state validation, cryptographic re-establishment of current source
identity on reuse, and materializer verification. Receipt is not signed/self-digested;
`report` rederivation protects artifact consistency but does not authenticate a
maliciously reconstructed receipt. Changing status to INCOMPLETE still verifies.
Do not confuse report consistency with a trusted-source attestation.

## 20. Resource enforcement — BLOCKED, with working scan controls

Process-tree RSS includes child processes, not parent only. Independent 64 MiB
child allocations exceeded a small test envelope and were refused; blocked workers
were terminated by deadline, and worker crashes refused. Ordinary oversized row,
output-cap and insufficient-space tests refused. Oversized review-manifest stat
was refused before decoding. No 12 GiB exhaustion or 64 MiB pathological document
was attempted.

Gaps: preparation/overlay happens before `_scan` starts its deadline; resume
validation and post-scan aggregation/publication are outside the supervisor.
An inserted 1.5 s aggregation delay completed under a 1 s deadline. With one
worker, a last task taking 0.8 s completed under a 0.4 s deadline even after the
monitor recorded expiry: no final check occurs after the yielded result.
Supervisor shutdown does not turn its recorded failure into publication refusal.

An output cap of 1 byte still wrote an 869-byte binding before refusing. Manifest
`read_bytes()` occurs before the advertised bounded metadata reader; unit reads
and zlib decompression have no expanded-size limit. `report` has no operational
envelope. Supervisor cannot interrupt a non-cooperative in-process detector or a
blocked parent file read; multiworker wait cancellation passed, not those cases.
Use one whole-command supervisor, cooperative/isolated work, bounded input and
expanded decoding, precharged atomic writes, and a checked publication gate.

## 21. Performance estimates — authored measurement, not a real-corpus promise

Independent mixture: 8 files, 5,778 docs, 33,604,664 physical bytes. No overlay.

| Workers | Scan seconds | Total seconds | Physical MB/s | Sampled scan tree RSS |
|---:|---:|---:|---:|---:|
| 1 | 5.187 | 5.797 | 6.479 | 94,801,920 bytes |
| 8 | 2.047 | 2.609 | 16.417 | 732,688,384 bytes |

Both result digests: `6a1548a9014bdded8c72b5b4410fea6878dd53356923600fd1d9eea0fb92fbb7`.
The benchmark retains 37,953,839 logical file bytes including its two outputs.
All five new test/benchmark scratch directories total 372,536,321 logical bytes;
this includes a 256 MiB + 1 review-size refusal fixture. This is not a peak
physical-disk allocation measurement; the initial interrupted fixture is excluded.
This 32 MiB experiment is startup-sensitive and deliberately adversarial; it does
not refute the earlier 256 MiB authored 32.9 MB/s measurement. Neither establishes
real throughput. RSS is scan sampling, not a peak for unsupervised aggregation.

Profile of one 723-document authored worker chunk: 0.957 s total, 0.878 s in
`analyze` (~92%); JSON parsing ~0.017 s (~2%); character counts ~0.032 s; collection
counting ~0.112 s; repeated line/paragraph/word splitting, joining and n-gram tuple
counting are major detector costs. Ordinary `_runs` was ~0.013 s in that mixture,
but dominated the pathological scaling experiment. Python/string/regex work is
the main observed cost, rather than JSON or raw byte transfer. IPC was not
separately profiled. Finalization cost was ~0.56–0.61 s in this tiny benchmark;
the prior 2,035-unit aggregation claim was not independently re-benchmarked.

Planning estimates for **104,506,534,003 bytes**, after correctness fixes and
excluding unmeasured optional overlay time:

| Scenario | Explicit rate assumption | Scan estimate | Rounded planning allowance |
|---|---:|---:|---:|
| Optimistic | 32.9 MB/s, prior authored steady-state result | 0.88 h | about 1 h |
| Likely | 15–20 MB/s, around this independent short run | 1.45–1.94 h | about 1.5–2.5 h |
| Conservative | 5–7 MB/s, near serial/adverse mix | 4.15–5.81 h | about 4.5–6 h |

Add separately measured overlay and report time. Overlay stages membership in
arrays and filters them once per file (O(files × kept rows)); its full production
cost is unknown. Source rehashing for safe resume adds a full read on reuse.
Current pathological complexity means no defensible finite completion guarantee
under 12 h. No raw disk benchmark or real-corpus projection validation was run.

Later optimizations: first implement linear exact run detection; then profile
fused character/line passes, avoid repeated normalization copies, bound lexical
prefix construction without first splitting all words, and retain deterministic
exact n-gram semantics. Optimize public-membership grouping by file rather than
repeated full-array masks. No optimization was implemented in this audit.

## 22. Exact tests and static checks

Completed tests: **105 passed, 21 failed**, **4 baseline deselected**, no skips.
Breakdown: existing non-overlay quality selection 77 passed/4 deselected (16.26 s);
first independent 37 probes 20 passed/17 failed (17.01 s); eight added resource/
overlay/order probes 8 passed (7.05 s); final four semantic probes 4 failed
(2.98 s). The 21 failures are acceptance counterexamples, not repaired or xfailed.
The 37/45 deselections in incremental commands merely exclude previously run
independent nodes; they are not missing required tests. Initial interrupted run
is excluded from these totals.

Ruff format: 15 files already formatted; Ruff check: passed; mypy strict: 15 source
files passed (unused lm_eval override note only); all captured exit 0. Diff check
passed. Static selection covers quality implementation plus the existing quality
tests/fixture/benchmark, not the temporary audit-probe scripts. Full repository
suite, CUDA, production data, power-loss durability and protected deployment were
NOT RUN. Exact commands/exits, incidental command errors and artifact locations
are recorded in COMMANDS.md.

## 23. Issue ledger, severity and required fixes

All issues below are **BLOCKED** at audited HEAD; no repair is claimed. Severity
describes the acceptance impact. I01–I07/I10 must be closed before any first real
run; the remaining measurement/consumer contract defects must also be addressed
before claiming the requested complete Phase-A acceptance.

| ID | Severity | Evidence / location | Required fix |
|---|---|---|---|
| I01 | BLOCKER | Same-stat mutation reused by resume/report; mutation after source hash before commit; `scan.py:538,561` | Cryptographically reverify current source on reuse; bind stable opened source through commit and reject detected drift; document immutable-snapshot assumptions |
| I02 | BLOCKER | Manifest overwritten; nested units junction deletes source sentinel; `runner.py:92,160` | Protect all input paths; validate each final/staging path and reparse point; clean only provably owned output files |
| I03 | BLOCKER | Modified review, fabricated receipt and same-stat source all materialize; `cli.py:99`, `review.py:235` | Verify trusted receipt/binding/review SHA; source and row content; destination/protected-root policy and bounds |
| I04 | BLOCKER | Whole-command deadlines/RSS/disk absent, late COMPLETE, binding over cap; `runner.py:128,222`, `scan.py:126,528` | One dispatch-to-publication supervisor, final failure check, bounded reads/decompression and precharged writes; supervise report/materialization |
| I05 | HIGH / first-run blocker | 301 distinct numeric score bins produce `<other>` -> `int('<other>')` crash; `aggregate.py:370`, `report.py:331` | Fixed numeric histograms without label cap; deterministic categorical overflow; test 1001 bins and arbitrary merge orders |
| I06 | HIGH / first-run blocker | Run detector ~4× cost per doubling up to 192 KB; `detectors.py:705` | Exact linear run pass; scaled Unicode adversaries and monitored single-worker cancellation |
| I07 | HIGH / privacy blocker | Arbitrary provenance prose exported verbatim; `aggregate.py:207` onward | Constrained categorical values/opaque unknowns; identifier schema; privacy canaries for metadata as well as text |
| I08 | MEDIUM | XML DOCTYPE inside code becomes full HTML; `detectors.py:536` onward | Separate HTML/XML structure and code/example ambiguity; preserve raw metrics with accurate labels |
| I09 | HIGH | Ratio=1 advertises `>=1` with zero estimated impact; `report.py:169` | Correct upper endpoint/comparator or represent empty tail explicitly; verify advertised rule against exact fixture population |
| I10 | HIGH | No bound resource envelope; INCOMPLETE receipt verifies; `scan.py:582`, `runner.py:300` | Strict kind/status/schema validation and separately bound effective operational envelope; consumer verification |
| I11 | HIGH | Authenticated authored kept record with wrong doc_id accepted; `overlay.py:118`, `scan.py:227` | Preserve/verify public membership row identity and relevant content fields during source scan; mismatch must refuse |
| I12 | MEDIUM | One boilerplate line counted as four; `detectors.py:574` onward | Unique matching-line count or accurate renamed/versioned hit metric; correct impacts/definitions |
| I13 | MEDIUM | Duplicate JSON text keys / nonfinite values accepted; `scan.py:249` | Strict bounded canonical JSON parser; reject duplicate keys, nonfinite values, invalid encoding |
| I14 | LOW | OCR presence named `>0.001` but includes exactly .001; `report.py:221` | Make comparator label match bin selection, with boundary test |

Additional limitations: shell/CSV/bibliography class misses; context-insensitive
newsletter/page-number signals; review ranking uses fixed locators rather than
manifest-keyed rank; language overflow is first-seen; receipt has no independent
signing trust root. These are explicitly described above, not hidden cleaner
decisions. Candidate policies remain proposals, with no approved actions.

| Requirement group | Ledger status |
|---|---|
| Phase-A measurement / no cleaner implementation | IMPLEMENTED; VERIFIED on authored normal path |
| Read-only safety, integrity/resume, consumer receipt/security, privacy, resource envelope | BLOCKED |
| Worker determinism, normal conservation, Unicode controls, injected interruption handling | VERIFIED within documented fixtures |
| Detector distributions and candidates | IMPLEMENTED; acceptance BLOCKED by semantic/complexity defects |
| Public C05 overlay | IMPLEMENTED; authentication partly VERIFIED; row-identity acceptance BLOCKED |
| Full production performance/resources and protected deployment | NOT RUN |
| Cleaning, real review, C05 execution, tokenizer, training | OUT OF SCOPE; accidental authored C05 fixture execution disclosed above |

Next prompt: **Repair I01–I14 in a separate authorized implementation task, preserve
the measurement contract or version intentional changes, then rerun these exact
adversarial probes and the bounded quality acceptance selection. Do not start the
real audit or any transformation.** No full research campaign is an acceptance test.

FINAL VERDICT: QUALITY AUDIT NOT SAFE FOR REAL RUN
