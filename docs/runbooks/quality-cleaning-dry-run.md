# Global quality cleaning, Phase B: policy DRY RUN (read-only)

**Status (2026-10-04): implemented and tested on authored fixtures only. The real dry
run has NOT been run. No cleaned corpus exists and none is produced by anything on this
page.** Report: [QUALITY-CLEANING-PHASE-B-DRYRUN](../implementation/reports/QUALITY-CLEANING-PHASE-B-DRYRUN.md).
Phase A (measurement): [quality-audit.md](quality-audit.md).

Phase B v1 evaluates a frozen cleaning policy on every canonical document and reports
what it WOULD do: `KEEP`, `DROP` or `REVIEW`. It never writes, drops or transforms a
document. No production `TRANSFORM` exists in v1. C05, the tokenizer fit and training
are untouched. Agents must not run it on real data or open review text; the operator
does.

## The policy

`recipes/quality/cleaning_policy_v1.yaml` is the versioned rule template. Its `policy`
section is pinned in code (`POLICY_SECTION_DIGEST`); any edit refuses. It carries no
thresholds. `clean-freeze-policy` copies, once, each component's CONSERVATIVE Phase-A
candidate cut into a new self-digested `FROZEN` file. The copied cuts are the six
severe-repetition signals and, for `finepdfs_en` only, the three OCR signals. Each
comparator, cut, cut bin, quantile bin and Phase-A impact is copied verbatim, with
provenance:

- Phase-A receipt, result and binding digests;
- input-manifest digest and file SHA-256;
- detector policy;
- code identity and commit;
- SHA-256 and size of the candidate YAML, checked against the receipt.

After the freeze the dry run reads only the frozen file. It never reads the mutable
candidate YAML again. The frozen file is content-free and may be committed.

| rule | action | condition |
|---|---|---|
| `hard.full_html` | DROP | `markup_full_html` (true HTML page; fenced/indented/inline code examples never set it) |
| `hard.nul` | DROP | `nul >= 1` |
| `hard.noncharacters` | DROP | `noncharacters >= 1` |
| `rep.severe_default` | DROP | class `prose_like` / `other` / `markup_like` / `empty` and >= 2 severe signals |
| `rep.severe_structured` | DROP | class `math_table_like` / `code_like` and >= 3 severe signals |
| `rep.structured_two_signals` | REVIEW | `math_table_like` / `code_like` and exactly 2 severe signals |
| `ocr.finepdfs_with_repetition` | DROP | `finepdfs_en`, >= 2 OCR signals and >= 1 severe signal |
| `ocr.finepdfs_review` | REVIEW | `finepdfs_en`, >= 2 OCR signals and 0 severe signals |
| `enc.replacement_review` | REVIEW | `replacement_chars >= 8` |
| `enc.mojibake_review` | REVIEW | `mojibake_hits >= 16` |
| `enc.replacement_and_mojibake` | DROP | both encoding signals |
| `enc.with_forbidden_controls` | DROP | either encoding signal and `c0_controls >= 1` or `c1_controls >= 1` |

The six severe signals are `compression_ratio` (low), `ngram10_excess_ratio`,
`dup_line_byte_ratio`, `dup_paragraph_byte_ratio`, `repeated_char_ratio` and
`max_char_run` (high). The OCR signals are `page_number_line_ratio`,
`repeated_header_ratio` and `single_char_line_ratio`. A signal fires on
`value <comparator> cut`, using the Phase-A comparator exactly (`>=` high, `<` low). A
not-applicable value never fires. A component/signal with no Phase-A rule is `null`
and never fires.

Outcome: DROP if any DROP rule fires, else REVIEW if any REVIEW rule fires, else KEEP.
A default-group document with exactly one severe signal stays KEEP and is review
sampled. Non-ASCII, accents, CJK, Arabic/Hebrew, emoji, combining marks, ZWJ/ZWNJ,
math Unicode, BOM, bidi, zero-width, URLs, generic markup, boilerplate, page numbers,
headers, code, math and tables are never a reason on their own. C0/C1 controls alone
are not a reason either.

**Guardrails** (strict `>`, exact integers): component DROP docs > 2 %, global DROP
docs > 2 %, component DROP canonical bytes > 10 %, global DROP canonical bytes > 5 %.
Crossing one is NOT an execution failure; it sets `POLICY_REQUIRES_REVIEW` (otherwise
`POLICY_WITHIN_GUARDRAILS`) in every artifact and the receipt.

## Commands (Windows, operator)

The CLI and the supervision model are those of Phase A: deadline, process-tree RSS,
free-space reserve, output ceiling, the job-owned output tree, resume, and a
two-phase receipt. Stdout carries one JSON result; progress goes to stderr with the
`[quality-clean]` prefix.

```powershell
$q = 'uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.quality'
$m = '<the "manifest" path inside G:/XLM/c05/p0002.proof.json>'   # the manifest Phase A audited
$p = 'G:/XLM/c05/p0002.proof.json'
$a = 'G:/XLM/quality/audit-v3'                                     # the COMPLETED Phase-A audit output
$f = 'recipes/quality/cleaning_policy_v1.frozen.yaml'              # NEW file (content-free)
$o = 'G:/XLM/quality/clean-dry-run-v1'                             # NEW, empty or absent
$l = 'C:/XLM-logs/quality-clean-dry-run-v1.progress.log'           # NOT under $o or G:/XLM/data
$w = 16                                                            # the Phase-A benchmark's recommended_workers
# 0. Freeze ONCE (reads only $a: strict receipt + candidate YAML SHA-256; writes $f).
Invoke-Expression "$q clean-freeze-policy --template recipes/quality/cleaning_policy_v1.yaml --audit-output $a --destination $f"
# 1. The dry run. Resumable: after any interruption rerun the IDENTICAL command.
#    --c05-proof is optional (diagnostic kept/removed overlay only); as in Phase A it
#    refuses while X: is mounted and needs the proof's key environment variables.
Invoke-Expression "$q clean-dry-run --manifest $m --policy $f --output $o --c05-proof $p --workers $w --max-rss-gib 12 --free-reserve-gib 8 --max-output-gib 4 --max-document-mib 64 --deadline-hours 12 --progress-interval-seconds 5 --progress-log $l"
# 2. Verification: strict receipt, current binding, FULL source re-hash, every artifact
#    re-derived from the units and compared byte for byte.
Invoke-Expression "$q clean-report --manifest $m --policy $f --output $o --c05-proof $p --workers 4 --max-rss-gib 8 --deadline-hours 6"
# 3. OPERATOR ONLY, optional: copy the SELECTED review rows' text (<= 150) into a NEW
#    local directory outside the repository, the data root and the dry-run output.
Invoke-Expression "$q clean-materialize-review --output $o --c05-proof $p --destination C:/XLM-review/clean-dry-run-v1 --operator-confirm --max-documents 150 --max-chars 20000 --max-output-mib 256"
```

Without the overlay, drop `--c05-proof $p` from steps 1-3. Step 3 can be narrowed with
`--strata`, `--components` and `--outcomes DROP REVIEW`.
`clean-materialize-review` reads the manifest and policy paths from the receipt unless
`--manifest` / `--policy` are given.

Refusals that protect the run:

- the template (unfrozen) policy, or an edited frozen file (self-digest);
- a policy frozen from a Phase-A audit of a different manifest, or under different
  detector semantics;
- an OCR threshold outside `finepdfs_en`;
- a manifest component without thresholds;
- an output directory that holds a Phase-A audit or anything foreign;
- a changed binding on resume (code, policy, manifest, overlay, chunking);
- a source file that changed.

## Progress

The same phases and line format as Phase A: `prepare`, `overlay`, `resume-verify`,
`scan`, `verify-drain`, `aggregate`, `write`, `publish`. Each line shows files, docs,
GB, percent, window/EWMA MB/s, ETA, measured active workers, in-flight chunks, CPU,
process-tree RSS and output bytes. `--progress-interval-seconds` (0.2-3600),
`--no-progress` and `--progress-log` behave as in Phase A. The log holds numbers and
phase names only.

Each worker task parses, verifies, runs the Phase-A detectors ONCE and evaluates the
policy for every row of its chunk. There is no audit-then-rescan.

## Expected runtime

Per-worker throughput equals the Phase-A audit's within measurement noise (same 128 MiB
authored corpus, 1 worker, 4-vCPU container, three runs: dry run 6.80-6.92 MB/s, audit
6.83-7.00 MB/s). Use
your measured Phase-A audit wall time as the estimate. The Phase-A projection for
104.5 GB on the Ryzen 7 5700X3D at 16 workers was about 26.5 min likely (22
optimistic, 33 conservative). Add about 1 minute for the C05 overlay load when
`--c05-proof` is given. That is a projection, not a measurement. `clean-report`
re-hashes the corpus (disk bound) and re-derives the artifacts.

## Outputs (`--output`, all content-free)

| File | Content |
|---|---|
| `cleaning-dry-run-summary.md` | policy status, KEEP/DROP/REVIEW docs + bytes per scope, guardrails, 0-6 signal histogram, rule table, review strata |
| `cleaning-dry-run.json` | everything per global/component: exact outcomes, DROP/REVIEW unions, 0-6 severe-signal histogram (by class group and outcome), single-signal review-eligible KEEP, per-signal fires and not-applicable, OCR scope, class distribution per outcome, every rule's marginal/exclusive impact; guardrails; frozen threshold table; C05 overlay diagnostic; review strata report |
| `cleaning-by-component.json` | full summary and guardrail breaches per component |
| `cleaning-by-rule.json` | per rule: action, definition, global marginal/exclusive impact, by class, per component |
| `cleaning-rule-intersections.json` | per scope: DROP/REVIEW unions recomputed from the exact rule-combination table, pairwise overlaps, every observed rule combination |
| `cleaning-review-manifest.jsonl` | <= 150 locator rows (path, row, offset, `doc_id_sha256`, `row_sha256`, outcome, rules, signals, numbers, strata); never text |
| `cleaning-policy-binding.json` | the dry-run identity and output-ownership marker (manifest, data root, detectors, frozen policy digest/SHA-256 and Phase-A provenance, code, overlay, chunking) |
| `cleaning-dry-run-receipt.json` | the only completion signal (strict, self-digested; envelope checked against measured facts) |
| `units/` | per-file committed statistics (resume) |

Review sampling is deterministic: a keyed BLAKE2b rank under SHA-256(seed, manifest
digest), bottom-k per stratum, order independent. It is byte-identical for every worker
count. Strata are taken in this order:

1. priorities: finewiki_en severe repetition and compression, finepdfs_en OCR plus
   repetition, common_pile_prose character runs, ultrax_ultrafineweb severe tails,
   encoding in essential_science / essential_prose / ultrax_ultrafineweb;
2. 8 per DROP rule;
3. 6 per REVIEW rule;
4. one per component and outcome;
5. protected structured / code-example cases;
6. near-threshold controls.

A stratum takes new documents only until `quota` selected rows belong to it. Selection
stops at 150 rows. Shortfalls are reported with their reason.

## What to return for policy review

Return these content-free files and the `clean-report` JSON line:

- `cleaning-dry-run-receipt.json`
- `cleaning-dry-run-summary.md`
- `cleaning-dry-run.json`
- `cleaning-by-component.json`
- `cleaning-by-rule.json`
- `cleaning-rule-intersections.json`
- `cleaning-policy-binding.json`
- the frozen policy file

Materialized review text never leaves the operator machine.
