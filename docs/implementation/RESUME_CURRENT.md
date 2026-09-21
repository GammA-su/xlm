# Resume — CLI-first completion (continued session, 2026-09-21)

## Current state (do not reset)

- Branch: `closeout/readiness`; HEAD: `5527b4a` (handoff commit, unchanged).
- Worktree: `D:\Project\xlm-final-integration`; env: `.venv-final`
  (CPython 3.12.13, torch 2.14.0+cpu, pytest 9.1.1, xdist 3.8.0).
- Owned work (interrupted session + this session, uncommitted unless noted):
  - `src/xlm/cli/data_cmd.py` — production fetch resolves verified admission.
  - `src/xlm/cli/experiment_cmd.py` — `experiment plan --profile <file>`.
  - `src/xlm/data/acquisition/plan.py` — `plan_requires_production_admission()`.
  - `src/xlm/data/sources/admission.py` — `resolve_verified_production_admission()`.
  - `src/xlm/experiments/plans.py` — `measured_profile` binding in plan resolve.
  - `src/xlm/training/profile.py` — `resolve_measured_profile()` validation.
  - `tests/test_acquisition_bounds.py`, `tests/test_acquisition_plan.py`
    (extended; one typing nit fixed this session: bare call at plan:312).
  - `tests/test_experiment_profile.py` (new, untracked→to be committed).
  - `docs/implementation/P23-DIRECT-CLI.md` (new direct-CLI guide, this session).
  - `docs/implementation/RESUME_CURRENT.md` (this file).
- No reset/clean/stash performed. Scratch `D:\Project\xlm-guide-home`
  removed. A stray `.venv/` from one probe command was removed earlier.

## Last failed run — cause (no product failures)

- Invocation: `run_parallel.py --workers 1 --partitions
  data/audit/p23-remediation/stage06/final01/partitions-finalserial.json`
  → `missing=[]`, `failed=[final-serial-artifact_identity,
  final-serial-configurable_workflow]`, elapsed 1239.1s, EXIT 1.
- `parallel-summary.json`: both groups `exit_code 99`, `argv []`,
  `scheduler_error: "[Errno 17] File exists: '...-0.log'"`.
  Exclusive-create (`xb`) log collision with stale same-day logs — the two
  groups never launched (0.0 s). Classification: test-runner/setup failure.
- The other four groups ran 15:13–15:26 on the current tree content (all
  source edits 14:38–14:58 precede them) and passed: frozen_execution (14),
  frozen_recovery (1), offline_workflow (4), queue (19), exit 0, JUnit present.
- Completed run preserved in place. No orphaned child processes.

## Verification completed (this session, tied to tested source)

All in `data/audit/p23-remediation/stage06/final02/` (new evidence dir,
git-ignored, preserved on disk):

- Targeted (`-n 0`, `--maxfail=1`): admission scope 9 passed (`tgt-admit`);
  profile binding 20 passed (`tgt-profile`); production-fetch CLI 3 passed
  (`tgt-bounds`).
- Rerun of the two never-launched groups (fresh logs/basetemp/XML/homes):
  `final-serial-artifact_identity` 76 passed; single diagnostic invocation of
  the whole `test_configurable_workflow.py` file was used because the prior
  log shows collection of 2 items with zero tests executing (no failing node
  to select; no XML). Result: 2 passed in 342 s. Preserved as diagnosis +
  regression evidence; not to be repeated for flag changes.
- Connector/caller regression, ONE xdist controller
  (`-n 16 --dist=worksteal --max-worker-restart=0 --maxfail=1`, 110 tests,
  7 files): 110 passed in 33 s (`connreg`).
- Quality: `ruff format --check src tests` exit 0; `ruff check src tests`
  exit 0; `mypy src` (enforced gate) exit 0 over 201 files. `mypy src tests`
  reports 45 test-file errors, all outside the gate; the single one in added
  test code (plan:312) was fixed and re-verified (1 passed).
- Direct-CLI spot checks (isolated home, rc 0): `data sources`,
  `config validate recipes/experiments/baseline_50m.yaml`,
  `experiment plan` without profile (honest blockers, basis `unmeasured`).

## Deliverable status

- A. Direct CLI experience: DONE. `docs/implementation/P23-DIRECT-CLI.md`
  gives one-operation-at-a-time commands with real flags (verified from
  `--help` + executed/test-proven paths), no ps1 driver or hidden state.
  Live probe, admission decision, hardware profile, and ticket remain
  explicit operator actions (not missing implementation).
- B. Production source-admission connection: IMPLEMENTED + VERIFIED
  (fixture scope). Fetch resolves the stored decision for the exact
  source/view/revision, re-evaluates the full gate, passes verified
  eligibility; stale/missing/rejected/mismatched evidence refused.
- C. Measured training-profile connection: IMPLEMENTED + VERIFIED (fixture
  scope). `experiment plan --profile` validates measured identity against
  the plan and binds the cost basis; mismatches refused; first profile
  needs no prior profile (`xlm profile` measures from preset + flags).

## Next specific action

Commit the scoped set (6 src + 3 tests + 2 docs; evidence dirs ignored),
then hand off. Suggested:

```powershell
git add src/xlm/cli/data_cmd.py src/xlm/cli/experiment_cmd.py src/xlm/data/acquisition/plan.py src/xlm/data/sources/admission.py src/xlm/experiments/plans.py src/xlm/training/profile.py tests/test_acquisition_bounds.py tests/test_acquisition_plan.py tests/test_experiment_profile.py docs/implementation/P23-DIRECT-CLI.md docs/implementation/RESUME_CURRENT.md
git commit -m "closeout(cli-first): verified admission + measured-profile connections, direct CLI guide"
```

Genuinely unperformed broad verification (NOT RUN, not passed): full
platform audit beyond the 6-group serial selection + 110-test regression;
live source probe/admission/fetch; hardware profile measurement;
production tokenizer fit; official benchmarks; GPU runs. No background jobs
launched; no session state lives only in chat.
