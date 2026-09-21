# Direct CLI operator guide (no staged driver)

All commands are ordinary `xlm` invocations, one operation at a time. Nothing
here depends on `scripts/operator_pilot.ps1`, staged state files, or hidden
initialization. Configuration JSON you create yourself (`rows.json`,
`limits.json`, draft YAML edits) is plain input; every identifier passed
forward (behavior hash, plan hash, revision, ticket) comes from the previous
command's own output, which you inspect before proceeding.

Tested tree: `D:\Project\xlm-final-integration` at `5527b4a` plus the
uncommitted CLI-first completion (admission + profile connections).
Environment: existing `.venv-final` (CPython 3.12.13). Shell: PowerShell.
Set once per terminal (a new terminal needs only this line):

```powershell
$env:XLM_HOME = "D:\Project\xlm-operator-home"
```

Run from the repo root so catalog/recipe relative paths resolve.
`uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm ...`
keeps imports pinned to this tree. Steps marked OPERATOR-RUN need your live
call or your hardware; they were not executed while writing this guide.
Steps marked VERIFIED-HERE exited 0 on the tested tree (or are covered by a
named test quoted inline).

## Part 1 — bounded acquisition pilot

### 1.1 Catalog listing (offline, VERIFIED-HERE rc 0)

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data sources --catalog manifests/datasets.catalog.yaml
```

Expected: table of 20 candidates, all `Pending`, plus the FineWeb/FineWeb-Edu
denial note. No network, no side effects.

### 1.2 Bounded live discovery (OPERATOR-RUN, your live call)

Pick `SOURCE`/`VIEW` from the table above. Then:

```powershell
$env:HF_HUB_OFFLINE = "0"; $env:HF_DATASETS_OFFLINE = "0"
uv run --locked --extra cpu --extra eval --no-sync -- xlm data probe --source SOURCE --view VIEW --live --budget-mib 16 --probe-id pilot01 --json > probe.json
$env:HF_HUB_OFFLINE = "1"; $env:HF_DATASETS_OFFLINE = "1"
```

(`UV_OFFLINE=1` stays on; only the two HF flags are relaxed for this call.)
Inspect `probe.json`: require `immutable_revision` present and not
`latest/master/main/head`/empty, plus `probe_fingerprint`,
`observed_files_count`, `declared_license`, `outcome`. Any provider denial,
allowlist mismatch, or unresolved revision stops here — pick another source.
Discovery probing persists evidence (`--publish` default) but approves
nothing.

### 1.3 Admission review (OPERATOR-RUN, your decision, never automatic)

Pilot execution does **not** require this step. Record a decision only after
your own review, passing every value explicitly (do not rely on defaults):

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data admit --source SOURCE --view VIEW --adapter JsonlAdapter --notes "your review notes" --decision approve --license-review approved --benchmark-risk clean
```

Use the adapter class you actually tested (`src/xlm/data/adapters/`).
`--decision reject` records a rejection; a pending license stays a refusal
at fetch time. The decision is bound to the exact probed revision and probe
fingerprint — re-probe means re-admit.

### 1.4 File/range selection (your values, plain JSON you author)

Choose one exact corpus filename from the source's own file listing and a
`[start, stop)` record range (default pilot: 3 records). Write `rows.json`:

```json
{"data.jsonl": [0, 3]}
```

Stay inside pilot ceilings (16 MiB transfer, 100 records, 64 MiB
scratch/output) or declare production limits explicitly (step 1.5).

### 1.5 Unsigned plan — read the behavior hash (offline)

Pilot:

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data plan --source SOURCE --files data.jsonl --mode selected_records --row-ranges rows.json --pilot-approved --output plan-unsigned.json
```

Production (over C13 thresholds or non-pilot): same command with explicit
limits (e.g. `--max-bytes 300000000`) and **without** `--pilot-approved`.
The output prints `Behavior Hash: <hash>`. Copy it exactly.

### 1.6 Authorized plan (offline, test-proven: `test_production_fetch_with_verified_admission`)

Repeat step 1.5 adding `--authorization-hash <hash> --output plan.json`.
Inspect `plan.json`: `is_pilot`, `revision` (must equal the admitted
revision), `authorization.authorization_hash` (must equal the behavior hash).

### 1.7 Fetch (Test-proven, incl. refusals)

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data fetch --plan plan.json
```

Pilot plans additionally require `--pilot-approved` here. Production plans
resolve the stored admission decision for the plan's exact
source/view/revision and pass verified eligibility to the fetcher — there is
no flag to skip this. Expected refusals (each a stop, not a retry):
`no probe evidence`, `no recorded operator admission`,
`is not admitted (...)`, `does not match plan revision`.
Proven: `test_production_fetch_without_admission_is_refused`.

### 1.8 Status and receipt (offline, Test-proven)

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data status --plan plan.json --json
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data verify --plan plan.json --output-dir <raw-dir-from-status> --json
```

`status` reports `records_acquired`. `verify` emits the receipt:
`receipt_id == receipt_<plan_id>`; production receipts carry
`"eligibility": "admission_required"`.

## Part 2 — full pipeline to a measured, authorized, launched run

Preparation stages use the exact commands verified in P09–P12 (unchanged by
this completion; see `docs/implementation/STATUS.md`); identifiers flow the
same way — each command's output is the next command's input.

```powershell
# clean / dedup / split (fixtures shown; substitute your verified raw dir)
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data clean --input <raw> --output-dir data/clean/run01 --preset educational_prose --publish
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data dedup --input data/clean/run01/documents.jsonl --output-dir data/dedup/run01
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data split --input data/dedup/run01/documents.jsonl --dedup-report data/dedup/run01/dedup_report.json --output-dir data/splits/run01
# pool + tokenizer regime + shards + mixture
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data pool build --input data/splits/run01/documents.jsonl --views fixtures/pools/views.yaml --binding fixtures/pools/binding.yaml --split-assignment data/splits/run01/split_assignment.json --output-dir data/pools/run01 --budget-tokens 200000
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data pool verify --manifest data/pools/run01/pool_manifest.json --input data/pools/run01/documents.jsonl
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data freeze --manifest data/pools/run01/pool_manifest.json --vocab-size 512 --fit
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm data tokenize --input <pool-docs> --tokenizer <regime-tokenizer-dir> --output-dir data/shards/run01
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm mixture validate --recipe <your-mixture.yaml>
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm mixture matched-plan --recipe <your-mixture.yaml> --shards data/shards/run01 --basis canonical_bytes --budget 50000 --output data/shards/run01/matched.json
```

### 2.1 Measure a bounded profile (OPERATOR-RUN, your hardware)

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm profile --config recipes/models/tiny.yaml --device cpu --precision fp32 --attention-backend eager --global-batch 1024 --microbatches 1,2,4 --dry-steps 2 --budget-targets 1024 --output-dir data/profiles/run01
```

Substitute your model preset, device (`cuda` on the RTX 4090 box),
precision, and the batch/context the training draft declares — the binding
in step 2.2 refuses any mismatch explicitly. Output: `profile.json` with a
measured `throughput_tokens_per_sec_range` and a feasible
`selected_microbatch_sequences`. No fixture estimate is accepted in its
place (a missing throughput range or infeasible selection is refused).

### 2.2 Resolve the experiment plan against the measured profile (Test-proven)

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm config validate recipes/experiments/baseline_50m.yaml
```

(VERIFIED-HERE rc 0.) Then, with your draft declaring
`resources.profile_artifact` and the backend the profile was measured with
(never `profile_required`):

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm experiment plan <your-draft>.yaml --snapshot-dir snapshots/run01 --profile data/profiles/run01/profile.json --output plan-exp.json
```

Without `--profile` this exits 0 but reports honest blockers
(VERIFIED-HERE: `cost_unestimated`, `missing_*_artifact`, basis
`unmeasured`). With a matching `--profile` the cost basis becomes
`measured_profile` bound to your declared profile artifact
(proven: `test_cli_plan_with_matching_profile_reports_measured`); any model,
device, precision, backend, batch, or context mismatch refuses
(proven: `test_cli_plan_with_mismatched_profile_is_refused` plus 13
unit-level refusal tests in `tests/test_experiment_profile.py`).
Producing the first profile never requires a profile: `xlm profile`
measures from the model preset and calibration flags alone.

### 2.3 Authorize, submit, launch (flags verified from CLI; tickets are yours)

```powershell
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm experiment authorize --plan-hash <plan_hash-from-plan-exp.json> --max-targets <budget> --approver <name> --ticket-id <id> --output ticket.json
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm experiment submit plan-exp.json --snapshot-dir snapshots/run01 --ticket ticket.json
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm queue run --once
uv run --locked --extra cpu --extra eval --no-sync --offline -- xlm queue status
```

`authorize` binds one ticket to one plan hash and explicit limits.
`submit` validates the ticket and enqueues; `queue run` executes locally
until the queue drains (`--once` for a single job). Evaluate/report/export
follow the standard commands (`xlm evaluate`, `xlm report`, `xlm export`);
nothing in this completion changed them.

## Remaining operator prerequisites (not missing implementation)

- A live source choice + probe output (steps 1.2–1.4): your live call.
- An admission review outcome (step 1.3, production only): your decision.
- Real hardware profile artifacts (step 2.1): your measurement.
- An authorization ticket + approver identity (step 2.3): your signature.
- No code, flag, or revision is missing for any step above; `--help` was not
  substituted for any workflow — every command lists its real flags.
