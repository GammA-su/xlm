# Global quality cleaning Phase B: policy dry run (v1)

Date: 2026-10-04. Base `51e0098` (Phase-A audit with the performance work). Fixture-only
implementation. **The real dry run has not been run, and no thresholds from the real
Phase-A audit are in this repository.**

## What was built

A read-only evaluator for a versioned cleaning policy:

- `recipes/quality/cleaning_policy_v1.yaml` is the rule template. Its `policy` section
  is pinned by digest in `xlm.data.quality.cleaning_policy`; any edit refuses.
- `python -m xlm.data.quality clean-freeze-policy` copies each component's
  CONSERVATIVE Phase-A candidate cut into a new self-digested FROZEN policy with full
  provenance. The input must be a Phase-A output whose receipt validates strictly
  and whose `candidate-policy-conservative.yaml` matches the receipt's SHA-256 and
  size. The provenance records the receipt, result and binding digests, the manifest
  digest and SHA-256, the detector policy, the code identity and commit, the
  candidate-file SHA-256 and size, and the template SHA-256.
- `python -m xlm.data.quality clean-dry-run` reads the canonical corpus once. Each
  worker task parses and verifies the rows, runs the Phase-A detectors once, evaluates
  the frozen policy and returns exact, content-free statistics plus bounded review
  candidates. It writes the six report artifacts, the binding and a strict receipt.
  It never writes text or a cleaned corpus.
- `clean-report` validates the receipt, re-hashes every source and re-derives every
  artifact byte for byte.
- `clean-materialize-review` (operator only, `--operator-confirm`) copies the selected
  review rows' text. It uses the Phase-A materialization chain:
  1. strict receipt, then the exact binding;
  2. artifacts re-derived from the units;
  3. the review manifest checked against the receipt (SHA, size, records);
  4. the exact row schema;
  5. a full re-hash of every selected source, with each (offset, row) locator proven;
  6. each row's SHA-256 and `doc_id` digest;
  7. only then is HTML-escaped, bounded text written to a NEW directory outside the
     repository and every input.

Reused unchanged from Phase A:

- the manifest loader and strict row parser;
- the authenticated C05 kept overlay (diagnostic only here);
- 8 MiB line-aligned chunks and the ordered worker pool;
- the bracketing re-hash before every commit and the job-owned output tree;
- the whole-command supervisor and two-phase receipt publication;
- live progress, resume, envelope validation and `check_execution_envelope`.

Two small Phase-A edits:

- `progress.Reporter` gained a `prefix` argument (dry-run lines read
  `[quality-clean]`);
- `runner._publish_receipt` gained a `name` argument.

Phase-A defaults are unchanged. Detector code and `policy.py` are untouched, so the
detector policy identity is still `8a8c5cc6...`. Because `src/` changed, the code
identity changed as well; see Limitations.

## Frozen policy table

| family | signal / metric | comparator | threshold | scope |
|---|---|---|---|---|
| hard | `markup_full_html` | flag | true | all |
| hard | `nul` | `>=` | 1 | all |
| hard | `noncharacters` | `>=` | 1 | all |
| severe repetition | `compression_ratio` | `<` | Phase-A conservative cut, per component | all |
| severe repetition | `ngram10_excess_ratio` | `>=` | Phase-A conservative cut, per component | all |
| severe repetition | `dup_line_byte_ratio` | `>=` | Phase-A conservative cut, per component | all |
| severe repetition | `dup_paragraph_byte_ratio` | `>=` | Phase-A conservative cut, per component | all |
| severe repetition | `repeated_char_ratio` | `>=` | Phase-A conservative cut, per component | all |
| severe repetition | `max_char_run` | `>=` | Phase-A conservative cut, per component | all |
| OCR | `page_number_line_ratio` | `>=` | Phase-A conservative cut | `finepdfs_en` only |
| OCR | `repeated_header_ratio` | `>=` | Phase-A conservative cut | `finepdfs_en` only |
| OCR | `single_char_line_ratio` | `>=` | Phase-A conservative cut | `finepdfs_en` only |
| encoding | `replacement_chars` | `>=` | 8 | all |
| encoding | `mojibake_hits` | `>=` | 16 | all |
| encoding | `c0_controls` / `c1_controls` (co-occurrence) | `>=` | 1 | all |
| guardrail | component DROP docs | `>` | 2 % | flag only |
| guardrail | global DROP docs | `>` | 2 % | flag only |
| guardrail | component DROP canonical bytes | `>` | 10 % | flag only |
| guardrail | global DROP canonical bytes | `>` | 5 % | flag only |

The per-component numeric cuts are produced by `clean-freeze-policy` from the real
Phase-A output on the operator machine. They are not in this commit; the real Phase-A
artifacts are not in the repository, and writing invented numbers would be fabrication.
The dry run refuses any policy that is not FROZEN, so it cannot run on placeholders.
`cleaning-dry-run.json` prints the full frozen table (`frozen_thresholds`).

## Decision semantics

Evaluated per document on the Phase-A measurements:

1. Hard corruption DROP: full HTML page, `nul >= 1` or `noncharacters >= 1`.
2. `severe_repetition_signal_count` is the number of the six signals that fire. A
   not-applicable value (e.g. compression below 256 bytes) or a `null` cut never fires.
   - Default group (`prose_like`, `other`, `markup_like`, `empty`): >= 2 is DROP;
     exactly 1 is KEEP and review-eligible.
   - Structured group (`math_table_like`, `code_like`): >= 3 is DROP, 2 is REVIEW,
     <= 1 is KEEP.
   - No class is exempt.
3. `finepdfs_en` only: with `ocr_signal_count >= 2`, >= 1 severe signal is DROP and 0
   severe signals is REVIEW.
4. Encoding: `replacement_chars >= 8` and `mojibake_hits >= 16` are each REVIEW. Both
   together is DROP. Either one together with a C0 or C1 control is DROP. Controls
   alone do nothing.
5. Outcome: DROP if any DROP rule fired, else REVIEW if any REVIEW rule fired, else
   KEEP. Every fired rule is recorded.

Never a reason on its own: non-ASCII, accents, CJK, Arabic/Hebrew, emoji, combining
marks, ZWJ/ZWNJ, math Unicode, BOM, bidi, zero-width, URLs, generic markup,
boilerplate, page numbers and headers (outside the `finepdfs_en` OCR rule), code, math
and tables. Each of these has a test.

Reports, globally and for every component:

- exact KEEP/DROP/REVIEW docs and canonical bytes;
- the DROP and REVIEW unions, recomputed from the exact rule-combination table and
  cross-checked;
- the 0-6 severe-signal histogram, by class group and outcome;
- each rule's marginal and exclusive impact, also by class;
- pairwise overlaps and every observed rule combination;
- the class distribution per outcome;
- OCR-scope counts;
- per-signal fires and not-applicable counts;
- the guardrails;
- C05 kept/removed outcomes, as a separately labelled diagnostic that never enters a
  decision.

Review manifest: 150 rows at most, deterministic, locators only.

- Strata, in order: priorities (finewiki_en, finepdfs_en, common_pile_prose, ultrax,
  encoding in essential_science / essential_prose / ultrax), 8 per DROP rule, 6 per
  REVIEW rule, one per component and outcome, protected structured and code-example
  cases, and near-threshold controls.
- Ranks are keyed BLAKE2b under SHA-256(seed, manifest digest), with a per-stratum
  salt and per-chunk bounded heaps, so the selection is identical for every worker
  count.
- Per-stratum members, selected counts and shortfall reasons are reported.

## Requirement ledger

| requirement | status |
|---|---|
| versioned `recipes/quality/cleaning_policy_v1.yaml`, KEEP/DROP/REVIEW, no TRANSFORM | IMPLEMENTED, VERIFIED (pinned digest; edit and duplicate-key refusal tests) |
| conservative thresholds copied/frozen with provenance; no later dependency on candidate YAML | IMPLEMENTED, VERIFIED on authored Phase-A output (verbatim-copy test, tamper refusals); real freeze NOT RUN |
| hard corruption, full HTML vs code examples | VERIFIED (detector-level tests) |
| severe repetition 0/1/2/3 by class group, structured protection | VERIFIED (parametrized decision tests and end-to-end counts) |
| FinePDFs-only OCR rule | VERIFIED (scoped tests; refusal of OCR cuts outside the scope) |
| encoding rule incl. C0/C1 co-occurrence | VERIFIED (boundary table 7/8, 15/16, both, controls) |
| legitimate Unicode preserved | VERIFIED (13 authored cases KEEP) |
| exact KEEP/DROP/REVIEW docs+bytes, unions, 0-6 histogram, marginal impacts, overlaps, classes | VERIFIED against an independent per-document oracle |
| C05 overlay diagnostic only | VERIFIED (authored proof; outcomes identical with and without the overlay) |
| guardrails, POLICY_REQUIRES_REVIEW, not an execution failure | VERIFIED (strict-boundary unit test; end-to-end flag) |
| deterministic review manifest <= ~150, priorities, coverage, controls, protected | VERIFIED (presence of every stratum, cap test, repeat-run identity, sampler vs brute-force sort) |
| materialize-review security model | VERIFIED (operator confirm, repo-destination refusal, tampered manifest refusal, exact excerpts, HTML escaping) |
| one detector pass + policy evaluation in the same worker task | VERIFIED (detector call count == documents) |
| `--workers`, `--progress-interval-seconds`, `--progress-log`; progress throughout | VERIFIED (log lines, prefix, no leakage) |
| worker determinism | VERIFIED (1/2/4 workers byte-identical artifacts and binding) |
| no source mutation, no text leakage | VERIFIED (hash of corpus, Phase-A audit and policies; canaries in artifacts, units, receipt, binding, log) |
| receipt/binding | VERIFIED (strict validation, forged status / envelope refusals, re-derivation, changed-policy refusal) |
| interruption/resume | VERIFIED (interrupt after 2 commits, resume identical; changed policy and mutated source refuse) |
| real dry run, Windows runtime | NOT RUN (operator) |

## Tests

`tests/test_quality_cleaning.py` (55 tests; fixtures in `tests/cleaning_fixtures.py`):
55 passed. The quality selection (7 modules) gave 429 passed and 2 failed; the 2 are
the PowerShell-junction tests, which cannot run on Linux and pass under the existing
Linux shim. The serial selection gave 1 passed. ruff format, ruff check,
`mypy --strict` and `git diff --check` are clean. Commands and logs:
[evidence](../evidence/QUALITY-CLEANING-PHASE-B-DRYRUN/COMMANDS.md).

## Performance

The dry run does no rescan: per-document work is the Phase-A detector pass plus an
O(1) rule evaluation and a bounded-heap sampler. On the same 128 MiB authored corpus,
1 worker, 3 runs, the dry run scanned 6.80-6.92 MB/s and the audit 6.83-7.00 MB/s.
Expected production time is therefore the operator's measured Phase-A audit time. The
earlier Phase-A projection for 104.5 GB at 16 workers on the 5700X3D was about 26.5 min
likely (22-33 min), plus about 1 min for the C05 overlay load. This is a projection,
not a measurement. Review-candidate memory is bounded by the stratum quotas, not by the
rows in a chunk.

## Limitations

- **No real thresholds yet.** The operator must run `clean-freeze-policy` against the
  completed Phase-A audit output before the dry run. Freezing validates the Phase-A
  receipt strictly and the candidate file's SHA-256, but does not re-hash the corpus.
  Run Phase-A `report` first if that has not been done.
- **Phase-A `report` / `materialize-review` at this commit.** They will refuse a
  Phase-A audit produced at `51e0098`: the code identity covers all of `src/`. Run
  them from the audit's own commit. The freeze does not need them.
- The dry run requires the frozen policy's Phase-A manifest digest to equal the
  dry-run manifest. Thresholds frozen for one corpus never judge another.
- `empty` documents are placed in the default class group; v1 has no empty-document
  rule.
- Windows behaviour (spawn cost, 16 workers) is inherited from Phase A and is not
  re-measured here.

## Next

On the operator machine, run the runbook's freeze → `clean-dry-run` → `clean-report`
sequence ([runbook](../../runbooks/quality-cleaning-dry-run.md)). Return the
content-free artifacts and the frozen policy for review. Use `clean-materialize-review`
locally if text inspection is wanted.
