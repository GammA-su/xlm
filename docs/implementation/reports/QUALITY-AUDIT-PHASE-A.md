# Global quality cleaning, Phase A: read-only quality audit

Date: 2026-10-04. Branch `feat/global-quality-audit`, based on `40ce62a`
(`feat/c06-fit-fast`, the line that carries the C05 and C06 code). Worktree:
`F:/Project/xlm-quality-audit`.

**Verdict: implemented and fixture-verified, ready for the operator run. The real
audit was NOT run.** No corpus document was read, modified or written. No network or
`X:` access occurred. No C05, tokenizer fit or training was run, and no threshold
was frozen. All results below come from authored fixtures or authored synthetic
data; the real-corpus figures are projections.

## 1. Cleaning that already exists before this stage

- **Canonical normalization** (`xlm.data.normalization.canonical_normalize`): NFC
  and `\r\n`/`\r` -> `\n` only. It preserves case, whitespace, indentation,
  punctuation, angle brackets, math, emoji and combining marks.
- **Source adapters** (`xlm.data.adapters.mix01_adapters`) apply row validity and
  source-policy rules only:
  - they reject empty or whitespace-only content and missing fields;
  - English routing: FineWiki `in_language == "en"`, FinePDFs `eng_Latn` routing
    label, SYNTH row `language == "en"`;
  - Nemotron organic `quality_category`;
  - FinePDFs keeps `docling` text extraction only and rejects `rolmOCR` image OCR;
  - UltraX uses upstream `cleaned_content` and drops it when empty;
  - Essential-Web uses the frozen B-normal selector. It is metadata-only and was
    not validated on text; `fasttext_english` is preserved.

  Every adapter records `language_provenance` with "downstream (language)
  cleaning still decides".
- **Legacy `xlm.data.cleaning`** is the pre-Mix-01 `xlm data clean` path, with
  fixed thresholds such as `max_char_run=50` and duplicate-line ratio 0.30. It is
  **not** on the Mix-01 acquisition path. Phase A deliberately reuses none of its
  thresholds.
- **C05** does exact and near dedup, benchmark contamination, lineage and splits. It
  is not quality cleaning.

## 2. What is genuinely missing

There is no global stage between the canonical corpus and C05 that measures or
removes the following, and no policy for them:

- markup and boilerplate;
- repetition loops;
- pathological character runs;
- control, encoding and mojibake junk;
- OCR layout damage;
- extreme composition;
- unverified (inherited) language.

Phase A supplies the measurement. Phase B (policy, then DROP/TRANSFORM) is not
implemented.

## 3. Audit architecture (`src/xlm/data/quality/`)

| Module | Role |
|---|---|
| `policy.py` | Frozen, data-only detector definitions (65 metrics, 40 flags, 6 interpretation classes, 8 boolean and 13 joint intersections, histogram bins, review strata, language evidence keys) and their digest |
| `detectors.py` | Pure per-document `analyze(text, utf8_bytes)` that returns numbers, flag indices and a class. No text leaves it. |
| `aggregate.py` | Exact, mergeable integer populations: histograms (docs and bytes per bin), extrema, sums, flags, classes, intersections and language counts. Binning is vectorized with numpy. |
| `review.py` | Bottom-k review sampling by keyed BLAKE2b rank, role assignment, and operator-only `materialize_review` |
| `overlay.py` | Optional authenticated C05 kept overlay via `fitfast.open_streamed` |
| `scan.py` | Manifest loading, the sequential hashing reader, fixed 32 MiB line-aligned chunks, an ordered spawn pool, per-file atomic units and resume checks |
| `report.py` | Deterministic artifacts from units (streamed) |
| `runner.py` / `cli.py` | Orchestration, limits, supervisor, receipt; `python -m xlm.data.quality {audit,report,materialize-review}` |

The parent process reads each manifest file once, sequentially, and hashes every
byte. Workers measure chunks and return dense integer histograms; results are
consumed in task order. A file's unit is committed only after its SHA-256, size,
row count and canonical-byte total equal the manifest. Aggregation streams the
units one at a time. The existing `Supervisor` enforces process-tree RSS, the free
reserve and the deadline.

## 4. Detectors implemented (all heuristics; none is a decision)

- **A, basic statistics:** UTF-8 bytes, code points, lines, non-empty lines,
  max and mean line length, whitespace, alphabetic, digit, punctuation (P*), symbol
  (S*) and non-ASCII ratios, unique characters.
- **B, size:** empty, whitespace-only, under 16/32/64/128/256 characters, over
  64 KiB / 256 KiB / 1 MiB / 4 MiB, each with docs and bytes.
- **C, markup:**
  - known-element HTML tags and their character ratio; any tag-shaped token;
    entities; `<script`/`<style` blocks;
  - flags for DOCTYPE, `html`, `head`, `body`, `script`, `style`, `nav`, `footer`,
    `form`, closing tags, entities, XML declaration and HTML comments;
  - classes `markup_full_html`, `markup_light`, `markup_ambiguous_code` and
    `markup_any`.
- **D, boilerplate:**
  - six keyword-gated phrase categories: cookie, privacy, terms, subscribe,
    copyright, social share;
  - three whole-line menu sets: account, navigation, social sites;
  - URLs and URL character ratio, common-TLD domain mentions, menu/anchor-like line
    ratio.
  - Only the category is recorded.
- **E, character runs:** the longest same-character run overall and per class
  (punctuation/symbol, whitespace, letter/digit, other); the fraction of
  characters in runs of 8 or more; buckets at 8/16/32/64/128/256.
- **F, line and paragraph repetition:** compared on a whitespace-collapsed form,
  with lines of at least 16 characters and paragraphs of at least 64. Reported:
  duplicate count and byte fraction, top-line occurrences and byte share, unique-line
  ratio, duplicate paragraphs and byte fraction.
- **G, loops (heuristic):** word 5-gram and 10-gram excess ratios, top 10-gram
  share, and the zlib level-1 compression ratio (documents of 256 bytes or more,
  first 1 MiB). It uses whitespace units only, with no tokenizer, and reads at most
  100,000 words.
- **H, Unicode:** NUL, C0 (except LF and TAB) plus DEL, C1, U+FFFD, zero-width
  (U+200B/2060/180E), ZWJ/ZWNJ (reported separately), BOM, bidi controls,
  private use, noncharacters, surrogates, and conservative mojibake sequences.
- **I, OCR/PDF:** single-character lines, fragment lines (under 40 characters),
  page-number-like lines, repeated 16–100-character header lines (3 or more
  times), letter-hyphen-newline breaks, lowercase soft breaks.
- **J, composition:** words, type/token ratio, distinct words, code-shaped lines,
  table lines, unambiguous math symbols, LaTeX commands.
- **Classes:** `empty`, `markup_like`, `code_like`, `math_table_like`,
  `prose_like` and `other`. They interpret detector precision; they are not
  training labels.
- **K, language:** the `language` field values, `language_confidence` histogram,
  row-level evidence keys (`fasttext_english`, `upstream_full_doc_lid(_score)`,
  `upstream_page_average_lid(_score)`), `language_provenance`, other `lang`/`lid`
  metadata keys, and an assessment per scope.

## 5. False-positive protections (each one fixture-tested)

- **Markup:**
  - opening tags need a known element name and must not follow a word character
    or `<`, so `vector<a>`, `x<b>` and `a<<b` are excluded;
  - closing tags are always counted;
  - C++ generics and XML are `markup_ambiguous_code`, never HTML;
  - `0 < x < 1` and `a<b` produce no markup.
- **Math:** ASCII `= + < > ^ ~` are not counted as math (they also mean code and
  markup). `|f(x)|` is not a table row.
- **Repetition:**
  - `}`, `{` and `---` are below the minimum comparison length;
  - code braces give 0 duplicate lines;
  - a Markdown separator is recorded as a 40-character run, but the document stays
    `prose_like`;
  - ASCII art has no run of 8 or more.
- **Unicode:** accented Latin, Japanese, emoji, combining marks and ordinary
  prose give zero controls and zero mojibake. Emoji ZWJ is reported apart from
  zero-width junk.
- **OCR:** clean scientific text gives zero page-number, hyphen, single-character
  and header signals. Hyphenation is measured, never joined.
- **Classes:** Japanese prose is `prose_like` (a character-length fallback replaces
  the whitespace word count); code, LaTeX and tables are protected classes.
- **No text and no decisions:** detectors emit no text and no decision; candidate
  bands carry `action: null`.

Four probes found and fixed real defects before the tests were written:

- the DOCTYPE and XML-declaration lookahead never matched;
- closing tags after a word were missed;
- ASCII "math" pushed light HTML and XML into `math_table_like`;
- `|f(x)|` and symbol garbage counted as table rows.

## 6. Source-specific handling

Every summary is reported for the global scope, all 11 components, all 17
allocations (`component|view|upstream`) and all 10 source keys. The candidate
policies record a source-type prior per component: web (`essential_*`,
`ultrax`), pdf (`finepdfs_en`), synthetic (`synth`, `nemotron_wiki_rewrite`, IFM),
encyclopedic (`finewiki_en`), mixed public domain (`common_pile_prose`), synthetic
stories (`simple_stories`). They also record a detector scope proposal:

- universal: size, H, E, F, G;
- web-specific: C and D, applied by default to web and mixed;
- PDF/OCR-specific: I, applied by default to pdf and mixed;
- prose-specific: A ratios and J.
- Protected classes are code-like and math/table-like.

All of this is PROPOSAL_ONLY. The real distributions decide.

## 7. Review sampling design

- **Rank and strata:** rank = BLAKE2b-64 keyed by the frozen seed
  `xlm-quality-audit-review-v1` over `(path, row)`. Each stratum
  `(metric, coarse bin)` XORs its own salt into the rank and keeps the 4
  smallest, with ties broken by `(path, row)`. Bottom-k is order independent, so
  the selection is identical for any chunking or worker count; this is tested.
- **Roles,** per component and detector:
  - `strong_positive`: the populated coarse bin at the suspicious end;
  - `control`: the populated bin at the clean end;
  - `near_threshold`: the bin containing the moderate PROPOSAL_ONLY cut;
  - `stratum`: everything else.
- **Content:** rows hold locator (path, row, byte offset, doc_id), detector, value,
  class, three context sizes and C05 kept status. They hold no text. The size is
  bounded: at most 11 components × 31 review metrics × 12 coarse bins × 4.
- **Materialization:** `materialize-review` requires `--operator-confirm`. It
  writes only to a new directory outside the repository and the data root,
  verifies each source file's size and mtime and each row's `doc_id`, bounds the
  documents (at most 5,000) and characters (head and tail excerpt), and
  HTML-escapes the output.

## 8. Artifact schemas

The full list is in the [runbook](../../runbooks/quality-audit.md#outputs---output). Notes:

- **Impact:** every flag, bucket, class, intersection and candidate rule reports
  `docs`, `bytes`, `docs_pct` and `bytes_pct` against its population.
- **Quantiles:** p50, p75, p90, p95, p99 and p99.9, as histogram bins `[lo, hi)`
  (exact below 16 for counts, width 0.001 for ratios), with max, min, and mean
  for counts.
- **Populations:** `all`, plus `c05_kept` and `c05_removed` with the overlay.
- **Receipt:** binds the input manifest digest and file SHA-256, every source
  file's identity (path, SHA-256, bytes, rows, size, mtime), the code identity and
  commit, the dependency lock digest, the detector-policy version and digest, the
  overlay digests, and the worker-independent `result_digest` (a digest of the
  artifact SHA-256s). Execution facts are kept separate.

## 9. Resume behaviour

- Per-file atomic units are reused only under an identical `audit-binding.json`
  (code, dependencies, policy, manifest, overlay, chunk and row ceilings) and an
  unchanged source size and mtime. Anything else refuses.
- A completed directory refuses a re-audit; use `report`.
- Without the receipt there is no result, and `report` refuses.
- The interrupt-then-resume output is byte-identical to a clean run (tested).

## 10. Tests

All tests use authored fixtures only and have no network access.

- `tests/test_quality_detectors.py` has 49 tests: HTML, repetition, Unicode, OCR,
  composition and classes, scalar/vectorized bin equivalence, merge associativity,
  review order independence, and policy identity.
- `tests/test_quality_audit.py` has 32 tests:
  - content-free artifacts (canaries absent from artifacts and units) and corpus
    hashes unchanged;
  - PROPOSAL_ONLY YAML;
  - review locators;
  - inherited versus row-level language evidence;
  - byte-identical artifacts at workers 1/2/4/8 with 4 KiB chunks, and
    chunk-size invariance;
  - resume equal to a clean run;
  - changed-file and changed-policy refusal;
  - completed-run refusal, `report` re-derivation and tamper detection;
  - SHA, row-count, byte-count, malformed-row, ceiling and manifest-digest
    refusals;
  - output placement rules, the output byte ceiling and limit bounds;
  - the C05 overlay from an authored `scripts.c05_synthetic_flow` proof:
    all = kept + removed equals the signed completion; authored proof refused
    without the flag; membership tamper refused; foreign manifest refused;
  - review materialization (escaping, refusals);
  - the CLI, including `--operator-confirm`, and `python -m` spawning workers.

The command used the AGENTS.md test settings (`OMP_*`, `MKL`, `OPENBLAS` and
`NUMEXPR` at 1, `TOKENIZERS_PARALLELISM=false`):

```text
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest \
  tests/test_quality_detectors.py tests/test_quality_audit.py \
  -n 8 --dist=worksteal --max-worker-restart=0 -p no:cacheprovider -q
81 passed in 10.87s, exit 0
```

This is a focused run. The full offline acceptance suite was **NOT RUN**: no
existing module was modified, and the run is not a release gate.

## 11. Static checks (exit 0 each; logs in `evidence/QUALITY-AUDIT-PHASE-A/`)

The 15 files checked: the package, the 3 test files and the benchmark script.

- `ruff format --check`: 15 files already formatted.
- `ruff check`: all checks passed.
- `mypy --strict`: no issues in 15 source files.
- `git diff --check`, with the new files intent-added: clean.

## 12. Authored throughput

Command: `scripts/quality_audit_benchmark.py`. The corpus was 268,480,026 bytes,
47,015 documents and 8 files: a synthetic mix of 70% prose plus code, HTML,
tables, OCR, web and loops, with 5.7 KB mean rows. Machine: Windows 11, 16 logical
and 8 physical cores, 72 GiB RAM. One result digest held across 1/4/8/16 workers.

| workers | MB/s (file bytes) | docs/s | peak process-tree RSS |
|---:|---:|---:|---:|
| 1 | 6.6 | 1,157 | 0.13 GiB |
| 4 | 20.2 | 3,544 | 0.71 GiB |
| 8 | 32.9 | 5,753 | 1.31 GiB |
| 16 | 30.5 | 5,335 | 1.80 GiB |

Other measurements:

- **Aggregation scale:** 2,035 units replayed across 11 components took 21.9 s at
  0.13 GiB peak RSS. The largest artifact is about 2.7 MB.
- **Overlay staging:** 184k membership rows/s, about 5.4 s per million kept rows
  (synthetic 623-byte rows).
- **Before optimization:** the first detector draft ran at 2.5 MB/s per core.
  Keyword-gated boilerplate, numpy character counts and run probes brought it to
  7.65 MB/s.

## 13. PROJECTED real audit time (projection, not a measurement)

At 104,506,534,003 file bytes and 15,097,174 documents, the 8-worker authored
rate gives:

- **0.88 h by bytes, 0.73 h by documents.**
- Add the overlay: about 5 s per million kept rows, plus hashing the membership
  file.
- Add aggregation: under 1 min.

Plan for 1–2 h; the deadline default is 12 h. Caveats:

- the real detector-cost mix may differ (more markup, longer lines);
- the `G:` SATA read rate (about 0.5 GB/s, an assumed drive-class figure) is far
  above the CPU-bound rate, so I/O should not bind.

## 14. Exact real operator command

The full sequence is in [the runbook](../../runbooks/quality-audit.md#commands):
`audit`, then `report`, then the optional `materialize-review`. The core command:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.quality audit --manifest <"manifest" path inside G:/XLM/c05/p0002.proof.json> --output G:/XLM/quality/audit-v1 --c05-proof G:/XLM/c05/p0002.proof.json --workers 8 --max-rss-gib 12 --free-reserve-gib 8 --max-output-gib 4 --max-document-mib 64 --deadline-hours 12
```

Prerequisites:

- `X:` is detached;
- the trust-key environment variables are set, as for C06;
- the code is committed, so `code_commit` is meaningful. Resume binds the `src/`
  hash in any case.

The committed `docs/implementation/evidence/C05-GLOBAL-CONTAMINATION-PLAN/input-manifest.json`
has digest `11724d92...`. The overlay refuses unless the proof's plan binds that
same manifest digest.

## 15. Expected output paths

Everything goes under `G:/XLM/quality/audit-v1/`:

- `quality-audit.json`
- `quality-by-component.json`
- `quality-histograms.json`
- `quality-intersections.json`
- `quality-language.json`
- `review-manifest.jsonl`
- `candidate-policy-{conservative,moderate,aggressive}.yaml`
- `quality-summary.md`
- `quality-audit-receipt.json`
- `audit-binding.json`
- `units/f00000.unit.zz` to `units/f02034.unit.zz`, expected around 0.1–0.2 GB

The optional review text goes to `C:/XLM-review/quality-v1/`, which is local only.

## 16. What the operator should return for policy selection

- **Content-free files:** the receipt, `quality-audit.json`,
  `quality-by-component.json`, `quality-intersections.json`,
  `quality-language.json`, `quality-summary.md`, the three candidate YAMLs and the
  `report` JSON line.
- **Run facts:** the stderr tail with throughput and RSS.
- **Optional, also content-free:** `quality-histograms.json` and
  `review-manifest.jsonl`.
- **Review results, if a review is done:** per detector and role, the counts
  judged true positive, false positive and unclear. Never the review text.

## 17. Detectors that need a human/operator policy choice

- **Every candidate rule:** whether it applies, its cut, and its action
  (KEEP/DROP/TRANSFORM). Each action is `null`.
- **Markup on web components:** DROP full-HTML pages or TRANSFORM (strip)? A
  TRANSFORM creates new duplicates and needs a C05 rerun.
- **Boilerplate lines:** line removal (TRANSFORM) or tolerate? The precision of
  the phrase and menu categories must be reviewed first.
- **OCR on FinePDFs and Common Pile:** DROP heavy fragmentation, or a reviewed
  dehyphenation/line join? Phase A never joins.
- **Encoding:** DROP mojibake and replacement-character documents, or repair them?
  Repair would need a new dependency, which is not installed. Is a BOM or
  zero-width character inside text strippable, given that ZWJ/ZWNJ are legitimate?
- **Repetition:** the cut for loops (10-gram excess, compression) and duplicate
  paragraphs; whether to DROP or keep the first occurrence (TRANSFORM).
- **Protected classes:** whether prose-specific detectors are disabled for
  code-like and math/table-like documents.
- **Language:** components assessed as inherited only (expected:
  `simple_stories`, `ifm_*`, `nemotron_wiki_rewrite`, `common_pile_prose`,
  `ultrax`; SYNTH's row label is checked at adaptation but no score is retained)
  may need a classifier. A small fastText LID is proposed but **not installed or
  used**; the operator decides.
- **Analysis bounds:** documents over 100,000 words or 1 MiB are flagged
  `truncated_analysis` for loop statistics. Decide whether their tail needs
  separate review.

## Requirement ledger

| Requirement | Status |
|---|---|
| Read-only streaming audit over the C05 input-manifest format | IMPLEMENTED, VERIFIED (fixtures) |
| `--c05-proof` kept/removed overlay, authenticated, no C05 writes | IMPLEMENTED, VERIFIED (authored proof) |
| Workers 1/2/4/8 (and 16) give byte-identical aggregate artifacts | VERIFIED (fixtures; benchmark 1/4/8/16) |
| Dimensions A–K, flag intersections, byte impact | IMPLEMENTED, VERIFIED (fixtures) |
| Deterministic bounded review sampling, operator-only materialization | IMPLEMENTED, VERIFIED |
| Candidate bands PROPOSAL_ONLY with impact; action schema KEEP/DROP/TRANSFORM | IMPLEMENTED (no action decided) |
| Resume, changed-file refusal, partial never complete | IMPLEMENTED, VERIFIED |
| Memory < 8 GiB | VERIFIED on authored data (1.3 GiB scan at 8 workers, 0.13 GiB aggregation of 2,035 units); real NOT RUN |
| Authored throughput benchmark and real-time projection | VERIFIED (authored) / PROJECTION only |
| Real-corpus audit | NOT RUN (operator) |
| Final cleaning thresholds, DROP/TRANSFORM, cleaned corpus, new C05, tokenizer, training | OUT OF SCOPE (Phase B and later) |
| Language classifier | OUT OF SCOPE (proposal only) |
| Full offline acceptance suite | NOT RUN (focused only) |

## Open limitations

- The detectors are heuristics. Boilerplate, page-number and roman-numeral lines,
  `from `/`class ` code prefixes and the class rules will have measurable false
  positives; the review exists to measure them.
- The CPU-bound rate stops scaling beyond the 8 physical cores.
- Candidate impacts are marginal per rule. The union of rules needs a Phase-B dry
  run.
- Language label tables keep at most 256 distinct values per scope; the rest go
  to `<other>`.

Next: the operator commits this branch, then runs the audit, `report` and
optionally `materialize-review` as above, and returns the files listed in section
16.
