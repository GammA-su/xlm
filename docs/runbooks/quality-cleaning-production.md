# Phase C: production quality cleaning (operator runbook)

`clean-production` writes a NEW cleaned corpus. The policy is `cleaning_policy_v2`, and
it is DROP-only:

- a KEEP row is copied as its ORIGINAL input JSONL line bytes, unchanged, in input
  order;
- a DROP row is omitted.

Nothing is lowercased, normalized, reserialized, reindented, truncated or repaired, and
no metadata field changes. The input corpus is opened read-only and never modified.

The run binds itself to the approved Phase-B v2 dry run. It does not trust the numbers
given to it; it re-derives them and refuses on any difference:

- **Before any byte is written:**
  - the frozen policy validates (status, self-digest, pinned v2 rule set, v2 version,
    frozen from an audit of this manifest under the current detector semantics);
  - the approved dry-run receipt validates strictly. It must be COMPLETE and
    `POLICY_WITHIN_GUARDRAILS`, and its result digest must equal
    `--approved-result-digest`;
  - the dry run's policy, manifest (digest, file SHA-256, kind, mode, totals), data
    root and every source identity must equal the inputs given now;
  - every dry-run artifact hash must match its receipt, and every dry-run artifact,
    re-derived from its per-file units, must be byte-identical to the published one;
  - the path mapping and every root overlap are checked (see Safety).
- **For every input file, before its output is published:** the cleaner re-evaluates
  every row with the SAME worker kernel as the dry run, in ONE pass that both decides
  and writes. The file's complete decision statistics must equal the dry-run unit for
  that file: KEEP/DROP documents and canonical bytes, rule marginals and exclusives,
  the severe-signal histogram, class and OCR tables, exact rule combinations and strata.
- **Before the receipt:** the global, per-component and per-rule accounting
  (`global`, `components`, `by_component`, `rules`, `guardrails`) must equal the
  approved dry run's published artifacts exactly.

A mismatch is fatal: no completion receipt is written.

## Layout

| Location | Contents |
|---|---|
| `--output-root` (e.g. `G:/XLM-clean-v1`) | ONLY the cleaned canonical JSONL files. Each input `<data_root>/<path>` maps to `<output-root>/<path>`, the same validated relative path. While a file is being written: `<path>.xlmclean-tmp`. No log, unit or receipt is ever here. |
| `--state-output` (e.g. `G:/XLM/quality/clean-production-v1`) | `cleaning-production-binding.json`, `units/fNNNNN.unit.zz`, `cleaning-production-summary.md`, `cleaning-production.json`, `cleaning-by-component.json`, `cleaning-by-rule.json`, `dropped-membership.jsonl`, `cleaned-inventory.json`, `cleaning-production-receipt.json`; then `cleaning-production-verification.json` and `cleaned-input-manifest.json`. All content-free. |
| `--progress-log` (e.g. `C:/XLM-logs/...`) | Operational progress lines only. |

Mapping: the output relative path is the input manifest's relative POSIX path, built
from validated components (no drive-letter or prefix string replacement). A component
refuses when it is:

- empty, `.` or `..`;
- holding a backslash, a colon, a Windows-forbidden or control character;
- ending in a dot or space;
- a reserved device name;
- ending in the temporary suffix.

Two inputs whose outputs are equal after NFC and case folding refuse, as does a file
path that is also another output's directory. So: one input -> one output, no two
inputs share an output, nothing escapes the root, and the mapping is the same on
Windows and Linux. The mapping is recorded in the binding (`mapping.digest`) and in the
inventory.

Empty-output policy: one output per input. An input whose rows are all DROP (or an
empty input) yields a zero-byte output file. It is listed in the inventory and the
cleaned manifest with `documents: 0`.

## Safety (refusals)

The command refuses when:

- the output root equals, contains or lies inside the input data root, a corpus file
  directory or any input (manifest, policy file, approved dry run);
- the state output overlaps the output root, a corpus file directory or an input, or
  contains the data root. The state may live below the data root, as the Phase-A/B
  outputs do;
- any path component of the output root or the state tree is a symbolic link,
  junction or other reparse point, or a created directory resolves elsewhere;
- a fresh run finds a non-empty output root, or a resume finds an entry that is not an
  expected output, its temporary or a directory leading to one. Nothing is deleted;
- the frozen policy, the approved dry run or a source file is stale or changed;
- RSS, deadline, output ceiling or free-space reserve would be exceeded. At start the
  free space minus the remaining output upper bound (the input file bytes) must stay
  above `--free-reserve-gib`.

No existing file is overwritten. An output is published only by an atomic rename onto
an absent name.

## Atomicity and resume

Per input file, in this order:

1. KEEP bytes go to `<path>.xlmclean-tmp`, which is flushed, fsynced and closed.
2. A verifier thread re-hashes the source against the manifest and re-reads the
   temporary output against the bytes written.
3. The file's decision statistics are compared with the dry-run unit.
4. The temporary output is atomically renamed to `<path>`.
5. The unit `units/fNNNNN.unit.zz` is committed (atomic write). It records the input
   identity, the policy digest, the code identity, docs and canonical bytes
   (input/kept/dropped), output bytes and SHA-256, DROP rule accounting and the file's
   dropped-membership rows.

To resume, **rerun the identical command**. The binding (manifest, policy, approved dry
run, code identity, output root, mapping) must be identical; otherwise the run refuses
and needs a new `--state-output` and a new `--output-root`. Then:

- owned temporaries are deleted and never count;
- every committed unit is reloaded and checked against the dry run, its source is
  re-hashed and its output re-hashed. A changed or missing completed output refuses;
  nothing is overwritten;
- a final file with no unit (interrupted between steps 4 and 5) is re-derived and
  adopted only if the re-derived bytes are identical. Otherwise the run refuses.

## Verification

`clean-production-verify` never writes the corpus. It:

1. validates the receipt and repeats every pre-cleaning check;
2. requires exactly the expected outputs in the corpus root: no temporary, no foreign
   entry, no link;
3. re-derives every artifact from the units, byte for byte;
4. re-reads every output in worker processes. It checks the SHA-256 and size, that
   every row is strict canonical JSONL with a correct `utf8_byte_count`, the row and
   canonical byte counts, and that none of that file's dropped rows is present
   (row SHA-256);
5. checks the totals against the inventory, the receipt and the approved dry run.

- `--compare-sources` also re-hashes every source and walks it in lockstep: each KEEP
  source row must be the next output row byte for byte, each DROP row must carry its
  recorded SHA-256, and no row may be missing or extra.
- `--reevaluate` also re-decides every output row (all must be KEEP).

Success writes `cleaning-production-verification.json`. A failure withdraws an
existing verification record and cleaned manifest.

`clean-production-manifest` accepts only a verified cleaning. It writes
`cleaned-input-manifest.json` with:

- kind `xlm_cleaned_input_manifest` (`authored_cleaned_input` for authored inputs);
- data root = the output root;
- per file: the output SHA-256, bytes, documents and canonical bytes, plus a
  `cleaned_from` record of the input file;
- lineage digests (original manifest, policy, approved dry run, production receipt,
  verification, inventory, membership);
- status `CANDIDATE_REQUIRES_INDEPENDENT_AUDIT_AND_C05`.

It is a new manifest with a new semantic digest, and it loads with the standard input
manifest loader. Nothing is admitted downstream by this command.

## Commands (Windows, operator)

Run them from the checkout of the commit that implements this runbook. Its code
identity is bound into the receipt.

```powershell
$q  = 'uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.quality'
$o2 = 'G:/XLM/quality/clean-dry-run-v2'                                   # the APPROVED v2 dry run
$m  = (Get-Content -Raw "$o2/cleaning-dry-run-receipt.json" | ConvertFrom-Json).input_manifest.path
$f2 = 'recipes/quality/cleaning_policy_v2.frozen.yaml'                    # the frozen v2 the dry run used
$d  = 'ca1135c3dde9a2be0b729bda5380f64e91d13c90f3ff1d8e04250ba66e5513c7'  # approved result_digest
$c  = 'G:/XLM-clean-v1'                                                   # NEW cleaned corpus root (absent or empty)
$s  = 'G:/XLM/quality/clean-production-v1'                                # NEW state output (absent or empty)
$l  = 'C:/XLM-logs/quality-clean-production-v1.progress.log'
$lv = 'C:/XLM-logs/quality-clean-production-v1-verify.progress.log'

# 1. Production cleaning (resumable: rerun the identical command after an interruption).
Invoke-Expression "$q clean-production --manifest $m --policy $f2 --approved-dry-run $o2 --approved-result-digest $d --output-root $c --state-output $s --workers 16 --max-rss-gib 12 --free-reserve-gib 16 --max-output-gib 160 --deadline-hours 12 --progress-interval-seconds 5 --progress-log $l"

# 2. Independent verification (never modifies the corpus).
Invoke-Expression "$q clean-production-verify --manifest $m --policy $f2 --approved-dry-run $o2 --approved-result-digest $d --output-root $c --state-output $s --workers 16 --compare-sources --max-rss-gib 8 --deadline-hours 12 --progress-interval-seconds 5 --progress-log $lv"

# 3. Cleaned-corpus input manifest candidate (only after step 2 succeeded).
Invoke-Expression "$q clean-production-manifest --state-output $s --output-root $c"
```

Progress (stderr and `--progress-log`, every 5 s) shows:

- phase;
- files, docs and input GB (done / total);
- throughput (now and EWMA) and ETA;
- active workers, tasks in flight and pending verifications;
- CPU cores (parent + workers) and process-tree RSS;
- output GiB, DROP docs so far, and free disk on the output volume.

Expected accounting. These numbers are what the approved dry run recorded; the
cleaner derives and checks them itself:

- DROP 9,967 docs / 493,825,100 canonical bytes;
- KEEP 15,087,207 docs / 81,365,827,139 canonical bytes.

Every existing C05 dedup, contamination, lineage, split and membership artifact is
stale for the cleaned corpus. Next stage (operator):

1. independently audit `cleaned-input-manifest.json`;
2. rerun C05 from scratch on the cleaned corpus.

Do not reuse the old manifest digest.

## Resources

- **Runtime:** the clean phase runs the same detector pass as the dry run. On authored
  data at 16 workers it measured 64.6 MB/s end-to-end, against 65.8 MB/s for the dry
  run on the same data (evidence `bench.log`). The real v2 dry run scanned at
  ~61.7 MB/s, so the clean phase should take about 30 min for ~100 GB of input file
  bytes. Add 1-3 min of approved-dry-run re-verification and aggregation. If `G:` is
  IO-bound it could take up to about 1 h: the run reads the source twice (hash-while-
  read plus a bracketing re-hash), writes the output once and re-reads it once.
  `--compare-sources` verification reads the output and the sources once more (IO-bound;
  expect 10-30 min).
- **Disk:** the cleaned corpus is about the input file bytes minus the dropped rows'
  line bytes (about 0.5 GB). Temporary files are at most a few input files at a time.
  The state is well under 1 GB. The run refuses unless the free space on the output
  volume, minus the remaining input file bytes, stays above `--free-reserve-gib`.
  `--max-output-gib` must be at least the input file bytes; 160 covers ~100 GB.
