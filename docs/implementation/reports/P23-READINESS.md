# P23 readiness closeout — supported workflows on the integrated tree

Date: 2026-09-20. Branch `integrate/d07-d08`, tested commit `7d8334b` plus the
scoped closeout commit recorded below. Worktree
`D:\Project\xlm-final-integration`, env `.venv-final` (CPython 3.12.13, torch
2.14.0+cpu, lm-eval 0.4.13, pytest 9.1.1, xdist 3.8.0), offline/locked.
Overall platform acceptance remains **BLOCKED**; main unchanged, no pushes.

## 1. Final audit result (one stable tree)

- Parallel `-n 8 --dist=worksteal` over 78 files: **1065 collected, 1064 passed,
  1 skipped, 0 failed** (`par78d-xdist`, 333.6 s). Includes the fixed operator
  determinism test and all 10 receipt tests.
- Serial files (queue, frozen×2, offline, configurable, artifact_identity,
  operator): all green on the final tree (`serialb-*`, plus merge-tree
  `serial-*` for dependency-unaffected files).
- Serial-marked selection (publishers race ×2, inventory timing): 3 passed.
- Union of executed IDs: **1184 of 1184 selectable** (12 policy-deselected:
  network/cuda/operator markers); whole-dir collection confirms the total.
- Quality (`ruff format --check`, `ruff check`, `mypy src`): exit 0. Base-only
  (torch absent): green. Demo slice (200/200 targets): exit 0.
- Combined 9-stage chain (acquire → tokenize → train → resume → evaluate
  complete + limited → shuffled D08 compare → single-copy export → fresh
  reload parity → report): exit 0.
- The only remaining failures anywhere in scope are the two pre-existing
  product refusals below; both reproduce serially and are preserved, not
  weakened: `test_reports` legacy-plan refusal, `test_recipes`
  immutable-snapshot-conflict (now via isolated `--snapshot-dir`; the frozen
  `snapshots/` tree is never overwritten).

## 2. Closeout corrections in this stage

- Recipe test now plans into an isolated `--snapshot-dir` (caller fix; the
  repo's frozen snapshots stay immutable; conflict refusal covered by
  `test_snapshot_reuse_conflicts_and_paths`).
- Reports test builds a current frozen plan via the shared `make_plan` helper;
  the legacy v1 shape moved to `test_submit_refuses_legacy_unfrozen_plan`
  (refusal preserved, not relabeled).
- Acquisition-receipt verification in `verify_evaluation_inputs` (+
  `--acquisition-store-root` through verify-only, in-process, and frozen-worker
  paths): resolves declared IDs against D01/D02 store services, validates
  schema/verified-status, and binds every executed selection by exact bytes or
  `_xlm_acquisition` locator chain (hash/revision/file/population). Ten new
  tests cover valid/missing/corrupt/substituted/tampered/derived/changed
  lineage plus CLI verify-only. Fixture manifests without receipts are
  unchanged. Trust boundary documented in code: attested-selection proof, not
  store re-authentication, rights, or admission certification.
- Operator determinism fix (single authorized object per execution) and
  `comparable_resolved()` containment fix (Windows `\\?\` resolve-form race),
  each with controlled reproduction and maintained regression tests.
- Test policy: focused-first default; fast `-n 16` (this audit used `-n 8`
  with the serial set aside after measuring `-n 16` inventory-gate collapse);
  `serial` marker only for the inventory-timing and concurrent-publishers
  tests. Per-worker XLM_HOME via conftest; runner reports executing vs queued
  honestly. AGENTS.md updated so future sessions stop auto-rerunning full
  audits.

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
  machinery fully green (75 eval tests).
- **Matched-tokenizer research: STILL DEFERRED.** D03 work not closed by this
  stage; no tokenizer-comparison claims are made.
- **Real-source/hardware/official/protected validation: OPERATOR WORK.** No
  live source was contacted, no corpus prepared, no GPU profiling, no official
  benchmark, no protected deployment in this stage.

## 4. First one-source bounded acquisition pilot (implemented commands)

Prerequisites: isolated `XLM_HOME`, the locked CPU env below. Replace
`$source`, `$files`, `$revision` with values from step 0 — nothing here
invents a revision, file size, or Parquet feasibility.

```powershell
$env:XLM_HOME = 'D:\Project\xlm-operator-pilot'
uv sync --offline --locked --extra cpu --extra eval
uv run --offline --locked --extra cpu --extra eval xlm data sources --json
```

**Step 0 (preliminary discovery — operator, explicitly network-authorized).**
Without this, no revision exists to pin and no pilot may proceed:

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data probe --source $source --live --budget-mib 16 --json
```

Record the immutable revision, view schema, and adapter from its output.
Then review license/provenance/benchmark risk and persist the admission
decision:

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data admit --help
uv run --offline --locked --extra cpu --extra eval xlm data audit --json
```

**Step 1 (approval-bound plan).** Author `rows.json` (`filename → [start, stop)`,
JSONL counts nonblank object records) and `limits.json` (all ceilings,
e.g. 16 MiB transfer). Whole-file mode omits `--row-ranges`:

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data plan --source $source --files $files --mode selected_records --row-ranges rows.json --limits limits.json --output pilot-plan.json
```

**Step 2 (fetch / resume).** Unapproved fetch must refuse; approved fetch runs;
interrupt with Ctrl+C and rerun the identical command to resume (same roots,
deadline, and remaining allowance — never delete the journal):

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data fetch --plan pilot-plan.json
uv run --offline --locked --extra cpu --extra eval xlm data fetch --plan pilot-plan.json --pilot-approved
uv run --offline --locked --extra cpu --extra eval xlm data status --plan pilot-plan.json --json
```

**Step 3 (verify + inspect records and receipts).**

```powershell
uv run --offline --locked --extra cpu --extra eval xlm data verify --plan pilot-plan.json --output-dir $raw --json
```

Confirm `records_acquired > 0`, `files[].record_count`, per-row
`_xlm_acquisition` locators (source/revision/file/row/byte offset/selection
hash), and `receipt_id`, `plan_hash`, `eligibility`, plus
`resource_metrics` (`transferred_bytes`, `requests_made`) and the journal at
`scratch/journals/PLAN.progress.json`. Production fetch additionally requires
verified admission with plan-bound `--authorization-hash`; an
admission-reference string alone is refused.

## 5. Preparation and training prerequisites (not launched)

Preparation: verified raw path, tested adapter, authored prepare config with
explicit aggregate `budgets`, isolated home/output roots:

```powershell
uv run --offline --locked --extra cpu --extra eval xlm prepare --config operator-prepare.yaml --plan-only --json
uv run --offline --locked --extra cpu --extra eval xlm prepare --config operator-prepare.yaml --authorize --json
```

Production preparation additionally requires the unavailable production
admission binding — the CLI refuses without it. Training additionally
requires `experiment plan`, `experiment authorize`, and queue submission with
measured profiles (see `demo` for the tiny-CPU smoke shape). None were
launched here and no production/profile guard was removed.

## 6. Remaining blockers and next actions

D06 inventory gate under parallel disk load (300 s proposal unapplied, D06
review); recipe/report product refusals preserved above (owning stages decide
refusal-vs-caller); receipt authenticity inside eval still gap-documented;
deferred tokenizer work; operator-run validation. Next: operator pilot per §4.
