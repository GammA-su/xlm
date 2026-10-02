# Common Pile prose: component allowlist audit (no acquisition, no decision)

Base `5eb02078e1b4a7870adb6df53d27a3c412f4c034` (`fix/ifm-production-bounds`).
Worktree `F:\Project\xlm-common-pile`, branch `feat/common-pile-allowlist`.
No corpus payload was downloaded, no allowlist was recorded, nothing was
admitted, planned or authorized. Operator artifacts under `G:\XLM` were read
only (`common_pile.discovery.*` sha256 identical before and after). Not legal
advice: the labels below are evidence classes for an operator decision.

Target: `common_pile_prose`, 5% of Mix-01, final 300,000,000 exact tokens,
first pass 330,000,000 estimated tokens = 1,320,000,000 canonical bytes
(x1.15 safety = 1,518,000,000 B planned). Source
`common-pile/comma_v0.1_training_dataset` @ `5afc546db324e7f39f297ba757c9a60547151e7c`
(HEAD still equals the pin on 2026-10-02; `lastModified` 2025-06-06).

Evidence directory: `docs/implementation/evidence/COMMON-PILE-PROSE-ALLOWLIST-AUDIT/`
(no corpus text):

| File | Content |
|---|---|
| `components.json` / `enumerate_components.py` | exact universe, per-component files/bytes; re-verifies the listing digest and re-derives the discovery inventory |
| `license-matrix.json` / `build_matrix.py` | 31-component evidence matrix (authored judgments + cited sources) |
| `hf-cards.tsv`, `github-files.tsv` | every fetched document: URL revision, bytes, sha256 |
| `allowlist-simulation.json` / `simulate_allowlists.py` | ESTIMATE of first-plan composition per candidate list |
| `certification-candidates.json` / `certification_candidates.py` | deterministic real-row certification files |

## 1. Pinned component universe (Task 1)

From `G:\XLM\inventories\common_pile.discovery.listing.json` (listing digest
`a32b9b12649da4b72a07fbbe41bd3bbd9d7df9da9df32136fa8080802d8b5038`, complete,
3 pages, 0 unknown sizes) and `common_pile.discovery.inventory.json`
(inventory digest `d81e5c8417785ab75deb69bd30b52639299b98cafd9bbf7c46ae3912a1c7e33a`,
seed 20260918). The inventory re-derives byte-for-byte from the listing.
Every path is exactly `<component>/<component>.chunk.NN.jsonl.gz`.

**31 components, each exactly 64 files; 1,984 files; 516,327,289,637 B.**
The prompt's expected list matches exactly. Within a component, chunk sizes
are near-uniform (largest max/min spread: `public_domain_review`, 3.3x).

## 2. What `common_pile_prose` is for (Task 2)

- `prompts/13_real_dataset_views_and_mix01.md`: "selected Common Pile prose 5%";
  view `common_pile_prose`: "selected instructional/narrative/transcript
  sources with exclusions/provenance intact".
- `DATA_CATALOG.md` #18: "Initial mixture selected complementary prose only;
  inspect source metadata/terms."
- Registry note (`mix01_views.yaml`): allowlist, weights and per-component
  licensing are unresolved policy decisions; the 9-row certification does not
  certify the other components.

Content fit is therefore: instructional (open textbooks, OER), narrative
(literature, essays, journalism) and transcript (spoken) prose that
complements the existing web, educational-PDF, Wikipedia, synthetic and story
components. Nothing in the specification supports code, Stack Exchange QA,
research papers, Wikipedia, or government/legal bulk, so those are
`EXCLUDE_CONTENT_FIT` regardless of license. Content fit and license are
separate columns below.

## 3. Structural findings that bound every decision

1. **The pinned rows are text only.** Every certified row is exactly
   `{"text": str}`. The upstream `_filtered` datasets carry per-document
   `metadata.license`, but the Comma consolidation drops it. Licensing in this
   repository can only be decided **per top-level component**; per-document
   filtering is impossible here.
2. **The Comma repository holds one copy per source.** The card's mixing table
   applies repeats at training time. Its main-stage token counts (Comma
   tokenizer) are the best available size estimate per component.
3. **`cccc` predates a licensing correction.** Its card says the upstream
   dataset "has been updated to remove instances of incorrect licensing" and
   the exact version Comma trained on is available only on request. The Comma
   copy cannot be corrected (text-only rows): `EXCLUDE_PROVENANCE`.
4. **Upstream documentation is imperfect.** The LibreTexts filtered card says
   3.6 GB, but 0.093B Comma tokens and 122 MB compressed imply about 0.4 GB.
   The News README site list differs from the card (Liberty TV, Caravanserai).
   The UK Hansard card claims the Open Parliament Licence for devolved
   legislatures that are not UK Parliament. The published LibreTexts scraper
   contains a debug `break` and an undefined name, so repository code is
   evidence of method, not a replay of the run.
5. **License laundering is acknowledged by the publisher** on every card, and
   the paper says it "is a notoriously hard problem to identify exhaustively".

## 4. 31-component matrix (Tasks 1, 3, 4)

Comma tok = main-stage tokens from the pinned card (Comma tokenizer, not XLM
tokens). A/B/C = membership in the three candidate lists (section 6).

| # | Component | Files | Compressed bytes | % repo | Comma tok (B) | Content fit | License / provenance class | Conf. | A | B | C |
|---|---|---:|---:|---:|---:|---|---|---|:-:|:-:|:-:|
| 1 | `arxiv_abstracts` | 64 | 889,633,257 | 0.1723 | 0.57 | EXCLUDE_CONTENT_FIT | VERIFIED_OPEN_LICENSE | HIGH |  |  |  |
| 2 | `arxiv_papers` | 64 | 5,996,196,704 | 1.1613 | 6.0 | EXCLUDE_CONTENT_FIT | PER_DOCUMENT_OPEN_LICENSE | MEDIUM |  |  |  |
| 3 | `biodiversity_heritage_library` | 64 | 14,500,293,977 | 2.8084 | 9.8 | EXCLUDE_CONTENT_FIT | MIXED_BUT_FILTERED_TO_OPEN | MEDIUM |  |  |  |
| 4 | `caselaw_access_project` | 64 | 24,956,737,971 | 4.8335 | 19.7 | EXCLUDE_CONTENT_FIT | VERIFIED_PUBLIC_DOMAIN | MEDIUM |  |  |  |
| 5 | `cccc` | 64 | 20,819,212,852 | 4.0322 | 15.2 | EXCLUDE_CONTENT_FIT | EXCLUDE_PROVENANCE | LOW |  |  |  |
| 6 | `data_provenance_initiative` | 64 | 1,266,390,817 | 0.2453 | 0.92 | EXCLUDE_CONTENT_FIT | PER_DOCUMENT_OPEN_LICENSE | MEDIUM |  |  |  |
| 7 | `doab` | 64 | 3,512,954,742 | 0.6804 | 3.0 | PARTIAL_FIT | PER_DOCUMENT_OPEN_LICENSE | MEDIUM |  |  | x |
| 8 | `foodista` | 64 | 31,880,650 | 0.0062 | 0.025 | NEEDS_OPERATOR_REVIEW | NEEDS_OPERATOR_REVIEW | LOW |  |  | x |
| 9 | `github_archive` | 64 | 13,709,650,508 | 2.6552 | 11.0 | EXCLUDE_CONTENT_FIT | PER_DOCUMENT_OPEN_LICENSE | MEDIUM |  |  |  |
| 10 | `library_of_congress` | 64 | 12,879,350,670 | 2.4944 | 9.5 | NEEDS_OPERATOR_REVIEW | VERIFIED_PUBLIC_DOMAIN | MEDIUM |  |  | x |
| 11 | `libretexts` | 64 | 122,167,467 | 0.0237 | 0.093 | FIT | PER_DOCUMENT_OPEN_LICENSE | HIGH | x | x | x |
| 12 | `news` | 64 | 98,602,096 | 0.0191 | 0.064 | FIT | MIXED_BUT_FILTERED_TO_OPEN | MEDIUM | x | x | x |
| 13 | `oercommons` | 64 | 17,149,251 | 0.0033 | 0.012 | FIT | PER_DOCUMENT_OPEN_LICENSE | HIGH | x | x | x |
| 14 | `peS2o` | 64 | 55,339,172,580 | 10.7178 | 43.3 | EXCLUDE_CONTENT_FIT | PER_DOCUMENT_OPEN_LICENSE | MEDIUM |  |  |  |
| 15 | `pre_1929_books` | 64 | 16,718,436,810 | 3.2380 | 12.4 | NEEDS_OPERATOR_REVIEW | VERIFIED_PUBLIC_DOMAIN | MEDIUM |  |  | x |
| 16 | `pressbooks` | 64 | 201,539,814 | 0.0390 | 0.14 | FIT | PER_DOCUMENT_OPEN_LICENSE | HIGH | x | x | x |
| 17 | `project_gutenberg` | 64 | 7,689,423,474 | 1.4893 | 5.7 | FIT | VERIFIED_PUBLIC_DOMAIN | HIGH |  | x | x |
| 18 | `public_domain_review` | 64 | 2,915,747 | 0.0006 | 0.0017 | FIT | VERIFIED_OPEN_LICENSE | HIGH | x | x | x |
| 19 | `pubmed` | 64 | 42,841,411,375 | 8.2973 | 36.6 | EXCLUDE_CONTENT_FIT | PER_DOCUMENT_OPEN_LICENSE | MEDIUM |  |  |  |
| 20 | `python_enhancement_proposals` | 64 | 3,743,588 | 0.0007 | 0.0027 | EXCLUDE_CONTENT_FIT | VERIFIED_PUBLIC_DOMAIN | HIGH |  |  |  |
| 21 | `regulations` | 64 | 1,310,483,072 | 0.2538 | 1.4 | EXCLUDE_CONTENT_FIT | VERIFIED_PUBLIC_DOMAIN | MEDIUM |  |  |  |
| 22 | `stackexchange` | 64 | 32,354,037,772 | 6.2662 | 23.9 | EXCLUDE_CONTENT_FIT | PER_DOCUMENT_OPEN_LICENSE | HIGH |  |  |  |
| 23 | `stackv2_edu` | 64 | 63,655,535,965 | 12.3285 | 67.8 | EXCLUDE_CONTENT_FIT | PER_DOCUMENT_OPEN_LICENSE | MEDIUM |  |  |  |
| 24 | `stackv2_html` | 64 | 1,513,090,235 | 0.2930 | 1.2 | EXCLUDE_CONTENT_FIT | PER_DOCUMENT_OPEN_LICENSE | MEDIUM |  |  |  |
| 25 | `ubuntu_irc` | 64 | 1,812,469,483 | 0.3510 | 1.9 | EXCLUDE_CONTENT_FIT | VERIFIED_PUBLIC_DOMAIN | MEDIUM |  |  |  |
| 26 | `uk_hansard` | 64 | 3,101,805,340 | 0.6007 | 2.3 | NEEDS_OPERATOR_REVIEW | NEEDS_OPERATOR_REVIEW | MEDIUM |  |  | x |
| 27 | `usgpo` | 64 | 10,383,314,469 | 2.0110 | 8.8 | EXCLUDE_CONTENT_FIT | VERIFIED_PUBLIC_DOMAIN | MEDIUM |  |  |  |
| 28 | `uspto` | 64 | 147,270,341,710 | 28.5227 | 157.4 | EXCLUDE_CONTENT_FIT | VERIFIED_PUBLIC_DOMAIN | MEDIUM |  |  |  |
| 29 | `wikimedia` | 64 | 21,265,747,440 | 4.1187 | 15.8 | EXCLUDE_CONTENT_FIT | VERIFIED_OPEN_LICENSE | HIGH |  |  |  |
| 30 | `wikiteam` | 64 | 5,802,685,412 | 1.1238 | 4.3 | EXCLUDE_CONTENT_FIT | MIXED_BUT_FILTERED_TO_OPEN | LOW |  |  |  |
| 31 | `youtube` | 64 | 6,260,914,389 | 1.2126 | 4.7 | PARTIAL_FIT | NEEDS_OPERATOR_REVIEW | MEDIUM |  |  | x |
| | **total** | 1984 | 516,327,289,637 | 100 | 463.6 | | | | | | |

Per-component license basis, caveats and cited evidence IDs are in
`license-matrix.json` (`components.<name>`). Every component's license is
corpus-level in this repository (finding 1). The "per-document" classes
describe how upstream filtered documents before consolidation.

### Prose candidates in depth (Task 4)

Each entry gives origin and method, then license basis, then caveats.

- **libretexts.** Origin: LibreTexts textbook sections scraped from HTML.
  License: `to_dolma.py` reads each section's `pageTagsHolder` license tag
  and keeps only `ccby`, `ccbysa`, `gnufdl` or `publicdomain`; any other tag
  (including NC) is skipped. This is **verified from primary code**, so the
  prompt's claim holds. Caveats: GFDL obligations; the pages are on the open
  web (C05 duplicate overlap with Essential-Web, UltraX and FinePDFs). Real
  rows are certified.
- **pressbooks.** Origin: open-access books from the PressBooks directory.
  License: `to_dolma.py` uses `LICENSE_MAP` = CC BY, CC BY-SA, CC0 or Public
  Domain, and skips the rest. The license is book-level, so "except where
  otherwise noted" third-party content is possible. Not real-certified.
- **oercommons.** Origin: OER Commons courseware, from a search filtered to
  public domain, CC BY and CC BY-SA. License: `to_dolma.py` maps strictly to
  PD, CC BY or CC BY-SA (a `KeyError` otherwise). Caveat: worksheets and
  problem sets carry benchmark risk (ARC-style questions), which C05 must
  handle. Tiny: 0.012B tokens. Not real-certified.
- **news.** Origin: sites listed by Open Newswire as CC BY or CC BY-SA.
  License: assigned per site, not per article. Caveats: syndicated articles
  may not carry the site license; the site lists in the card and README
  disagree; state-affiliated outlets are included. Class
  `MIXED_BUT_FILTERED_TO_OPEN`, MEDIUM. Real rows are certified.
- **public_domain_review.** Origin: PDR essays and collection posts. License:
  PDR states that unquoted text is CC BY-SA 4.0 (`pdr-reuse`, via search
  summary), and the scraper assigns CC BY-SA uniformly. Quoted passages fall
  outside that license and are usually public-domain sources. Real rows are
  certified. Tiny: 0.0017B tokens.
- **project_gutenberg.** Origin: English books whose PG `dcterms:rights` is
  "Public domain in the USA." (`build-index.py` regex `^Public domain`), plus
  PG19 special cases. Two items PG marks as copyrighted were added because of
  their age (1599, 1490). Caveats: the public-domain basis is US-only; the
  language is archaic. This is the only large narrative component with a
  HIGH-confidence basis. Not real-certified.
- **doab.** Origin: English books whose DOAB metadata says CC BY or CC BY-SA,
  converted from PDF with Marker. License: `cc_filter.py` additionally
  requires an open-license statement in the first or last ~5% of lines.
  Caveats: third-party figures and quotes inside CC books; the keyword-based
  check; a scholarly-monograph genre that overlaps FinePDFs (the same
  open-access PDFs may be in Common Crawl). Not real-certified.
- **youtube.** Origin: about 2,000 manually curated "original content"
  channels, transcribed with Whisper. License: uploader-declared CC BY. The
  project itself says CC license laundering on YouTube is rampant, and the
  curated channel list is not published. Caveats: ASR errors; YouTube's
  download terms are a separate question. This is the only true
  spoken-transcript source. License class `NEEDS_OPERATOR_REVIEW`. Not
  real-certified.
- **library_of_congress.** Origin: LoC "Selected Digitized Books", English,
  OCR text. License: LoC designates the collection public domain (item
  advisories such as "no copyright renewal found"; via search summary). The
  card's "intended CC-BY-4.0" is a dataset-level statement. Caveats: OCR
  noise; US basis. Content fit is `NEEDS_OPERATOR_REVIEW`. Not real-certified.
- **biodiversity_heritage_library.** Page-level OCR documents (42M pages),
  rights-statement whitelist including "no known copyright restrictions",
  with taxonomic and non-English content. `EXCLUDE_CONTENT_FIT`.
- **pre_1929_books.** Origin: HathiTrust-identified US publications before
  1929, Internet Archive OCR. License: US public domain by date. Caveats:
  catalog-derived publication data; OCR. Content fit is
  `NEEDS_OPERATOR_REVIEW`.
- **uk_hansard** (transcript). License: the OPL v3.0 permits commercial use
  with attribution and exempts personal data and third-party rights. The
  component also includes the Scottish Parliament, the Senedd (in English and
  Welsh), the NI Assembly and London material, which the OPL does not cover.
  Both classes are `NEEDS_OPERATOR_REVIEW`.
- **foodista** (instructional recipes). License: site-wide CC BY claim, but
  the database is partly built "through an automated crawl of the Web", and
  user comments are appended. `NEEDS_OPERATOR_REVIEW` / LOW.
- Also considered and excluded for fit: `ubuntu_irc` (technical support chat,
  PD dedication per card), `wikimedia` (Wikipedia duplication; text-only rows
  cannot isolate Wikibooks or Wikivoyage) and `wikiteam` (laundering
  acknowledged).

## 5. Duplication and mixture fit (Task 5)

| Candidate | Adds | Duplicates |
|---|---|---|
| libretexts, pressbooks, oercommons | clean sectioned instructional prose (the anchor intent) | same textbooks as PDFs (FinePDFs-Edu) or HTML (Essential/UltraX); C05 will remove some |
| news, public_domain_review | edited essay/journalism prose | partial web overlap; tiny volumes |
| project_gutenberg | long-form narrative literature; no existing component supplies it (SimpleStories is short synthetic) | some Gutenberg HTML in web crawls |
| doab | long-form scholarly non-fiction | genre and possibly files overlap FinePDFs-Edu |
| youtube, uk_hansard | spoken register (transcript intent) | little; ASR/debate format |
| library_of_congress, pre_1929_books | historical books | overlap Gutenberg titles; OCR noise |
| foodista | recipes/procedures | Essential practical covers web recipes |
| wikimedia; stack*/github; peS2o/pubmed/arxiv; caselaw/usgpo/uspto/regulations | — | FineWiki/Wiki-Rewrite; code/QA; FinePDFs/Essential science; legal bulk (all excluded) |

**Volume (ESTIMATE, `allowlist-simulation.json`).** Comma tokens x 4 B, with
the planner emulated using the true per-file mean:

| List | Files | Compressed bytes | Est. canonical bytes | vs 1.32e9 required | vs x1.15 | First-plan files | First-plan est. share |
|---|---:|---:|---:|---:|---:|---:|---|
| A | 320 | 442,374,375 | 1,242,800,000 | 0.94x | 0.82x | 320 (all) | pressbooks .45, libretexts .30, news .21, oer .04, pdr .005 |
| B | 384 | 8,131,797,849 | 24,042,800,000 | 18.2x | 15.8x | 25 | gutenberg .974, all others .026 |
| C | 768 | 50,637,140,450 | 151,742,800,000 | 115x | 100x | 8 | LoC .65, gutenberg .19, hansard .16 |

Two consequences:

- **A is volume-insufficient.** It is short of the requirement before any
  C05 cross-source removal, and the planner would take every file at once
  with no top-up headroom.
- **The current planner sizes a plan from one average bytes-per-file over a
  hash-ordered inventory.** With a book component in the list, books dominate
  the bytes, and the small instructional sources contribute about 3%.
  Allowlist membership is not a weight.

## 6. Candidate allowlists (Task 6), none recorded

**A. CONSERVATIVE:** `libretexts, news, oercommons, pressbooks, public_domain_review`.
- Rationale: clearest prose fit, with per-document or per-site licenses
  applied upstream at collection. Three of the five already have real
  certified rows.
- Basis: PER_DOCUMENT_OPEN_LICENSE / VERIFIED_OPEN_LICENSE /
  MIXED_BUT_FILTERED_TO_OPEN (news). Confidence HIGH except news (MEDIUM).
- Available: 442,374,375 B compressed; estimated 1.24 GB canonical.
- Diversity: instructional and journalistic.
- Risk: estimated about 6% short of the requirement (18% short of the planned
  x1.15) before dedup. It cannot meet the 5% without a quota change, and no
  quota change is proposed here.

**B. BALANCED:** A + `project_gutenberg`.
- Rationale: adds the only large, HIGH-confidence, clearly narrative
  component.
- Basis: A plus US public domain (PG rights metadata). Confidence HIGH.
- Available: 8,131,797,849 B compressed; estimated 24.0 GB.
- Diversity: instructional plus long-form narrative.
- Risks: US-only public-domain basis; archaic prose; under the current
  planner the first plan is about 97% Gutenberg by bytes.

**C. MAXIMAL-REASONABLE:** B + `doab, foodista, library_of_congress, pre_1929_books, uk_hansard, youtube`.
- Rationale: everything with a defensible prose fit.
- Caveats: five components are flagged (`foodista`, `library_of_congress`,
  `pre_1929_books`, `uk_hansard`, `youtube`) and need `--accept-flagged`.
- Available: 50,637,140,450 B compressed; estimated 151.7 GB.
- Diversity: adds the transcript register (youtube, hansard) and scholarly
  and historical books.
- Risks: laundering (youtube, foodista), license scope (hansard), OCR
  (LoC, pre-1929), FinePDFs overlap (doab), Welsh text. The first plan would
  be dominated by LoC OCR.

**Audit recommendation for the existing anchor intent: B**, with these
qualifications:

- A is the purest match, but it is estimated to be unable to fill the frozen
  5%.
- B keeps every component at HIGH-confidence evidence with a clear fit.
- Under today's single-prefix planner, B's bytes will be mostly Gutenberg. If
  the operator wants the instructional core represented in proportion, that
  needs a separate future decision: a per-view requirement split (core
  allowlist taken whole, Gutenberg filling the remainder) via a registry
  amendment. That split is NOT implemented here.

The transcript leg of the intent is served only by C's flagged components.
The operator decides; nothing is recorded.

## 7. Adapter certification gap (Task 7)

All 31 components share the same file format (`.jsonl.gz`) and, where
observed, the same row schema `{"text": str}`. However, only news, libretexts
and public_domain_review have real rows (9 rows: 3 per component, from files
`news.chunk.09`, `libretexts.chunk.44` and `public_domain_review.chunk.56`).
The September sample directory also selected pressbooks and oercommons files
but recorded no rows for them. **The existing certification covers 3
components and nothing else.**

Minimum additional real certification is one file per included, uncertified
component:

- A: pressbooks, oercommons (2)
- B: + project_gutenberg (3)
- C: + doab, foodista, library_of_congress, pre_1929_books, uk_hansard, youtube (9)

Rule: the first file of the component in frozen inventory order, which is the
same in any filtered inventory, rows `[0, 16)`. Files are listed in
`certification-candidates.json`: pressbooks.chunk.18, oercommons.chunk.49,
project_gutenberg.chunk.43, doab.chunk.56, foodista.chunk.38,
library_of_congress.chunk.24, pre_1929_books.chunk.37, uk_hansard.chunk.56
and youtube.chunk.20.

What must be certified:

- each line decodes as UTF-8 JSON;
- the row keys are exactly `{"text"}`;
- `text` is a non-empty string, preserved verbatim by `CommonPileAdapter`;
- the derived `upstream_component` equals the directory;
- doc_ids are unique;
- the revision is `5afc546…`.

This is a prefix sample, so it proves schema, not corpus-wide content.

Prepared, NOT executed (network; needs the operator's explicit authorization):

    # 0. metadata only: HEAD must still equal the pin, else STOP
    #    GET https://huggingface.co/api/datasets/common-pile/comma_v0.1_training_dataset  -> "sha"
    # 1. bounded metadata probe (16 MiB)
    uv run --offline --locked --extra cpu --extra eval xlm data probe --catalog manifests/datasets.catalog.yaml --source common_pile --view common_pile_prose --live --budget-mib 16 --probe-id cp-cert02 --json
    #    STOP unless the reported immutable revision is 5afc546db324e7f39f297ba757c9a60547151e7c
    # 2. row ranges = {<file>: [0, 16]} for the allowlist's uncertified components
    # 3. selected-record plan (gzip prefix streaming, early close)
    uv run --offline --locked --extra cpu --extra eval xlm data plan --source common_pile --view common_pile_prose --catalog manifests/datasets.catalog.yaml --files <csv> --mode selected_records --row-ranges G:\XLM\calib\common_pile_cert02\rows.json --max-bytes 268435456 --max-records <16 x k> --max-output-disk 536870912 --seed 20260918 --attempt 1 --pilot-approved --output G:\XLM\calib\common_pile_cert02\plan.json
    uv run --offline --locked --extra cpu --extra eval xlm data fetch --plan G:\XLM\calib\common_pile_cert02\plan.json --output-dir G:\XLM\calib\common_pile_cert02\raw --scratch-dir G:\XLM\calib\common_pile_cert02\scratch --pilot-approved
    uv run --offline --locked --extra cpu --extra eval xlm data adapt --plan G:\XLM\calib\common_pile_cert02\plan.json --adapter common_pile --input G:\XLM\calib\common_pile_cert02\raw\selected_records.jsonl --output-dir G:\XLM\calib\common_pile_cert02\canonical

Offline validation of step 3 refused correctly, before any network:
"Source 'common_pile' lacks an immutable revision. Probe the source with
'xlm data probe' first." No Common Pile probe exists in `G:\XLM\xlm-home`.
The fetch/adapt argument shapes come from the CLI option lists and were not
run.

## 8. Allowlist mechanism (Task 8)

Before this change it was **missing**: only the hard `BLOCKED` entry in
`scripts/mix01_source.py` existed, and no artifact recorded component
membership. Implemented (generic, write-once, analogous to the IFM
requirement split):

- `src/xlm/data/acquisition/component_allowlist.py`: kind
  `mix01_component_allowlist` v1, self-digested. It binds `source_key`,
  `component_id`, `source_id`, repository, revision, the listing digest and
  file-list digest, `universe` (files/bytes per component, derived from the
  verified complete listing), exact `included` and `excluded`, the per-component
  `evidence` entry for all universe components, `evidence_sources` (cited
  URLs and sha256s), the evidence-matrix sha256, `accepted_flags`, `operator`,
  `rationale` and `digest`. Write-once: an identical record is reused and a
  divergent one is refused. A flagged included component must be named in
  `accepted_flags`, and stale or extra acceptances are refused.
- `scripts/mix01_source.py allowlist preview|record|show` (offline). It
  resolves the pin from the registry view and refuses a listing at another
  revision. Default home: `<data-root>\calib\component_allowlists\<key>.json`.
  The default evidence is the committed `license-matrix.json`.
- `ALLOWLIST_REQUIRED = {"common_pile": <matrix>}` with a gate in `plan`,
  `plan-supersede`, `plan-repair`, `authorize` and `benchmark plan`
  (including named benchmark files). The gate refuses when there is no
  allowlist, the allowlist is for another listing or pin, or the inventory
  does not bind it, misses files, adds files, resizes files or holds an
  excluded component. Other sources are untouched, but a stray allowlist
  binding on them is refused.
- `common_pile` stays in `BLOCKED` (message updated, still containing
  "license/provenance"). No operator allowlist was recorded.

## 9. Production inventory (Task 9)

`mix01_inventory.py freeze --listing <discovery listing> --allowlist <record>`
works as follows:

- It keeps exactly the listed files whose first path segment is an included
  component.
- It uses the same seed (default 20260918, passed explicitly), revision and
  `SHA-256(seed|repository|revision|file)` order. The production order is
  therefore the discovery order restricted to the included components.
- It adds `component_allowlist {digest, included, excluded}` and inserts
  `|allowlist:<digest>` into the inventory digest input. Inventories without
  an allowlist keep their historical digests (tested).
  `source_plan.inventory_digest` mirrors the same rule.

Top-ups continue from the same inventory cursor, and every plan re-verifies
the binding, so excluded components cannot enter. Command (operator, after
recording, not run):

    uv run --offline --locked --extra cpu --extra eval python scripts/mix01_inventory.py freeze --source common_pile --repo common-pile/comma_v0.1_training_dataset --revision 5afc546db324e7f39f297ba757c9a60547151e7c --seed 20260918 --listing G:\XLM\inventories\common_pile.discovery.listing.json --allowlist G:\XLM\calib\component_allowlists\common_pile.json --output G:\XLM\inventories\common_pile.inventory.json

## 10. Admission workflow after the operator's choice (Task 10)

| Step | Command / work | State today |
|---|---|---|
| 1 | `allowlist preview` then `allowlist record` then `allowlist show` (section 11) | ready; operator decision |
| 2 | production inventory freeze (section 9) | ready |
| 3 | real certification of uncertified included components (section 7) | NOT RUN; network authorization needed |
| 4 | calibration on the allowlisted inventory (a stratified calibration of one file per included component fits the uniform per-file planner); `mix01_inventory.py estimate` then `common_pile_prose` becomes `ESTIMATED` | BLOCKED: headroom entry is `NEEDS_CALIBRATION`; the calibration driver has no Common Pile unit and is parquet-oriented |
| 5 | code milestone: a `.jsonl.gz` production transport (the Mix-01 runner verifies `verified_source_parquet` only), a `SourceSpec` for `common_pile`, the catalog revision pin (catalog `revision: null`), then removal from `BLOCKED` | BLOCKED: not implemented |
| 6 | `evidence show/publish/verify --source-key common_pile` | after 3–5 |
| 7 | `review show`; `review record --license-decision approve_research_pretraining\|reject --provenance-decision approved\|rejected --benchmark-risk suspect_with_mitigation --operator <name> --rationale <text> --review-dir <dir>` | after 6 |
| 8 | `admit --source-key common_pile --review-dir <dir>` | after 7 |
| 9 | `policy freeze --source-key common_pile` | after 8 |
| 10 | `plan --source-key common_pile`, then **STOP at PLAN DIGEST** | after 9; the allowlist gate re-checks |

Common Pile remains blocked until every row is satisfied. Steps 3–5 are
outside the allowlist decision and are recorded as blockers, not as
completed paths.

## 11. Exact operator commands (not executed)

Each command records a choice when run with `record`. Run `preview` first.

    # A
    uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py allowlist record --source-key common_pile --data-root G:\XLM --include libretexts,news,oercommons,pressbooks,public_domain_review --operator <name> --rationale "<text>"
    # B
    uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py allowlist record --source-key common_pile --data-root G:\XLM --include libretexts,news,oercommons,pressbooks,public_domain_review,project_gutenberg --operator <name> --rationale "<text>"
    # C
    uv run --offline --locked --extra cpu --extra eval python scripts/mix01_source.py allowlist record --source-key common_pile --data-root G:\XLM --include libretexts,news,oercommons,pressbooks,public_domain_review,project_gutenberg,doab,foodista,library_of_congress,pre_1929_books,uk_hansard,youtube --accept-flagged foodista,library_of_congress,pre_1929_books,uk_hansard,youtube --operator <name> --rationale "<text>"

Read-only real-data check (2026-10-02): `allowlist preview` against the real
discovery listing with `--allowlist` pointed at a scratch path. A, B and C
previewed with exit 0. C without `--accept-flagged` was refused, naming the
five flagged components. Nothing was written: the scratch path was absent
afterwards, `G:\XLM\calib\component_allowlists` does not exist, and the
inventory sha256s were unchanged.

## 12. Requirement ledger

| Requirement | Status |
|---|---|
| T1 universe from the pinned listing, files/bytes/% | VERIFIED (re-derived inventory, digest checks) |
| T2 content intent separated from license | IMPLEMENTED (matrix columns) |
| T3 license/provenance matrix, 31 components | IMPLEMENTED (evidence-backed; uncertainty kept as NEEDS_OPERATOR_REVIEW) |
| T4 deep prose-candidate review | IMPLEMENTED; LibreTexts claim VERIFIED from code |
| T5 duplication/mixture fit | IMPLEMENTED (volume and composition are ESTIMATES, not measurements) |
| T6 three allowlists + recommendation | IMPLEMENTED; operator choice NOT RECORDED |
| T7 certification gap + bounded commands | IMPLEMENTED (plan); certification NOT RUN |
| T8 allowlist artifact + production refusal | IMPLEMENTED, VERIFIED (33 focused tests) |
| T9 production inventory filtering | IMPLEMENTED, VERIFIED (fixture tests); real freeze NOT RUN |
| T10 admission workflow | IMPLEMENTED (design); steps 3–5 BLOCKED |
| Legal determination | OUT OF SCOPE |
| Corpus download / admission / plan / authorization / C05 / tokenizer / training / push | NOT RUN by instruction |

## 13. Checks (Windows 11, Python 3.12.13, uv offline locked)

The first `uv run` in the new worktree failed: application control blocked
the freshly copied pyarrow `_parquet` DLL. While it was blocked, the
enumeration ran with the `xlm-ifm-bounds` venv (same `uv.lock`) and this
worktree's `src` on `PYTHONPATH`. The worktree's own environment imported
pyarrow later the same session, and every check below used it.

| Command | Exit |
|---|---|
| `uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_component_allowlist.py -n 0` | 0 (33 passed) |
| related selection (17 modules incl. `test_mix01_source_cli`, `test_mix01_inventory`, `test_source_plan`, `test_source_run`, `test_source_repair`, `test_hf_inventory`, `test_ifm_*`, `test_essential_web_{bulk,readiness,production_selector}`, `test_mix01_views`, `test_common_pile_live_certification`, `test_range_reach`, `test_parquet_window_sampling`), `-n 16 --dist=worksteal --max-worker-restart=0`, non-serial | 1: 452 passed, 1 failed (pre-existing, below) |
| same modules, serial markers, `-n 0` | 5 (no serial tests in the selection) |
| `ruff check` / `ruff format --check` on changed and new files | 0 / 0 |
| `mypy --strict` (compiled) on component_allowlist, source_plan, mix01_source, mix01_inventory, the new test, and 4 evidence scripts | 0 |
| `git diff --check` | 0 |

Pre-existing failure:
`test_essential_web_production_selector.py::test_registry_binds_the_frozen_selector_and_leaves_the_mixture_alone`.
It expects `recipes/mixtures/mix01.yaml` to be 864 B, the CRLF working-copy
size; git stores 835 B LF. It fails identically on the unmodified base
worktree `F:\Project\xlm-ifm-bounds`.

Also pre-existing and fixed in a touched file: a `mypy --strict` `no-redef`
of `sizes` in `scripts/mix01_inventory.py` (present at base line 149).

The full suite was not run (not a final acceptance gate). No live, network
or CUDA tests were run.

## 14. Next

Operator: choose A, B or C (or another list), run `allowlist preview`, then
`allowlist record`, then the production inventory freeze. Then authorize the
bounded real certification (section 7). Then a code milestone: a
`.jsonl.gz` production transport, a Common Pile calibration unit, a
`SourceSpec` and the catalog pin. Next prompt: "Implement the Common Pile
`.jsonl.gz` production transport and calibration unit for the recorded
allowlist; keep Common Pile blocked until certification, calibration and
admission pass."
