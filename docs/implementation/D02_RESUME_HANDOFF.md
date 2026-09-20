# D02 secured handoff — paused at the user's usage limit

**D02 remains IN PROGRESS, not accepted or fully verified.** On 2026-09-20 the
user reported 5% usage remaining and requested completion or a secured stop.
Astra stopped implementation, retained failures/evidence, and prepared a scoped
local WIP commit on `fix/d02`. No push or integration occurred; main is unchanged.

## Baselines and ownership

- Original ancestor: `20673e3c6a2f80ebaaf4e89705d2951397fff3d0`.
- Shared source baseline: `0a8ff6592b3a893d0bd70f99649fdfcdf7fde310`;
  tree `f93a6ce4a5df89f71d08e873b4cd8df1f4dbacb8`.
- Astra: `D:\Project\xlm`, `fix/d02`; secured commit is the branch tip reported
  in the conversation (`git rev-parse fix/d02`).
- Opus: `D:\Project\xlm-d0405`, `fix/d04-d05`; last observed commit
  `18cc02b3000603c3a7879d1d23bc4508a03bc235`, with active uncommitted edits.
  Do not merge its dirty checkout or derive its changes from Astra's newer tree.
- All 278 previously recorded source/test paths exist in the shared baseline,
  including previously untracked D01/D06/D03 work. Mid-flight D02 code was already
  in Opus's baseline. Full inventory: `evidence/P23-D02/baseline-audit.json`.

## Work secured

Durable acquisition consumption/reservations/occupancy/deadlines; metered transport;
corrupt-cache refusal; whole-file record gates; distinct JSONL/Parquet selections
with original locators; D01 publication; bounded prepare accounting, child output,
owned-process termination and nested fetch reservations. Actual registered public
workflow tests and an operator runbook are present. No parallel execution system.

Product paths: `src/xlm/cli/data_cmd.py`; acquisition `disk`, `fetcher`, `plan`,
`progress`, `records`, `selection`, `verifier`; source `transport` and checksum
checks in `admission`; prepare `bounds`, `config`, `integrity`, `runner`.
Tests: `test_acquisition_bounds.py`, `test_prepare_bounds.py`, and updates to
`test_acquisition_fetcher.py`. Stage docs/evidence are scoped separately.
No dependency pins, frozen contracts, generic CLI registration or evaluator code
were changed by Astra.

## Actual checks

All use uv offline/locked, Windows CPU, Python 3.12.13, actual imports from
`D:\Project\xlm\src`. Exact argv, exits, RSS, source hashes and XML are archived
under `docs/implementation/evidence/P23-D02/after01`, `after02`, `after03`.

| Check | Result |
|---|---|
| Original before observations / refusals | 4 failed + 1 positive passed / 2 failed; untouched |
| after02 focused acquisition/source/prepare/config/CLI | 196 passed, 1 network case deselected; exit 0; unchanged source |
| after02 D01 artifact/ledger | 99 passed; exit 0; unchanged source |
| after02 D03 | 80 passed, 2 failed; exit 1: new guard rejected existing declared external output paths |
| after02 D06 core / callers | Intentionally stopped after incompatibility surfaced; exit 15; incomplete, not passes |
| after03 latest format/lint/mypy | All exit 0; mypy 198 source files |
| after03 short secure check | 11 passed, 1 failed; exit 1; unchanged source |

Latest changes after after02:

1. Explicit external preparation outputs now join the same accounting boundary
   and journal root binding. The new maintained external-output test passes its
   16-byte positive case and 17-byte refusal. **Full D03 workflow rerun pending.**
2. A newly absent journal is not read again without a lock; first mutation reloads
   under lock. Fresh-process reservation and killed-download tests pass.
3. Discarded overflow probes (at most one byte per child pipe) are explicitly
   counted separately as `discarded_child_probe_bytes`; retained logs remain capped.

**Current unresolved failure:**
`test_prepare_bounds.py::test_child_stdout_and_stderr_are_bounded_and_accounted`
observed restored `transferred_bytes == 0`, failing its `> 0` assertion. Inspect
the simultaneous pipe reservation/overflow/termination interleaving and distinguish
retained bytes, committed consumption and pending reservations. Keep the assertion;
do not declare a single passing retry sufficient. Trace: `after03/secure-0.log`
and `secure.xml`. No subsequent fix was attempted under the usage constraint.

## Resume

Read the approved D02 plan, shared contracts, this handoff and `reports/P23-D02.md`.
Use a **new after04 evidence directory**, preserving all earlier files/temp trees.

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'D:\Project\xlm\data\audit\p23-remediation\stage01\after01\.venv'
$env:PYTHONPATH = 'D:\Project\xlm\src'
$env:UV_OFFLINE = '1'
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
uv run --offline --locked --extra cpu --extra eval pytest tests/test_prepare_bounds.py::test_child_stdout_and_stderr_are_bounded_and_accounted -vv -o faulthandler_timeout=0
```

Resolve the failure; rerun both unchanged D03 public workflow cases. Freeze product
and tests, then run the focused suite (expanded argv in after02 results), D01,
complete D06 core, callers, the 82-case D03 group, and quality. Record current
imports/source hashes/resources, archive final evidence and only then update D02's
status and defect/code/test/result map. The actual CLI operator runbook exists in
`D02_ACQUISITION_PILOT.md` but remains IN PROGRESS.

Then coordinate Opus's final scoped commits and integrate in a clean separate
worktree based on completed D02, using the shared baseline for a three-way merge.
Review shared interfaces even without text conflicts. Opus was observed editing
evaluation CLI/harness/suites/evidence, frozen evaluation execution/worker and
report collection, plus `comparison/bootstrap.py`. **D08 is not authorized by
D04/D05 approval:** inspect the bootstrap diff for scope before importing it.
Run both regressions and a combined authored workflow. Report integrated commit,
resolutions and results before updating main. Preserve both branches/evidence.

D07, D08 and deferred D03 tokenizer comparison remain OPEN. Real-source compatibility,
admission and all live/research operations remain operator work. Full platform
acceptance is BLOCKED. The complete offline platform rerun is still deferred.
