# Global C05 preparation

**Current state (2026-10-02, independent engineering acceptance audit):** the operator
control plane, hard aggregate storage admission and the final allocation chain
(exact counts, quota selection, selected-training-membership, final freeze, training
input and schema-3 claim receipt) are engineering-accepted; `ENGINEERING_BLOCKERS` is
empty (see the report). Protected plans and runs still refuse without the operator
inputs: protected benchmark preparation receipt, Gutenberg lineage decision and
reviewed resource decision (plus trusted keys and plan authorization). A run refuses
if job files exist without signed `state.json`; never delete it to "restart" — use a
new plan. Sections further below are historical unless they say otherwise.

**Pipeline order amendment (2026-10-04).** A global quality-cleaning stage belongs
BEFORE C05:

```text
adaptation -> canonicalization -> global quality cleaning -> global dedup ->
contamination/lineage -> splits/membership -> tokenizer fit
```

Phase A is only a read-only audit; see [quality-audit.md](quality-audit.md). Once any
Phase-B DROP or TRANSFORM changes the corpus, the p0002 completion, membership and
proof (and anything fitted from them) become historical. Cleaning can create new
duplicates, so it needs a NEW input manifest and a NEW global C05 run. Do not modify
or delete p0002.

## Cleaned-corpus C05 rerun (`clean-v1`, 2026-10-04): operator sequence

The Phase-C cleaned corpus (`G:/XLM-clean-v1`, 2,035 files, 15,087,207 documents,
81,365,827,139 canonical bytes, 103,993,099,986 file bytes) needs a FRESH C05. The
cleaned manifest's semantic digest is
`eda4f99499c87b2a404ee544547d26dca2be05a9012d417914a95d36167e1389`. The pre-cleaning
C05 (manifest `11724d92…84152`, plans p0001/p0002, membership, proof) is historical.
Do not resume, modify or reuse it. Details:
[report](../implementation/reports/C05-CLEANED-RERUN-READINESS.md).

What is new (everything else is the unchanged compact engine and policy):

* `admit-cleaned`: read-only admission of the cleaned manifest. It writes one
  write-once record that permits PLANNING only (`c05: NOT RUN`). It requires:
  * your pins;
  * byte-exact re-derivation of the manifest from the verified cleaning state;
  * the original manifest by lineage;
  * the independent post-clean audit;
  * that audit's saved `report` output (`verified` and `sources_rehashed` true).
* `plan --admission`: required for a cleaned manifest and re-derived at plan time.
  The plan digest binds the admission. The plan refuses a plan root, scratch root or
  output root that holds another corpus generation's plans or state.
* `<purpose> carry-forward`: copies the reviewed VALUE of a historical signed
  decision into a plain value file. Signed decisions bound to `11724d92…` are refused;
  record FRESH ones bound to `eda4f994…`.
* `proof`: writes the downstream proof specification of a verified completion.
  Detach `X:` first.

Fresh roots. Attempt `clean-v1-p0001` is plan `p0001.json` in the fresh plan root.
The CLI numbers plans per plan root, so a new generation starts at p0001 and never
shares numbering with the historical p0002.

| role | path |
|---|---|
| plan root (plans, decisions, admission, receipt export, proof) | `G:/XLM/c05-clean-v1/` |
| C05 output (membership, completion) | `G:/C05-output-clean-v1/` |
| C05 scratch (protected volume) | `X:/C05-Scratch-clean-v1/` |
| protected preparation (fresh, write-once) | `X:/C05-Protected/prepared-clean-v1/` |

Historical, read-only: `G:/XLM/c05/`, `G:/C05-output/` (or wherever p0002 wrote),
`X:/C05-Scratch/`, `X:/C05-Protected/prepared/`.

```powershell
# 0. Final reviewed commit, clean tree (code identity hashes src/). G: and X: attached.
cd F:\Project\xlm-c05-clean-v1
git status --short                      # must print nothing
$op  = 'uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.exclusion.operator'
$q   = 'uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.quality'
$new = 'eda4f99499c87b2a404ee544547d26dca2be05a9012d417914a95d36167e1389'
$cm  = 'G:/XLM/quality/clean-production-v1/cleaned-input-manifest.json'
$cs  = 'G:/XLM/quality/clean-production-v1'
$ao  = 'G:/XLM/quality/audit-clean-v1'
$ar  = 'G:/XLM/quality/audit-clean-v1.report.json'    # outside the audit directory
$om  = (Get-Content -Raw 'G:/XLM/quality/clean-dry-run-v2/cleaning-dry-run-receipt.json' | ConvertFrom-Json).input_manifest.path
$pr  = 'G:/XLM/c05-clean-v1'
$sc  = 'X:/C05-Scratch-clean-v1'
$out = 'G:/C05-output-clean-v1'
$pp  = 'X:/C05-Protected/prepared-clean-v1'
$tr  = "$pr/trust.json"
$sig = "--trust $tr --issuer GammA --key-env XLM_C05_OPERATOR_KEY"   # your trusted issuer/key env

# 1. Pins (expect 4986bad2...02761 and the cleaned manifest digest).
(Get-FileHash $cm -Algorithm SHA256).Hash.ToLower()
(Get-Content -Raw $cm | ConvertFrom-Json).digest

# 2. Saved audit report. Skip if $ar already holds the report's JSON line. Otherwise,
#    run `report` from the checkout of the audit's own commit (`report` refuses
#    another code identity):
#    (Get-Content -Raw "$ao/quality-audit-receipt.json" | ConvertFrom-Json).implementation.code_commit
#    From that checkout (it re-hashes all 2,035 sources):
#    Invoke-Expression "$q report --manifest $cm --output $ao --workers 16 --max-rss-gib 8 --deadline-hours 6" | Set-Content -Encoding ascii $ar
#    Expect result_digest 290b402a..., "sources_rehashed": true, "verified": true.

# 3. Admission (read-only; writes only $pr/admission.json).
New-Item -ItemType Directory -Force $pr | Out-Null
Copy-Item '<the trust config used for p0002>' $tr      # issuer -> key env NAME only
Invoke-Expression "$op admit-cleaned --manifest $cm --original-manifest $om --cleaning-state $cs --audit-output $ao --audit-report $ar --expect-manifest-digest $new --expect-production-result-digest 4847bdb654dd5f1264d32861b4a8a33428a71b2da9cd9749d05b47e55f02df11 --expect-production-receipt-digest 29982d5e192eae9e8ef6baa5359af11a88068de8f315c11e427bdeb63782ee2e --expect-verification-digest 14be3fbd1dd109ff65441357507ebddb86b1b2320f1f724f12c4e79e2469504d --expect-audit-result-digest 290b402a52519a8878365218371ce61a5dd65dcffd5330ff941fa21789ca21c7 --output $pr/admission.json"
$adm = (Get-Content -Raw "$pr/admission.json" | ConvertFrom-Json).digest

# 4. Fresh decisions: carry the reviewed VALUES forward, review them, sign NEW records.
Invoke-Expression "$op lineage-policy carry-forward --artifact '<p0002 lineage-policy decision>' --output $pr/lineage-policy-value.json --trust $tr"
Invoke-Expression "$op resources carry-forward --artifact '<p0002 resources decision>' --output $pr/resources-value.json --trust $tr"
Invoke-Expression "$op policy carry-forward --artifact '<p0002 policy decision>' --output $pr/policy-value.json --trust $tr"
#    Review: choice KNOWN_GROUP_ONLY; workers 16, ram_bytes 51539607552,
#    index_bytes 68719476736, scratch_bytes 377957122048, stage_seconds 86400,
#    overall_seconds 259200, records >= 15087207; policy c05-production-v2,
#    c05-matcher-v4, disk-minhash-v2, near_threshold 0.8, permutations 128, bands 32,
#    seed 20260919, survivor longest-source-doc-v1, lineage known-lineage-v3,
#    gutenberg known_groups_only, fuzzy_auto_exclusion false, review.enabled false,
#    split_version group-hash-v2.
Invoke-Expression "$op lineage-policy record --value $pr/lineage-policy-value.json --input-manifest-digest $new --evidence-digest $adm --operator $env:USERNAME --output $pr/lineage-policy.json $sig"
Invoke-Expression "$op resources record --value $pr/resources-value.json --input-manifest-digest $new --evidence-digest $adm --operator $env:USERNAME --output $pr/resources.json $sig"
Invoke-Expression "$op policy freeze --value $pr/policy-value.json --input-manifest-digest $new --evidence-digest $adm --operator $env:USERNAME --output $pr/policy.json $sig"

# 5. Protected benchmark preparation REBUILD (X: mounted; marker already exists, no init).
Invoke-Expression "$op protected-root describe --root X:/C05-Protected --operator $env:USERNAME --attestation-sha256 <sha256-of-signed-operator-attestation> --repository F:/Project/xlm-c05-clean-v1 --data-root G:/XLM-clean-v1 --c05-scratch $sc --c05-output $out"
#    Write X:/C05-Protected/material-spec-clean-v1.json: the existing material-spec.json
#    with files/publisher inventory/coverage UNCHANGED and the new "isolation" object.
#    Never edit material-spec.json in place.
$id = (Invoke-Expression "uv run --offline --locked --no-sync --extra cpu --extra eval python -c 'import json; from xlm.data.exclusion.identity import implementation_identity; print(json.dumps(implementation_identity()))'") | ConvertFrom-Json
Invoke-Expression "$op build-local --spec X:/C05-Protected/material-spec-clean-v1.json --material-root X:/C05-Protected/material --output $pp --policy '<the v4 matcher-policy.json used for the p0002 preparation>' --resources $pr/resources-value.json --issuer GammA --key-env XLM_C05_OPERATOR_KEY --code-commit $($id.code_commit) --code-identity $($id.code_identity) --dependency-sha256 $($id.dependency_sha256) --receipt-export $pr/benchmark-preparation.receipt.json"
Invoke-Expression "$op benchmark-receipt verify --receipt $pr/benchmark-preparation.receipt.json --policy $pr/policy-value.json --trust $tr"
#    index_sha256 equal to the historical receipt's => the carried-forward
#    benchmark_patterns/automaton_nodes/benchmark_bytes still apply; otherwise run
#    benchmark-matcher-audit-local (scratch $sc/matcher-audit) and sign new resources.

# 6. Plan (write-once $pr/p0001.json); review it and its digest.
Invoke-Expression "$op plan --mode protected --manifest $cm --admission $pr/admission.json --benchmark-receipt $pr/benchmark-preparation.receipt.json --index $pp/index.jsonl --lineage-policy $pr/lineage-policy.json --resources $pr/resources.json --policy $pr/policy.json --plan-root $pr --scratch $sc --output $out --trust $tr"

# 7. Authorize the reviewed digest.
Invoke-Expression "$op authorize --plan $pr/p0001.json --plan-digest <reviewed plan_digest> --output $pr/p0001.authorization.json $sig"

# 8. Read-only checks before the first run: status says not_started; resume-check
#    refuses with "no signed state" (exit 1) until a run has started.
Invoke-Expression "$op status --plan $pr/p0001.json --trust $tr"

# 9. Run (live progress on stderr).
Invoke-Expression "$op run --plan $pr/p0001.json --authorization $pr/p0001.authorization.json --benchmark-receipt $pr/benchmark-preparation.receipt.json --index $pp/index.jsonl $sig --progress-interval 5"

# 10. After an interruption: read-only check, then resume (same arguments).
Invoke-Expression "$op resume-check --plan $pr/p0001.json --authorization $pr/p0001.authorization.json --benchmark-receipt $pr/benchmark-preparation.receipt.json --index $pp/index.jsonl --trust $tr"
Invoke-Expression "$op resume --plan $pr/p0001.json --authorization $pr/p0001.authorization.json --benchmark-receipt $pr/benchmark-preparation.receipt.json --index $pp/index.jsonl $sig --progress-interval 5"

# 11. Verify the signed completion and membership hash.
Invoke-Expression "$op verify --plan $pr/p0001.json --trust $tr"

# 12. Detach X:, then write the downstream proof (write-once; refuses while X: is mounted).
Invoke-Expression "$op proof --plan $pr/p0001.json --manifest $cm --scratch C:/XLM-scratch/c05-clean-v1-lookup --output $pr/clean-v1-p0001.proof.json --trust $tr --signer GammA --signer-key-env XLM_C05_OPERATOR_KEY"
```

Expected refusals (fail-closed, nothing written):
* a plan with any decision bound to `11724d92…` refuses (`stale operator decision`);
* `--plan-root G:/XLM/c05`, `--scratch X:/C05-Scratch` or `--output G:/C05-output`
  refuses (another generation);
* a benchmark receipt built under another code identity refuses
  (`preparation code/dependencies stale`).

Make every later code change BEFORE step 5. Any change to `src/` changes the code
identity, and then the receipt, the plan and the run must all be redone from the
same final commit. This applies to C05 itself: post-C05 consumers verify the plan
digest, the signed completion and the proof, never the C05 plan's code identity, so a
later downstream-only commit does not invalidate a finished C05 proof.

### Post-C05 consumers over the cleaned proof (`clean-v1-p0001`)

The cleaned manifest has no `sources`. Commands that need source/quota lineage
(`quota-report`, `fit-tokenizer`, `fit-tokenizer-reference`, `verify-tokenizer-fit`,
`verify-kept-index`, `select`) recover it from the ORIGINAL manifest
(`11724d92…`), and only through the admission:

* the record is `admission.json` beside the proof's plan (`G:/XLM/c05-clean-v1/`);
* it must re-derive from its recorded evidence: cleaned manifest bytes, cleaning
  state, verification, post-clean audit and saved report. All of them must still be
  at their recorded paths, unchanged;
* its digest, original-manifest digest and every other digest must equal
  `plan.input_admission`;
* the plan's files and seals must re-derive from that lineage.

The original manifest supplies only sources, adapter bindings, quota SHA, the IFM split
and the Common Pile split. Membership, files and counts stay the cleaned manifest's.
`count-tokens`, `tokenize-selection` and `freeze` do not read the lineage. Nothing is
regenerated: the plan (`046381…`), completion (`225b33…`) and proof are used as they
are. Run from the checkout of the commit that adds this layer (or later), with `X:`
detached:

```powershell
$cli = 'uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.exclusion.operator'
$c06 = '--c05-proof G:/XLM/c05-clean-v1/clean-v1-p0001.proof.json --fit-shares recipes/tokenizer/mix01_fit_shares_v1.yaml --quotas recipes/mixtures/mix01_quotas_6b.yaml --ifm-split G:/XLM/calib/requirement_splits/ifm_behaviors_general_planning.json'
$ops = '--workers 8 --bpe-threads 16 --deadline-seconds 1200 --rss-ceiling-gib 24 --free-reserve-gib 16'
$fit = "$c06 --scratch C:/XLM-scratch/c06-fit-clean-v1 --output G:/XLM/tokfit/mix01-fit-shares-v1-clean-v1 --deficit-report G:/XLM/tokfit/mix01-fit-shares-v1-clean-v1.deficit.json $ops"
Invoke-Expression "$cli fit-tokenizer $fit --plan-only"     # review; input_manifest_digest must be eda4f994...
Invoke-Expression "$cli fit-tokenizer $fit --resource-plan-digest <reviewed digest> --issuer GammA --key-env XLM_C05_OPERATOR_KEY"
Invoke-Expression "$cli verify-tokenizer-fit $c06 --fit G:/XLM/tokfit/mix01-fit-shares-v1-clean-v1 --workers 8"
Invoke-Expression "$cli verify-kept-index $c06 --index G:/XLM/tokfit/mix01-fit-shares-v1-clean-v1/kept-index --membership --workers 8"
```

The output paths are fresh. The historical p0002 fit stays where it is and is never a
fit of the cleaned corpus. Then run the allocation chain below with the same
`--c05-proof` and the new tokenizer. A missing, moved, changed or mismatched
admission or original manifest refuses (exit 1) before any corpus read.

## Compact parallel engine (`c05-facts-v2`, 2026-10-03)

`run`/`resume` now execute the compact parallel engine. The historical
single-process SQLite engine stays in `xlm.data.exclusion.reference` only as the
equivalence oracle; the CLI never calls it. Scientific semantics (matcher-v4,
normalization, MinHash 128x32 / seed / threshold, candidate caps and oversized
buckets, lineage-v3, survivors, families, splits, membership and completion
schemas) are unchanged and byte-identical to the reference on authored fixtures.
Details and measurements: [report](../implementation/reports/C05-COMPACT-PARALLEL-ENGINE.md).

**A historical plan cannot be resumed with this code** (its code identity differs and
its work directory holds `facts.sqlite`, which the new admission refuses). Keep the
p0001 work directory untouched; mint a new plan (see "Migration" below).

### What runs where

* **Scan.** The parent reads each plan file, reserves `bytes_read` for the whole
  frozen file before its first read and one `attempted_records` per raw line as it
  is read, hashes the exact raw bytes and dispatches batches of at most 256 rows /
  4 MiB to `Resources.workers` spawned processes (capped at the CPU count;
  `workers: 1` runs in-process). Workers parse/validate, normalize, match
  (`CompactExactMatcher`, re-verified and memory-mapped read-only in every
  worker), hash shingles, compute the exact MinHash, band keys, lineage keys,
  parents and the per-document facts-digest fragment. They never write files.
* **Commit.** The parent integrates results strictly in file/row order, verifies
  SHA-256, row count and canonical bytes at the end of each file, then publishes
  `facts/<ordinal>.unit` (sections assembled behind a signed header, fsync,
  rename). Anything earlier is untrusted staging and is discarded on the next run.
* **Group.** Dense ids in exact UTF-8 id order; exact runs; per-band sorted
  (12-byte key, id) buckets; exact replay of the historical near-candidate order,
  cap and oversized rules; lineage postings (external sort, digest equality then
  exact key bytes); parent edges; min-root union-find; survivors; families;
  splits; group digest. Seal: `seal.json` (signed digest + group file hashes).
* **Publish.** Membership/decision lines rendered in parallel, written in id order.

### Progress (stderr only; stdout is still the single final JSON)

`run`/`resume` show progress by default: `--progress-interval 1.0` (seconds),
`--progress-format text|jsonl`, `--no-progress`. Every stage line is content-free
(fixed stage names and numbers only). Stages: `PREFLIGHT`, `INDEX VERIFY`,
`MATCHER COMPILE: <phase>`, `MATCHER VERIFY: <phase>`, `SCAN: VERIFY COMMITTED`,
`SCAN: START WORKERS`, `SCAN`, `GROUP: PREPARE`, `GROUP: EXACT`,
`GROUP: BAND INDEX BUILD`, `GROUP: NEAR`, `GROUP: LINEAGE INDEX BUILD`,
`GROUP: LINEAGE`, `GROUP: PARENTS`, `GROUP: FAMILY PROPAGATION`,
`GROUP: PATH COMPRESSION`, `GROUP: SURVIVORS`, `GROUP: FAMILY BUILD`,
`GROUP: SPLITS`, `GROUP: DIGEST`, `PUBLISH: MEMBERSHIP`, `PUBLISH: FSYNC`,
`PUBLISH: COMPLETION`, `COMPLETE`. `SCAN` shows processed documents (prepared and
integrated, not yet durable) separately from `committed` documents/files (inside a
published unit). ETA uses a 30-second rolling rate, needs 10 seconds of samples,
resets at each stage and shows `--:--:--` without a denominator or a rate. RSS,
working-index, scratch and free-space telemetry is sampled every 5 seconds.

```text
[C05] SCAN | 50,570/100,000 docs (50.57%) | files 6/13 | 0.33/0.56 GiB input | committed 45,194 docs | 2,869 docs/s rolling | 2,743 docs/s avg | 16.2 MiB/s | workers 15/16 busy | tasks 15/32 results 0 | RSS 1.8/48.0 GiB (peak 1.8) | index 0.06/64.0 GiB | scratch 0.10 GiB | free 753.4 GiB | elapsed 00:00:18 (run 00:00:23) | ETA 00:00:17
```

### Storage and resources (restated meanings; same field set)

* `index_bytes`: hard ceiling of the whole compact working index (units, unit
  staging, group arrays, band index, sort spills), charged before every write.
* `journal_bytes`: still checked against the derived journal bound of a store of
  `index_bytes`; that SQLite store exists only with heuristic review enabled.
* Worst case = `index_bytes` + review store/journal (review only) + decisions +
  membership + completion/state/seal envelopes + locks + benchmark index + derived
  compiled matcher + `(files + 49 + 16) x 2 MiB` allocation slack.
* `workers`: reviewed maximum for scan, grouping and publication workers.
* `ram_bytes`: process-tree working set (parent + every worker, shared mapped pages
  counted once per process, i.e. conservatively). See the report's RAM section
  for the recommended p0002 value.

`resume-check` stays read-only and now also reports `files_total`,
`documents_committed` and `documents_total`.

### Migration from p0001 to a new plan (operator only)

1. Stop p0001 if it is still running; preserve its work directory unchanged.
2. Review this implementation and its evidence; push the accepted commit.
3. Record the new `code_commit` / `code_identity` / `dependency_sha256`.
4. Rebuild the protected benchmark preparation receipt under the new code identity
   (same frozen matcher-v4 policy and index semantics); verify it.
5. Review and sign a new resource decision (see the report for `ram_bytes`,
   `index_bytes`, `scratch_bytes`, `workers: 16`).
6. Create a NEW plan (sequence 2, "p0002") with a fresh scratch job directory;
   review its digest; sign its authorization.
7. `run` with progress on; `resume-check`/`resume` after any interruption.

## Detached-volume isolation (`detached_volume_v1`, same Windows account)

Chosen deployment: no second Windows account. The protected benchmark material,
protected index **and the C05 scratch** (it stores detailed match facts and, with
review enabled, raw signature tokens) live on a dedicated volume, e.g. `X:`, that is
attached only for protected preparation and the C05 run. This is *detached-volume
operational isolation* against accidental/process-level contamination; it does not
deny the agent OS access and is not adversarial security against the same user.

Contract, enforced at preparation, plan creation and run time (fixture-tested):

* `X:\C05-Protected` (benchmark root) carries the write-once marker
  `C05-PROTECTED-ROOT.json`; its marker digest, logical id and filesystem device
  (`os.stat` volume serial) are bound into the receipt and the plan.
* Benchmark material and the prepared index stay inside the root; the only
  artifact written outside is the content-free receipt copy (`--receipt-export`).
* The C05 scratch (e.g. `X:\C05-Scratch`) must be on the protected device and must
  not overlap the root; the C05 output (kept membership, completion) and the data
  root `G:/XLM` must be on another device; no repository checkout may overlap.
* `plan --index` binds the index *in place*; `run` refuses any other index path,
  a different marker/device, an unmounted root or roots that moved volumes.
* The same OS principal is permitted only under this mechanism and must equal the
  recorded operator; `separate_principal_v1` receipts keep refusing one principal.
* Downstream (tokenizer, counting, tokenization, mixture, training): every C05
  proof consumer refuses while `X:\C05-Protected` is accessible and refuses paths
  inside the root or the C05 scratch. Only that bound path is probed; mounting the
  volume elsewhere is not detected. Detach `X:` before any tokenizer/training work.
* Capacity: the C05 scratch worst case (about 345 GiB with the proposed resource
  values, including the derived compiled-matcher bound, plus the reserve) must fit
  on the protected volume.

```powershell
$op = 'uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.exclusion.operator'
# 1. Once: create the root marker (content-free random identity nonce).
Invoke-Expression "$op protected-root init --root X:/C05-Protected --logical-id c05-protected-v1"
# 2. Measure a content-free isolation proposal; paste its "isolation" into material-spec.json.
Invoke-Expression "$op protected-root describe --root X:/C05-Protected --operator $env:USERNAME --attestation-sha256 <sha256-of-signed-operator-attestation> --repository F:/Project/xlm-c05-global --repository F:/Project/xlm-data-ultrax --data-root G:/XLM --c05-scratch X:/C05-Scratch --c05-output G:/XLM/c05/output"
# 3. Prepare inside the root; export only the content-free receipt.
Invoke-Expression "$op build-local --spec X:/C05-Protected/material-spec.json --material-root X:/C05-Protected/material --output X:/C05-Protected/prepared --policy <matcher-policy.json> --resources <resources.json> --issuer <issuer> --key-env <KEY_ENV> --code-commit <commit> --code-identity <identity> --dependency-sha256 <deps> --receipt-export G:/XLM/c05/benchmark-preparation.receipt.json"
# 4. Plan binds root + index in place; run reads the index from the mounted volume.
Invoke-Expression "$op plan --mode protected --index X:/C05-Protected/prepared/index.jsonl --scratch X:/C05-Scratch --output G:/XLM/c05/output <other plan arguments>"
```

After `verify`, detach `X:` before `count-tokens`, tokenizer fitting, tokenization,
freeze or training; those commands refuse while it is mounted.

## Parallel protected preparation (`build-local`, `Resources.workers`)

`build-local` honors the reviewed `Resources.workers` value (validated 1..16; no
CLI override exists). `workers: 1` runs in-process and serially. `workers: N > 1`
spawns `min(N, tasks)` child processes, one task per Parquet row group or JSONL
file, which decode, render and derive patterns. They return batches of at most 32 rows
through a result queue bounded at `2 x workers` batches to one parent writer.
That writer alone owns SQLite, the duplicate/pattern counters and every ceiling.
Children receive only paths, the content-free material entry and the matcher
policy. They write no files, and nothing protected appears on a command line.

Artifacts do not depend on the worker count. The canonical index, the receipt and
the signed envelope are byte-identical for 1, 2, 4 and 16 workers on authored
fixtures, and identical to the pre-parallel serial implementation. The receipt
does not contain `workers`. A plan binds the full `Resources`, so a plan made
with a different resource decision has a different plan digest.

Ceilings stay global: stage deadline, parent plus all worker RSS, free-space
reserve, destination bytes, pattern total, item counts and file identity. The
writer checks them after every batch and at least every 0.25 s while it waits.
Every file is size+SHA-256 verified before any row is read and re-verified after
its last row. A change during processing is refused.

Production worker count is a reviewed resource decision:

- Write a NEW reviewed `resources.json` restating every field with the chosen
  `workers` (e.g. 16). Do not reuse the earlier `workers: 1` decision for a
  16-worker protected run, and do not edit the reviewed file in place.
- If the same values are used later for `plan`, sign a new resource decision.
  The plan digest changes. Since the compact engine, the C05 scan and grouping
  also use this reviewed maximum (see "Compact parallel engine" above).
- Measured on authored fixtures: 16 workers peaked at about 1.3 GiB process-tree
  RSS. Keep `ram_bytes` well above that plus the parent's SQLite cache.

Progress is on by default. A line goes to **stderr** at most once per
`--progress-interval` seconds (default 1.0), plus forced lines at start, at each
completed file, at the index phase and at completion. `--no-progress` silences
it. Both flags are display-only and never enter an artifact. Stdout still carries
only the final JSON object (`{"prepared": true, "mode": ...}` or a content-free
refusal). Progress is content-free: counts, rate, elapsed time and ETA only.
It never shows text, tokens, hashes or provenance:

```text
[C05 prepare] process | files 18/76 | rows 42,381/153,182 (27.7%) | patterns 615,202 | workers 16/16 | 2,940 rows/s | elapsed 00:00:14 | ETA 00:00:38
```

`patterns` counts pattern/provenance records after exact per-item dedup, which is
the quantity capped by `benchmark_patterns`. It equals the final `index.jsonl` line
count and the receipt's `patterns`. `build` asserts the equality. `fallback items`
counts c05-matcher-v4 whole-item fallback signatures.

## Matcher v4 and the content-free signature audit

`c05-matcher-v4` keeps every v3 normal signature unchanged. For an item with **zero**
normal signatures only, it adds one exact whole-item fallback over the label-free
`item-composite-v1`:

* ARC: question + choices.
* BLiMP: good + bad sentence.
* PIQA: goal + sol1 + sol2.
* HellaSwag: context + endings.

The floor is 4 tokens / 16 characters / 3 distinct. There are no sliding windows.
`c05-matcher-v3` keeps its historical meaning and identity. In protected mode,
`build-local` refuses before writing the index/receipt/export if any item stays
unsigned. The real counts (813 unsigned under v3) are operator measurements, not
agent-tested. Rerun the audit before freezing a v4 policy or resources:

```powershell
Invoke-Expression "$op benchmark-audit-local --spec X:/C05-Protected/material-spec.json --material-root X:/C05-Protected/material --policy <v4-matcher-policy.json> --resources <resources.json> --workers 16 > G:/XLM/c05/benchmark-audit-v4.json"
```

The audit writes nothing and needs no key. Stdout is aggregate counts only, with
no text, labels, tokens, signatures, hashes, provenance or rows. It exits 0 when
every item is signed and 2 otherwise. Size `benchmark_patterns` from
`totals.emitted_patterns_after_lossless_dedup`, then sign new resource and policy
decisions binding the v4 matcher and rerun `build-local` into a fresh destination.
Details: [report](../implementation/reports/C05-MATCHER-V4.md).

Interrupted or failed preparation: Ctrl+C (exit 130), worker error, malformed row,
changed file, ceiling refusal or SQLite error terminates every worker and fails
closed. The destination then keeps `PREPARATION-INCOMPLETE` and has no receipt; it
may hold a partial `preparation.sqlite` or `index.jsonl`. **Never reuse it.**
Delete the whole `--output` directory (inside the protected root) and, if present,
a `--receipt-export` file from that attempt, then rerun into the empty
destination. There is no resume. A receipt is valid only when the destination
has no `PREPARATION-INCOMPLETE` marker.

## Compact exact matcher and its capacity audit

`run` matches with the compact exact backend (`c05-compact-exact-v1`), not the
Python Aho-Corasick automaton. Its hits are identical to the automaton's and every
hit is verified token by token. It compiles a private matcher from the protected
index into `X:\C05-Scratch\<plan-digest>\matcher\`. The matcher holds benchmark
signatures, so it never leaves the protected scratch and is never exported.

* **Publication.** A crash leaves only `matcher.staging`. The next run discards it
  (known file names only) and rebuilds it.
* **Reuse.** A published matcher is reused only after every file hash, binding and
  re-derived count matches; any mismatch refuses.
* **Ceilings.** `automaton_nodes` bounds the *logical* exact trie size (root plus
  distinct prefixes), derived without building a trie. `benchmark_patterns` still
  bounds index records. Admission includes the derived compiled-matcher storage.

Before signing new resources, measure the real index, content-free:

```powershell
Invoke-Expression "$op benchmark-matcher-audit-local --index X:/C05-Protected/prepared/index.jsonl --resources <resources-value.json> --scratch X:/C05-Scratch/matcher-audit --self-check 1000 > G:/XLM/c05/benchmark-matcher-audit.json"
```

The audit command:

* requires the index to be inside the marked root, and the scratch to be on the
  same device, outside the root and outside any checkout;
* enforces `ram_bytes`, `scratch_bytes` and `stage_seconds`;
* only *reports* `automaton_nodes`, `benchmark_patterns`, `benchmark_bytes` and
  the anchor-bucket fit;
* prints aggregates only: counts, bucket quantiles, compiled bytes, peak compile
  RSS, seconds and a self-check.

Exit 0 means everything fits, exit 2 means a ceiling does not fit, exit 1 means a
refusal (see `ceiling`). With the current `automaton_nodes = 8000000`, expect exit
2. Size `automaton_nodes` from `logical_trie_nodes` and `benchmark_patterns` from
`index_records`, then sign a new resource decision. Delete
`X:\C05-Scratch\matcher-audit` afterwards: it is a private signature copy.
Details: [report](../implementation/reports/C05-COMPACT-MATCHER.md).

## Readiness check (metadata only)

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.exclusion.operator plan-readiness --manifest docs/implementation/evidence/C05-GLOBAL-CONTAMINATION-PLAN/input-manifest.json --ifm-split G:/XLM/calib/requirement_splits/ifm_behaviors_general_planning.json --geometry-probe-dir G:/XLM/temp/c05-geometry-probe
```

Expected exit 2 until the operator inputs exist. The JSON separates
`engineering_blockers` (now `[]`), `operator_decisions`, `protected_evidence` and
`engineering_checks`. The checks re-derive the 17 frozen
allocations (6,000,000,000 valid targets) from the real quota table, IFM split and
Common Pile split, and admit the *proposed* `Resources()` defaults against the SQLite
journal geometry measured in the probe directory (a few kB, removed afterwards).
A present decision/receipt file is only "present"; `plan` verifies it.

## Storage admission (applies to every run)

`plan` measures the scratch volume's SQLite rollback-journal geometry and binds it
(the geometry now bounds only the optional heuristic-review store). The plan refuses
unless `journal_bytes` covers the derived journal bound and `scratch_bytes` covers the
worst case of: the compact working index (`index_bytes`, hard byte ledger over fact
units, unit staging, grouping arrays, band index and sort spills), the review store
and its derived journal (review enabled only), private decisions (`decision_bytes`,
hard), membership staging/publication (`output_bytes`, hard), completion/state/seal
envelopes (hard), locks, benchmark index, the derived compiled exact matcher
(`13/4 * benchmark_bytes + 40 * min(benchmark_patterns, benchmark_bytes // 36)` plus
manifest and slack) and allocation slack of `(plan files + 49 + 16) x 2 MiB`. Before
creating any file, `run`/`resume` re-measure the geometry, refuse unaccounted entries
(WAL/SHM, foreign files, a historical `facts.sqlite`) and require each volume to hold
the remaining growth (`bound - present`) plus `free_bytes`. The same reserve check is
sampled during the run (every 5 s). Nothing widens a ceiling; a larger ceiling is a new
plan and authorization. Recommended p0002 values and the derived worst case are in the
[compact engine report](../implementation/reports/C05-COMPACT-PARALLEL-ENGINE.md).

## Tokenizer fit (C06)

`fit-tokenizer` fits the production 32,768 ByteLevel BPE on exact C05 kept-train
membership, using only the frozen policy `recipes/tokenizer/mix01_fit_shares_v1.yaml`.
The policy fixes:
- 512 MiB of canonical bytes;
- equal integer weights over the eleven components, with IFM and Common Pile divided
  by their frozen splits;
- a 1 MiB cap that applies to fitting only;
- whole crossing documents;
- refusal on shortfall, with no redistribution.

The CLI has no policy options.

`fit-tokenizer` is the **fast path** (`c06-fast-v1`). It is scientifically identical
to the bb886bd reference, which is still available as `fit-tokenizer-reference`. Its
stages:

- **Before any read.** It refuses while the plan-bound protected root (`X:`) is
  mounted, and refuses any path inside the protected root or the C05 scratch.
- **Membership stream.** `membership.jsonl` is read once, and the parsed bytes are
  the hashed bytes. Nothing is used until the SHA-256, byte size and row count equal
  the signed completion. Rows must be in strictly ascending doc-id order (so no
  repeats), name a frozen plan file, row and allocation, and reconcile the
  completion's kept/train-byte/non-kept totals. No SQLite.
- **Exact selection.** The same rank, budgets, cap and crossing rule as the
  reference. A deficit refuses here (exit 2 with a content-free report) before any
  source byte is read.
- **One source pass.** Every plan file is hashed exactly once and must match the
  plan's SHA-256, size and row count; reading stops and refuses as soon as a file
  exceeds its frozen bytes or rows. Every kept row is strict-JSON parsed to check
  its doc id, byte count and **original** split: a C05-train row whose canonical
  `split` is not `train` refuses, exactly as the reference does. Only selected rows
  are fully decoded and content-digested, and they are spooled in plan-file and row
  order, which is the reference BPE feed order.
- **Authenticated BPE spool.** The parent hashes the exact framed spool bytes as it
  writes them (with byte and frame ceilings), fsyncs and marks the file read-only.
  The BPE child hashes exactly the bytes it consumes and refuses before saving
  unless SHA-256, bytes, frames and payload equal the parent's; it re-hashes the
  file afterwards; the parent re-checks again. The signed manifest binds the spool
  digest (`bpe_spool`).
- **BPE.** Runs in a supervised child process with `TOKENIZERS_PARALLELISM=true` and
  `RAYON_NUM_THREADS=--bpe-threads` (default 16). Inherited values are overridden.
- **One supervisor.** The `--deadline-seconds` budget (default 1200) is the TOTAL
  command time, starting at dispatch (before proof verification). A monitor thread,
  independent of progress display, samples every 0.25 s: deadline, the whole
  process tree's RSS against `--rss-ceiling-gib` (default 24, reviewed maximum 32),
  free space against `--free-reserve-gib` on every owned volume, and the projected
  total. On any breach it terminates every descendant (terminate, 2 s grace, kill,
  reap); waits on workers are polled, never blocking. Publication is the parent's
  single atomic rename and happens only after every verification, a passing
  supervisor check and at least 1 s of deadline margin.
- **Workers.** `--workers` (1/2/4/8/16, default 8) is operational only; outputs are
  identical for every value.
- **Storage.** The plan lists every owned growth item (spool, index, sample,
  tokenizer, manifests, job/result) per volume; the run refuses unless each volume
  holds growth plus the reserve. Writers enforce byte ceilings. Cleanup removes only
  registered paths; any residue (for example an unknown file) is reported in the
  refusal and never deleted recursively.
- **Output.** One write-once directory, renamed into place last:
  - `tokenizer/{tokenizer.json, tokenizer_manifest.json, c05-binding.json}`
  - `tokenizer_fit_manifest.json` (signed)
  - `tokenizer_fit_sample.jsonl`
  - `tokenizer_fit_resource_plan.json` (plan plus measured stage times and throughput)
  - `kept-index/` (the reusable post-C05 kept-membership index, signed)

Use the NVMe `C:` for `--scratch` (it holds the ~0.6 GB BPE spool). The corpus on `G:`
is a SATA SSD; about 0.5 GB/s is an assumption from the drive class, not a
measurement of production throughput.

The reviewed operational envelope (workers, BPE threads, deadline, RSS ceiling,
free-space reserve, monitor interval, queue bound, chunk/block sizes, BPE and
finalization reserves, early-abort ratio) and the storage plan are inside the
`--plan-only` digest. The run recomputes the plan from its own flags and refuses
unless the digest is identical, so a plan reviewed for 8 workers cannot run with 16.

```powershell
$cli = 'uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.exclusion.operator'
$c06 = '--c05-proof G:/XLM/c05/p0002.proof.json --fit-shares recipes/tokenizer/mix01_fit_shares_v1.yaml --quotas recipes/mixtures/mix01_quotas_6b.yaml --ifm-split G:/XLM/calib/requirement_splits/ifm_behaviors_general_planning.json'
$ops = '--workers 8 --bpe-threads 16 --deadline-seconds 1200 --rss-ceiling-gib 24 --free-reserve-gib 16'
$fit = "$c06 --scratch C:/XLM-scratch/c06-fit --output G:/XLM/tokfit/mix01-fit-shares-v1-fast --deficit-report G:/XLM/tokfit/mix01-fit-shares-v1-fast.deficit.json $ops"
# 0. Detach X: first. Every key environment variable named by the proof's trust file must be set.
# 1. Metadata-only operational plan (reads no membership or corpus bytes); review it.
Invoke-Expression "$cli fit-tokenizer $fit --plan-only"
# 2. The fit with IDENTICAL flags. Exit 0 = published; 2 = deficit report; 1 = refused.
Invoke-Expression "$cli fit-tokenizer $fit --resource-plan-digest <digest> --issuer GammA --key-env XLM_C05_OPERATOR_KEY"
# 3. Verification: re-derives the sample from authenticated membership, checks the index
#    snapshot and tokenizer. Add --sources to also re-hash every source file and re-derive
#    the BPE spool digest (one more source pass).
Invoke-Expression "$cli verify-tokenizer-fit $c06 --fit G:/XLM/tokfit/mix01-fit-shares-v1-fast --workers 8"
# 4. Post-C05 kept index: signature, bindings, a private hashed snapshot, structural
#    validation and on-disk re-verification; --membership re-derives from
#    membership.jsonl; --sources also re-hashes and re-locates every source row.
Invoke-Expression "$cli verify-kept-index $c06 --index G:/XLM/tokfit/mix01-fit-shares-v1-fast/kept-index --membership --workers 8"
```

`--plan-only` refuses early if:
- the quota table bytes differ from the policy pin;
- the sealed sources bind a different table or IFM split;
- the proof's completion digest changed;
- an operational value is outside its reviewed bounds (for example a non-finite
  deadline or an RSS ceiling above 32 GiB).

Progress lines start with `[C06]`:
- `MEMBERSHIP STREAM` reports rows, MB/s and ETA;
- `SOURCE PASS` reports files, GiB, GB/s, split checks, selected rows parsed, the
  projected TOTAL and the deadline;
- `TOKENIZER FIT` shows elapsed time and process-tree RSS heartbeats, because BPE
  merge progress is not observable.

The supervisor projects the TOTAL completion time: elapsed + remaining membership +
selection reserve + remaining source + a 240 s BPE reserve + a 60 s finalization
reserve. Stages without measured progress use conservative planning rates
(membership 40 MB/s, source 0.2 GB/s). An `SLO WARNING | projected total ...` line is
printed to stderr (even with `--no-progress`) whenever the projection exceeds the
deadline, and is refreshed every 30 s; once the source rate is measured (>= 10 s and
>= 2 %), a projection above 1.25x the deadline aborts early. A deadline, RAM or disk
breach discards the run and publishes nothing; the refusal reports how long after the
deadline cleanup finished, and any cleanup residue.

The tokenizer for the allocation chain below is
`G:/XLM/tokfit/mix01-fit-shares-v1-fast/tokenizer`.

## Allocation chain after a verified completion

All commands take an explicit proof specification (`--c05-proof`), a JSON object
with `plan`, `manifest`, `completion`, `trust`, `scratch`, `plan_digest`,
`completion_digest` and, for signing shard attestations, `signer` and
`signer_key_env`. Artifacts are write-once and record the plan mode; an authored
chain is a rehearsal that Mix-01 training and official claims refuse.

```powershell
$cli = 'uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.exclusion.operator'
# 1. Exact per-record valid-target counts over every kept training record.
#    The tokenizer directory must carry c05-binding.json for this completion.
Invoke-Expression "$cli count-tokens --c05-proof <proof.json> --tokenizer <tokenizer-dir> --scratch <scratch> --output <counts-dir> --issuer <issuer> --key-env <KEY_ENV>"
# 2. Deterministic exact selection per frozen allocation (exit 2 + report on deficit).
Invoke-Expression "$cli select --c05-proof <proof.json> --tokenizer <tokenizer-dir> --scratch <scratch> --counts <counts-dir> --quotas recipes/mixtures/mix01_quotas_6b.yaml --ifm-split G:/XLM/calib/requirement_splits/ifm_behaviors_general_planning.json --deficit-report <deficit.json> --output <selection-dir> --issuer <issuer> --key-env <KEY_ENV>"
# 3. One shard per logical component, exactly the selected records.
Invoke-Expression "$cli tokenize-selection --c05-proof <proof.json> --tokenizer <tokenizer-dir> --selection <selection-dir> --output-root <shards-dir>"
# 4. Signed final freeze plus the bounded training-data block.
Invoke-Expression "$cli freeze --c05-proof <proof.json> --tokenizer <tokenizer-dir> --selection <selection-dir> --shards <shards-dir> --output <freeze-dir> --issuer <issuer> --key-env <KEY_ENV>"
# 5. Schema-3 receipt, independently derived claim binding and the claim check.
Invoke-Expression "$cli final-receipt --plan <plan.json> --benchmark-receipt <receipt.json> --c05-proof <proof.json> --freeze <freeze-dir>/freeze.json --output <final-receipt.json> --trust <trust.json> --issuer <issuer> --key-env <KEY_ENV>"
Invoke-Expression "$cli claim-binding --c05-proof <proof.json> --plan <plan.json> --freeze <freeze-dir>/freeze.json --checkpoint-hash <checkpoint> --suite-fingerprint <suite> --output <claim-binding.json>"
Invoke-Expression "$cli claim-check --receipt <final-receipt.json> --binding <claim-binding.json> --trust <trust.json>"
```

A deficit never substitutes across allocations, renormalizes, repeats or tops up:
acquire more for that same allocation under a new reviewed acquisition, then build
a new input manifest and rerun global C05; every older count/selection/freeze is
then stale. `python -m xlm.data.parallel_tokens ... --c05-proof <proof>
--c05-selection <selection-dir>` is the parallel alternative to step 3 for one
component. The generated end-to-end rehearsal is `python -m scripts.c05_synthetic_flow
--root <new-dir> --output <summary.json>` (`PYTHONPATH` must include the checkout
root). The real 6B token payload exceeds the existing 2 GiB bounded
training-input contract; no unbounded training path was added.

## Historical engine notes

The engine continuation now has authored streaming/recovery and low-level gate
tests. **Protected plan creation/execution remain disabled in code.** The missing
benchmark receipt is not the only blocker: production resource certification,
fuzzy review and operator/parallel/final-receipt/quota integration remain incomplete.
The report's current section supersedes its retained historical audit.

## New offline schema and local-material tools

The commands below do not download anything or authorize C05. Use the existing
offline environment settings shown later in this runbook. Schema export is safe
in the agent workspace; the existing committed schema is under the report's
`engine-v2/artifact-schemas.json`. A fresh export is write-once:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.exclusion.operator schema --output C:/XLM-scratch/c05-artifact-schemas.json
uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.exclusion.operator plan-readiness
```

Expected exits are 0 and 2 respectively. `plan-readiness` explicitly reports no
executable plan; it is not benchmark coverage verification.

Only on the separate operator identity/machine, prepare `material-spec.json` from
the exact local publisher inventory using the exported `MaterialSpec` schema.
Every entry needs task, repository, immutable revision, actual config/split,
relative JSONL path, SHA-256, byte size and item count. Include the publisher
inventory digest, explicit all-published-coverage review and isolation attestation.
Do not populate unknown fields with sample counts or example configurations.
The following proposed private paths must already be outside agent access:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.exclusion.operator inspect-local --spec C:/XLM-operator-private/c05/material-spec.json --material-root C:/XLM-operator-private/c05/material --output C:/XLM-operator-private/c05/local-inspection.json
```

This checks existence/size only. It does not certify material hashes or completeness.
Local availability is presently unknown; no payload acquisition command is
authorized or inferred. Publisher formats other than reviewed JSONL need an
explicit adapter/conversion and provenance review before this builder can be used.

`build-local` implements the authored-tested JSONL preparation path. It requires
the specification, explicit matcher/resource JSON files, destination, trusted
issuer and signing-key environment variable, plus actual code/dependency identity.
Inspect its exact arguments with:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.exclusion.operator build-local --help
uv run --offline --locked --no-sync --extra cpu --extra eval python -c "import json; from xlm.data.exclusion.identity import implementation_identity; print(json.dumps(implementation_identity()))"
```

Do not run protected building as the agent or send the key, raw index, examples,
labels, per-item signatures or private decisions back to the agent. A reviewed
operator build must run with a separate principal and proven denied agent access;
the code verifies the principal and checks the supplied source/dependency identity.
Return only `benchmark-preparation.receipt.json` and content-free inspection metadata.
The expected handoff location is `G:/XLM/c05/benchmark-preparation.receipt.json`;
it was absent when checked. This is a proposed handoff path, not a claim that
material is absent on every machine.

There is still no accepted protected `plan/authorize/run/resume` CLI or valid
execution digest. Do not call the authored Python authorization helper with real
inputs as a workaround. Keep the explicit protected refusal until the remaining
engineering and acceptance checks are completed.

## Existing inventory verification and historical preparation notes

Current state: **blocked preparation, not an executable C05 plan**. See the
[audit and requirement ledger](../implementation/reports/C05-GLOBAL-CONTAMINATION-PLAN.md).
The source input manifest is not an exclusion receipt or permission to train.

Run from `F:\Project\xlm-c05-global`. The existing environment has Python 3.12.13
and the locked CPU/eval dependencies. No installation or network is needed:

```powershell
$env:UV_PROJECT_ENVIRONMENT='F:/Project/xlm-common-pile/.venv'
$env:PYTHONPATH='F:/Project/xlm-c05-global/src'
$env:UV_OFFLINE='1'
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'

uv run --offline --locked --no-sync python -m xlm.data.exclusion.preparation verify --data-root G:/XLM --scratch-root C:/XLM-scratch --manifest docs/implementation/evidence/C05-GLOBAL-CONTAMINATION-PLAN/input-manifest.json

uv run --offline --locked --no-sync python -m xlm.data.exclusion.preparation preflight --data-root G:/XLM --scratch-root C:/XLM-scratch --manifest docs/implementation/evidence/C05-GLOBAL-CONTAMINATION-PLAN/input-manifest.json
```

`verify` expects exit 0: metadata and sizes reproduce. It does not reread corpus
contents. `preflight` expects exit **2**: required production facilities/material
evidence are missing. Do not interpret that exit as authorization or bypass it.
An altered seal/receipt/plan/file size or malformed pin exits 1. The first-pass
manifest is write-once; a changed corpus requires a new reviewed lineage.

To recreate the manifest at a fresh output path outside the data store, use the
same arguments with `inventory` and a new `--manifest` path. The command inventories
all ten fixed baseline sources and fails on any missing component. It cannot add
txt360, narrow to a convenient subset or run C05. No `authorize`, `run`, `resume`
or post-run verification verb exists yet.

## Benchmark preparation boundary

The operator must first inventory local benchmark artifacts under the isolated
identity/machine required by `EVALUATION_POLICY.md`. Return only content-free
metadata and trusted receipts: exact repository/revision/config/split/count/file
hashes, complete split coverage, render/normalization/signature identities,
code/dependency identity and isolation attestation. Do not send labels, example
text, raw signatures or detailed matches to the agent.

If material discovery needs network, the following **future operator-only** commands
list metadata at the four existing immutable pins. They were NOT EXECUTED. They
do not fetch benchmark payloads and do not create a usable benchmark index.
The live lister allows only `https://huggingface.co/api/datasets/.../tree/...`;
payload/resolve endpoints are refused. Scope is these four exact repositories and
revisions. Each call: 16 pages, 4,096 items, 32 requests, 8 MiB metadata, 2 retries,
15-second request timeout, 300-second deadline. Aggregate maxima: 128 requests,
32 MiB metadata and 1,200 seconds. Keep a separate bounded metadata output directory;
no corpus cache or extraction is involved. A refused/incomplete listing stops the
workflow; never widen bounds or switch revisions automatically.

Run only after the operator separately authorizes this metadata access, from an
operator-controlled checkout/environment. `UV_OFFLINE=1` and uv's `--offline` keep
dependency resolution offline; the explicit HF transition permits only the lister's
bounded network code. Restore both HF flags even on error:

```powershell
$env:HF_HUB_OFFLINE='0'
$env:HF_DATASETS_OFFLINE='0'
try {
    uv run --offline --locked --no-sync python scripts/mix01_inventory.py list-hf --source arc_easy --view ARC-Easy --repo allenai/ai2_arc --revision 210d026faf9955653af8916fad021475a3f00453 --max-pages 16 --max-items 4096 --max-requests 32 --max-metadata-bytes 8388608 --max-retries 2 --timeout 15 --deadline 300 --output C:/XLM-operator/c05-metadata/arc_easy.json
    if ($LASTEXITCODE -ne 0) { throw 'ARC metadata listing failed' }
    uv run --offline --locked --no-sync python scripts/mix01_inventory.py list-hf --source blimp --view all --repo nyu-mll/blimp --revision 877fba0801ffb7cbd8c39c1ff314a46f053f6036 --max-pages 16 --max-items 4096 --max-requests 32 --max-metadata-bytes 8388608 --max-retries 2 --timeout 15 --deadline 300 --output C:/XLM-operator/c05-metadata/blimp.json
    if ($LASTEXITCODE -ne 0) { throw 'BLiMP metadata listing failed' }
    uv run --offline --locked --no-sync python scripts/mix01_inventory.py list-hf --source hellaswag --view all --repo Rowan/hellaswag --revision 218ec52e09a7e7462a5400043bb9a69a41d06b76 --max-pages 16 --max-items 4096 --max-requests 32 --max-metadata-bytes 8388608 --max-retries 2 --timeout 15 --deadline 300 --output C:/XLM-operator/c05-metadata/hellaswag.json
    if ($LASTEXITCODE -ne 0) { throw 'HellaSwag metadata listing failed' }
    uv run --offline --locked --no-sync python scripts/mix01_inventory.py list-hf --source piqa --view all --repo baber/piqa --revision 142f6d7367fd9877f0fb3b5734ea6a545f54cdd1 --max-pages 16 --max-items 4096 --max-requests 32 --max-metadata-bytes 8388608 --max-retries 2 --timeout 15 --deadline 300 --output C:/XLM-operator/c05-metadata/piqa.json
    if ($LASTEXITCODE -ne 0) { throw 'PIQA metadata listing failed' }
} finally {
    $env:HF_HUB_OFFLINE='1'
    $env:HF_DATASETS_OFFLINE='1'
}
```

No filters are specified: discover the repository files without assuming format or
silently missing splits. `view` labels above are inventory labels, not verified
publisher dataset configurations. Follow-up protected payload acquisition needs an
exact reviewed file list, host/redirect allowlist, rights decision, byte/cache/
scratch/output limits and an actual supported operator implementation. That
executable acquisition command is not available at this stop point.

## Before a future execution plan

Complete the streaming matcher, known-lineage propagation, deterministic per-file
journal/resume, bounded disk/RAM/deadline enforcement and protected atomic output
publication in the existing C05 implementation. Bind the verified benchmark inventory
and all policy/code identities. Demonstrate short-item handling, repeated leakage,
option permutations, false-positive controls, interruption and crash recovery on
authored fixtures. Integrate protected kept-membership verification into tokenizer
fit and final component selection. The current train-split check is insufficient.

Then generate a concrete plan and resource envelope for operator review, with a
new execution digest and exact authorization/run/post-run commands. Until then,
do not run C05 globally or use first-pass data for tokenizer/model training.
