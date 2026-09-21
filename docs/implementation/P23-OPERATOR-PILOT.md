# First bounded real-data acquisition pilot — operator guide

You run every live operation below personally. Nothing here probes, downloads,
approves, admits, or trains on your behalf: each live stage is a separate
command you launch after reading its output. Tested checkout
`D:\Project\xlm-final-integration` at `08ff6bf`, env `.venv-final`
(CPython 3.12.13); the driver script passed syntax validation and its
offline stages (Env, Catalog) were executed green. No live request was made
while preparing this guide.

## 0. Setup (no network)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Env -HomeDir D:\Project\xlm-operator-pilot
```

What it does: verifies the repo path/branch/commit, runs
`uv sync --offline --locked --extra cpu --extra eval` against the existing
`.venv-final` (no downloads beyond the local cache; fails loudly otherwise),
and checks the interpreter plus `xlm.__file__` points at the tested tree.
Expected: `Env OK`, exit 0. Creates `D:\Project\xlm-operator-pilot\` and
`pilot-state.json` inside it. Refusals: any mismatch stops immediately.

All later stages accept `-HomeDir` (default `D:\Project\xlm-operator-pilot`)
and `-Repo` (default the tested checkout). The script always
`Set-Location`s to the repo and calls `uv run --offline --locked --no-sync`,
so it can never resolve imports or CLIs from another checkout. State persists
in `pilot-state.json`; a new terminal needs no shell variables.

## 1. Catalog listing (offline)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Catalog
```

Runs `xlm data sources --json` (reads the local catalog only), saves
`catalog.json` under your home, and prints `candidate_number`,
`source_id`, `provider` for all 20 candidates. Expected: exit 0, 20 rows.

## 2. Source/view selection (your choice)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Select
```

Prompts for `source_id` (must match the catalog list exactly) and `view`
(default `default`). Saves both to state. No network, no side effects.

## 3. Bounded live discovery (your live call)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Discover
```

Runs `xlm data probe --source <id> --view <view> --live --budget-mib 16
--probe-id pilot01 --json` with `HF_HUB_OFFLINE=0`/`HF_DATASETS_OFFLINE=0`
for this call only (restored to `1` afterward; `UV_OFFLINE=1` stays on).
Budget: at most 16 MiB transferred for discovery (CLI ceiling is 32).
Saves `probe.json`. Then **requires** `immutable_revision` to be present and
not one of `latest/master/main/head`/empty, else stops and prints the keys
actually returned. Expected fields: `immutable_revision` (a commit SHA or
tag), `probe_fingerprint`, `observed_files_count`, `declared_license`,
`resource_metrics`, `unresolved_requirements`, `reason`, `outcome`.
Refusals: provider-denied, allowlist mismatch, unresolved revision (pick
another source), or any non-zero exit — all stop before planning, with the
full log under `<home>\logs\`.

## 4. Admission review (optional, never automatic)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Admit
```

Pilot execution does **not** require production admission. The script asks
whether to record a decision (default no). If yes, you must type review
notes, a benchmark-risk value, a license value, an explicit
approve/reject decision, and the identifier of the adapter you actually
tested (e.g. `JsonlAdapter`, `TextAdapter` — classes in
`src/xlm/data/adapters/`). Nothing is pre-filled or defaulted to an
approval. Calls `data admit` with exactly your values.

## 5. File/range selection and bound files (your values)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Files
```

Shows the probe's `observed_files_count`. Prompts for one exact corpus
filename **from your own review of the source's file listing** (never
invented; the fetch will fail on unknown names) and a `[start, stop)` record
range, defaulting to `[0, 3)` — three records. Writes `rows.json`, e.g.
`{"data.jsonl": [0, 3]}` (JSONL counts nonblank object records), and
`limits.json` with the small pilot ceilings (16 MiB transfer, 32 MiB
decompressed, 100 records, 64 MiB scratch/output, 20 requests, 1 retry,
1 worker, 10 s request timeout, 600 s deadline, ratio 15, 1 MiB record cap,
32 MiB parser cap, 1000 scanned records). Both files are UTF-8 without BOM.
If the selection cannot fit these ceilings, stop here and explain which
limit binds — do not raise limits automatically, substitute sources, or
re-plan repeatedly to replenish allowance.

## 6. Immutable pilot plan

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Plan
```

Runs `data plan --source … --files … --mode selected_records --row-ranges
rows.json --limits limits.json --output pilot-plan.json`. Refuses to
overwrite an existing `pilot-plan.json`. The plan binds
`max(probe.evidence.immutable_revision, catalog.revision)` and errors when
neither exists. Verifies the plan's `revision` equals your probed revision,
then persists `plan_id`, `plan_hash`, `raw_dir`
(`<home>\acquisition\<plan_id>\raw`), and `scratch_dir` to state.

## 7. Explicit confirmation (your approval)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Confirm
```

Displays plan id, source:view@revision, files, ranges, every ceiling, plan
hash, and pilot scope. Type exactly `YES` to approve this plan and limits —
anything else stops before acquisition. This is pilot approval of a displayed
plan, not production admission or certification of source rights.

## 8. Fetch actual corpus records (your live call)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Fetch
```

Runs `data fetch --plan … --output-dir <raw> --scratch-dir <scratch>
--pilot-approved` with live flags enabled for this call only. First fetch
without approval refuses by design. Expected: `Acquisition finished`,
transferred/request/record counts printed.

## 9. Status inspection

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Status
```

Runs `data status --plan … --scratch-dir … --json` and prints
`status/records_acquired/transferred_bytes/requests_made`.

## 10. Resume from a new terminal

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Resume
```

Reads the saved plan and identical roots/journal/allowance/deadline from
state (no reinitialization, no new plan, no fresh account), reruns the
approved fetch, then status. Do not rerun Env/Catalog/Select/Discover/Plan;
do not delete the journal.

## 11. Verification and receipt inspection

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Verify
```

Runs `data verify --plan … --output-dir <raw> --scratch-dir … --json`,
saves `receipt.json`, and prints `receipt_id`, `eligibility`
(`pilot_only` expected), `plan_hash`, and per file
`relative_path/bytes/record_count/sha256`. Run twice to confirm identical
republication; verify the receipt's `plan_hash` matches your plan.

## 12. Bounded record preview

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Preview
```

Parses at most the first three lines of `selected_records.jsonl` and prints
each record's id plus its `_xlm_acquisition` locator (source, revision,
file, row index, byte offset/length, selection hash). Never loads a whole
original to print three records.

## 13. Final summary

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\Project\xlm-final-integration\scripts\operator_pilot.ps1 -Stage Summary
```

Prints source/revision/files/ranges, plan/receipt identities, record
counts, output paths, and pilot-only status. This ends the pilot:
actual corpus records plus their provenance, no training or admission.

## Stop conditions (do not work around these)

- Any non-zero exit: read the preserved `<home>\logs\` file first.
- Cap/compatibility refusals (limits, formats, ranges, validators, corrupt
  originals): reduce scope in a NEW reviewed plan or stop; never edit limits
  in place, never overwrite plans/artifacts, never re-plan to reset spent
  allowance.
- Production fetch needs verified admission with plan-bound authorization,
  which the CLI cannot currently satisfy from a stored decision (see
  readiness report §6) — do not split production work into pilots.
- No Parquet selection is claimed feasible until YOUR probe/verify evidence
  shows exact ranges working; start with JSONL rows.
