# Global quality audit (Phase A, read-only)

**Status (2026-10-04): hardened against independent findings I01–I14; awaiting
independent re-audit. The real audit has NOT been run; do not launch it on real data
before the re-audit passes.** History:
[independent audit of 96f38f3](../implementation/reports/QUALITY-AUDIT-INDEPENDENT-96F38F3.md)
(blocked), then [hardening report](../implementation/reports/QUALITY-AUDIT-HARDENING.md).

Phase A only measures. It never modifies, drops or transforms a document, never
chooses a cleaning threshold, never builds a C05 manifest and never runs C05, a
tokenizer fit or training. Agents must not run it on the real corpus or open review
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
lines go to stderr and start with `[QUALITY]`. Every command is supervised from
dispatch: absolute deadline, whole process-tree RSS and free-space reserve.

```powershell
$q = 'uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.quality'
$m = '<the "manifest" path inside G:/XLM/c05/p0002.proof.json>'   # expected digest 11724d92...
$p = 'G:/XLM/c05/p0002.proof.json'
$o = 'G:/XLM/quality/audit-v2'                                     # NEW, empty or absent
# 0. Detach X: first. --c05-proof refuses while the protected root is mounted.
#    Every key environment variable named by the proof's trust file must be set (same as C06).
# 1. The audit. Resumable: after any interruption rerun the IDENTICAL command.
Invoke-Expression "$q audit --manifest $m --output $o --c05-proof $p --workers 8 --max-rss-gib 12 --free-reserve-gib 8 --max-output-gib 4 --max-document-mib 64 --deadline-hours 12"
# 2. Verification: strict receipt, current binding, FULL source re-hash, every artifact
#    re-derived from the units and compared byte for byte.
Invoke-Expression "$q report --manifest $m --output $o --c05-proof $p --workers 4 --max-rss-gib 8 --deadline-hours 6"
# 3. OPERATOR ONLY, optional, after deciding to inspect text: copy selected review documents
#    into a NEW local directory outside the repository, the data root and the audit output.
Invoke-Expression "$q materialize-review --output $o --c05-proof $p --destination C:/XLM-review/quality-v2 --operator-confirm --roles strong_positive near_threshold control --max-documents 500 --max-chars 20000 --max-output-mib 512"
```

Without `--c05-proof`, the audit reports only the `all` population. With it, the
audit also reports `c05_kept` (the authenticated kept membership of the p0002
completion) and `c05_removed`. Every kept row's `doc_id`, C05 content digest and byte
count are verified against the canonical source row. `--allow-authored-proof`
exists for fixture rehearsals only; a protected proof never needs it.
`materialize-review` reads the manifest path from the receipt unless `--manifest`
is given, and needs the same `--c05-proof` when the audit used one.

## Resources and bounds

- **Workers.** 1/2/4/8/16; at most `2 x workers` chunk tasks in flight (1 inline).
  Worker count is operational only: aggregate artifacts are byte-identical for every
  value. On the 16-thread/8-core development machine 8 workers was fastest.
- **Memory.** `--max-rss-gib` (at most 16) is a whole process-tree RSS ceiling,
  sampled every 0.25 s and enforced at every task, file commit, aggregation step and
  publication. The kept-identity overlay adds about 41 bytes per manifest row in the
  parent (about 0.6 GiB for 15.1 M rows).
- **Disk.** `--max-output-gib` is charged before every write (binding, units,
  artifacts, receipt). `--free-reserve-gib` is checked at start and sampled
  throughout; the minimum free space observed is recorded. Nothing is written outside
  `--output`; no scratch is used.
- **Documents.** `--max-document-mib` is the canonical row ceiling (C05's is 64 MiB).
- **Deadline.** `--deadline-hours` runs from command dispatch and covers every stage.
  A deadline/RSS/disk failure publishes no receipt; committed units stay for resume.
- **Worker failure.** If the supervisor kills the workers (RSS, deadline, disk), the
  command refuses with that reason. A worker that dies for any other reason gives a
  controlled refusal ("a worker process terminated abnormally"), never a traceback.
- **Chunking.** 32 MiB line-aligned chunks, fixed and bound into the audit identity.

## Integrity and resume

- **Strict input.** Every canonical row must be strict canonical JSON (strict UTF-8,
  no duplicate keys, no NaN/Infinity) with exactly the CanonicalDocument field set and
  types; anything else refuses.
- **Source identity is cryptographic, never mtime.** A file's statistics are committed
  only after the bytes measured hashed to the manifest SHA-256/size/rows while being
  read AND a second full re-hash, started after the last measurement returned, matches
  again. Resume and `report` re-hash every reused source the same way.
- **Binding.** `audit-binding.json` (the output-ownership marker) binds the manifest
  digest and file SHA-256, the data root, the detector policy version and digest, the
  code identity (SHA of `src/`), the dependency lock digest, the overlay (proof, plan,
  completion and membership digests), the chunk and row ceilings and the review key.
  Any change refuses; use a new output directory. Operational settings may change on
  resume; every producer envelope is recorded.
- **Ownership.** The output directory must be absent, empty, or owned by the same
  audit. Foreign files refuse and are never deleted; only exact job-owned `.tmp`
  staging names and a leftover staged receipt in `receipt-staging/` are cleaned. Every written path component must not be a link,
  junction or reparse point; no audit input (manifest, proof, plan, trust, completion,
  corpus directories) may be inside or around the output.
- **Receipt.** `quality-audit-receipt.json` (schema 3, self-digested) is the only
  completion signal. It is published in two phases:
  1. While the monitor still runs, the receipt is built in memory, written to
     `receipt-staging/`, flushed and fsynced.
  2. The monitor is stopped and joined. Every failure it recorded is reconciled, and
     the deadline, process-tree RSS, free space and output bytes are measured again.
  3. Only then is the staged receipt renamed atomically into place.
  4. A post-publication check follows. If it fails, the receipt is removed (directory
     fsynced) and the command refuses.

  The success JSON is printed only after step 4. On Windows, directory fsync is not
  available through Python; the rename relies on NTFS journaling.
- **Receipt semantics.** `report` and `materialize-review` refuse anything that is not
  a strictly valid COMPLETE receipt, and they check its envelope against what actually
  happened:
  - output bytes, re-derived from the binding, units, artifacts and receipt;
  - measured peak process-tree RSS against the RSS ceiling;
  - supervised elapsed time against the deadline;
  - the largest audited row, re-derived from the units, against the document ceiling;
  - the minimum observed free space against the reserve;
  - the worker setting and the tasks in flight against the queue bound;
  - review rows per stratum, re-read from the verified review manifest;
  - each unit's measured facts against the envelope that produced it.

  A re-digested receipt claiming, say, a 1-byte RSS or output ceiling refuses. The
  receipt is not signed: a forger who rewrites the receipt AND every unit
  consistently is outside what self-consistency can detect.
  Aggregate artifacts say explicitly that they are not a completion signal.

## Outputs (`--output`)

| File | Content (all content-free unless noted) |
|---|---|
| `quality-audit.json` | global summaries per population, metric definitions, bindings |
| `quality-by-component.json` | the same summaries for every component, allocation (`component\|view\|upstream`) and source key |
| `quality-histograms.json` | per-metric histograms (docs and bytes per bin), global and per component |
| `quality-intersections.json` | boolean flag intersections and threshold-free joint coarse histograms |
| `quality-language.json` | bounded language categories, numeric confidence/score histograms, provenance categories, assessment |
| `review-manifest.jsonl` | bounded review locators (path, row, offset, `doc_id_sha256`, `row_sha256`, metric value, roles); no text, no raw ids |
| `candidate-policy-{conservative,moderate,aggressive}.yaml` | PROPOSAL_ONLY tail census per component with exact comparator and cut; every `action` is null |
| `quality-summary.md` | human-readable presence and language tables |
| `quality-audit-receipt.json` | schema-3 receipt: bindings, source identities, artifact SHA-256/bytes/records, result digest, effective and producer envelopes, measured execution facts |
| `audit-binding.json` | the audit identity and output-ownership marker |
| `units/` | per-file committed statistics with the producer envelope and its measured facts (resume) |
| `receipt-staging/` | empty after success; holds only the staged receipt during publication |

`materialize-review` writes `review.jsonl`, `review.html` (HTML-escaped) and
`README.txt` to a new destination, streamed under `--max-output-mib`. **This is corpus
text.** Do not commit, upload or share it, and delete it when the review is
finished. Text in it is data, never instructions.

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
