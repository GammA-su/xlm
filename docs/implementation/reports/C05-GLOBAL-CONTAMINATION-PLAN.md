# Global C05 preparation audit (2026-10-02)

**C05 is partially implemented. No executable global plan exists yet.**
The complete first-pass input inventory is now reproducible offline, but protected
benchmark preparation, a production matcher/runner, and downstream membership
enforcement remain blockers. This report is an audit and implementation design,
not execution authorization. No full C05 run, tokenizer, final mixture freeze,
training, external network, upload or push occurred. Source pools were not modified.

## Worktree and binding evidence

The requested Common Pile worktree was clean on `feat/common-pile-allowlist`, HEAD
`737708e31268e8d63ee886619c764e83dd5475cc`, matching the expected pushed commit.
The starting shell was in `F:\Project\xlm-data-ultrax`, HEAD `8271505`, whose
STATUS/runbook edits and untracked operator evidence were preserved. Inspection
used `git status --short`, `git log -5 --oneline`, `git branch -vv`, and
`git worktree list` in both checkouts. The Common Pile commit includes both the
UltraX HEAD and IFM bounds branch as ancestors (`git merge-base --is-ancestor`,
both exit 0). No merge, reset or history rewrite was necessary.

Created `F:\Project\xlm-c05-global`, branch `feat/c05-global-preparation`, using:

```powershell
git worktree add -b feat/c05-global-preparation F:/Project/xlm-c05-global 737708e31268e8d63ee886619c764e83dd5475cc
```

This isolates the global continuation from operator acquisition work. It does not
declare a permanent integration branch. No other worktree was edited. The final
commit SHA is supplied in the chat handoff; `git rev-parse HEAD` resolves it.

[Evidence directory](../evidence/C05-GLOBAL-CONTAMINATION-PLAN/) contains the
manifest, preparation refusal, source facts, matcher characterization, command
arguments, exit statuses, wall times, sampled RSS and logs. All digests below are
stored artifacts, not sample hashes.

* Input manifest: `input-manifest.json`, digest
  `11724d92c011dd01e8e8c3ab944ac2adeef76aa8ff4abc921bf882eb0ac84152`.
* Preparation audit: `preparation-audit.json`, digest
  `76ca1070bdfc7060b01b2fffa71994836d8d6423577a48ada9476e94a8ad3bc0`.
* **C05 execution PLAN DIGEST: absent (`null`). Sequence/path: not allocated.**
  Neither digest above can authorize execution.

## What C05 means here and what exists

Binding sources: `CONTRACTS.md` C02/C03/C05/C06/C07 and C04 benchmark-risk v2/v3;
`EVALUATION_POLICY.md`; historical `reports/P10.md`, P11/P12 pool/tokenizer work,
P21 operator facilities, and P28 sharded deduplication. P10 explicitly certified
development-mode A19 only. A receipt interface is not a completed production scan.

C05 includes global exact/near deduplication before final splits, retained aliases,
known lineage grouping, diagnostic/quick/audit partitions independent of mixture
weights, and exclusion of **every benchmark split**, including development train
splits, before tokenizer fitting or gradient training. Source admission and first-
pass seals establish none of those later results. An official benchmark claim
additionally requires trusted protected exclusion evidence bound to evaluation
checkpoint/suite and exact training membership. No universal zero-contamination
claim is permitted.

| Surface | Existing behavior and limits |
|---|---|
| `data/dedup/matchview.py` | Match view v1: NFKC, Unicode casefold, replace non-word/non-whitespace characters with spaces, collapse whitespace. Underscores survive `\w`. Never writes this lossy view to model text. |
| `data/dedup/engine.py`, `minhash.py`, `index.py` | Exact matching plus deterministic BLAKE2b word shingles, MinHash/LSH; partitioned disk index. Defaults: 5-word shingles, 128 permutations, 32 bands, seed 20260919, estimated Jaccard >=0.8, 64 candidate comparisons/document, bucket cap 256. Oversized buckets are counted/skipped, not guaranteed recall. |
| `data/dedup/sharded.py` | Deterministic signature shards and bounded worker candidate verification; rereads input for survivor output. Failure aborts publication. Not an integrated resumable global C05 exclusion runner; document facts, union-find and other global structures still need scale/RAM certification. |
| `data/dedup/clusters.py` | Stable cluster IDs; survivor is longest canonical byte length, then smallest source ID, then doc ID. Source aliases retained. Lineage is not automatically duplication. |
| `data/dedup/lineage.py` | Explicit lineage, synthetic seed, conversation/book/document/page IDs, canonical URL, then parent/singleton. Non-URL metadata keys are source-namespaced. Uses first available lineage key and first sorted parent fallback. Does not infer missing book IDs or arbitrary semantic ancestry. |
| `data/pools/splits.py`, `freeze.py`, `builder.py` | Group-safe deterministic splits and leak checks; defaults 50 MiB diagnostic, 5 MiB nested quick, 5 MiB audit, seed 20260919. Late group merging invalidates old freeze. These functions have not been applied globally here. |
| `data/exclusion/benchmark.py` | Development exact full-example hash and informative spans. `scan` materializes all input documents; suppression and matching iterate corpus x indexed spans. No fuzzy/semantic benchmark matcher, global journal or bounded production runner. |
| `data/exclusion/receipt.py` | Opaque `FinalExclusionReceipt`, HMAC issuer trust, input/kept-membership/policy/index binding, protected/development separation. Existing membership hash is over sorted doc IDs; it does not alone bind the newly inventoried file content/seals. New production integration must bind both. |
| `operator/final.py`, evaluation suite/harness code | Official-claim gate verifies protected receipt and independently supplied checkpoint/suite/pool bindings. Existing CLI does not supply a production Mix-01 pool receipt. `prepare_exclusion_receipt` records counts; it is not a matcher. |
| `data/pools/tokenizer_fit.py`, `cli/tokenizer_cmd.py` | Checks/selects train split; **no C05 receipt or kept-membership gate** for raw first-pass input. A `split=train` row is not proof of exclusion. |
| `data/pools/manifest.py`, `cli/data_cmd.py`, `cli/mixture_cmd.py` | Policy strings and token-shard availability are not protected C05 verification. Some paths permit `none_declared`; final Mix-01 screening enforcement is missing. |
| New `data/exclusion/inputs.py` / `preparation.py` | Bounded read-only first-pass metadata inventory; exact baseline coverage; source seal, receipt, plan/accounting and size drift refusal. `inventory`, `verify`, `preflight`; no authorization/run command. Preflight exits 2 and writes a non-executable blocker audit. |

Existing CLI includes `xlm data dedup`, split/pool/tokenizer operations and operator
final-receipt facilities, but no complete production `c05 authorize/run/resume`
workflow. The new entry point is `python -m xlm.data.exclusion.preparation`.
It does not pretend to implement those missing verbs.

## Benchmark identities and isolation

`manifests/eval_dataset_pins.yaml` SHA-256:
`f230cea66b4bc24a484cff89d6780991e5ceeae89aa120ad4034ec51af1150dd`.
These are metadata pins already present in the repository, not new live evidence.
Harness dependency is locked to `lm-eval==0.4.13`.

| Task | Dataset | Immutable revision | Required configs / field mapping to validate |
|---|---|---|---|
| ARC-Easy | `allenai/ai2_arc` | `210d026faf9955653af8916fad021475a3f00453` | `ARC-Easy`; question and every choice text, not gold label alone |
| BLiMP | `nyu-mll/blimp` | `877fba0801ffb7cbd8c39c1ff314a46f053f6036` | Every publisher subdataset; both sentence variants |
| HellaSwag | `Rowan/hellaswag` | `218ec52e09a7e7462a5400043bb9a69a41d06b76` | Publisher config; context/ctx_a/ctx_b and every ending; reconcile harness preprocessing |
| PIQA | `baber/piqa` | `142f6d7367fd9877f0fb3b5734ea6a545f54cdd1` | Publisher config; goal and both solutions |

All publisher splits are required. Evaluation policy uses ARC train/validation/test,
HellaSwag and PIQA grouped train plus final validation, and whole BLiMP config
partitions. This does not license omission of any other published benchmark split.
Exact material config/split lists, item counts, duplicate item IDs, file SHA-256s,
render/signature counts and index identity are **NOT VERIFIED**; no values are
invented. Pin availability is not material availability.

The operator must inventory local material in the isolated preparation environment
first. Additional network acquisition is **unknown**, not automatically necessary.
No protected raw index, official examples, labels, benchmark caches or label-bearing
final definitions were opened. The agent-accessible G:\XLM artifact directory
metadata and repository contain no supplied verified protected preparation receipt.
This is not an assertion that no such receipt exists on another machine/account.

EVALUATION_POLICY requires final-data preparation under a different OS identity or
machine, outside the agent's permissions. The result returned here must be a trusted
content-free inventory/receipt with exact revision/config/split/count/file identities,
normalization/render version, signature policy/index identity, code/dependency
identity and isolation attestation. Do not return detailed matches or signatures:
hashes of public benchmark strings are not confidentiality protection. Source
manifest membership must be frozen before queries; no adaptive membership oracle.

The [runbook](../../runbooks/c05-global-preparation.md) supplies bounded future
metadata-only listing commands. Payload acquisition cannot yet be honestly supplied
as a reviewed executable command: exact files/formats/bytes and isolated destination
remain unresolved. No unrestricted `load_dataset` or `snapshot_download` substitute
is authorized. Remote code and automatic license acceptance remain prohibited.

## First-pass inventory and identities

Every sealed baseline pool is included: 10 source seals, 12 leaf views, 11 logical
components, **2,035 canonical JSONL files**. IFM general/planning share one logical
component; Common Pile's six upstream components remain separately identifiable.
Optional txt360 is explicitly excluded.

| Source seal | Exact stored digest |
|---|---|
| Essential-Web (all three views) | `a77c78f7636695ef0ab241c176e07cc9bcfd1815907a13f55dc2f6f7c28c7516` |
| UltraX | `efdb99dbf1bc8380985925fcbbe654536d02c5b0c87162eecc9e92e858302f1f` |
| FinePDFs | `9c2e38c36d2a6528027416347d20850ebd1f08cd036cf7aac1e98c6509fa3b3b` |
| SYNTH | `b020a96fcb14699fd56dfec72a917916f216eb50fd8b3d86bb53077daba6178a` |
| Wiki Rewrite | `08895109ddb498d6cc018b884f121905969435d13e969df5345278120f98f42e` |
| FineWiki | `18321fb27a5ac535190232ccb79ca1387469aa16cf1c45381d4d2f1666de1bf0` |
| IFM General | `7498428c0934be0b660e67154a1c1120ad2be06fd95d74ea958e426e804f3b81` |
| IFM Planning | `8ee097ba90403280ccfdeb578e64a80c7551777775ddf8a53b932e296c5dcc09` |
| Common Pile | `bd63336113131532c1470ff25d1e38c29bd3498a215deff9dcf54154d13491dd` |
| SimpleStories | `069fd0c7960a750ae24cc630921aa9086db0e44c6e56cf1d034afc346331d56d` |

**Prompt correction:** the supplied FinePDFs digest omitted its last `b` (63 hex
characters). The table uses the self-verified stored 64-character digest; no source
artifact was changed to fit the prompt. The other supplied seal identities match.

The manifest binds each canonical path/size/SHA-256, document and text-byte count,
source file/component/view, receipt, seal, source revision/adapter ID, plans,
recomputed accounting/sufficiency, and recorded adapter/admission inputs. Essential
campaign, batch, authorization and per-view membership bindings are preserved.
Document IDs and row locators are **transitively bound** by each complete document
file hash, not enumerated into a new document list. The source seals were self-
checked, their metadata cross-bindings reconstructed and all canonical file sizes
checked. We did not rehash 104.5 GB or revalidate every raw source payload. The future
authorized scan must hash bytes as it reads and refuse a same-size content change.
The preparation manifest explicitly records this limit; verification cannot silently
upgrade to content verification.

| Logical component | Documents | Canonical text B | Loss to first-pass byte target | Loss to final /4 estimate |
|---|---:|---:|---:|---:|
| essential_science | 315,173 | 2,692,218,118 | 1.940% | 10.854% |
| essential_practical | 1,278,649 | 6,962,804,542 | 62.084% | 65.531% |
| essential_prose | 4,239,644 | 20,071,278,364 | 93.423% | 94.021% |
| ultrax_ultrafineweb | 2,746,681 | 10,826,457,142 | 51.231% | 55.664% |
| finepdfs_en | 444,748 | 5,633,801,338 | 29.710% | 36.100% |
| synth_en_explanations | 1,751,337 | 4,771,301,422 | 17.004% | 24.549% |
| nemotron_wiki_rewrite | 1,000,000 | 3,635,905,169 | 41.913% | 47.193% |
| finewiki_en | 864,344 | 5,416,221,771 | 75.629% | 77.844% |
| ifm_behaviors_general_planning | 1,355,028 | 18,970,441,585 | 93.042% | 93.674% |
| common_pile_prose | 194,842 | 1,757,435,959 | 24.891% | 31.719% |
| simple_stories | 906,728 | 1,121,786,829 | 52.932% | 57.211% |
| **Total** | **15,097,174** | **81,859,652,239** | component-specific | not exact tokens |

Physical JSONL bytes to read once: **104,506,534,003**. The 15.1M count is before
global deduplication: not 15.1M unique independent works. Common Pile has 194,864
source rows and 194,842 accepted documents; these are different counters. Its seal
binds the expected p01 digest and 268 units. All six canonical requirements pass.

Headroom arithmetic is `(available - target) / available`, with first-pass and final
targets multiplied by 4. It is a sensitivity analysis, **not a contamination-rate
forecast**. Deduplication, filtering, split reservation and exact tokenizer ratios
all consume headroom. Essential Science is most sensitive by this measure. Aggregate
IFM/Common Pile headroom cannot replace their frozen internal allocations. News,
OER and PDR already consumed all eligible inventory; a later deficit can block the
mixture even when Gutenberg has surplus. No quota or source inventory was changed.

## Matching, false positives, SYNTH and Gutenberg

Current exclusion defaults: `span_length=13`, `min_span_characters=40`,
`max_spans_per_example=32`, `max_corpus_span_frequency=3`, both full and span
matching enabled. Full matching hashes the **entire document** against prompt plus
all answers in original order, so embedding or option permutation can defeat that
path. Span segments include prompt, prompt plus each answer and individual answers.
Stride is half-span; shorter-than-13-token segments can qualify by character count.
There is no entropy or unique-token floor. Only the first matching span/example is
reported. Duplicate full hashes overwrite an example entry; reported example counts
are not an authoritative benchmark item count. `scan` also mutates its index by
removing common spans, so partitioning/resume could change decisions.

`matcher-coverage.json` is an actual tiny authored-fixture run:

| Case | Observed |
|---|---|
| Exact full rendering | 1/1 excluded |
| Uppercase/punctuation prompt embedded in context | 1/1 excluded |
| Reordered choices with a long informative prompt | 1/1 excluded via prompt |
| Four prompt copies assigned different source IDs | **0/4 excluded**; repeated span suppressed |
| Short prompt embedded without answers | **0/1 excluded** |
| Paraphrase with no exact span | 0/1; no paraphrase guarantee exists |
| Five generic negatives: short answers, choice labels, ordinary phrases/fact | 0/5 false positives |
| Two SYNTH-shaped rows sharing `query_seed_url` | **not grouped** by lineage v1 |

0/5 is only an authored control result, not a measured corpus false-positive rate.
The two missed exact/prompt cases are coverage failures, not passed production
requirements. No weak fuzzy match deleted any real data. Existing corpus MinHash
is a near-duplicate heuristic, not a benchmark paraphrase detector, and its caps can
miss cross-source matches. A semantic GPU model would add tuning/compute/false-
positive risk; none was introduced.

The next matcher must be a **versioned extension of the existing exclusion surface**:
compile protected, task-aware exact full/prompt/answer variants and robust token-
boundary spans; preserve duplicate benchmark provenance; use a streaming index,
not a corpus x signature loop; never suppress repeated benchmark strings just
because training repeats them. Common boilerplate suppression must come from a
frozen, independently justified background policy, not contaminated corpus frequency.
Short BLiMP/PIQA prompts need explicit task-specific informativeness rules: applying
13 words universally misses them, while indexing `yes` or choice labels overdeletes.
Exact containment/option permutation, minimum lengths, distinct-token or entropy
thresholds and negative controls must be frozen and tested before a production plan.
No new thresholds are silently chosen by this audit.

Bounded fuzzy candidates may be recorded for operator review with matched fraction,
length/rarity thresholds, candidate caps and aggregate review limits. They must not
automatically exclude large amounts of data on weak similarity. No fuzzy kernel or
its false-positive calibration has been implemented/certified here; it remains a
separate explicitly bounded requirement, not an exact-exclusion guarantee.

SYNTH canonical text contains Context/query-seed text, Question/query, and Answer/
synthetic answer. Synthetic reasoning is excluded by the acquisition adapter; all
actually retained canonical text must be screened. Metadata retains `synth_id` and
optional seed URLs, but not a proven universal shared-seed lineage ID. Lineage v1
does not recognize `query_seed_url`/`additional_seed_url`. A versioned extension
should use available seed URLs and parent/duplicate relations without treating
each synth ID as an independent seed or exposing benchmark matches to the agent.

The bounded real provenance inspection read ten first rows, at most 2 MiB/row,
141,066 B per attempt (two identical reads after the wrapper-name correction),
and saved only metadata keys/locators/presence flags. Gutenberg's
sample has doc ID, source revision/file/row and `upstream_component`, but no parent,
cluster or book ID. Adapter v2 accepts text-only upstream rows. A row is a segment,
not proof of an independent book. The safe proposed exclusion unit is a canonical
row plus every **known** duplicate/derivative group member across sources. Unknown
book-wide propagation remains unprovable. Do not fabricate book IDs, infer books
from shard boundaries, or discard a whole multi-GB source file automatically. If
the required scientific policy demands whole-book exclusion, Gutenberg remains
blocked until real lineage is obtained or the operator explicitly reviews a
different source treatment. No such decision was made here.

## Output, downstream gates and recovery design (not implemented globally)

Source pools remain immutable. A production exclusion artifact should bind the
global input manifest; benchmark material/index and rendering policy; matcher,
dedup, lineage and split versions; code/dependencies; resource plan and authorization.
Within the protected environment, keep a deterministic ledger keyed by source seal,
file SHA, doc ID/row and exclusion reason/evidence identity. Propagate matches to
known groups and retain all aliases. Export only kept membership, opaque receipt,
content-free per-source/component counts and bounded aggregate reasons. Detailed
benchmark matches stay protected, even when evidence is hashed. No corpus text is
needed in the exported manifest or journal.

Tokenizer input must be training membership **minus exclusions**, verified against
the exact seal/content and protected receipt. Final exact-token down-selection must
use the same screened membership and unchanged component quotas. A receipt-ID string,
arbitrary `c05=true`, train split flag or a stale policy digest cannot pass. The
current tokenizer/final-mixture APIs do not enforce this end to end: they must not
be used for Mix-01 until that integration is implemented and tested. This audit
does not claim that the new preflight has repaired those APIs.

Required runner design: journal by immutable input-file identity and stage under a
plan lock; stream/hash each file; bounded record parsing; persist completed shards
with counts/digests; commit progress only after output fsync/atomic rename. Resume
verifies plan/index/source/code identity and every reusable shard, discards only
private incomplete staging under a validated output root, and restarts an interrupted
file deterministically. Exclusion keys are unique and final ledgers sorted, so replay
cannot duplicate drops. Track scratch plus partial/index/journal/temporary/publication
overlap against one aggregate disk cap; preserve spent deadline and record counts.
After every required file/group stage succeeds, verify aggregate membership/counts
and atomically publish a completion manifest. A crash before publication must leave
no apparently complete receipt. Lock/replay corruption, stale inputs and budget
exhaustion fail closed. Existing sharded dedup crash-abort behavior does not establish
this cross-stage resume contract; these C05 tests remain NOT RUN/unimplemented.

After exclusion and tokenizer freeze, count exact tokens per logical component and
its frozen IFM/Common Pile allocation. A deficit uses the remaining eligible cursor
of that component only, in a new reviewed acquisition plan/authorization. Re-screen
new material globally with existing groups; new group bridges invalidate affected
splits/membership and require a new pool/receipt lineage. Never reuse old C05 proof
for a changed corpus. No repeats, cross-component substitution, renormalization or
inventory reset. If a component is exhausted, stop for an operator decision. No
top-up or new source was acquired here.

## Resource design and measurements

CPU/disk deterministic processing is the recommendation. GPU is not used. One worker
for a bounded implementation pilot; consider four signature workers only after
measuring memory/I/O. This is distinct from 16-worker test execution.

One source read is 104.51 GB on disk; the text payload is 81.86 GB. Dedup survivor
output and protected screening may require additional reads. A pure I/O calculation
at hypothetical 100/500 MB/s is 1,045/209 seconds **per read**; neither is measured
throughput or a wall-time promise. NFKC/tokenization/hash generation and 128 MinHash
permutations may dominate CPU. The existing all-documents benchmark scan cannot
fit this corpus plus Python object overhead into the stated 72 GB RAM.

15,097,174 x 128 x 8 is **15,459,506,176 B** of raw uint64 signatures alone.
32 band postings/document means **483,109,568 postings** before keys, facts, union-
find, spill overhead and duplicates. Index storage cannot be certified from the
signature array alone. Proposed review targets: 24 GiB process-tree RAM, 192 GiB
aggregate scratch/index (including publication overlap), 32 GiB final metadata/
membership, 32 GiB free-space reserve. These are **unmeasured design targets, not
frozen plan limits**. They must be amended before execution if a measured bounded
pilot shows them insufficient. Final per-record, candidate, output, document and
elapsed-time ceilings remain to be made concrete with the actual benchmark index.
No compressed duplicate corpus or model weights are budgeted. No actual full C05
peak RAM, temporary/index footprint or throughput was measured.

Sampled available space: C 845,422,497,792 B, G 692,376,616,960 B. Runtime must check
again. Metadata inventory: 3.516 s, sampled process-tree RSS 106,254,336 B; this is
not a corpus scan benchmark. Authored matcher characterization: 0.703 s, 90,984,448 B.
RSS is sampled every 50 ms and can miss short peaks; wrapper wall includes startup.

## Validation and requirement ledger

Python 3.12.13, Windows-11-10.0.26200-SP0. Existing Common Pile CPU/eval environment
reused through `UV_PROJECT_ENVIRONMENT`; `PYTHONPATH` selects this worktree's `src`.
All commands use `uv run --offline --locked --no-sync`; no dependency sync/install
or lock change. `pyproject.toml`, `uv.lock`, `.python-version`, and the existing
mutually exclusive CPU/CUDA installation policy are preserved. HF Hub/datasets,
Transformers and uv offline flags were 1. Worker OMP/MKL/OpenBLAS/NumExpr threads
were 1; tokenizer parallelism false. These are test settings, not training changes.

* New focused input tests: 17 passed, exit 0, 4.843 s, 156,307,456 B sampled RSS.
* Related selection: 237 passed, exit 0, 13.219 s, 2,075,328,512 B sampled RSS;
  includes new Essential integration and exclusion/dedup/lineage/split/pool/tokenizer/
  suite/operator regressions. Authored acquisitions use loopback HTTP only.
* Complementary related serial/optional selection: 1 passed, 237 deselected,
  exit 0, 1.844 s, 114,925,568 B. No selected test was skipped.
  Focused and related selections overlap and must not be added as unique tests.
* First parallel selection exited 2 before running tests: the installed-harness
  optional test requires `-n 0` or audited loadgroup. It was moved to the explicit
  serial selection, not omitted. No retry-until-green was used.
* Initial authored characterization incorrectly assigned to a frozen dataclass;
  changed to `dataclasses.replace`. The failed command is preserved. An initial
  source-facts wrapper name collided with its output JSON name; the facts were
  retained, and a distinct command-evidence name records the successful invocation.
* Initial metadata prototype interpreted Essential authorization fields as self-
  digests; corrected to their existing declared authorization identities. No stored
  authorization was modified. Initial Ruff line-length findings were formatted/fixed.
* The first staged diff check exposed CRLF/trailing whitespace in captured Windows
  console evidence. Evidence text was normalized to LF with trailing whitespace
  removed; JSON values and artifact canonical digests are unchanged. The command
  recorder now does this explicitly and records the original stdout SHA-256 for
  subsequent commands. The failed check remains recorded, not recast as a pass.
* Final Ruff format/check and strict mypy over all seven touched Python files passed,
  exit 0; final cached mypy 1.032 s / 115,974,144 B sampled RSS (earlier check:
  28.000 s / 745,115,648 B). `git diff --check` is recorded
  separately in evidence. Complete repository acceptance, full-corpus
  C05, production resume/crash tests, live benchmarks, CUDA and performance tests
  were NOT RUN. Existing acquisition resume tests are not C05 resume certification.

| Requirement | Status |
|---|---|
| C05 contract/code/CLI/history map | VERIFIED audit |
| Eleven baseline components, ten seals, paths/revisions/metadata/counts | IMPLEMENTED / VERIFIED metadata-only |
| Corpus hashes / every doc ID reread | NOT RUN; bound transitively, mandatory scan-time check |
| Immutable source stores, optional txt360 exclusion | VERIFIED for preparation |
| Benchmark immutable revisions | VERIFIED repository metadata |
| Exact benchmark material/config/split/count/signature identities | BLOCKED: isolated operator preparation evidence missing |
| Development matcher and receipt interface | IMPLEMENTED previously / VERIFIED authored scope |
| Production exact/prompt coverage and scalable matching | BLOCKED: demonstrated coverage/performance gaps |
| Fuzzy detection and population false-positive estimate | NOT IMPLEMENTED / NOT RUN; no claim |
| Global lineage propagation incl. SYNTH; Gutenberg whole-book lineage | BLOCKED; known keys insufficient, no invented IDs |
| Resume/crash-safe global runner and atomic exclusion publication | NOT IMPLEMENTED / NOT RUN |
| Tokenizer/final-mixture C05 membership enforcement | NOT IMPLEMENTED end to end; blocked |
| Resource sizing and component-headroom sensitivity | VERIFIED arithmetic; production measurements NOT RUN |
| Executable plan, sequence, authorization/run/post-run commands | BLOCKED; no plan digest, no authorization |
| Tokenizer, final 6B freeze, training, CUDA, push | OUT OF SCOPE / NOT RUN |

## Exact stop and continuation

Stop is **verified metadata manifest plus a non-executable preflight refusal**,
not “ready for authorization.” There is no honest `c05 run` or post-run verification
command to give yet. Use the runbook's offline `verify` command (exit 0) or `preflight`
(expected exit 2) to reproduce this state. The new implementation is complete only
for preparation inventory/refusal; the full requested production preparation is
not complete. The missing runner and gates are engineering work, not cured merely
by authorizing a run.

Next prompt:

> Continue in F:\Project\xlm-c05-global on feat/c05-global-preparation. Read this
> report and evidence; verify the input manifest offline. Preserve all source seals,
> quotas and benchmark pins. Complete the existing C05 exclusion surface with a
> versioned streaming matcher, known-lineage propagation, bounded resumable runner,
> protected publication and tokenizer/final-mixture membership gates. Keep authored
> tests separate from live evidence. Obtain only content-free benchmark preparation
> metadata from the isolated operator environment; do not open final examples or
> raw signatures in the agent workspace. Freeze an executable plan only once exact
> protected material coverage and resource bounds verify. No full C05 run, external
> network, tokenizer, training, source mutation or push is authorized.

C05 GLOBAL PLAN NEEDS FURTHER WORK
