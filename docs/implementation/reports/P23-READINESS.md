# P23 readiness closeout — supported workflows on the integrated tree

Date: 2026-09-21. Branch `closeout/readiness` at `35aed0b` (docs-only pilot
handoff on top of readiness closeout `49461e5` and test-perf `a2441af`), integrating `17e386f`
(D07+D08 merge of `6405885` and `7427dba` on base `bd01e60`). Worktree
`D:\Project\xlm-final-integration`, env `.venv-final` (CPython 3.12.13, torch
2.14.0+cpu, lm-eval 0.4.13, pytest 9.1.1, xdist 3.8.0), offline/locked.
Overall platform acceptance remains **BLOCKED**; main unchanged, no pushes.

## 1. Final audit result (one stable tree)

Historical evidence (kept under `docs/implementation/evidence/P23-FINAL/` and
prior stage reports) versus final-tree evidence:

- `par78d-xdist` (`-n 8`, 78 files, final tree): **1065 collected, 1064 passed,
  1 skipped, 0 failed** (333.6 s). Includes the operator determinism fix and
  all 10 receipt tests.
- Serial files on the final tree: artifact_identity (76 passed), operator (22
  passed), offline_workflow (4 passed). Merge-tree serial runs for queue,
  frozen×2, and configurable_workflow are carried forward because the closeout
  diff provably does not intersect them (no `ArtifactStore`/manifest/queue/
  frozen-worker usage; established by import graph, and their test files are
  unchanged).
- Serial-marked selection (concurrent publishers ×2, inventory timing):
  3 passed on the final tree.
- Union of executed IDs: **1184 of 1184 selectable** (12 policy-deselected:
  network/cuda/operator markers); whole-dir collection confirms the total.
- Quality (`ruff format --check`, `ruff check`, `mypy src`): exit 0. Base-only
  (torch absent): green. Demo slice (200/200 targets): exit 0.
- Combined 9-stage chain (acquire → tokenize → train → resume → evaluate
  complete + limited → shuffled D08 compare → single-copy export → fresh
  reload parity → report): exit 0 on the final tree with receipt flags. The chain
  exercises a forked continuation; exact unchanged-plan resume (bitwise-equal,
  unforked) is covered separately by maintained
  `test_cli_train_and_resume_subprocess`, green in the same audit — the fork
  is never relabeled as exact resume.
- **Zero failures.** The two former public-workflow failures are resolved,
  not deferred: `test_reports` and `test_recipes` now pass. Their underlying
  product refusals are preserved by dedicated negative tests —
  `test_submit_refuses_legacy_unfrozen_plan` (legacy plan submitted to the
  queue is refused, never silently upgraded) and
  `test_snapshot_reuse_conflicts_and_paths` (frozen snapshot overwrite
  refused). A passing refusal test is compliance evidence, not a remaining
  failure.

## 2. Closeout corrections in this stage

- Recipe test now plans into an isolated `--snapshot-dir` (caller fix; the
  repo's frozen snapshots stay immutable).
- Reports test builds a current frozen plan via the shared `make_plan` helper;
  the legacy v1 shape moved to `test_submit_refuses_legacy_unfrozen_plan`.
- Acquisition-receipt verification in `verify_evaluation_inputs` (+
  `--acquisition-store-root` through verify-only, in-process, and frozen-worker
  paths). Implemented checks: receipt presence under the store root, JSON
  schema validity via the D02 receipt model, `is_verified` status, exact-byte
  binding (digest + size) or `_xlm_acquisition` locator-chain binding
  (selection hash, revision, source file, population bound), every declared
  receipt bound to ≥1 executed selection and vice versa, duplicate declarations
  refused. Ten new tests cover valid/missing/corrupt/substituted/tampered/
  derived/changed lineage plus CLI verify-only. Fixture manifests without
  receipts are byte-identical in behavior.
- Operator determinism fix (single authorized object per execution) and
  `comparable_resolved()` containment fix (Windows `\\?\` resolve-form race),
  each with controlled reproduction and maintained regression tests.
- Test policy: focused-first default; fast `-n 16` (this audit used `-n 8`
  with the serial set aside after measuring `-n 16` inventory-gate collapse);
  `serial` marker only for the inventory-timing and concurrent-publishers
  tests. Per-worker XLM_HOME via conftest; runner reports executing vs queued
  honestly. AGENTS.md updated so future sessions stop auto-rerunning full
  audits.

Remaining trust limitations for receipts (explicit, not gaps in the above):
store-side re-authentication is D01 `verify_artifact` territory (callable
separately, not repeated here); source rights, license review, benchmark-risk
judgment, and admission certification stay operator decisions recorded via
`data admit`; untransferred bytes beyond the attested selection are out of
scope by construction.

## 3. Supported-workflow answers

- **Bounded raw acquisition: SUPPORTED (pilot).** Durable budgets, resume,
  whole-file refusal, selected JSONL/Parquet with locators, D01 publication —
  all exercised over loopback and locally (combined chain, 3 records,
  byte-identical republication).
- **Local preparation: SUPPORTED (pilot).** Aggregate budgets, bounded child
  output with exact retained/discarded accounting, nested fetch budgets,
  reuse/resume semantics — covered by `test_prepare*` (45 tests) and the
  chain's prepare-equivalent copy stage.
- **Fixed-tokenizer baseline training: PREREQUISITES.** Needs a frozen
  version-2 plan (`experiment plan`), an operator ticket
  (`experiment authorize`), and queue submission; tiny-CPU smoke proven via
  `demo` and `train`/`resume` CLIs (32 targets + forked extension in the
  chain). Production training additionally needs measured cost profiles and
  ticket authorization — both operator actions, still gated.
- **Declared development evaluation: PREREQUISITES.** Needs operator-selected
  records in the official schema with `xlm_item_locator` where the schema has
  no id, a manifest built by `scripts/build_eval_inputs.py`, and (when
  receipts are declared) `--acquisition-store-root` pointing at the D01 store.
  Verify-only needs no model; full runs need a checkpoint. Authored-fixture
  machinery fully green (75 eval tests + 10 receipt tests).
- **Matched-tokenizer research: STILL DEFERRED.** D03 work not closed by this
  stage; no tokenizer-comparison claims are made.
- **Real-source/hardware/official/protected validation: OPERATOR WORK.** No
  live source was contacted, no corpus prepared, no GPU profiling, no official
  benchmark, no protected deployment in this stage.

## 4. First one-source bounded acquisition pilot (implemented commands)

Working directory: any operator-owned directory (examples below use
`D:\Project\xlm-operator-pilot`; the tested checkout was
`D:\Project\xlm-final-integration` at `a2441af`, imports verified as that
tree's `src`). Environment (verified):

```powershell
$env:XLM_HOME = 'D:\Project\xlm-operator-pilot'
$env:UV_PROJECT_ENVIRONMENT = '<checkout>\.venv-final'
$env:UV_OFFLINE = '1'
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
uv sync --offline --locked --extra cpu --extra eval
uv run --offline --locked --extra cpu --extra eval xlm data sources --json
```

`UV_OFFLINE=1` only freezes dependency installation (the env is already
synced); it does not block data requests. For the operator-only live step
below, additionally set `HF_HUB_OFFLINE=0` and `HF_DATASETS_OFFLINE=0` in that
invocation only (restoring them afterward); never set `HF_TOKEN`/`HUGGING_FACE_HUB_TOKEN`
to bypass a provider gate, and never enable dataset remote-code execution.

**Step 0 (preliminary discovery — operator, explicitly network-authorized).**
Without this step no revision exists to pin and no pilot may proceed:

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data probe --source $source --live --budget-mib 16 --json
```

`$source` is a `source_id` from `data sources` (e.g. a catalog entry you
chose, not an invented name). From the probe output record: the immutable
`revision` (a commit SHA/tag — never `latest`/`master`/`main`/empty), the view
(`--view`, default `default`), the observed schema/adapter, and the probed
file inventory. Then review license, provenance, and benchmark-contamination
risk yourself and persist the admission decision (this records YOUR decision;
it does not fetch anything):

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data admit --source $source --adapter $adapter --notes 'review summary' --license-review pending --benchmark-risk clean
uv run --offline --locked --extra cpu --extra eval xlm data audit --json
```

A `--decision reject` records refusal instead. Pilot execution does not
require admission; production-scope execution does (§6).

**Step 1 (approval-bound plan).** `$files` is a comma-separated list of exact
relative filenames from YOUR probe inventory (not a glob, not invented).
`rows.json` maps each exact filename to one zero-based half-open `[start,
stop)` interval (JSONL counts nonblank object records; Parquet counts rows).
`limits.json` sets every ceiling; the pilot stays at or below 256 MiB
transferred, 25,000 records, and 2 GiB output or it leaves the pilot path.
Validated examples (checked against the `AcquisitionLimits` schema):

```json
{"data.jsonl": [0, 3]}
```

```json
{
  "max_transferred_bytes": 16777216,
  "max_decompressed_bytes": 33554432,
  "max_records": 100,
  "max_temp_disk_bytes": 67108864,
  "max_output_disk_bytes": 67108864,
  "max_requests": 20,
  "max_retries": 1,
  "max_workers": 1,
  "per_request_timeout_seconds": 10.0,
  "overall_deadline_seconds": 600.0,
  "max_decompression_ratio": 15.0,
  "max_record_bytes": 1048576,
  "max_parser_bytes": 33554432,
  "max_scanned_records": 1000
}
```

Whole-file mode omits `--row-ranges` (and then refuses, rather than
truncates, any file exceeding a limit):

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data plan --source $source --files $files --mode selected_records --row-ranges rows.json --limits limits.json --output pilot-plan.json
```

Review `pilot-plan.json` in full: `plan_id`, `plan_hash`, pinned `revision`,
requested files/ranges, sampling limits, and output identity. For whole-file
plans you may additionally supply independently verified publisher digests;
selected-row artifacts cannot verify untransferred bytes.

**Step 2 (fetch / resume).** The first command must refuse (no approval); the
second runs. Interrupt with Ctrl+C and rerun the identical second command to
resume within the original deadline and remaining allowance — same roots,
never delete the journal:

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data fetch --plan pilot-plan.json
uv run --offline --locked --extra cpu --extra eval xlm data fetch --plan pilot-plan.json --pilot-approved
uv run --offline --locked --extra cpu --extra eval xlm data status --plan pilot-plan.json --json
```

Optional `--output-dir`/`--scratch-dir` redirect roots; resume must reuse the
same roots. Default roots live under `$XLM_HOME/acquisition/<plan_id>/`.

**Step 3 (verify + inspect records and receipts).** `$raw` is the raw output
directory: by default `$XLM_HOME/acquisition/<plan_id>/raw` (take the actual
`plan_id` from your `pilot-plan.json`), or the path you passed to
`--output-dir`:

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data verify --plan pilot-plan.json --output-dir $raw --json
```

Confirm `records_acquired > 0` (status), `files[].record_count`,
per-row `_xlm_acquisition` locators (source, revision, file, row index, byte
offset/length, selection hash), and `receipt_id`, `plan_hash`,
`eligibility` (`pilot_only` for this flow), plus `resource_metrics`
(`transferred_bytes`, `requests_made`) and the journal at
`scratch/journals/<plan_id>.progress.json`. These are application body bytes,
not wire traffic or provider billing.

**Stopping on a genuine refusal.** Caps refuse rather than truncate (reduce
scope in a NEW reviewed plan; never edit limits in place). Unsupported
formats, ignored ranges, changed validators, or oversized parser work refuse
explicitly with no whole-shard fallback. A corrupt/incomplete completed
original is refused, never repaired in place. Production fetch additionally
requires verified admission with plan-bound `--authorization-hash`; an
admission-reference string alone is refused (see §6 for why that guard cannot
be satisfied from the CLI today).

## 5. Preparation and training prerequisites (not launched)

Preparation: verified raw path, tested adapter, authored prepare config with
explicit aggregate `budgets`, isolated home/output roots:

```powershell
uv run --offline --locked --extra cpu --extra eval xlm prepare --config operator-prepare.yaml --plan-only --json
uv run --offline --locked --extra cpu --extra eval xlm prepare --config operator-prepare.yaml --authorize --json
```

Production preparation additionally requires the production admission binding
described in §6 — the CLI refuses without it. Training additionally requires
`experiment plan`, `experiment authorize`, and queue submission with measured
profiles (see `demo` for the tiny-CPU smoke shape). None were launched here
and no production/profile guard was removed.

## 6. Production admission binding: what is implemented, what is missing

Pilot scope is complete: plan/fetch/status/verify enforce pilot approval,
journal-bound budgets, immutable revisions, and hash-bound authorization
(`validate_plan_authorization`, exercised by unit tests and the chain).

For production scope, the library check exists
(`validate_plan_authorization(plan, catalog_source_approved=True)` with a
behavioral-hash-bound `PlanAuthorization`), and `data admit` persists operator
decisions to the P01 store — but `fetch_cmd` (`src/xlm/cli/data_cmd.py`)
constructs `BoundedFetcher(plan, ...)` with `catalog_source_approved=False`
unconditionally. No CLI/service code resolves a stored admission decision into
that call, so even a genuine recorded approval cannot enable a production
fetch through the CLI today; only unit tests pass `True`. This is a narrow
missing connection, not missing operator evidence: implementing it means
resolving and verifying the stored decision bound to source/view/revision
before constructing the fetcher. It is deliberately left unimplemented here
(the guard stays fail-closed) and is proposed as the single next connector,
not another broad repair. It does not block pilot discovery, which uses the
`--live` probe route above. Training cost/profile inputs are in the same
shape: ticket authorize/submit/queue exist, while measured-profile lookup
integration is absent (unchanged).

## 7. Remaining blockers and next actions

D06 inventory gate under parallel disk load (300 s proposal unapplied, D06
review); deferred tokenizer work; operator-run validation. The recipe/report
refusals are preserved compliance evidence (§1), not failures. Receipt
limitations are now implemented checks plus the explicit boundary in §2, not
an open gap. Next: operator pilot per §4.
