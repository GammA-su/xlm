# Mix-01 deficit remediation analysis (read-only, 2026-10-05)

Branch `analysis/mix01-deficit-remediation`, from `6e5cf11`. Inputs: repository code,
frozen inventories and planners, committed evidence, and the deficit figures supplied
by the operator from the real exact selection. Nothing on G:/X: was read. Nothing was
acquired, and no quota, cleaning, C05, C06 or count artifact was changed or rerun.

## 1. Evidence used

| fact | source |
|---|---|
| Acquired documents, canonical bytes and files per allocation | original input manifest `11724d92…` (`evidence/C05-GLOBAL-CONTAMINATION-PLAN/input-manifest.json`) |
| Per-source plans, cursors and sufficiency seals | same manifest, `sources[].plans`, `sources[].sufficiency` |
| Cleaned corpus size | 15,087,207 docs / 81.37 GB (`reports/COUNT-TOKENS-FAST.md`) vs 15,097,174 acquired |
| C05 kept / kept train | 12,624,198 / 12,613,085 (operator) |
| Exact eligible targets, quotas, deficits | operator (real deficit report) |
| C05 rules | `exclusion/policy.py`, `grouping.py`, `publish.py`, `dedup/lineage.py` |
| Common Pile inventory and per-upstream capacity | `reports/COMMON-PILE-BALANCED-PRODUCTION-READINESS.md`, `COMMON-PILE-PRODUCTION-PLAN.md` |
| Essential-Web inventory and campaign | `evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/production.inventory.json`, `ESSENTIAL-WEB-FAST-TRANSPORT/campaign.json`, `reports/MIX01-ESSENTIAL-FIRST-PASS-TRANSITION.md` |
| SYNTH calibration and adapter | `reports/SYNTH-LARGE-ROWGROUP-CALIBRATION.md`, `adapters/mix01_adapters.py` (`SynthExplanationsAdapter`) |

Not available in the repository: the real C05 completion's per-allocation
`kept / excluded / duplicate / train_bytes`, the cleaned manifest's per-allocation
sizes, and the real counts envelope's totals for the allocations that are not
deficient. Section 9 gives the read-only command that prints them.

## 2. Observed end-to-end yield

The yield runs from acquired canonical bytes to exact eligible valid targets. It
includes cleaning, C05, the split and the tokenizer.

| allocation | files acquired / inventory | docs acquired | canonical B acquired | eligible targets | B / target | quota | deficit |
|---|---|---:|---:|---:|---:|---:|---:|
| synth_en_explanations/default | 14 / 500 | 1,751,337 | 4,771,301,422 | 33,621,008 | **141.91** | 900,000,000 | 866,378,992 |
| essential_science | 576 / 23,200 | 315,173 | 2,692,218,118 | 475,286,697 | 5.66 | 600,000,000 | 124,713,303 |
| common_pile / project_gutenberg | 3 / 64 | 8,678 | 938,887,688 | 51,277,105 | **18.31** | 139,585,956 | 88,308,851 |
| common_pile / pressbooks | 28 / 64 (63 eligible) | 23,919 | 253,767,080 | 38,730,930 | 6.55 | 50,000,000 | 11,269,070 |
| common_pile / libretexts | 46 / 64 | 28,876 | 257,656,768 | 46,017,785 | 5.60 | 50,000,000 | 3,982,215 |
| common_pile / oercommons | **64 / 64** | 5,312 | 51,878,861 | 7,055,426 | 7.35 | 10,130,113 | 3,074,687 |
| common_pile / public_domain_review | **63 / 63 eligible** | 1,384 | 6,932,089 | 1,290,951 | 5.37 | 1,387,537 | 96,586 |

The total deficit is 1,097,823,704, which equals the operator's figure. Cleaning is
negligible corpus-wide: it removed 9,967 of 15,097,174 docs, and the frozen v2
guardrail caps any component at 2 % of docs and 10 % of bytes. C05 removed 2,463,009
docs.

## 3. SYNTH root cause (first priority)

* **A. Accidental small subset: no.** The plan selected 14 whole files (frozen
  inventory ranks 0-13 of 500), and exactly those were acquired: 1,751,337 English
  docs (81 %, matching the 806/1000 calibration), 4.77 GB canonical. The seal says
  `SUFFICIENT` with 1,192,825,355 estimated tokens against a 990M first-pass target.
* **B. Dataset this small: no, in raw terms.** 486 inventory files remain, about
  165 GB of canonical text at the observed 341 MB per file. Under the frozen C05
  policy, however, its *effective* unique size is probably close to what survived
  (below).
* **C. Filtering removed it: cleaning no, C05 yes.** 141.9 B/target against 5.4-5.7
  for the comparable allocations means at least 96 % of SYNTH's bytes did not become
  eligible targets. At PDR's 5.37 B/target, 4.77 GB would give about 889M.
* **D. Planning error: yes, contributing.** The plan sized SYNTH at canonical bytes /
  4 as if every row were independent material. It never modeled C05 loss for a
  seed-expanded synthetic set, although the C05 plan report flagged SYNTH lineage as
  open.
* **E. Mechanism inside C05 (confirmation needed).** Every SYNTH document is
  rendered as `Context: <query_seed_text>` + question + answer. Rows generated from
  the same seed passage therefore share most of their text. In addition, lineage v3
  turns `query_seed_url` and `additional_seed_url` into cross-source `url:` keys.
  These are the only URL lineage keys in Mix-01; Essential-Web keeps `source_domain`.
  Two frozen rules then remove rows:
  1. **Near-duplicate collapse.** MinHash on 5-word shingles keeps pairs with
     estimated Jaccard >= 0.8. Only the longest document of each duplicate group is
     kept (`longest-source-doc-v1`), so about one survivor per seed passage.
  2. **Family exclusion.** `publish.py` marks a document `excluded` when any member
     of its family has a benchmark hit. `additional_seed_url` edges chain seed
     articles into large families, and one hit removes the whole family.

  33.6M targets at SYNTH's mean 2,724 B/doc is roughly 5x10^4 surviving documents.
  This assumes about 4.3 B/target for survivors, which is an assumption. The figure is
  consistent with about one survivor per seed passage. The completion's SYNTH
  `duplicate` vs `excluded` counts decide between (1) and (2); the ledger aggregates
  in section 9 measure family size and saturation.

**Consequence.** Both mechanisms saturate. The other 486 files reuse the same seed
passages and URLs, so their rows join existing duplicate groups or excluded families.
A linear extrapolation (about 361 more files for 866M) is therefore invalid. **SYNTH
cannot plausibly supply 900M under the frozen adapter plus frozen C05 policy.** Do not
acquire SYNTH until the ledger saturation metric shows otherwise.

## 4. essential_science

The first pass was sealed on the bytes/4 estimate with only a 2 % margin over its
660M first-pass target: 673,054,530 estimated tokens. The 4-B/token assumption was
the error: the real end-to-end cost is 5.66 B/target, so about 41 % more canonical
bytes were needed. The extra cost has three parts:

* the tokenizer's real bytes per token on long educational pages;
* C05 cross-source duplicates: UltraX is also Common-Crawl derived, and the longest
  copy survives;
* family hit propagation.

This is not exhaustion. 576 of 23,200 inventory files were acquired, as a contiguous
prefix, and 22,624 remain (about 5.9 TB raw at 262 MB per file, about 106 GB of
science canonical text). Top-up is deterministic: campaign batches of 32 files take
the next inventory indices, with the same seed, revision and admission.

## 5. Common Pile, per upstream (no cross-upstream offset)

| upstream | cause | remaining eligible inventory | closable from same source |
|---|---|---|---|
| project_gutenberg | C05 removes about 76 % (18.3 B/target): duplicate editions; whole books excluded on a hit | 61 files, about 19.4 GB canonical (projection) | yes, but marginal yield may fall (more editions of books already held) |
| pressbooks | bytes/4 projection with a 1.15 margin vs a real 6.55 B/target | about 35 files, about 323 MB canonical | yes |
| libretexts | same, 5.60 B/target | 18 files, about 103 MB canonical | yes |
| oercommons | same, 7.35 B/target | **0: all 64 inventory files acquired** | **no (exhausted)** |
| public_domain_review | same, 5.37 B/target | **0: all 63 eligible acquired; 1 benchmark-reserved tail file of about 0.1 MB** | **no (exhausted)** |

The production plan already recorded that news, OER and PDR "consume all eligible
inventory". The OER and PDR quotas were capacity-capped projections
(`floor(projected/1.15)`) that the real yield did not meet.

## 6. Recommended top-up (observed yield, explicit safety)

Formula: canonical bytes needed = deficit / observed yield; files = ceil(needed × s /
observed canonical bytes per file). The safety factor `s` covers per-file variance, a
fresh global C05 (new data can change survivors and family hits of existing
documents) and the C06 refit, which shifts every exact count.

| allocation | s | files | canonical B | projected new targets | headroom over deficit | source capacity |
|---|---:|---:|---:|---:|---:|---|
| essential_science | 1.25 | **192** (6 campaign batches; 189 needed) | about 897 MB | about 158M | about 34M | ample |
| project_gutenberg | 1.5 (n = 3 files) | **8** (cursor 16-23) | about 2.50 GB | about 137M | about 48M | 61 left |
| pressbooks | 1.3 | **11** (from cursor 185) | about 100 MB | about 15.2M | about 3.9M | about 35 left |
| libretexts | 1.3 | **6** (from cursor 266) | about 34 MB | about 6.0M | about 2.0M | 18 left |
| oercommons | n/a | 0 | n/a | n/a | n/a | **exhausted: 3,074,687 unrecoverable** |
| public_domain_review | n/a | 0 | n/a | n/a | n/a | **exhausted: 96,586 unrecoverable** |
| synth_en_explanations | n/a | **0 until saturation is measured** | n/a | n/a | n/a | **structurally capped (section 3)** |

Essential-Web cost: 192 files are about 50 GB raw transfer and about 66 GB durable
footprint, plus practical/prose surplus. Gutenberg: 8 files of about 120 MB
compressed each. Any currently surplus allocation within a few percent of its quota
is also at risk after a fresh C05 and refit; the summary in section 9 prints every
allocation's margin. Raise `s` or add files there before acquiring anything.

## 7. Reuse and staleness after a top-up

**Reusable:** source revisions, adapters, admissions (per source/view at the pinned
revision), license and provenance decisions, frozen inventories and orders,
transport policies, the cleaning policy v2 thresholds, the C05 policy values, and the
quota table. Top-ups are new hash-chained plans from the recorded cursors (planner
`mix01-source-planner-v1`; Essential-Web campaign batches). Already-acquired files are
not redownloaded.

**Stale:**

* source seals and sufficiency records, and the original manifest `11724d92…`;
* the cleaning dry run, production, verification and audit report;
* the cleaned manifest `eda4f994…` and its admission;
* C05 plan `0463816…`, completion `225b330…` and proof;
* the C06 fit and tokenizer `8ef1a2dd…`, plus its kept index;
* counts `6bcaff4a…`, the deficit report, and any selection, shards or freeze.

**Smallest pipeline:**

1. top-up plans, authorization and acquisition;
2. new seals and a new original manifest;
3. frozen v2 cleaning: dry run, approval, production, verify, audit report;
4. `admit-cleaned`;
5. fresh global C05 in a new plan root (protected preparation reusable if
   `index_sha256` is unchanged);
6. proof;
7. `fit-tokenizer` (fast) and verify;
8. `count-tokens` (about 55 min);
9. `select` (about 2 min);
10. `tokenize-selection` and `freeze`.

C05 must be global, because deduplication and benchmark exclusion are not
component-invariant, so it cannot be run over the top-up files alone.

## 8. Option A (preserve Mix-01) vs option B (revise the mixture)

**A, as frozen, is infeasible.** OER Commons and PDR are exhausted: 3,171,273 targets
cannot be acquired without breaking "no borrowing". SYNTH (866M) is very probably
capped by C05, not by acquisition. A closes essential_science, Gutenberg, PressBooks
and LibreTexts: 228.3M of the 1,097.8M deficit.

**B (reallocate 1.098B to surplus allocations) invalidates, under current contracts:**

* `mix01_quotas_6b.yaml` (`quota_id mix01_6b_v1`, sha `9b16db76…`);
* every sealed source binding of that sha: `frozen_requirements` refuses another
  quota file. That forces new seals, a new original manifest, a new cleaned-manifest
  lineage and admission, and therefore a new C05 plan, unless a reviewed
  quota-amendment mechanism is first added;
* the IFM requirement split and the Common Pile component split, which bind the
  quota sha;
* the C06 fit policy `mix01_fit_shares_v1` (binds `quotas_sha256`), and therefore the
  tokenizer, counts and selection;
* `mix01.yaml` weights, the `mix01_views_v2` registry and the 32M pilot view;
* the P35 scientific contract and every comparison defined relative to Mix-01. In
  particular the SYNTH ablations `m1_less_synth` (5 % = 300M) and `m2_more_synth`
  (30 % = 1.8B) are themselves infeasible at 6B with about 34M of SYNTH.

B is therefore not faster than A under the current contracts. Scientifically, B
produces a different mixture: SYNTH at about 0.56 % instead of 15 %. It must be a new,
versioned and pre-registered mixture (for example "Mix-01r"), decided before any
training result. It must report what changed, and never be presented as Mix-01.
A partial B is acceptable only for the parts that cannot be recovered (SYNTH if
confirmed saturated, OER, PDR), as such an explicit versioned revision. A should still
be used for the four closable deficits, so that their real material is not replaced
by other sources.

**Recommendation:** run the section-9 summary, with the ledger, first. If SYNTH is
saturated, the operator must decide among:

1. a versioned mixture revision;
2. a new SYNTH treatment, for example question + answer without the shared context.
   This is a new adapter version with new evidence and admission, and still a
   mixture-semantics change;
3. a reviewed C05 lineage/near-duplicate policy change. This is a frozen-contract
   change and would make every C05 result stale.

Acquire the A top-ups only after that decision, so that one fresh C05 serves both.

## 9. Read-only metadata command

`scripts/mix01_deficit_summary.py`: stdout is one JSON object of aggregates and
artifact identities only. It contains no text, document ids, group ids, file names,
paths, benchmark material or per-record hashes. Run from this branch's checkout, with
`X:` detached:

```powershell
$om = (Get-Content -Raw 'G:/XLM/quality/clean-dry-run-v2/cleaning-dry-run-receipt.json' | ConvertFrom-Json).input_manifest.path
uv run --offline --locked --no-sync --extra cpu --extra eval python -m scripts.mix01_deficit_summary --original-manifest $om --cleaned-manifest G:/XLM/quality/clean-production-v1/cleaned-input-manifest.json --proof G:/XLM/c05-clean-v1/clean-v1-p0001.proof.json --counts G:/XLM/counts/mix01-clean-v1 --deficit-report <your deficit report> --inventory-dir G:/XLM/inventories --inventory ew-fast=docs/implementation/evidence/ESSENTIAL-WEB-PRODUCTION-READINESS/production.inventory.json | Set-Content -Encoding utf8 C:/XLM-scratch/mix01-deficit-summary.json
```

Optional second run, which decides the SYNTH mechanism and saturation: add `--decisions
X:/C05-Scratch-clean-v1/0463816982fdaf3616f1a032e2107c785dea88e8edf600fc8fa6e190ac65a021/decisions.jsonl`.
This needs `X:` attached. It streams the private ledger twice (about 15M rows, minutes)
and prints only counts:

* duplicate groups by survivor allocation;
* excluded family sizes and their component spread;
* new duplicate groups and families per row in each deficient allocation's last
  quarter of files.

Detach `X:` again afterwards.

Per allocation it prints:

* acquired files, documents and bytes;
* cleaned documents and bytes;
* C05 kept, kept train, excluded, duplicate and kept-train bytes;
* exact targets, quota, eligible targets and deficit;
* targets per acquired, cleaned and kept-train byte;
* cleaning and C05 survival.

Per source it prints remaining inventory files and known bytes (Common Pile per
upstream, excluding the reserved tail). Signatures are not verified; compare the
printed identities with the pins (`225b3304…`, `0463816…`, `6bcaff4a…`, `eda4f994…`).

Tests: `tests/test_mix01_deficit_summary.py`, 2 passed. They cover the authored
17-allocation chain with a SYNTH deficit, ledger reconciliation, an inventory
remainder and the reserved tail, the no-deficit case, and a scan for identity leaks.
ruff is clean. `mypy --strict` on the script reports only the environmental
`import-untyped` for `xlm.data.evidence_v2` when the script is checked alone.
