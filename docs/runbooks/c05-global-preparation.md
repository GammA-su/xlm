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
  The plan digest changes. The C05 scan runner itself remains single-process.
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

`plan` measures the scratch volume's SQLite rollback-journal geometry and binds it.
The plan refuses unless `journal_bytes` covers the derived journal bound and
`scratch_bytes` covers the worst case of: facts database (`index_bytes`, hard SQLite
page cap), derived rollback journal, private decisions (`decision_bytes`, hard),
membership staging/publication (`output_bytes`, hard), completion/state envelopes
(hard), locks, benchmark index, the derived compiled exact matcher
(`13/4 * benchmark_bytes + 40 * min(benchmark_patterns, benchmark_bytes // 36)` plus
manifest and slack) and per-file allocation slack. Before creating any
file, `run`/`resume` re-measure the geometry, refuse unaccounted entries (WAL/SHM,
foreign files) and require each volume to hold the remaining growth
(`bound - present`) plus `free_bytes`. The same reserve check is sampled during the
run. Nothing widens a ceiling; a larger ceiling is a new plan and authorization.
With the proposed defaults the worst case is 370,303,419,928 B (512 B journal
header measured on C: and G:), inside the proposed 352 GiB scratch ceiling.

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
