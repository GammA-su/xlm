# Independent command and result ledger

All commands ran in `G:\Project\xlm-p35-microbatch-astra`, Windows PowerShell,
existing `G:\Project\xlm-p35-m3\.venv-p35-m3`. No installation or network.
`R` below is the exact argument-preserving wrapper invocation:

```powershell
& docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/env.ps1
```

It executes `uv run --offline --locked --no-sync --extra cuda --extra eval`.
Python 3.12.13, torch 2.14.0+cu126, RTX 4090, driver 596.49. CPU numerical
libraries use one thread; tokenizer parallelism is disabled. No dependency
files changed. This reuses the existing environment, not a fresh lock install.
See `env.ps1` for all environment variables and bounded scratch location.

Every pytest command below ends with
`-p no:cacheprovider --junitxml=docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/NAME.xml`
and redirects stdout/stderr with `*>` to the adjacent `NAME.log`.
Logs retain failed first attempts. No retries without a specific repair.
All data is authored/synthetic; CUDA tests executed, rather than skipping.

## Independent probes and repairs

```text
R python -m pytest docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/test_independent.py -n 0
```

- `independent-before`: exit 1, 15 passed / 2 failed, 13.04 s. The malformed
  evaluator guard is a real defect. The first M4 probe called its fixture helper
  without run ID/seeds: a harness error, NOT a valid adversarial result.
- Corrected that call and added three planned-history probes. Then:

```text
R python -m pytest docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/test_independent.py -k "evidence_rejects or planned_history" -n 0
```

- `defects-before`: exit 1, 4 failed / 16 deselected, 8.88 s. The M4 wrong-counter
  attack reached extraction and was accepted; all three planned histories
  reached the real evaluator fingerprint and exceeded the generic node limit.
- After product repairs, the full independent command: `independent-after`,
  exit 0, 20 passed, 17.81 s. A1 passes by **demonstrating the trust boundary**;
  it is not an attack kill. A2-A8 reach their intended checks and are refused
  or cause the required recovery-only failure.

```text
R python -m pytest tests/test_p35_receipt_history_regressions.py -n 0
```

`regressions`: exit 0, 8 passed, 19.94 s. Permanent tests exercise planned
1B/3B/6B metadata through real guard/boundary functions, canonical parity,
three unreadable receipt mutations and M4 schedule-counter refusal.

## Requested affected selections

```text
R python -m pytest tests/test_p35_hardening_payload.py tests/test_p35_hardening_runtime.py tests/test_p35_readiness_runtime.py tests/test_p35_readiness_recoverability.py tests/test_p35_readiness_planner.py tests/test_p35_readiness_payload.py tests/test_p35_readiness_m4.py tests/test_p35_astra_boundaries.py -m "not serial" -n 8 --dist=worksteal --max-worker-restart=0
```

`hardening`: exit 1, 211 passed / 2 failed, 56.36 s. Both failures were expected
diagnostic-text regressions from placing the new M4 check too early. Restored
the existing validation order, then ran the failing nodes and related cases:

```text
R python -m pytest tests/test_p35_hardening_payload.py::test_evidence_refuses_a_receipt_that_disagrees_with_its_declaration tests/test_p35_hardening_payload.py::test_evidence_refuses_a_history_beyond_the_committed_state docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/test_independent.py::test_evidence_rejects_wrong_lr_schedule_counter -n 0
```

`evidence-repair`: exit 0, 5 passed. The first node expands to three cases.
The 213-case group was not rerun wholesale after this focused repair.

```text
R python -m pytest tests/test_p35_science_pilot.py tests/test_p35_checkpoint_cadence.py tests/test_p35_checkpoint_retention.py tests/test_p35_checkpoint_rescore.py tests/test_p35_eval_training.py tests/test_p35_eval_cadence.py tests/test_p35_eval_cuda.py tests/test_p35_science.py tests/test_checkpoint.py tests/test_p34_commit_boundary.py tests/test_p34_adversarial.py tests/test_prefetch.py tests/test_prefetch_training.py tests/test_trainer_mixture.py tests/test_trainer.py tests/test_trainer_data.py tests/test_p35_m5_runtime.py tests/test_p35_wall_allowance.py -m "not serial" -n 8 --dist=worksteal --max-worker-restart=0
```

`affected`: exit 0, 278 passed, 165.47 s.

```text
R python -m pytest tests/test_p35_m4_stats.py tests/test_p35_m4_manifest.py tests/test_p35_m4_eligibility.py tests/test_p35_m4_promotion.py tests/test_p35_m4_evidence.py tests/test_p35_m5_evidence.py tests/test_p35_m5_order.py tests/test_p35_m5_stream.py tests/test_p35_m5_pilot.py tests/test_comparison.py -n 0
```

`comparison`: exit 1, 305 passed / 1 failed, 27.29 s. Sole failure:
`test_legacy_p17_modules_are_byte_identical_to_certified_m3`. Before classifying
it, `bounds_compatibility.py` proved parent Git blobs equal starting HEAD blobs
and normalized worktree bytes for all five protected files. Raw checkout bytes
have CRLF. See the hashes in `bounds-compatibility.json`. No golden assertion
was changed, bypassed or marked xfail.

```text
R python -m pytest tests/test_p35_m3_toy_flow.py tests/test_trainer_mixture.py tests/test_prefetch.py tests/test_p35_hardening_runtime.py tests/test_p35_astra_boundaries.py -m serial -n 0
```

`serial`: exit 0, 4 passed / 66 deselected, 341.94 s. Authored M3 CUDA recovery,
receipt-fault frozen queue worker and two prior Astra frozen-worker transports.

```text
R python -m pytest tests/test_p35_science_workflow.py -n 0
```

`workflow`: exit 0, 3 passed, 577.58 s. Both attention-policy direct/queue/resume
cases and M2 evaluation cadence completed. Only one xdist controller was active at a time. The
50M measurement and tiny route check ran without other test CUDA jobs.

## Bounded diagnostics

```text
R python docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/bounds_compatibility.py docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/bounds-compatibility.json
R python docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/cuda_routes.py .hardening-scratch/astra-cuda-routes docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/cuda-routes.json
R python docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/cost_50m.py .hardening-scratch/astra-cost50m docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/cost-50m.json
R python docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/inventory.py docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA/inventory.json
```

All exit 0. Bounds are arithmetic, not campaign measurements. CUDA routes:
591 targets, 37.14 s, 1,360,753 scratch bytes, four distinct child PIDs. 50M:
196,608 targets, 20.281 s, 3,685,549 scratch bytes; subprocess watchdog 295 s,
GPU allocator fraction 0.5, one warmup + one receipt-off + one receipt-on
complete global update. No rerun and no useful-model or training-evidence claim.
Meta inventory uses zero additional targets: 92 parameter tensors, no objective
state/auxiliaries. Analytic AdamW moments are labeled as such. At inventory,
evidence occupied 294,293 bytes and scratch 73,983,984 bytes; these are snapshots,
not peak disk use.

## Static checks

Final ruff scope (`S`) is the following exact file/directory list:

```text
src/xlm/data/sampling/update_payload.py src/xlm/training/science.py src/xlm/training/trainer.py src/xlm/training/checkpoint.py src/xlm/training/evaluation.py src/xlm/comparison/science_evidence.py tests/test_p35_hardening_payload.py tests/test_p35_hardening_runtime.py tests/test_p35_m4_evidence.py tests/test_p35_receipt_history_regressions.py docs/implementation/evidence/P35-MICROBATCH-HARDENING-ASTRA
```

```text
R python -m ruff check S
R python -m ruff format --check S
R python -m mypy --follow-imports=silent src/xlm/data/sampling/update_payload.py src/xlm/training/science.py src/xlm/training/trainer.py src/xlm/training/checkpoint.py src/xlm/training/evaluation.py src/xlm/comparison/science_evidence.py
git diff --check
```

All final exits 0 (`ruff-final.log`, `format-final.log`, `mypy.log`), 17 files
formatted, six source files typed. Existing unused `lm_eval` mypy section note.
An initial format check exited 1 on mixed newline formatting and one assertion;
`ruff format` corrected those four files, exit 0. Earlier scratch-script lint
found import ordering, long strings and loop closure binding; the later inventory
script also needed one long generator line wrapped. All were corrected before
final checks. No source semantics changed during formatting.

NOT RUN: complete repository offline acceptance/release suite, live-source
tests, real 32M pilot, formal B8/B16/B32 runs, research campaign. No skipped test
is counted as a pass. This is the requested focused acceptance scope.

Next command (read-only):
`git -C G:\Project\xlm-p35-microbatch-astra log --oneline e7644a3..HEAD`.

## Local closeout

`git add` selected only the three repaired source modules and new permanent
test module, then:

```text
git commit -m "fix(p35): bound receipt history hashing and fail closed on invalid evidence"
```

Exit 0, commit `8e5f6c57520337531c7d2c1c581b03cfdb72b4c9`. The subsequent docs
commit contains the report, STATUS, science guide and this evidence directory.
No push/merge. Final read-only checks: `git diff --check`,
`git diff --exit-code e7644a3 -- pyproject.toml uv.lock .python-version recipes/experiments/draft_science_v1_pilot_32m.yaml`
both exit 0. The first staged `git diff --cached --check` exited 1 on trailing
whitespace in pytest diagnostics. Trimmed terminal spaces in logs/JUnit and
rechecked. PowerShell log files were decoded to UTF-8; 42 evidence
files occupied 249,625 bytes before this final ledger addition. All JUnit XML
files report zero errors and zero skips. The known P17 test remains a failure,
and repaired first-round failures remain recorded.
