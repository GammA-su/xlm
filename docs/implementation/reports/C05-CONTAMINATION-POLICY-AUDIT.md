# C05 contamination-policy audit: matcher precision and lineage propagation (2026-10-06)

Branch `analysis/c05-contamination-policy-audit`, from `d1c9e0f` (fast forensics). Analysis
and read-only tooling only. Nothing in production was changed: no recipe, quota, C05 policy,
acquisition, cleaning, C05 run or tokenizer fit. X: (protected volume) was not attached in
this session, so the **production counterfactual has not been run**. Every number below is
either quoted from the operator's production forensic report
(`C:/XLM-scratch/c05-component-forensics-fast.json`) or measured on authored fixtures, and
each one is labelled as such.

## 1. Result

| question | answer |
|---|---|
| matcher root cause | short WHOLE-variant patterns: a rendered prompt of 4-13 normalized tokens, or a BLiMP sentence of 3-13, is emitted as one exact pattern. The frozen floors are prompt 4 tokens / 16 characters / 3 distinct and sentence 3 / 12 / 3. Any occurrence anywhere in a document marks it. One mark excludes the whole lineage family. |
| why it fires widely | the trigger behaves like a common English n-gram: the chance that a document contains it grows with document length. Gutenberg direct-hit rate rises 4.0 % -> 47.7 % -> 82.4 % across the size buckets. That is the signature of chance occurrence, not of copying. |
| lineage | `additional_seed_url` turns 40,007 query-seed families into one 1,699,540-document component. Transitive closure has no contamination meaning across seeds. Query-seed families (B) or one hop (C) bound the propagation. |
| candidate matchers | `floor8` (one 8-token / 40-character / 5-distinct floor for every kind), and `floor8_pair` (`floor8` plus a same-item, co-located, non-overlapping pair of short patterns). `prompt8` is a diagnostic. No list of patterns is allowed or blocked. |
| recall | made explicit per task/split and pattern kind/length. Authored injected-copy tests show what each candidate keeps and what it gives up (section 7). The production tables come from the stage-1 run. |
| tool | `scripts/c05_policy_counterfactual.py` (`audit`, then `project-tokens`). It reproduces production exactly, then evaluates 4 matchers x 3 lineage policies and projects exact token supply under the current tokenizer. |
| production run | **not run** (X: detached here). Estimated 9-11 min full scope at 8 workers, about 4-5 min focused, plus 3-4 min for the token stage (section 9). |

**C05 POLICY AUDIT NEEDS PRODUCTION COUNTERFACTUAL**: whether the acquired corpus meets
every frozen 6B quota under a defensible policy can only come from the two commands in
section 9.

## 2. The production matcher, traced (85e21f3 = d1c9e0f for every matcher file)

`git diff 85e21f3 d1c9e0f` is empty for `streaming.py`, `compact.py`,
`prepare_workers.py`, `protected.py`, `policy.py`, `scanprep.py`, `grouping.py`,
`publish.py`, `factstore.py`, `runner.py`, `dedup/matchview.py` and `dedup/lineage.py`.
The frozen production policy is `G:/XLM/c05-clean-v1/policy-value.json`, matcher
`c05-matcher-v4`.

**Rendering** (`streaming.render`, `task-render-v3`). Variants per item:

| task | `prompt` variants | `answer` variants | `combined` variants |
|---|---|---|---|
| ARC-Easy | question | every choice | question + each choice; question + all choices |
| PIQA | goal | sol1, sol2 | goal + each; goal + both |
| HellaSwag | `ctx`; `ctx_a`; `ctx_b`; `ctx_a ctx_b`; the lm-eval 0.4.13 preprocessed form of each; `activity_label: ` + each, preprocessed | endings and preprocessed endings, deduplicated | every prompt + every ending; every prompt + all endings |
| BLiMP | none; `sentence` kind: `sentence_good`, `sentence_bad` | none | none |

Labels are never read. **c05-matcher-v4 fallback**: an item with no normal pattern gets
one exact pattern, its label-free whole-item composite (floor 4 / 16 / 3).

**Normalization** (`match-view-v1`). The steps:

* NFKC, then casefold;
* every non-`\w`, non-space character becomes a space;
* whitespace collapses;
* tokens are split on spaces.

The `distinct` count is the number of distinct tokens with at least two alphabetic
characters.

**Pattern construction** (`streaming.patterns`). For each variant:

1. The whole variant must pass its kind's floor (tokens, characters of the space-joined
   view, distinct).
2. If it has at most `span_tokens` = 13 tokens, the **whole variant is one pattern**.
3. Otherwise it yields 13-token windows at stride 6, at most 32, each re-checked against
   the floor.
4. Patterns are deduplicated exactly per item (tokens, `ref:kind`). One index line holds
   one (tokens, provenance) pair.

Frozen floors:

| kind | tokens | characters | distinct |
|---|---:|---:|---:|
| prompt | 4 | 16 | 3 |
| sentence | 3 | 12 | 3 |
| answer | 8 | 40 | 5 |
| combined | 8 | 40 | 5 |
| item fallback | 4 | 16 | 3 |

No corpus-frequency suppression applies (deliberately, by contract), and the frozen
`background` list is empty.

**Matching** (`compact.CompactExactMatcher`, called by `scanprep`):

* Input: the whole document's normalized token sequence.
* An occurrence is any contiguous, token-aligned, exact sequence equal to a pattern. It is
  not a character substring: `sing` never matches inside `sings`.
* No overlap rule, minimum count, length normalization or position constraint applies.
* The fact unit stores **one** hit per document: the pattern that ends earliest (longest
  on ties).

**Decision** (`grouping.families`, `publish.publish_rows`):

* A family is hit if any member has a hit. Families are the transitive closure of
  duplicates, every lineage key and parent edges.
* Every member of a hit family is `excluded`. Otherwise a member is `kept` if it is its
  duplicate group's survivor, else `duplicate`.

**How a 3-5-token pattern becomes an exclusion trigger.** Any of these is emitted as one
pattern on its own:

* a prompt variant with 4-13 tokens and at least 16 characters;
* a BLiMP sentence with 3-13 tokens and at least 12 characters.

That covers short ARC questions, PIQA goals and HellaSwag `ctx_a`/`ctx_b`. `ctx_b` is by
construction the opening fragment of the sentence the endings complete. The
activity-prefixed and preprocessed forms add more. One occurrence of those 4 tokens
anywhere in a 500 KB book excludes the book and its whole lineage family.

## 3. Why short patterns fire so widely

Production observations (operator forensic report):

* Fingerprint `199bc1956aa069d8` is first hit in 25,786 SYNTH documents and 4,389
  Gutenberg books.
* Across all non-SYNTH allocations, 649,597 of 649,745 exclusions (99.98 %) are direct
  matcher hits.
* Direct hits run from 5 % to 9 % of documents in every web allocation, for example
  UltraX 135,915 and essential_prose 254,636.

A fixed n-gram occurs at a rate q per token position. The chance that an n-token
document contains it is about 1 - exp(-q * n). Gutenberg's direct-hit rate by size
fits that law:

| size bucket | hit rate |
|---|---:|
| < 10 KiB | 4.0 % |
| 10-100 KiB | 47.7 % |
| 100 KiB-1 MiB | 82.4 % |

Fitting the middle bucket (about 7k words) gives q of about 1e-4 per word, the frequency
of an ordinary phrase. A copied benchmark item does not become 20 times likelier because
a book is longer.

What is known about `199bc...` from the production index alone:

* kind `prompt`, exactly **one** provenance record, so one benchmark item;
* 4 normalized tokens; by construction at least 16 characters and at least 3 distinct
  two-letter tokens;
* it can only be a whole 4-token prompt variant: HellaSwag `ctx`/`ctx_a`/`ctx_b`/
  preprocessed/activity form, PIQA goal, or ARC question. Most of these are complete
  questions only for ARC/PIQA. For HellaSwag `ctx_b` it is a dataset sub-field
  fragment.

These still need the production run:

* its suite/task/split (`items_by_task_split`, from the receipt);
* its render slot and whether that slot is a complete published field, a sub-field
  fragment, preprocessed or a renderer composite (`render_slots[...].class`, needs
  `--benchmark-material`);
* character length, distinct count, allocations hit and DF rank (`top_patterns[0]`).

To identify it without the old salt, take the `top_patterns` entry whose
`first_hit_documents_by_allocation` shows SYNTH 25,786 and Gutenberg 4,389. No text is
printed.

The global 3-5-token census is in `short_pattern_census`:

* distinct patterns by kind and length;
* items with a 3-5-token pattern, per task/split;
* documents whose recorded first hit is 3-5 tokens;
* rescanned documents whose every occurrence is 3-5 tokens;
* rescanned documents still hit per candidate.

## 4. Lineage policies (evaluated separately from the matcher)

| policy | definition | production evidence (current matcher, SYNTH giant only, forensic star approximation) |
|---|---|---|
| A `current_transitive` | undirected closure over duplicates, parents, every lineage key including both seed-URL fields | 1,699,540 excluded from 31,280 direct hits |
| B `query_seed_family` | the same closure with `additional_seed_url` ignored: the seed article (plus duplicates, parents, all other keys) defines the family | 950,226 |
| C `query_seed_one_hop` | B families; a hit family also excludes every B family linked to it by one `additional_seed_url` key; never further | 1,146,892 |
| duplicates/parents only | | 31,405 |

Assessment:

* **A has no contamination meaning across seeds.** Take X (seed P, context Q), Y (seed Q,
  context R) and Z (seed R). Z shares no source text with X. Removing any single major URL
  leaves the giant connected, so this is a dense co-citation graph, not one bad key.
* **B** matches the actual risk path. If a seed article carries benchmark text, the rows
  generated from it can restate it. 1,560,060 giant members are `memorization`
  exercises.
* **C** adds a bounded margin for leakage through context articles, in both directions,
  one hop only.
* The current-matcher B/C figures are large because 31k scattered direct hits each
  remove a ~40-row seed family. Under a precise matcher, B/C exclusion shrinks with the
  hits. The tool's C is exact and global (family x key bipartite one hop). The
  forensic figure above was a star approximation inside the giant, so the two may
  differ.
* **Split integrity is a separate question.** Grouping by A for diagnostic/audit leakage
  prevention costs about 55 MB and can stay. B/C here change only which families are
  excluded.

## 5. Matcher candidates (data-only, frozen in `candidates.CANDIDATES`)

| name | rule | justification |
|---|---|---|
| `current` | every frozen pattern triggers alone | baseline; must reproduce production |
| `prompt8` | prompt and item-fallback patterns need 8 / 40 / 5; sentence floor unchanged | diagnostic: isolates the prompt fix (keeps 3-token BLiMP triggers, equally non-specific) |
| `floor8` | every kind needs 8 / 40 / 5 | the frozen answer/combined floor applied uniformly. Exact-overlap screens in the literature use 8-13-word n-grams or ~50-character substrings for this specificity reason. Long exact matches (13-token windows, combined prompt+answer, long fields) are kept. |
| `floor8_pair` | `floor8`, plus a hit when two DIFFERENT short patterns of ONE benchmark item occur without overlap, within 64 normalized tokens, covering at least 8 tokens together | restores detection of copies of short items whose evidence is several fields, such as both BLiMP sentences or a short question with its options. One common phrase, or the same phrase repeated, never qualifies. |

How the candidates are evaluated:

* Without rebuilding the protected index. A candidate keeps the frozen patterns that pass
  its floor for at least one provenance kind. That is exactly the set a rebuild would
  emit for those kinds, except that a rebuilt v4 might add new whole-item fallbacks for
  items left with no signature (not evaluated; it could only raise recall).
* Every rule depends only on pattern kind, length, item structure and position. None
  depends on corpus frequency. No pattern (including `199bc...`) is singled out.

What the candidates deliberately give up (shown per task in the recall audit):

* a bare short prompt (4-7 tokens) without its options no longer excludes on its own;
* a tiny item with no 8-token signature and fewer than two short patterns cannot be
  detected at all, even as a full copy (for example a 5-token goal with yes/no options).

## 6. The read-only tool

`scripts/c05_policy_counterfactual.py audit` ([counterfactual.py](../../../src/xlm/data/exclusion/counterfactual.py)).
X: must be attached. It writes nothing except an optional state file.

1. **Plan, group seal, group arrays.** Every group file is re-hashed against the signed
   seal.
2. **Ledger** (parallel). Totals must equal the plan and the completion. Ledger order
   must equal the C05 dense order.
3. **Fact units.** Recorded hits. Lineage-key digests and parent edges of every document
   in an A family that contains SYNTH.
4. **Protected index** (parallel). SHA-256 and bytes must equal the receipt. Each
   provenance is mapped to its benchmark item and task/split by recomputing receipt
   references (no protected text needed).
5. **Compiled matcher.** The production matcher is opened read-only with every file
   re-hashed. Pattern identities are recomputed in bulk, and the pattern set must equal
   the index's.
6. **Corpus** (8 workers). For every production direct hit in scope, every exact
   occurrence is found (`OccurrenceMatcher`, the compiled matcher without its early
   stop) and every candidate is evaluated. The first occurrence must equal the recorded
   hit. For every SYNTH row in a changeable family, `known-lineage-v3` keys are
   recomputed with and without `additional_seed_url` and must equal the fact unit.
   Files are read once, hashed and verified (size, SHA-256, rows, unchanged mtime). The
   parent sends byte spans and workers re-read them from the page cache.
7. **Lineage.** The graph of every SYNTH-containing family is rebuilt and must reproduce
   `fam.u32` exactly. B and C are derived from it.
8. **Matrix** (4 x 3). For each cell:
   * the C05 decision rule and the historical greedy diagnostic/audit split are applied;
   * `current x A` must equal every ledger decision and split and the group's family
     splits, else the run refuses;
   * per allocation it reports: direct hits, propagated exclusions, kept, duplicate,
     kept canonical bytes, train documents/bytes, recovered vs production, moved
     into/out of train, conservative unverified hits.
9. **Census, recall and slots.** Top patterns by first-hit DF, the 3-5-token census and
   index-level recall. With `--benchmark-material`, injected-copy recall and render
   slots.

`--rescan-allocation` limits the rescans. Out-of-scope hits whose recorded pattern fails
a candidate stay hits (conservative) and are counted.

`project-tokens` ([supply.py](../../../src/xlm/data/exclusion/supply.py)) runs with
**X: detached**:

* It reads the state file (plan file/row/allocation/per-cell train bit; no text), the
  plan, signed `counts.json`, the deficit report and the tokenizer. The tokenizer must
  match the counts' fingerprint `8ef1a2dd...`.
* Documents that leave train are always tokenized.
* Documents that enter train are tokenized for DEFICIT allocations (or
  `--token-allocation`). Elsewhere the projection is a stated lower bound.
* Per cell and allocation: projected valid targets, deficit/surplus, status, and
  `all_frozen_quotas_met`.
* It is labelled **counterfactual supply under current tokenizer**. A fresh C05 would
  require refitting C06.

Every report is content-free (self-checked; no URL, no 32+-hex outside named digest
fields), and so is every refusal (fixed reason, stage).

## 7. Protected-benchmark recall on the authored tests

Fixture: a real authored C05 run (3,346 documents). Its benchmark:

* ARC: the flow's 7-token question with yes/no, and a 13-token question with 4 options;
* PIQA: a 5-token goal with yes/no;
* BLiMP: a 4-token sentence pair;
* HellaSwag: a 5-token context with yes/no, and an item whose `ctx_b` is a 4-token
  fragment planted as ordinary prose in every ninth corpus row.

Evidence: [authored-audit.json](../evidence/C05-POLICY-AUDIT/authored-audit.json).

Index-level (standalone signature / pair-only / unsigned, out of items):

| task | current | prompt8 | floor8 | floor8_pair |
|---|---|---|---|---|
| ARC (2) | 2/0/0 | 2/0/0 | 2/0/0 | 2/0/0 |
| BLiMP (1) | 1/0/0 | 1/0/0 | 0/0/1 | 0/1/0 |
| HellaSwag (2) | 2/0/0 | 1/0/1 | 1/0/1 | 1/0/1 |
| PIQA (1) | 1/0/0 | 0/0/1 | 0/0/1 | 0/0/1 |

Injected exact copies (item between neutral filler), detected / items:

| task, form | current | prompt8 | floor8 | floor8_pair |
|---|---|---|---|---|
| ARC whole item | 2/2 | 2/2 | 2/2 | 2/2 |
| ARC question only | 2/2 | 1/2 | 1/2 | 1/2 |
| BLiMP sentence pair | 1/1 | 1/1 | 0/1 | **1/1** |
| BLiMP one sentence | 1/1 | 1/1 | 0/1 | 0/1 |
| HellaSwag whole item | 2/2 | 1/2 | 1/2 | 1/2 |
| HellaSwag context only | 2/2 | 1/2 | 1/2 | 1/2 |
| PIQA whole item | 1/1 | 0/1 | 0/1 | 0/1 |
| PIQA goal only | 1/1 | 0/1 | 0/1 | 0/1 |

The losses are exactly the authored tiny items (5-token context or goal with yes/no
options, 7 tokens in all) and bare short prompts. Real ARC/HellaSwag/PIQA items are
usually longer. **Their production share is what the stage-1 recall tables measure; do
not adopt a candidate before reading them.** The planted 4-token HellaSwag fragment was:

* top pattern, rank 1;
* hit in 17 of 17 allocations;
* attributed to render slot `hellaswag.ctx_b`, class `published_subfield_fragment`;
* a trigger only under `current`.

The planted long ARC copy stays excluded under every candidate. Matrix totals
(direct hits / excluded / kept):

| | A | B | C |
|---|---|---|---|
| current | 57 / 2,986 / 349 | 57 / 206 / 3,127 | 57 / 500 / 2,834 |
| floor8 | 1 / 2,928 / 403 | 1 / 49 / 3,280 | 1 / 149 / 3,181 |
| floor8_pair | 2 / 2,930 / 402 | 2 / 51 / 3,279 | 2 / 151 / 3,180 |

Authored token projection (stage 2), SYNTH quota 200,000:

| cell | projected SYNTH valid targets | status |
|---|---:|---|
| `current x A` | 36,059 (equals counts) | DEFICIT |
| `floor8_pair x B` | 1,321,078 | SUFFICIENT |
| `floor8_pair x C` | 1,276,860 | SUFFICIENT |
| any matcher x A | about 36k | DEFICIT |

So on the authored data, the transitive lineage alone keeps SYNTH in deficit.

## 8. Performance (authored, measured; 5700X3D, C: NVMe; one run each)

| measurement | result |
|---|---|
| stage 1 on the 895,136-document forensic bench fixture (292k-document SYNTH giant), 8 workers | **12 s** total; corpus stage 318 MiB/s; peak RSS 0.8 GiB |
| same, 4 / 16 workers | 15 s / 13 s (8 is optimal); reports byte-identical at 4/8/16 and to the pre-optimization report |
| before span re-reads and continuous task streaming | 22 s, corpus 107-142 MiB/s (Windows pipe bound; per-file drain) |
| `occurrences` vs historical `match`, 3M-pattern authored compiled index (Zipf vocabulary) | 2.01 vs 2.59 M tokens/s per core; compile 42.6 s, verified open 0.8 s |
| index parse | 79k lines/s per worker |
| bulk pattern identities | 387k patterns/s per worker |
| real tokenizer `8ef1a2dd...` on a 20 MiB SYNTH sample from G: | 3.69 MiB/s per worker; 543 valid targets/document; 0.200 targets/byte |

Production projection, stage 1, full scope, 8 workers:

| stage | basis | estimate |
|---|---|---|
| ledger | 12.6M rows at ~375k rows/s | 35 s |
| index, identities, matcher open | 9.47M lines; ~9M patterns | ~1 min |
| corpus | ~103 GB of plan files at ~0.3 GB/s parent rate; rescan CPU for ~682k hit documents overlaps | ~6 min |
| SYNTH attribution | 1.75M rows | ~10-20 s |
| lineage, 12-cell matrix, injected recall (153k items) | | ~1.5-2 min |
| **total** | | **9-11 min** |

Focused scope (SYNTH, Common Pile, essential_science, FinePDFs; about 18 GB): about
4-5 min. Stage 2 is CPU-bound tokenization, up to about 1.7M SYNTH rows (about 4.6 GB of
text) at 8 x 3.69 MiB/s, so about 3-4 min at 8 workers.

Sample estimate, not exact: SYNTH's total supply is roughly 0.95-1.2B valid targets
(543-684 per document x 1.75M) against a 900M quota. A defensible policy must therefore
keep SYNTH exclusions to a small fraction of SYNTH. Stage 2 gives the exact number.

## 9. Production commands (operator)

Stage 1, X: and G: attached, read-only:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m scripts.c05_policy_counterfactual audit --plan G:/XLM/c05-clean-v1/p0001.json --benchmark-index X:/C05-Protected/prepared-clean-v1/index.jsonl --benchmark-receipt G:/XLM/c05-clean-v1/benchmark-preparation.receipt.json --benchmark-material X:/C05-Protected/material --state-out C:/XLM-scratch/c05-policy-audit.state.npz --workers 8 --progress-interval 5 | Set-Content -Encoding utf8 C:/XLM-scratch/c05-policy-audit.json
$LASTEXITCODE   # 0 = report; then check reproduction.* are all true
```

Faster focused variant: add these, and read `conservative_unverified_hits`:

```text
--rescan-allocation synth_en_explanations/default/-
--rescan-allocation essential_science/essential_science/-
--rescan-allocation finepdfs_en/eng_Latn/-
--rescan-allocation common_pile_prose/common_pile_prose/project_gutenberg
--rescan-allocation common_pile_prose/common_pile_prose/pressbooks
--rescan-allocation common_pile_prose/common_pile_prose/libretexts
--rescan-allocation common_pile_prose/common_pile_prose/oercommons
--rescan-allocation common_pile_prose/common_pile_prose/public_domain_review
```

The state file must not exist yet.

Stage 2, **detach X: first**:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m scripts.c05_policy_counterfactual project-tokens --state C:/XLM-scratch/c05-policy-audit.state.npz --plan G:/XLM/c05-clean-v1/p0001.json --counts G:/XLM/counts/mix01-clean-v1/counts.json --deficit-report G:/XLM/selection/mix01-clean-v1.deficit.json --tokenizer G:/XLM/tokfit/mix01-fit-shares-v1-clean-v1/tokenizer --expect-fingerprint 8ef1a2dde17084f34e216c0595527d7cd02b1252cb742bf6714b18b40e1eb965 --workers 8 --progress-interval 5 | Set-Content -Encoding utf8 C:/XLM-scratch/c05-policy-supply.json
$LASTEXITCODE
```

PowerShell creates the redirect target even on refusal. Always check the exit code.

How to read the results for question 7:

* Choose among `floor8_pair x query_seed_one_hop` (recommended if its recall tables are
  acceptable), `floor8_pair x query_seed_family` and the `floor8` cells.
* Do this only after reading `injected_copy_recall` and
  `benchmark_recall_index_level` for every task/split.
* If the chosen cell has `all_frozen_quotas_met: true`, no top-up is needed yet.
* Otherwise its `remaining_deficits` are the top-up numbers.

## 10. Tests and commands

Each command was run from `F:/Project/xlm-c05-policy-audit` with
`OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1` and `TOKENIZERS_PARALLELISM=false`:

| command | result |
|---|---|
| `uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_c05_policy_counterfactual.py tests/test_c05_policy_candidates.py -n 0 -q -p no:cacheprovider --basetemp=C:/t/pa7` | **49 passed**, exit 0 (42 s) |
| `ruff check` and `ruff format --check` (new files) | clean |
| `mypy --strict` (`candidates.py`, `counterfactual.py`, `supply.py`, script) | no issues |
| `git diff 85e21f3 d1c9e0f -- <matcher files>` | empty |

`tests/test_c05_policy_candidates.py` (25 tests):

* `occurrences` equals brute force on random documents, and its first item equals the
  historical `match`;
* bulk identities equal `canonical.digest`;
* the standalone mask equals `streaming.informative`;
* frozen floors;
* the pair rule (8 cases);
* the slot renderer equals `streaming.render`;
* recall tallies, buckets, injection forms;
* span re-read and changed-file refusal.

`tests/test_c05_policy_counterfactual.py` (24 tests, one real authored C05 run):

* exact reproduction;
* **all 12 matrix cells equal an independent naive oracle** (brute-force matching,
  Python union-find over recomputed lineage keys and duplicate groups, the survivor
  rule);
* propagation bounds on the seed chain;
* short-phrase recovery versus kept true copies;
* index and injected recall; render slots;
* census;
* content-free stdout and stderr; worker identity (1 vs 4);
* refusals (short ledger, changed index, existing state);
* conservative rescan scope;
* exact token projection, re-tokenized independently; wrong-tokenizer refusal.

Not run: the full offline acceptance suite, any production command, X:-dependent steps.

## 11. Requirement ledger

| requirement | status |
|---|---|
| worktree from `d1c9e0f`, branch `analysis/c05-contamination-policy-audit` | IMPLEMENTED |
| 1 matcher audit at 85e21f3 (generation, fields, floors, normalization, algorithm, overlap, decision) | IMPLEMENTED (code trace, section 2) |
| 2 lineage A/B/C evaluated separately from the matcher; split grouping kept separate | IMPLEMENTED, VERIFIED (authored oracle) |
| 3 principled candidates, no whitelist/blacklist | IMPLEMENTED |
| 4 recall per task/split, kind, length; self/exact-copy and injected-copy detection | IMPLEMENTED, VERIFIED (authored); production NOT RUN |
| 5 read-only counterfactual, current x current reproduces production, per-allocation tables | IMPLEMENTED, VERIFIED (authored and 895k bench); production NOT RUN |
| 6 exact token projection under the current tokenizer | IMPLEMENTED, VERIFIED (authored); production NOT RUN |
| 7 can the acquired corpus meet every frozen quota | BLOCKED on the production run (X: detached) |
| 8 `199bc...` sanity checks; global 3-5-token census | IMPLEMENTED in the report; partly answered (section 3); production NOT RUN |
| 9 fast path, stderr progress + ETA, 8 workers | IMPLEMENTED, VERIFIED (bench) |
| 10 no production modification | VERIFIED: writes only in this worktree and authored scratch under C:/t; production artifacts were only read (plan, receipt, policy value, counts, deficit report, tokenizer, the forensic report, a 20 MiB SYNTH sample) |

## 12. Limitations

* Candidates are subsets of the frozen index. New v4 whole-item fallbacks that a rebuild
  would emit are not evaluated.
* B/C rebuild only families that contain SYNTH rows. Only SYNTH rows produce seed-URL
  keys, so every other family is unchanged by construction.
* All-occurrence DF covers rescanned documents only. First-hit DF covers every
  document.
* Runtime projections extrapolate authored benchmarks and one 20 MiB real-tokenizer
  sample. G: read rate is the main uncertainty.
* `prompt8` is diagnostic. It keeps 3-token sentence triggers, which are as
  non-specific as 4-token prompts.
