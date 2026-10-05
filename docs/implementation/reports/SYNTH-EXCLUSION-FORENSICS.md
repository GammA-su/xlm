# SYNTH giant exclusion component: code forensics and read-only tool (2026-10-05)

Branch `analysis/mix01-deficit-remediation`. Inputs:

* repository code;
* the operator's real C05 decision-ledger aggregates (below).

Nothing on G:/X: was read. No acquisition, quota, C05 policy, adapter, manifest, C05
run or tokenizer change. The graph code below is byte-identical between the
production C05 checkout (`85e21f3`) and this branch: `git diff 85e21f3 HEAD` over
`dedup/lineage.py`, `exclusion/{grouping,publish,scanprep,factstore,policy,streaming}.py`
and `adapters/mix01_adapters.py` is empty.

Operator's real figures, SYNTH:

* 1,750,338 cleaned docs: 49,186 kept, 13 duplicate, 1,701,139 excluded;
* 1,747,820 duplicate groups;
* 42,028 families;
* the largest excluded component has 1,699,540 docs (1,699,538 SYNTH, 2 UltraX).

Gutenberg: 8,665 docs, of which 2,340 kept, 5 duplicate, 6,320 excluded.

## 1. How C05 builds groups, families and exclusions

`Grouper.run` (`exclusion/grouping.py:358-383`) builds two partitions over every input
document:

| term | ledger field | construction |
|---|---|---|
| **duplicate group** | `duplicate_group` | `dup = components(exact ∪ near)` (`grouping.py:367`) |
| **family** (= lineage group = connected component) | `lineage_group` | `fam = components(lineage ∪ parent, initial=dup)` (`grouping.py:373`) |
| **excluded family/component** | `decision = "excluded"` | `fam_hit = max(direct hit)` over the family (`grouping.py:820`); `publish.py:60`: excluded if the family has any direct hit, else the dup survivor is kept and the rest are duplicates |

These are the only edge types. Every one of them can join two families:

| # | edge | rule | crosses sources |
|---|---|---|---|
| 1 | exact duplicate | equal SHA-256 of normalized text | yes |
| 2 | near duplicate | MinHash 5-shingle, 128x32 bands, estimated Jaccard >= 0.8; buckets > 256 skipped; <= 64 candidates/doc | yes |
| 3 | lineage key `url:<canonical_url>` | metadata `url`/`source_url`/`canonical_url`, and **SYNTH `query_seed_url` and `additional_seed_url`** (`lineage.py:114-121`) | **yes, by design** |
| 4 | lineage key `metadata:<source:revision>:<rule>:…` | `lineage_id`, `lineage_group`, `synthetic_seed`, `seed`, conversation, book, document/article/page ids, `cluster_ids` | no (namespaced) |
| 5 | lineage key `parent:<source:revision>:<id>` | own id and `parent_ids` | no (namespaced) |
| 6 | parent edge | a `parent_ids` entry equal to any document id | yes |

`canonical_url` (`lineage.py:39`) drops the scheme, `www.`, the fragment, a trailing
slash and `utm_*`/`ref*`/`fbclid`/`gclid`/`session*`/`sid*` query parameters. It
keeps the host and path; the path is not lowercased.

Union-find gives the **transitive closure** of all these edges, and one direct hit
anywhere in the closure excludes every member. The same `fam` partition drives the
split (`grouping.py:854`), where transitivity is required, and exclusion.

**"42k families" is not 42k families merged into one.** 42,028 is the number of
*final* families that contain SYNTH rows: the giant component, plus 42,027 small
families that hold the other 50,800 SYNTH rows (49,186 kept). Inside the giant, about
1.7M duplicate groups (groups ≈ rows) are joined by lineage edges.

## 2. What the code and the real aggregates already prove

| candidate edge | verdict | proof |
|---|---|---|
| duplicate relation | **cannot create the component** | 1,747,820 groups for 1,750,338 rows is at most 2,518 duplicate unions |
| parent edges / `parent:` keys | **absent** | every Mix-01 adapter passes `parent_ids=[]`, `cluster_ids={}` (`mix01_adapters.py:195,199`) |
| `metadata:` rule keys | **absent for SYNTH** | SYNTH metadata keys (`synth_id`, `seed_license`, `exercise`, `model`, `words`, …) match no lineage rule name; `seed_license` is not `seed` |
| `url:` keys from `query_seed_url` / `additional_seed_url` | **the only SYNTH edge left** | `lineage.py:114-121`; both fields are populated on the sampled real row (`C05-GLOBAL-CONTAMINATION-PLAN/source-facts.json`) |
| benchmark-hit propagation | creates *exclusion*, not connectivity | `publish.py:60` |
| null/default identifier | excluded by code | blank and non-http(s) seed URLs make no key |

**Conclusion established from code:**

* The giant component is SYNTH rows joined through shared canonical seed URLs.
* Duplicate edges add only the cross-source passengers.
* One or more direct hits inside it exclude all 1.70M documents.
* At least 1,699,540 minus the number of direct hits are excluded **only through
  propagation**.

**What only the production run can decide.** It is still open which URL relation
bridges thousands of per-seed families:

* **(i) co-citation chains.** Every row with an `additional_seed_url` joins its own
  seed's family to another seed's family. With ~1.7M rows over tens of thousands of
  seed articles, the mean degree is far above the percolation threshold of 1, so a
  single component is the expected outcome, not an accident.
* **(ii) a hub.** One generic value (domain-level, dataset, placeholder) is shared
  by many rows.
* **(iii) over-merging** by `canonical_url`. This is unlikely for article URLs: only
  scheme, `www.`, fragment, trailing slash and tracking parameters are dropped.

The tool reports the dominant keys (salted fingerprints, member counts, URL shape,
producing field) and single-key removal probes. Under (ii), removing one key splits
the component; under (i) it does not.

**The two UltraX members.** UltraX metadata has no URL or lineage field (`uid`,
`upstream_source`, `processed_functions`: `mix01_adapters.py:738-752`), and
`parent_ids` is empty. Their only possible edge to SYNTH is a **duplicate edge**
(exact, or estimated Jaccard >= 0.8 with a SYNTH document). That is expected global
deduplication: they are passengers, not the bridge. The tool confirms the edge class,
their duplicate-group size and whether either one is itself a direct hit.

## 3. Is it scientifically intended?

* **Intent.** Contract C05 and `lineage.py` keep "synthetic examples sharing a seed"
  in one lineage group, so that a split cannot separate derivatives, and propagate a
  hit to "the whole known group".
* **Case A (correct lineage) for the same seed.** Every SYNTH row's training text is
  `Context: <query_seed_text>` + generated Q/A (`mix01_adapters.py:929`). Rows of one
  seed share that passage verbatim, so propagating within a seed family is
  defensible.
* **Case B (overly coarse) for transitive chains.** The *additional* seed's text is
  **not** in the training text. Take row R (seed A, extra B) and row R' (seed B,
  extra C). A hit in a C-derived row then excludes A-derived rows, although there is
  no derivation path from C's material into A rows' training text. Transitive closure
  is the right relation for split integrity. It is over-broad as a *contamination*
  relation: one partition serves both purposes.
* **If (ii) a generic hub value,** that is Case B, or arguably Case C (a
  non-identifying value used as a family identity).
* **Case C (adapter).** The adapter copies the fields verbatim. The family identity is
  chosen by lineage v2's SYNTH branch, not by the adapter. The semantically correct
  derivation identity is `query_seed_url`. `additional_seed_url` is a one-hop
  conditioning link, not a transitive family bridge.
* **Not a recommendation to weaken protection.** A derivation-based rule would still
  exclude:
  * every row whose own seed family holds a hit;
  * every row conditioned on such a seed (one hop);
  * every duplicate.

  The tool computes exactly that counterfactual next to the current number. If the
  direct hits turn out to cover most seed families, the issue is matcher precision,
  not lineage.

## 4. Remaining SYNTH inventory (484 usable files)

Under the current mechanism, a new row with a seed URL joins the giant whenever any
of its seed URLs reaches a seed already connected to it. Co-citation makes that near
certain, so the row is excluded. Only rows outside the URL graph form new families;
the late-quarter rate is 0.022192 new families per row, against 0.024011 overall.

| quantity | value |
|---|---|
| rows per file (cleaned, English) | 125,024 |
| kept per row, overall / adjusted for the late-quarter decline (x0.924) | 0.0281 / about 0.0260 |
| targets per kept doc | 683.6 |
| **expected kept targets per new file** | **about 2.2M** |
| all 484 files | about 1.07B, an upper bound (more co-citation edges absorb today's isolated families) |
| files for 866.4M at s = 1.25 | **about 488, more than the 484 available** |

**900M is not realistically attainable** from the remaining inventory under the current
mechanism. Even the arithmetic needs essentially the whole inventory (about 229 GB),
with no margin. The survivors would also be a biased subset, mostly rows without seed
URLs; the tool's `url_metadata_presence_by_state` and exercise labels measure that.
Under a reviewed derivation-based propagation rule the yield could be far higher. The
production run's counterfactual gives the exact current-file figure.

## 5. Gutenberg

Common Pile documents carry no URL or lineage metadata (`upstream_component` and
provenance strings only: `mix01_adapters.py:1485`) and `parent_ids` is empty. Their
families are therefore their duplicate groups. With 5 duplicates, **about 6,315 or
more of the 6,320 exclusions must be direct benchmark hits, not propagation**. That is
73 % of public-domain books (mean 108 KB) matching ARC-Easy, HellaSwag, PIQA or BLiMP
patterns. Per-document hit probability grows with length; generic or short patterns,
including the v4 `item_fallback`, are the leading suspect.

The tool's `--focus-allocation` together with `--benchmark-index` reports:

* direct vs propagated exclusions;
* distinct patterns and the documents per pattern;
* token length and pattern kind;
* hit rate by document size.

**Hold the 8-file Gutenberg top-up until then.** If a few generic patterns explain the
hits, it is a reviewed matcher-precision question. If the hits are diverse genuine
items, the top-up stands (its observed yield already includes the 73 % loss).

## 6. Read-only forensic command (X: attached; it writes nothing)

`scripts/c05_component_forensics.py` reads:

* the C05 plan;
* `decisions.jsonl` and `facts/*.unit` from the plan's scratch (the exact keys,
  parents and direct hits C05 used);
* optionally the cleaned corpus (`--read-corpus`: seed-URL field attribution,
  exercise labels);
* optionally the protected index (pattern kind and token length only).

It prints no text, ids, raw URLs or benchmark text. Identifiers appear only as
16-hex HMAC fingerprints with a per-run salt. Since the fast path ([C05-FORENSICS-FAST](C05-FORENSICS-FAST.md)) it projects to about 1.5-2.5 min at 8 workers, with live `[FORENSICS]` progress on stderr.
Check `$LASTEXITCODE` (0) before reading the redirected report.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m scripts.c05_component_forensics --plan G:/XLM/c05-clean-v1/p0001.json --read-corpus --focus-allocation common_pile_prose/common_pile_prose/project_gutenberg --focus-allocation synth_en_explanations/default/- --benchmark-index X:/C05-Protected/prepared-clean-v1/index.jsonl --workers 8 --progress-interval 5 | Set-Content -Encoding utf8 C:/XLM-scratch/c05-component-forensics.json
```

Read in the output:

* `reconstruction_complete` (must be true);
* `staged_union`: which class leaves one component;
* `each_class_alone`;
* `dominant_keys` and `single_key_removal_probes`: hub vs chain;
* `exclusion_counterfactuals`;
* `direct_hit_documents` and `hit_roots_before_bridging`;
* `non_majority_members`: the UltraX pair;
* `corpus_attribution`;
* `focus_allocations` for Gutenberg.

## 7. Tests

`tests/test_c05_component_forensics.py` builds **real authored C05 runs**, chain and
hub schemes, so C05 itself builds the graph. Each run has:

* about 3,000 SYNTH rows (thousands of independent groups);
* 60 seed families with mixed URL spellings;
* a transitive `additional_seed_url` chain, or one domain-level hub link;
* URL-less survivors;
* a planted direct hit;
* cross-source exact copies, for the FinePDFs and Wiki-Rewrite passengers.

It asserts:

* exact reconstruction against the ledger;
* the bridge class and its merges;
* the hub detected through key shape and the removal probe;
* the duplicate-only attachment of the cross-source members;
* direct vs propagated counts and the counterfactual bounds;
* URL attribution with zero missing keys;
* Gutenberg-style focus counts;
* no ids, URLs, text, roots or paths in the output.

Results: 14 passed, plus 2 for the summary script. ruff is clean. `mypy --strict`
reports only the environmental `import-untyped` for script-side `xlm` imports.
