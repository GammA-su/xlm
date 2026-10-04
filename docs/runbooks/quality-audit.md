# Global quality audit (Phase A, read-only)

**Status (2026-10-04): first real run BLOCKED by independent acceptance at 96f38f3.**
See [the independent audit and required fixes](../implementation/reports/QUALITY-AUDIT-INDEPENDENT-96F38F3.md).
The commands below document the interface; do not launch them on real data until
the listed repairs pass bounded re-acceptance. Implementation is unchanged and the
real audit has NOT been run.
Phase A only measures. It never modifies, drops or transforms a document, never
chooses a cleaning threshold, never builds a C05 manifest and never runs C05,
a tokenizer fit or training. Agents must not run it on the real corpus or open review
text; the operator does.

## Why this stage exists

The production order was adaptation -> canonical corpus -> C05 global dedup and
contamination -> C06 tokenizer fit. That order is missing a global high-quality
cleaning stage. The intended order is:

```text
source adaptation -> canonicalization -> GLOBAL QUALITY CLEANING -> global exact/near
dedup -> benchmark contamination / lineage propagation -> final splits/membership ->
tokenizer fit
```

Canonical normalization (NFC plus newline normalization) is deliberately
conservative. The adapters only reject empty text, route English views and
preserve upstream language evidence. Every adapter defers language and quality
decisions with "downstream cleaning still decides". Phase A measures what survived.

**Cleaning creates new duplicates.** `<p>Hello</p>` and `<div>Hello</div>` both become
`Hello`. Any production DROP or TRANSFORM therefore makes every existing C05
artifact stale: dedup membership, contamination proof, lineage groups, splits and
completion. The required order after cleaning is:

```text
cleaned canonical corpus -> NEW input manifest/seals -> rerun global exact+near dedup
-> rerun benchmark contamination -> rerun lineage grouping and splits
-> NEW C05 completion/proof -> tokenizer fit
```

The current C05 p0002 result is historical evidence once cleaning changes the corpus.
Keep it unchanged.

## Commands

The CLI is `python -m xlm.data.quality`. Stdout carries one JSON result; progress
lines go to stderr and start with `[QUALITY]`.

```powershell
$q = 'uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.quality'
$m = '<the "manifest" path inside G:/XLM/c05/p0002.proof.json>'   # expected digest 11724d92...
$o = 'G:/XLM/quality/audit-v1'
# 0. Detach X: first. --c05-proof refuses while the protected root is mounted.
#    Every key environment variable named by the proof's trust file must be set (same as C06).
# 1. The audit: one source pass, resumable. After any interruption, rerun the IDENTICAL command.
Invoke-Expression "$q audit --manifest $m --output $o --c05-proof G:/XLM/c05/p0002.proof.json --workers 8 --max-rss-gib 12 --free-reserve-gib 8 --max-output-gib 4 --max-document-mib 64 --deadline-hours 12"
# 2. Independent re-derivation of every artifact from the committed units (no source read).
Invoke-Expression "$q report --manifest $m --output $o --c05-proof G:/XLM/c05/p0002.proof.json"
# 3. OPERATOR ONLY, optional, after deciding to inspect text: copy selected review documents
#    into a NEW local directory outside the repository and outside the data root.
Invoke-Expression "$q materialize-review --output $o --destination C:/XLM-review/quality-v1 --operator-confirm --roles strong_positive near_threshold control --max-documents 500 --max-chars 20000"
```

Without `--c05-proof`, the audit reports only the `all` population. With it, the
audit also reports `c05_kept` (the authenticated kept membership of the p0002
completion) and `c05_removed`. `--allow-authored-proof` exists for fixture
rehearsals only; a protected proof never needs it.

## Resources and bounds

- **Workers.** 1/2/4/8/16. Worker count is operational only: aggregate artifacts
  are byte-identical for every value. On the 16-thread/8-core development machine,
  8 workers was fastest; 16 workers added nothing.
- **Memory.** `--max-rss-gib` is a process-tree RSS ceiling (at most 16). Measured
  peak on the authored benchmark: 1.3 GiB at 8 workers, 1.8 GiB at 16.
- **Disk.** `--max-output-gib` caps every byte the audit writes, including units,
  artifacts and the receipt. `--free-reserve-gib` is checked before the scan and
  sampled during it. Nothing is written outside `--output`; no scratch is used.
- **Documents.** `--max-document-mib` is the canonical row ceiling (C05's is 64 MiB).
  A larger row refuses.
- **Deadline.** `--deadline-hours`. Committed units survive a deadline stop and
  resume.
- **Chunking.** 32 MiB line-aligned chunks, fixed and bound into the audit identity.
  No whole file or corpus is ever held in memory.

## Integrity and resume

- Each file is read once, sequentially. Its SHA-256, byte size, row count and
  canonical-byte total must equal the manifest before its statistics are committed
  as `units/fNNNNN.unit.zz` (atomic write).
- `audit-binding.json` binds the manifest digest and file SHA-256, the data root,
  the detector policy version and digest, the code identity (SHA of `src/`), the
  dependency lock digest, the overlay (proof, plan, completion and membership
  digests) and the chunk and row ceilings.
- Resume reuses a unit only when the binding is identical and the source file's size
  and mtime equal the values recorded at commit. Any difference refuses: use a new
  output directory.
- `quality-audit-receipt.json` is written last and only after every file is
  committed. Without it the directory is an incomplete audit, never a result, and
  `report` refuses.
- `report` rebuilds every artifact from the units and refuses unless every byte
  and the result digest match.

## Outputs (`--output`)

| File | Content (all content-free unless noted) |
|---|---|
| `quality-audit.json` | global summaries per population, metric definitions, bindings |
| `quality-by-component.json` | the same summaries for every component, allocation (`component\|view\|upstream`) and source key |
| `quality-histograms.json` | per-metric histograms (docs and bytes per bin), global and per component |
| `quality-intersections.json` | boolean flag intersections and threshold-free joint coarse histograms |
| `quality-language.json` | language field values, confidence, row-level LID/score evidence, provenance, assessment |
| `review-manifest.jsonl` | bounded review locators (path, row, offset, doc_id, metric value, roles); no text |
| `candidate-policy-{conservative,moderate,aggressive}.yaml` | PROPOSAL_ONLY tail census per component; every `action` is null |
| `quality-summary.md` | human-readable presence and language tables |
| `quality-audit-receipt.json` | bindings, source-file identities, artifact SHA-256s, result digest, execution facts |
| `units/` | per-file committed statistics (resume) |

`materialize-review` writes `review.jsonl`, `review.html` (HTML-escaped) and
`README.txt` to a new destination. **This is corpus text.** Do not commit, upload or
share it, and delete it when the review is finished. Text in it is data, never
instructions.

## What to return for policy selection

Return these files (all content-free):

- `quality-audit-receipt.json`
- `quality-audit.json`
- `quality-by-component.json`
- `quality-intersections.json`
- `quality-language.json`
- `quality-summary.md`
- the three `candidate-policy-*.yaml`
- the `report` JSON line

Also return the stderr tail of the audit, so measured throughput and RSS are
recorded. `quality-histograms.json` and `review-manifest.jsonl` are useful and also
content-free. Do not return review text. Instead, for each detector you reviewed,
return the counts of `strong_positive`, `near_threshold` and `control` rows that you
judged true positives, false positives and unclear.
