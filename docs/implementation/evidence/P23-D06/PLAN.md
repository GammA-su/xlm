# Stage 2 proposal: D06 frozen execution and authentic provenance

Status: **AWAITING SEPARATE USER APPROVAL**. D01 implementation and bounded offline
checks have passed; [D01 report](../../../../docs/implementation/reports/P23-D01.md)
records the exact scope. No D06 product implementation has been changed. Overall
production acceptance remains **BLOCKED**. This proposal preserves the shared XLM
research/evaluation contracts and the existing trainer, artifact store and queue.

## Adversarial reproduction against the current code

`test_d06_before.py` exercises the real `ExperimentQueue.run_job` and existing
Trainer with private authored queue fixtures. Each fixture contains a bounded
copy of the actual implementation, plugin descriptors and Python/uv pins. The
test recomputes the captured code and dependency identities. These are diagnostic
queue fixtures, not production-resolved/admitted research plans or operator
authorizations; the existing smoke policy applies. No executor substitute is used.

Executed from `D:\Project\xlm` in D01's fresh offline CPU/evaluation environment,
with `PYTHONPATH=D:\Project\xlm\src`, all uv/HF offline flags, and OMP/MKL threads 1:

```powershell
uv run --offline --locked --extra cpu --extra eval pytest data/audit/p23-remediation/stage02/test_d06_before.py -q --basetemp=data/audit/p23-remediation/stage02/before-tmp --junitxml=data/audit/p23-remediation/stage02/before.xml
```

Actual result: **3 failed, 1 passed; exit 1; 21.49 s**. Preserve this source,
`before.log`, `before.xml`, and each `before-tmp/*/observed.json`; future runs use
new directories. Three jobs actually executed eight valid targets each (24 total),
with two-layer, width-32, vocabulary-64 CPU models; the edited-live-tree case did
not train. No network, GPU, real data, official evaluation or research run occurred.

| Case and required assertion | Observed current defect |
|---|---|
| Enqueue A, edit live Trainer to authored B; intact A should still execute | BLOCKED because queue verifies the live tree, although captured A verifies intact |
| Alter captured Trainer; refuse before training | SUCCEEDED for eight targets; captured bytes are not what queue verifies/executes |
| Observe actual Trainer origin and saved producer/dependency/plan identities | Actual `train_step` origin is `D:\Project\xlm\src\xlm\training\trainer.py`, outside selected snapshot; checkpoint receipts retain placeholder producer/dependency/generated plan labels |
| Positive control: authored queue really executes eight targets and publishes an integrity-valid checkpoint | PASSED; this proves the diagnostic reaches real execution, not frozen reproducibility |

The origin assertion fails before the placeholder assertions; raw checkpoint
manifests are separately retained in `observed.json`. The before-test's process
profiler observes today's in-process executor. After repair, maintained tests must
observe the actual child execution and an authored A/B behavioral marker; merely
changing labels or trusting a printed snapshot path cannot make them pass.

## Focused implementation plan

1. **Validate the frozen execution envelope before admission and launch.** Extend
   the existing plan/snapshot/authorization structures with versioned execution
   evidence where necessary, using sidecars rather than silently amending frozen
   research/configuration schemas. Define canonical hashes for all executable
   configuration, code bytes, dependency lock, selected CPU/CUDA/eval extras,
   runtime policy, component versions, data/tokenizer manifests and seed policy.
   Bind envelope to plan/job and existing authorization limits; recompute hashes
   when reading persisted files instead of trusting IDs. Reject independently
   changed plan, job, ticket, snapshot, input or dependency identities. Defaults
   must be resolved before hashing, never silently supplied later. Keep unresolved
   inputs, production approvals and unsupported configuration blocked.

2. **Make the existing snapshot an executable immutable input.** Validate canonical
   paths, collisions, file set, byte totals, hashes and recomputed snapshot hash.
   Stream bounded copies/hashes into attempt-owned staging; refuse replacement of
   an existing conflicting/incomplete capture. Keep secret and unrelated-file
   exclusions. Explicitly enumerate the allowed executable source/config/plugin
   closure (including the workspace's existing untracked implementation), and
   refuse missing required code instead of borrowing it from a live package.
   Check captured `code/`, not the mutable planning checkout. A live A-to-B edit
   after admission neither changes nor invalidates the selected intact A capture.

3. **Launch one controlled fresh process through the existing queue.** The existing
   local queue keeps job ownership, leases, cancellation, heartbeats, retry policy
   and ledger transitions. A small worker entry point in the existing experiments
   package reconstructs the verified envelope and invokes the existing executor/
   Trainer. It does not introduce another training algorithm, evaluator, scheduler
   or artifact store. Use a selected uv-locked offline runtime; put temporary
   environment/work files under the existing job workspace, outside captured code.
   Suppress user-site, editable checkout, inherited `PYTHONPATH`, executable `.pth`
   hooks and current-directory package overrides. Verify observed XLM module origins
   against captured paths/hashes before computation. A conflicting live installation
   must be irrelevant or cause refusal, never silently supply an implementation.

4. **Bind and record the actual environment, not only its advertised lock.** Keep
   Python 3.12.13 and the existing tested dependency graph. Record selected extras,
   interpreter identity, installed distribution identities/origins and a bounded
   installed-file fingerprint policy. Validate these against the frozen execution
   environment before launch/resume; reject altered/missing dependencies. Distinguish
   normalized reproducibility fields from recorded hardware/runtime observations.
   No implicit uv update, dependency download, accelerator substitution, or runtime
   installation from an unreviewed source is allowed. Offline cache absence blocks
   a requested environment. Document trusted launcher/bootstrap assumptions; this
   work does not claim hostile same-user OS isolation.

5. **Carry execution evidence through the current checkpoint/resume/evaluation
   interfaces.** Replace `P05_TRAINER_CHECKPOINT`, `TORCH_P05`, truncated generated
   plan-hash labels and equivalent claimed provenance with the verified execution
   envelope and observed identities. Pass that context explicitly through existing
   construction paths; avoid global experiment state. Direct bounded smoke entry
   points use the same identity validation and existing execution machinery without
   granting production authority. Preserve current working component combinations;
   mixture/plugin execution expansion belongs to D03. Checkpoint/runtime sidecars,
   artifact manifests, run records and evaluation receipts must agree on code,
   lock/environment, plan, data, tokenizer, component versions and runtime policy.

6. **Resume only a compatible frozen run.** Restore the selected snapshot and
   original envelope with model/objective/optimizer/schedule/RNG/data state in a
   fresh process. Any changed behavior requires an explicit new plan/fork and the
   applicable authority. Preserve legacy checkpoint originals and inspection;
   missing execution evidence stays unresolved. A checksum-valid legacy checkpoint
   cannot silently become a proven frozen continuation. D01 continues to enforce
   declared request consistency and conflicts; it must not be weakened to make
   changed provenance fit a historical immutable ID.

7. **Preserve operational guards and bounded evidence.** Snapshot capture, worker
   output, results and runtime setup have explicit byte/file/time/disk limits and
   owned cleanup. Worker termination, cancellation, crash/retry and lease release
   preserve existing durable evidence and exact target accounting. Revalidate the
   selected capture/runtime at the execution boundary, including mutations between
   planning and launch. Do not automatically authorize a changed plan. Avoid any
   D02 acquisition, D03 mixture, D04/D05 scoring, D08 comparison or D07 export repair.

Expected product files: `src/xlm/experiments/{snapshot,plans,authorization,queue}.py`
and a minimal worker/bootstrap in that package; the existing training checkpoint/
runtime/input and train/resume CLI interfaces where execution context must pass;
existing evaluation receipt construction where provenance propagates. Existing
artifact infrastructure remains authoritative. No frozen contract amendment is
proposed; if a required semantic change cannot fit it, identify that issue for
separate review instead of changing the contract silently.

## Offline stage acceptance

All tests use authored fixtures and tiny CPU training, with fresh temporary/evidence
directories. Keep the before failures. Promote adversarial obligations into the
maintained repository tests and retain actual commands, exits, source/import
origins, environment, resource measurements and resulting artifacts.

- Public plan/submit/run of A followed by a live source/config edit B runs A in a
  fresh process. Assert an observable authored behavior and actual executed origin,
  not just checkpoint metadata. A new plan for B has a distinct identity.
- Tampered captured code, omitted/added required module, changed manifest/hash,
  traversal/link/collision or mutation between admission and launch is rejected
  before training and without repairing the original snapshot.
- A conflicting live package, editable installation, user-site module, `.pth`
  hook or `PYTHONPATH` injection cannot override captured code. Observe worker PID,
  import paths and bytes. Verify correct CPU extra and reject a changed dependency.
- Independently mutate plan/config, job, authorization/limits, lock/environment,
  seed/runtime policy, tokenizer and data identities: each appropriate guard
  refuses for the intended reason, before computation or unsafe data access.
- Tiny uninterrupted versus interrupted/fresh-process resumed runs agree on exact
  targets, next data IDs, numerical/computational state and all execution identities.
  Incompatible resume refuses; explicit fork records new plan/authority and lineage.
- A checkpoint and an existing diagnostic evaluation receipt carry real matching
  producer/dependency/plan identities without placeholders; D01 same-ID conflicts
  and legacy checksum-only behavior remain enforced.
- Cancellation, failed worker, bounded output, lease cleanup, retry limits and
  duplicate-job prevention regressions continue to pass. No fallback to live code
  is allowed as recovery from a failed frozen launch.
- Focused snapshot/plan/authorization/queue/checkpoint/runtime/CLI/evaluation-
  provenance and D01 regressions, format, lint and types pass via uv offline with
  locked dependencies. Use synthetic fixtures only; no full-size profile/training.

## Completion and separate validation

Keep D06 **IN PROGRESS** until its required checks pass. Then update STATUS, the
P23/D06 report and defect-to-code-to-test-to-result map. Label implementation and
offline verification separately from **NOT RUN — OPERATOR ACTION REQUIRED**:
Linux/CUDA platform validation, actual protected service deployment and any
real campaign execution. An offline identity result cannot approve production.

After D06 results, present D03's own focused plan/adversarial reproduction for
separate approval. The one complete offline acceptance rerun remains scheduled
after all separately approved remediation stages; none is launched by this proposal.

**Approval requested for Stage 2 D06 implementation and bounded offline verification
under this plan only.** The prior D01 approval explicitly excludes D06.
