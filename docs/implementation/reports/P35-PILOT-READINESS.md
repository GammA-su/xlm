# P35 pilot-readiness hardening (not Milestone 6)

Branch `research/p35-pilot-readiness`, created exactly from the certified M5
commit `8ccb4bc764ae50861e7d6872f71a2efc545484a0` (`research/p35-m5-certified`,
tag `p35-m5-certified`). Contract: [P35 scientific contract](P35-SCIENTIFIC-CONTRACT.md)
§E/§K/§L/§W; [M2](P35-M2.md), [M3](P35-M3.md) §17.8, [M4](P35-M4.md) §22/§25.13,
[M5](P35-M5.md).

This pass removes avoidable ambiguity before the user prepares real Mix-01
artifacts and runs the first 32M B8 pilot. It does not train, prepare data,
build real order manifests, choose margins or launch anything.

## 1. Pre-change audit (written before any product edit)

Read from the code at `8ccb4bc`. Paths are under `src/xlm/`.

### 1.1 The boundary sequence that decides recoverability

At every committed boundary, `Trainer._evaluate_boundary`
(`training/trainer.py`) runs three phases in a fixed order:

1. `EvaluationController.record_crossings` marks every evaluation event whose
   threshold the committed count reached as **due**. It binds the in-memory
   model-state digest and the computation identity.
2. `CheckpointController.at_boundary` publishes **one** checkpoint if a
   *checkpoint* threshold was first crossed here. That checkpoint's
   `science.json` shows the events as due.
3. `EvaluationController.run_pending` scores each due event of *this* committed
   count once per process. A process-local set, `_attempted`, prevents a second
   attempt. A failed attempt publishes a FAILED outcome artifact. **It raises
   nothing**, unless the state guard sees live-state mutation
   (`RecoveryRequiredError`).

`train()` runs the boundary once at start (C = 0 or the resumed C). The queue's
`execute_plan_run` calls `train_step()` directly, and `train_step()` runs the
boundary before and after every update. After the loop, at the exact budget,
`recover_failed_evaluations()` calls `EvaluationController.recover_from_retained`.
That rescoring skips events at the *current* C ("the live state still exists:
`run_pending` owns it"). It handles only LM tiers (`RESCORABLE_TIERS = (quick_lm,
full_lm)`), and only from a published, still-present checkpoint whose record
has the event's model-state digest, C and step (`locate_exact_checkpoint`). It
makes one attempt per call and respects the per-lineage bound
`MAX_ATTEMPTS_PER_EVENT = 3`.

No code path stops training because an evaluation failed. `train()` reports
`termination_reason = "completed"` and `execute_plan_run` returns a
**SUCCEEDED** job whose summary carries `evaluation.complete = false`.

Pilot geometry (`pilot_32m`, global batch 65,536, M3 projection): quick
`0/1M/4M/8M/16M/32M`, full `0/32M`, search `0/32M`; checkpoints `0/8M/16M/32M`
(all milestones, no rolling recovery).

| Event | First-crossing C | Update | Checkpoint at that boundary? |
|---|---:|---:|---|
| quick/full/search@0 | 0 | 0 | yes, `checkpoint@0` (milestone) |
| quick_lm@1M | 1,048,576 | 16 | **no** |
| quick_lm@4M | 4,063,232 | 62 | **no** |
| quick_lm@8M | 8,060,928 | 123 | yes, `checkpoint@8000000` (milestone) |
| quick_lm@16M | 16,056,320 | 245 | yes, `checkpoint@16000000` (milestone) |
| quick/full/search@32M | 32,000,000 | 489 | yes, `checkpoint@32000000` (endpoint milestone) |

### 1.2 Reachable retry routes for a 32M pilot

- **M2 live retry** (`retry_on_restart_v1`). A new process at the *same*
  committed count re-runs a non-complete due event. This happens after a queue
  resume or an at-budget resume. Within one process there is no second live
  attempt.
- **M3 exact rescore.** LM tiers only; at the exact budget, in the same process
  (train and queue). The same rescore is also available offline:
  `rescore_from_run_checkpoint(<final checkpoint>, <event>)`.
- **Queue resume.** Only a *lost runner* (stale heartbeat) is requeued, within
  `--max-retries`. A worker exception is terminal `FAILED`, and a finished job
  is `SUCCEEDED`. Neither is ever re-run.
- **CLI `xlm resume`.** Refused for this budget: `cli/train_cmd.py:98-101`
  enforces `committed <= budget <= 200_000` (the C13 smoke cap) *before* the
  at-budget branch. **The at-budget CLI route is therefore unreachable for the
  32M pilot.**

### 1.3 Pre-change recoverability matrix (traced, not inferred)

Columns:

- **State kept?** Is the exact model state of that boundary retained after
  training moves on?
- **M2 retry now?** Can a failed attempt be retried at the same state in the
  same process?
- **M3 rescore?** Can it be rescored from an exact checkpoint?
- **Resume retry?** Does a resume retry it?
- **Training advances?**
- **Permanently incomplete?** Can the run end permanently evaluation-incomplete
  after a *clean* evaluation failure (an exception inside the evaluator, no
  crash)?

| # | Event fails | State kept? | M2 retry now? | M3 rescore? | Resume retry? | Training advances? | Permanently incomplete? |
|---|---|---|---|---|---|---|---|
| 1 | quick_lm@0 | yes: `checkpoint@0` milestone (plus evaluation-dependency pin) | no (`_attempted`) | yes, at budget (1 attempt), then offline up to the bound of 3 | only if the process dies *before* update 1. A clean failure does not stop training. | **yes** | only if every rescore fails too |
| 2 | full_lm@0 | same as 1 | no | yes, same as 1 | same as 1 | **yes** | same as 1 |
| 3 | search_benchmark@0 | the weights of `checkpoint@0` are retained, pinned forever by the unresolved dependency | no | **no**: `tier_not_rescorable` | no, once update 1 has committed | **yes** | **YES**, silently in the job state (the job is SUCCEEDED) |
| 4 | quick_lm@1M | **no** checkpoint at C = 1,048,576 | no | **no**: `checkpoint_missing` | only a crash *before* the next checkpoint forces a replay that re-crosses 1M. A clean failure does not. | **yes** | **YES** |
| 5 | quick_lm@4M | **no** checkpoint at C = 4,063,232 | no | **no**: `checkpoint_missing` | as 4 | **yes** | **YES** |
| 6 | quick_lm@8M | yes: `checkpoint@8000000` milestone, same boundary | no | yes, at budget (1 attempt) | resume from that checkpoint before the next update | **yes** | only if every rescore fails |
| 7 | quick_lm@16M | yes: `checkpoint@16000000` milestone | no | yes, at budget | as 6 | **yes** | only if every rescore fails |
| 8a | quick/full@32M (endpoint) | yes: endpoint milestone, published before scoring | no | in-process **no**: current-C events are skipped. Offline `rescore_from_run_checkpoint` **yes**. | queue: only a lost runner. CLI: **refused** by the 200k cap. | n/a (budget reached) | only if the operator never runs the offline rescore |
| 8b | search@32M (endpoint) | yes: endpoint milestone | no | **no**: `tier_not_rescorable`, online or offline | as 8a | n/a | **YES**. A clean failure leaves a SUCCEEDED job that can never be re-scored. |

"Crash" rows use the M3 §5 semantics: every published checkpoint shows the
boundary's events as due, so a lost runner requeued by the queue resumes from
the newest checkpoint and re-runs owed events live. A **clean evaluator
failure** is the case that matters here, because it never stops training.

**Conclusions.**

1. `search_benchmark@0` fails open: training starts anyway, and the event can
   never be completed.
2. `quick_lm@1M`/`@4M` have no retained exact state.
3. At the 32M endpoint, `search_benchmark@32M` has no reachable retry route
   for a pilot-sized budget. The CLI route is capped, the queue never re-runs
   a finished job, and there is no checkpoint scorer for benchmarks.
4. `quick/full@0` and `@8M/@16M` are recoverable only through the at-budget
   rescore. A C = 0 failure still lets training start.
5. Nothing makes an evaluation-incomplete pilot fail. The queue records
   SUCCEEDED.

### 1.4 Capacity and retention facts

- `artifacts/retention.py`: roles `milestone`, `recovery`, `superseded`. Kept:
  - pinned milestones;
  - the two newest recovery states;
  - the last good state (milestone/recovery);
  - protected references;
  - any state whose digest an unresolved evaluation event names.
  Retention runs after each verified publication and after the at-budget
  rescore pass.
- `experiments/science_pilot.py::capacity_plan` walks the planned checkpoint
  events. Before each publication it counts
  `(pinned so far + planned recovery so far + KEEP_RECOVERY) × size` retained
  bytes, plus `2 × size` transient: one serialization directory and one store
  staging copy. For the pilot that is 5P + 2P = 7P before the 32M publication.
  It has no notion of checkpoints held only for evaluation.

### 1.5 Microbatch fairness evidence (for Part H)

- **Global update.** `MixtureBatcher.next_step_microbatches` builds an ordered
  list of packed windows (sequences). `_to_microbatches` then only **chunks**
  that list into `microbatch_sequences`-sized groups.
- **Per sequence, the model and objective see:**
  - `input_ids`, `labels`, `loss_mask` and `position_ids`;
  - `segment_ids` (used for `isolated_document` packing);
  - `metadata["input_attention_mask"]` (`token != pad`);
  - `metadata["packing_mode"]`.
- **Per target, as provenance:**
  - `source_attribution` (per position);
  - `metadata["target_doc_ids"]`, `["target_lineage_ids"]`,
    `["target_byte_spans"]` and `["target_token_offsets"]`.
- **Microbatch-level metadata depends on the partition:** `valid_targets`,
  `sequence_count` and `is_partial`.
- **P34 producer.** The child runs the same `MixtureBatcher`. `_encode` flattens
  the microbatches into one `[N, T]` NumPy array per field, plus a
  `CompactProvenance` string table. `PreparedUpdate.to_microbatches()` hands the
  trainer zero-copy CPU tensors. Those carry **no** source/doc/lineage
  provenance (`source_attribution=None`): provenance stays on
  `PrefetchingBatcher.pending_update`.
- **Existing digests:**
  - `PreparedUpdate.content_digest` includes `rows`, the per-microbatch sizes,
    so it depends on grouping;
  - the M4 evidence uses the chained per-target trace (`trace_digest`: source,
    doc, lineage, offset, label, byte span, epoch);
  - the M4 evidence also uses the update-boundaries digest.

  **No existing digest binds input ids, the loss mask, positions, segments or
  the attention mask per global update.**
- **Trainer seam.** `_train_update` receives the microbatches as CPU objects
  (lists or CPU tensors). It then moves each to the device. The science-v1 LR
  receipt is appended only after `batcher.commit()`, so failed or in-doubt
  updates never publish one. That is the natural seam for a committed payload
  receipt: hash the CPU representation before transfer, and record it next to
  the LR receipt.

### 1.6 The 2 GiB frozen-input cap (for Part O)

`training/inputs.py`:

- `_shard(path)`: the running total of the five shard files ≤ 2 GiB, and each
  JSON file ≤ 8 MiB.
- `resolve_training_input`, mixture branch: the aggregate of `tokens.bin`,
  `offsets.jsonl`, `shard_manifest.json` and `shard_counters.json` over the
  components ≤ 2 GiB.

The aggregate check runs **inside** the per-component loop, *after* `_shard`
has run `verify_integrity()` (a full SHA-256 of the payload) and after
`_validate_training_index` (a full index scan) for that component. An
oversized mixture therefore pays for hashing earlier components before it
fails. The error then names neither the source nor the sizes.

In the pilot planner the failure appears as
`execution_resolution: ValueError: aggregate frozen shard inputs exceed 2 GiB`.
Because resolution failed, the `data` section with `input_shard_bytes` never
runs. File sizes are cheaply available by `stat()` before any hashing.

## 2. Verdict

**NEEDS LOCAL CERTIFICATION.** Every scientific decision is implemented. The
following are VERIFIED in the cloud, on authored fixtures and pure code paths
(95 new tests plus regressions):

- barrier decisions;
- the recovery-checkpoint plan and its retention;
- the plan table and fail-closed validation;
- the worst-case capacity bound;
- the input-cap diagnostic;
- the payload digest and chain;
- M4 v2 eligibility.

Ten of ten adversarial mutants are killed. The Trainer, queue and checkpoint
integration is written and has torch tests, but is **NOT RUN** here, because the
container has no torch (§11). The real 32M pilot stays **DRAFT / NONEXECUTABLE**.
No data, training, pilot, margin choice, push to a certified branch or merge
happened.

## 3. Environment

Linux cloud container. As authorized for M4/M5, one scratch venv outside the
repository:

```
UV_PROJECT_ENVIRONMENT=<scratch>/venv UV_PYTHON=/usr/bin/python3.12 uv sync --locked
```

Exit 0. Base + dev only: exact `uv.lock` wheels from PyPI; no torch, no
`cpu`/`cuda`/`eval` extra, no models, datasets or Hugging Face. **Python
3.12.3**, not the pinned 3.12.13 (same deviation as M4/M5). numpy 2.5.3,
pytest 9.1.1, ruff 0.16.8, mypy 2.3.1. `pyproject.toml`, `uv.lock` and
`.python-version` are unchanged. Test runs set `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`
and `TOKENIZERS_PARALLELISM=false`.

## 4. Initial C = 0 evaluation barrier (`required_initial_evaluation_barrier_v1`)

**Policy.** `training.evaluation_recoverability` is a new optional science-v1
block. It is data only, every field is required, and it is omitted when absent,
so historical config, envelope and plan bytes are unchanged:

```yaml
evaluation_recoverability:
  version: xlm-evaluation-recoverability-v1
  required_events: all_planned_events_v1
  initial_barrier: required_initial_evaluation_barrier_v1   # or disabled
  recovery_checkpoints: evaluation_recovery_checkpoints_v1  # or disabled
  endpoint: endpoint_live_retry_then_fail_stop_v1           # or disabled
```

The block requires `training.checkpoint_cadence` and `evaluation.science`. Its
identity is bound into the checkpoint plan identity, so a changed policy refuses
ordinary resume. It is also in the resolved config, and therefore in the
envelope and plan hash.

**Semantics** (`evaluation/recoverability.py`, `training/evaluation.py::settle_required`,
`training/trainer.py`):

1. The M2 boundary order is unchanged. At C = 0: crossings recorded, then the
   `checkpoint@0` milestone published, then evaluations scored.
2. **Retries.** Each failed required C = 0 event is retried on the live C = 0
   state. The retry is an *ordinary M2 attempt*:
   - the next attempt number on the same crossing lineage;
   - a digest-verified replica;
   - the state guard;
   - bounded by the unchanged `MAX_ATTEMPTS_PER_EVENT = 3`;
   - canonical receipt by the unchanged `first_complete_attempt_v1`.

   The receipt's `retry_policy` field is unchanged. Only the trigger is new:
   now, instead of after a restart.
3. **Barrier check.** Update 1 may begin only if every required C = 0 event has
   a canonical COMPLETE receipt. The check reads the ledger. It runs after the
   boundary phase and again at the start of every `_train_step`. An event never
   recorded as due also blocks, so skipping the boundary cannot open the
   barrier.
4. **Exhaustion.** `InitialEvaluationBarrierError` is raised. The run stays at
   C = 0 with no LR receipt. `checkpoint@0` is durable and still owes the event.
   The queue job ends `FAILED`. Each further `train_step` raises again.
5. **PARTIAL.** Coverage-partial is not accepted. The §K "partial" label means a
   *frozen small search subset*, and the M2 canonical rule accepts only COMPLETE.
   A PARTIAL, FAILED, INTERRUPTED or unscored event blocks (fail closed).
6. **No policy.** Without the block, or with `initial_barrier: disabled`,
   behavior is exactly M2/M3.

**search@0 behavior:** a failure is retried at C = 0, at most 3 attempts in
all. If it completes, training proceeds. Otherwise the run fails at C = 0 and
no update happens.

## 5. Evaluation-recovery checkpoints (`evaluation_recovery_checkpoints_v1`)

**Role.** `CheckpointRole.EVALUATION_RECOVERY = "evaluation_recovery"`. It is
never a milestone, and never counts toward the latest two rolling recovery
states.

**Placement.** Every required evaluation threshold that no milestone or rolling
recovery event covers gets one such checkpoint event at the **same threshold**.
Two consequences follow:

- The same threshold and the same first-crossing rule put it on exactly the
  evaluation's boundary. No update is split.
- One boundary still publishes one checkpoint. A shared boundary takes the
  strongest role: milestone, then recovery, then evaluation_recovery.

**Record.** The checkpoint record carries the existing M3 identity: the planned
threshold and the actual C, step, model-state digest, data-cursor digest,
artifact id `{run}_ckpt-t{T}-a{n}`, and the verified manifest hash. It shows the
boundary's evaluation as due.

**Pilot.** `checkpoint@1000000` lands at C = 1,048,576 (update 16, overshoot
48,576). `checkpoint@4000000` lands at C = 4,063,232 (update 62). The update
arithmetic is unchanged: 488 × 65,536 + 18,432 = 32,000,000 over 489 updates.
Milestones 0/8M/16M/32M are unchanged.

**Behavior by boundary:**

- **quick_lm@1M / @4M.** A failed live attempt leaves its recovery checkpoint
  pinned. At the budget, the unchanged M3 rescore (`retained_exact_checkpoint_rescore_v1`,
  located by digest, C and step; never the latest) completes it as attempt 2,
  and attempt 1 is kept. Retention then releases the state.

  A crash after publication and before scoring resumes from that checkpoint and
  scores the owed event live. The ledger marks the checkpoint event handled, so
  it is never republished, and an identical durable publication is adopted.
- **8M / 16M.** Unchanged. Milestone checkpoints at the same boundary; M3
  at-budget rescore.

**Retention** (`artifacts/retention.py`). An `evaluation_recovery` state is kept
while an unresolved evaluation event names its digest (reason
`evaluation_dependency:…`), or while it is the last good state. Otherwise it is
retired with reason `evaluation_recovery_released`, through the unchanged
verified-retirement path, only after a newer verified publication. It is
therefore never retained forever after success, and never retired while a
dependent attempt is unresolved.

## 6. Endpoint (`endpoint_live_retry_then_fail_stop_v1`)

**Gap found by the audit (§1.3 row 8b).** A clean `search_benchmark@32M`
failure had no reachable exact-state route:

- the CLI at-budget resume is refused by the 200k smoke cap;
- the queue never re-runs a SUCCEEDED job;
- no checkpoint scorer exists for benchmarks.

The minimal fix below adds no generic search-checkpoint rescorer.

1. At the exact budget, failed required events are retried on the **live
   endpoint state** with the same bounded M2 attempts as at C = 0. The live
   state is digest-verified against the crossing, the same state the retained
   endpoint milestone holds.
2. LM endpoint events keep the offline exact route
   `rescore_from_run_checkpoint(<endpoint checkpoint>, …)`. No approximate or
   latest-state substitution is possible (unchanged M3 checks).
3. After the at-budget rescore pass, any required event still not COMPLETE
   raises `RequiredEvaluationIncompleteError` (`EVALUATION_INCOMPLETE`). This
   happens in `train()`, the queue (`execute_plan_run`) and the at-budget CLI
   resume. It runs *after* the endpoint checkpoint is durable. The job ends
   `FAILED`, never SUCCEEDED.

A search-benchmark endpoint failure is therefore recoverable while the state is
live, and fail-stop afterwards. It is never silently incomplete.

## 7. Plan recoverability table and validation

The planner, review, DRAFT and validation all call `pilot_run_plans`, which uses
the same derivation as the trainer's components. The review's
`schedules.recoverability` holds the policy, one row per event and a
`table_digest`. The new preflight section `recoverability` blocks:

- `recoverability_policy_required`: the §W pilot without the exact v1 policy;
- `required_event_unrecoverable`: any required event whose route is `NONE`, for
  **all** pilot contracts, authored fixtures included.

The checked-in §W draft now declares the v1 policy. It stays
`draft_nonexecutable`, with every artifact, pin and capacity input null. Pilot
table (asserted by `test_draft_review_table_has_a_route_for_every_required_event`):

| Event | Threshold → projected C | Required | State retention | Retry mechanism | Recoverability | Blocking policy |
|---|---|---|---|---|---|---|
| quick/full/search@0 | 0 → 0 | yes | initial milestone | initial barrier live retry at C=0 (+ at-budget rescore for LM) | BARRIER | no update until complete; fail-stop at C=0 |
| quick@1M | 1,000,000 → 1,048,576 | yes | evaluation-recovery checkpoint | at-budget exact-checkpoint rescore | EXACT_CHECKPOINT | fail-stop EVALUATION_INCOMPLETE at budget |
| quick@4M | 4,000,000 → 4,063,232 | yes | evaluation-recovery checkpoint | at-budget exact-checkpoint rescore | EXACT_CHECKPOINT | same |
| quick@8M | 8,000,000 → 8,060,928 | yes | milestone | at-budget exact-checkpoint rescore | EXACT_CHECKPOINT | same |
| quick@16M | 16,000,000 → 16,056,320 | yes | milestone | at-budget exact-checkpoint rescore | EXACT_CHECKPOINT | same |
| quick/full@32M | 32,000,000 → 32,000,000 | yes | endpoint milestone | endpoint live retry + offline exact rescore | EXACT_CHECKPOINT | same |
| search@32M | 32,000,000 → 32,000,000 | yes | endpoint milestone | endpoint live retry | LIVE_ENDPOINT_THEN_FAIL_STOP | same |

Without the policy, the same code reproduces the audited gaps.
`search@0`, `quick@1M`, `quick@4M` and `search@32M` read `NONE`, and each
`disabled` part loses exactly its events (tested). The policy is in the plan
hash: base, a changed part and an absent block give three distinct
`ExecutablePlan` hashes (tested).

## 8. Capacity

`capacity_plan` walks the checkpoint plan *with* the recovery events. Before
each publication it counts, from the measured checkpoint size P:

- every milestone so far;
- every planned rolling recovery state so far;
- **every evaluation-recovery state so far, as if its evaluation had failed**
  (it would stay pinned until the at-budget rescore);
- two unplanned terminal recovery states (`KEEP_RECOVERY`);

plus `2 × P` transient (the serialization directory and the ArtifactStore
staging copy). Receipts, evidence, cache, the launcher log bound, job JSON and
the safety margin come on top.

Recovery states are not assumed to be released early. Retention cannot prove
release while an evaluation may fail, and only a success releases them. The
bound is valid in the failure case and not a permanent-retention assumption
(success releases them, which only lowers the real peak).

Pilot peak: before the 32M publication, t0, t8M and t16M, plus t1M and t4M, plus
2 unplanned states = 7P retained, plus 2P transient = **9P** (M3: 7P). At
P = 600 MB that is 5.4 GB, which fits 8 GiB. At P = 1 GiB it is 9 GiB and
**blocks** (`new_output_exceeds_limit`); without the recovery states it would
have been 7 GiB. The size must still be measured: a null size gives
`capacity_unresolved`. No size is invented.

## 9. Global-update payload receipt (`global_update_payload_digest_v1`)

**Schema** (`data/sampling/update_payload.py`). Per update, a SHA-256 over:

1. **Header** (canonical JSON): version, sequence count N, context T, packing
   mode, valid targets (= non-zero loss-mask count, checked equal to the
   trainer's N), and the ordered list of bound fields.
2. **Integer fields**, each as little-endian int64 `[N, T]` with dtype/shape
   framing:
   - `input_ids`, `labels`, `loss_mask`, `position_ids`, `segment_ids`;
   - `attention_mask` (the boolean meaning of `input_attention_mask`: the
     trainer casts it to bool);
   - `byte_spans` `[N, T, 2]` and `token_offsets`.
3. **String fields**: `source_attribution`, `doc_ids`, `lineage_ids`. Each is a
   first-occurrence string table over the flattened global field, plus int64
   codes. A batcher that attributes whole sequences (`TrainingBatcher`) gives
   `[N, 1]`.

**Canonicalization.** All microbatches are concatenated in the batcher's global
sequence order before hashing. Microbatch sizes, per-microbatch counts,
`is_partial` and container types never enter the digest. Sequences are never
sorted. A field the batcher does not provide is absent and listed; nothing is
invented. "Lineage identity" is the existing per-target `lineage_id`. "Document
identity" is the per-target `doc_id` plus the canonical byte span. "Position
semantics" are the explicit `position_ids`; absent means implicit, and absence
is itself bound.

**Where it is hashed** (Part J). The CPU representation that exists before
device transfer:

- **P34 producer:** the `PreparedUpdate` NumPy arrays and compact provenance on
  `pending_update`.
- **Synchronous batcher:** the list-mode or CPU-tensor microbatches.

A device tensor is refused, never synchronized. The producer's own `_encode`
output and the synchronous batches give the same digest (tested). This is
unlike the P34 `content_digest`, which binds the grouping `rows`.

**Measured overhead** (SYNTHETIC 128 × 512 arrays, CPU-only container, no GPU):

| Path | Median | Share of one planning-rate B8 update (65,536 / 45,679 s = 1.435 s) |
|---|---|---|
| Producer | 21.0 ms (20 repetitions) | ≈ 1.5 % |
| Synchronous list | 52.3 ms (5 repetitions) | ≈ 3.6 % |

This is planning arithmetic, not a GPU measurement. The receipt is opt-in and
is not enabled for the §W pilot.

## 10. Chain, commit and resume (`xlm-update-payload-chain-v1`)

**Declaration.** `training.update_payload_receipt: global_update_payload_digest_v1`
(opt-in, omitted when absent).

**Staging and commit.** The trainer computes the digest before any compute and
*stages* `(step, committed_before, valid_targets, payload)`. It *commits* only
right after `batcher.commit()`, next to the M1 LR receipt. It *discards* the
staged receipt on:

- any exception in the update (including an in-doubt optimizer failure);
- a scaler skip.

`stage` refuses anything but the next contiguous `(step, C)`, so a replayed or
skipped update can never be appended.

**Chain digest.** `chain_i = identity_digest({version, previous, step,
committed_before, valid_targets, payload_digest})`, from a **run-independent
genesis**. B8, B16 and B32 runs of the same data therefore share one head.

**science.json.** `science.json.update_payloads` holds version, chain version,
genesis, columns, rows and head.

**Resume and forks.** The chain is restored after every link is re-derived and
the rows match the LR receipts update by update. This happens before any model,
optimizer or data state is restored (`CheckpointManager` step 2b). A presence
mismatch is refused both ways, so a chain cannot start mid-run or be dropped.
Forks adopt the chain, like the LR receipts. Pre-readiness checkpoints are
unchanged and never rewritten.

## 11. M4: `microbatch_grouping_v2` (tracks v3, evidence v3)

- **Tracks v3.** `update_payload_receipt` and `update_payload_chain_digest` are
  RECORDED on every track unless a track requires them.
  - `microbatch_grouping_v1` keeps its historical meaning: it records them only
    (tested eligible with mismatching or absent receipts).
  - `microbatch_grouping_v2` varies only `microbatch_sequences`. It keeps every
    v1 MUST_MATCH: global batch, update boundaries, trace, per-source exposure,
    M5 order id and membership, model/init, optimizer/LR/schedule,
    precision/runtime, evaluation contract. It adds both receipt fields as
    MUST_MATCH.
  - A missing receipt is UNKNOWN, which makes the comparison INELIGIBLE, and a
    mismatch is INELIGIBLE with a field diff. Final weights are
    RECORDED_MAY_DIFFER.
- **Evidence v3.** Re-derives the chain from `science.json` and requires it to
  match the LR receipts. An inconsistent chain is **refused**, not read as
  unknown. v1/v2 records stay verifiable and read the new fields as `None`; a
  v1/v2 record carrying them is refused.
- **Draft.** The B8/B16/B32 comparison draft now uses `microbatch_grouping_v2`
  and lists both invariants. It stays DRAFT; practical and NI margins remain
  null (Part Q).

## 12. 2 GiB frozen-input cap (not raised)

- **Constants.** The caps are now named constants in the torch-free
  `data/input_limits.py`, with unchanged values: 2 GiB per shard, 2 GiB
  aggregate, 8 MiB JSON. `training/inputs.py` uses them. A test asserts the
  values and that no second literal remains.
- **Planner diagnostic.** Before resolution hashes anything, the planner
  `stat()`s the bound shard files. The `input_bytes` preflight section reports
  per-source file bytes, the shard and aggregate totals, the caps, the aggregate
  excess, and which sources exceed a cap. An excess is a
  `frozen_input_cap_exceeded` blocker naming the sources and bytes.
- **Tests.** Tests use authored sizes and tiny real files. No large or sparse
  file was created.

When the user binds real Mix-01 shards, the review shows at once whether the
cap is the blocker.

## 13. Synthetic flow (AUTHORED/SYNTHETIC; not the pilot; no training)

Command:

```
python docs/implementation/evidence/P35-READINESS/synthetic_flow.py docs/implementation/evidence/P35-READINESS/synthetic_flow.json
```

Exit 0; 0.80 s; peak RSS 83.6 MiB; output ~6 KB.

- **Part A.** Authored-fixture plan: budget 4,096, batch 256, quick 0/1000/4096,
  search 0/4096, milestones 0/4096.
  - The derived evaluation-recovery event is `checkpoint@1000`, due at the
    natural crossing C = 1,024.
  - An injected `search_benchmark@0` failure closes the barrier; a retry at
    C = 0 opens it.
  - An injected `quick_lm@1000` failure keeps t1000 (`evaluation_dependency:quick_lm@1000`).
  - A simulated exact rescore gives attempts `[(1, failed), (2, complete)]`
    with canonical attempt 2, and t1000 is `evaluation_recovery_released`.
  - No required event is left without a route.
- **Part B.** Real authored shards and the real `MixtureBatcher`. Two
  consecutive global updates are partitioned 6/3/2 microbatches (B8/B16/B32) and
  also encoded by the P34 producer's `_encode`: one digest per update and one
  chain head.
- **Part C.** The overhead in §9.

## 14. Tests, mutations and static checks (cloud)

| Command | Exit | Result |
|---|---|---|
| `$PY -m pytest tests/test_p35_readiness_recoverability.py -n 0` | 0 | 33 passed |
| `$PY -m pytest tests/test_p35_readiness_planner.py -n 0` | 0 | 14 passed |
| `$PY -m pytest tests/test_p35_readiness_payload.py -n 0` | 0 | 37 passed |
| `$PY -m pytest tests/test_p35_readiness_m4.py -n 0` | 0 | 11 passed |
| `$PY -m pytest tests/test_p35_readiness_runtime.py -n 0` | 0 | **1 skipped (module, torch absent): 17 nodes NOT RUN** |
| `$PY -m pytest tests/test_p35_readiness_m4.py tests/test_p35_m4_{stats,manifest,eligibility,promotion,evidence}.py tests/test_p35_m5_evidence.py -n 4` (after the v3 changes) | 0 | 217 passed |
| Focused regression: the 35 pre-existing test files that import a changed module and collect without torch on both trees, `-n 0`, HEAD vs a clean `git archive 8ccb4bc` export | 1 / 1 | HEAD 562 passed, 19 failed, 21 errors; base 561 passed, 20 failed, 21 errors. **No HEAD-only failure**; one base failure now passes. All are torch/CPU-extra environment failures (`missing offline dependencies: ['torch']`, `installed torch accelerator does not match`). |
| 6 files newly collectable because `xlm.evaluation` exports are lazy (`test_comparison.py`, `test_eval_{coverage,inputs,suites}.py`, `test_p35_eval_{cadence,firewall}.py`) | 1 | 148 passed, 5 skipped (lm-eval/torch), 1 failed: imports torch through `p35_eval_support` (environmental) |
| `docs/implementation/evidence/P35-READINESS/mutate_readiness.py mutations.json` | 0 | control 21/21; **10/10 killed**, 27.7 s |
| `ruff check` / `ruff format --check`, the 30 changed Python files + evidence scripts | 0 / 0 | clean |
| `mypy --follow-imports=silent` on the 21 changed source files | 1 | 5 `unused "type: ignore"` in `training/{trainer,checkpoint}.py`, torch absent; **identical on the base export** |

Mutation method (runner SHA-256 `8fa3a0d7…` and every edit in
[mutations.json](../evidence/P35-READINESS/mutations.json)):

- a fresh `git archive HEAD` export per mutant (tested HEAD `8b7e6ac`);
- each anchor matches exactly once, and the mutated file must `py_compile`;
- `xlm` must import from the export;
- the listed nodes run with JUnit;
- a mutant counts as KILLED only if pytest exits 1, every listed node FAILED on
  an assertion, and nothing errored.

| # | Mutation | Killing node(s) | Observed |
|---|---|---|---|
| 1 | search@0 failure still allows update 1: the barrier skips the search tier | `test_any_failed_c0_event_closes_the_barrier[search_benchmark@0]`, `…_also_block[partial]`, `test_uncrossed_c0_events_block…` | `[] == ['search_benchmark@0: failed']`; `2 == 3` |
| 2 | quick@1M has no recovery checkpoint: the quick tier is excluded | `test_pilot_recovery_checkpoints_are_exactly_1m_and_4m`, pilot table, planner draft table | `() == (1000000, 4000000)`; `required_event_unrecoverable` blockers |
| 3 | an unresolved evaluation-recovery checkpoint is retired | `test_recovery_checkpoint_is_pinned_while_its_evaluation_is_unresolved` | `'t1m' in {…}` fails |
| 4 | capacity omits evaluation-recovery checkpoints | `test_capacity_counts_worst_case…`, `test_insufficient_capacity…` | `3000000000 == 7 * 600000000`; no `new_output_exceeds_limit` |
| 5 | the digest includes microbatch boundaries | B8/B16/B32, producer and chain tests | 3 distinct digests / heads |
| 6 | the digest ignores labels | `…changes_the_digest[labels]` | equal digests |
| 7 | the digest ignores masks and positions | `…[position_ids]`, `…[attention_mask]`, `test_a_moved_loss_mask_bit…` | equal digests |
| 8 | resume resets the chain | `test_resume_restores_the_chain_and_continues_exactly` | `[] == [[1, 0, 16, …]]` |
| 9 | microbatch v2 ignores a chain mismatch | `test_a_payload_chain_mismatch…`, `test_a_run_without_the_receipt…` | `assert not True` (eligible) |
| 10 | a required event without a route still plans | `test_missing_policy_blocks…`, `test_authored_fixtures_also_fail_closed…[recovery_checkpoints-lost1]` | `[] == ['quick_lm@1000000', …]` |

After the mutation round, the only source change was restoring the original CRLF line endings of `cli/train_cmd.py`, `experiments/queue.py`, `training/checkpoint.py` and `training/trainer.py`, which the edits had normalized to LF. `git diff --ignore-cr-at-eol` is empty, and none of these files is a mutation target.

A first runner attempt used a node id without its parametrization suffix. The
control run caught it (pytest exit 4, "no tests ran") before any mutant ran.
The id was corrected; no mutant verdict was affected.

**Test changes to earlier milestones** (intended new behavior, not weakening):

- `test_p35_science_pilot.py`:
  - pilot checkpoint projection gains the 1M/4M `evaluation_recovery` rows;
  - `rescorable_from_planned_checkpoint` is now true for 1M/4M;
  - capacity 7P → 9P;
  - the exact preflight-status dict gains `recoverability` and `input_bytes`
    (both `VERIFIED`).
- `test_p35_m5_evidence.py`: the evidence version string v2 → v3.
- `test_p35_m4_evidence.py::publish_run` gains an additive `update_payloads`
  argument.

`test_p35_science_pilot.py` is torch-bound: **its updated assertions are NOT
RUN here.**

## 15. NOT RUN in the cloud (needs torch / the local CUDA environment)

| Item | Test |
|---|---|
| Trainer barrier: quick/full/search@0 failure → no update after 3 attempts; retry success → training; legacy unchanged | `test_p35_readiness_runtime.py` (C=0 tests) |
| Recovery checkpoint published at the natural crossing (16/16/16 updates), failed eval keeps it, crash before scoring resumes exactly, no republication, release after success | `test_p35_readiness_runtime.py` (recovery tests) |
| Endpoint live retry, fail-stop `EVALUATION_INCOMPLETE`, disabled policy = M3 semantics | `test_p35_readiness_runtime.py` (endpoint tests) |
| Trainer payload chain: committed-only, failed optimizer update discarded, resume = uninterrupted, presence mismatch refused before restore, grouping-invariant head | `test_p35_readiness_runtime.py` (payload tests) |
| Queue / frozen / CLI paths with the policy and the receipt; process-producer route through the Trainer | `test_p35_science_workflow.py`, `test_p35_m3_toy_flow.py`, `test_p35_m5_runtime.py` (authored pilot drafts now inherit the policy) |
| Updated M3 pilot expectations; M1–M3 regressions touching changed trainer/checkpoint/evaluation code | `test_p35_science_pilot.py`, `test_p35_checkpoint_{cadence,retention,rescore}.py`, `test_p35_eval_training.py`, `test_p35_science.py`, `test_checkpoint.py`, `test_p34_commit_boundary.py`, `test_prefetch*.py`, `test_trainer_mixture.py` |
| Real Mix-01 input sizes against the cap; real checkpoint size; real payload overhead on GPU | none: needs real artifacts (user) |

## 16. Exact local certification commands

On the Windows CUDA environment, from the repository root, on branch
`research/p35-pilot-readiness`. Set the threads variables as in the handoff.
`R` = `uv run --offline --locked --extra cuda --extra eval`.

```powershell
R python -m pytest tests/test_p35_readiness_runtime.py tests/test_p35_readiness_recoverability.py tests/test_p35_readiness_planner.py tests/test_p35_readiness_payload.py tests/test_p35_readiness_m4.py -n 0 -rA
R python -m pytest tests/test_p35_science_pilot.py tests/test_p35_checkpoint_cadence.py tests/test_p35_checkpoint_retention.py tests/test_p35_checkpoint_rescore.py tests/test_p35_eval_training.py tests/test_p35_eval_cadence.py tests/test_p35_science.py tests/test_checkpoint.py tests/test_p34_commit_boundary.py tests/test_prefetch.py tests/test_prefetch_training.py tests/test_trainer_mixture.py tests/test_p35_m5_runtime.py -m "not serial" -n 8 --dist=worksteal --max-worker-restart=0
R python -m pytest tests/test_p35_m4_stats.py tests/test_p35_m4_manifest.py tests/test_p35_m4_eligibility.py tests/test_p35_m4_promotion.py tests/test_p35_m4_evidence.py tests/test_p35_m5_evidence.py tests/test_comparison.py -n 0
R python -m pytest tests/test_p35_m3_toy_flow.py tests/test_trainer_mixture.py tests/test_prefetch.py -m serial -n 0
R python -m pytest tests/test_p35_science_workflow.py -n 0
R python docs/implementation/evidence/P35-READINESS/synthetic_flow.py <scratch>/synthetic_flow_local.json
```

Expected: all pass, apart from the documented pre-existing Windows items (the
P17 golden SHA on a CRLF checkout, the cp932 README decode). Then send the next
prompt (§19).

## 17. Requirement ledger

| # | Acceptance criterion | Status | Evidence |
|---|---|---|---|
| 1 | No required C=0 event can fail and silently allow training | IMPLEMENTED; decision VERIFIED (pure, mutant 1); Trainer NEEDS LOCAL CERTIFICATION | §4 |
| 2 | Required 1M/4M quick events have exact recoverable state | IMPLEMENTED; plan and retention VERIFIED (mutants 2, 3); publication NEEDS LOCAL CERTIFICATION | §5 |
| 3 | No optimizer update split for alignment | VERIFIED (plan projection; runtime asserts 16/16/16 locally) | §5 |
| 4 | Recovery checkpoints retained only while needed | VERIFIED (pure retention; mutant 3) | §5 |
| 5 | Capacity includes worst-case recovery states | VERIFIED (9P; mutant 4) | §8 |
| 6 | Every required pilot event has a recovery/fail-stop path | VERIFIED (plan table; mutant 10) | §7 |
| 7 | Recoverability policy is plan-hash bound | VERIFIED | §7 |
| 8 | Endpoint events have a valid exact-state retry route | IMPLEMENTED; decision VERIFIED; Trainer/queue NEEDS LOCAL CERTIFICATION | §6 |
| 9 | Fairness digest independent of microbatch grouping | VERIFIED (real batcher, producer encoder; mutant 5) | §9 |
| 10 | Digest changes on relevant payload changes | VERIFIED (12 fields, moved mask, order; mutants 6, 7) | §9 |
| 11 | Fairness receipts survive resume exactly | VERIFIED (chain); Trainer resume NEEDS LOCAL CERTIFICATION (mutant 8) | §10 |
| 12 | In-doubt/failed updates never become evidence | VERIFIED (stage/discard); Trainer NEEDS LOCAL CERTIFICATION | §10 |
| 13 | M4 historical track semantics remain historical | VERIFIED | §11 |
| 14 | Future microbatch track requires strengthened evidence | VERIFIED (mutant 9) | §11 |
| 15 | Real pilot remains nonexecutable | VERIFIED | §7 |
| 16 | 2 GiB cap not silently raised | VERIFIED | §12 |
| 17 | High-risk mutations killed | VERIFIED 10/10 | §14 |
| 18 | Focused tests/static checks green (available environment) | VERIFIED (environmental failures identical on base) | §14 |
| 19 | No real data/training/pilot | VERIFIED | none performed |
| — | Practical/NI margins | OUT OF SCOPE (left null; user decision, §P) | — |
| — | Generic search-benchmark checkpoint rescorer | OUT OF SCOPE (not needed: endpoint live retry + fail-stop) | §6 |

## 18. Risks and open questions

1. **Runtime integration is unexecuted here** (§15). In particular:
   - the barrier's interaction with the queue worker's terminal FAILED state;
   - the endpoint fail-stop after `save_terminal_checkpoint("final")`;
   - the authored M3/M5 pilot flows, which now inherit the policy (the M3
     phase B toy flow gains `checkpoint@8192` as an evaluation-recovery state).
2. **Exhausted C = 0 retries end the job FAILED.** A new submission is a new run
   (a new job id); the old run's attempts stay as history. This is intended
   fail-stop, not an automatic resubmission.
3. **Bounded live retries add evaluation time on failure only**: up to 2 extra
   attempts per failing required event at C = 0 or the endpoint. Count this
   against the 60-minute allowance.
4. **Endpoint search failures have no checkpoint rescorer.** After three live
   failures the pilot is `EVALUATION_INCOMPLETE`; re-running it is a new run.
   A search-checkpoint rescorer stays out of scope unless the user wants it.
5. **Payload overhead**: about 21 ms per update on the producer path and 52 ms on
   the synchronous list path (§9, CPU container). The receipt is opt-in; measure
   it on the 4090 before the B8/B16/B32 study.
6. **1B plans.** `full_1b` search at 256M is a milestone without a benchmark
   scorer. It would read `NONE` under this table. A 1B plan needs its own policy
   decision; no 1B plan is generated here.
7. **`xlm.evaluation` package exports are lazy.** `from xlm.evaluation import X`
   behaves the same, but first attribute access now imports the submodule.
8. **The 2 GiB cap** remains a potential real-pilot blocker. The review now
   names the sources and bytes. Raising it stays a separate, reviewed decision.

## 19. Files, commits and next step

- **New source:**
  - `evaluation/recoverability.py`;
  - `data/sampling/update_payload.py`;
  - `data/input_limits.py`.
- **Modified source:**
  - `artifacts/retention.py`, `cli/train_cmd.py`;
  - `comparison/science_{evidence,tracks}.py`, `config/{schemas,science}.py`;
  - `evaluation/{__init__,cadence}.py`;
  - `experiments/{execution,queue,science_pilot}.py`;
  - `training/{checkpoint,components,evaluation,inputs,milestones,science,trainer}.py`.
- **Recipes:** the §W pilot draft (the policy block); the B8/B16/B32 comparison
  draft (v2 track).
- **Tests:**
  - new: `test_p35_readiness_{recoverability,planner,payload,m4,runtime}.py`;
  - updated: `test_p35_science_pilot.py`, `test_p35_m5_evidence.py`,
    `test_p35_m4_evidence.py`.
- **Evidence:** `evidence/P35-READINESS/{synthetic_flow.py,synthetic_flow.json,mutate_readiness.py,mutations.json}`.
- **Docs:** this report, `docs/science-v1.md`, `docs/implementation/STATUS.md`.

Commits:

1. `d31eb51` audit;
2. `6dfe8df` barrier + recovery checkpoints + endpoint fail-stop;
3. `9ccccd2` plan table/capacity/input diagnostic;
4. `7077774` payload receipt + chain;
5. `bedb591` microbatch v2;
6. `286db1e` synthetic flow;
7. `8b7e6ac` mutation runner;
8. `d1e6f87` report, docs and evidence;
9. `0f5876e` CRLF line-ending restoration;
10. this closing record.

Final cloud confirmation on the final HEAD: the 5 readiness files, the M4/M5
pure suites, `test_recipes.py` and `test_comparison.py`, run with
`-n 8 --dist=worksteal`, exit 0: **410 passed, 1 skipped** (the torch-only
runtime module).

Next prompt, after local certification: **"Certify the P35 pilot-readiness pass
locally on the CUDA environment: run PILOT-READINESS §16 on branch
research/p35-pilot-readiness, record exact results in a closeout section, fix
only defects found, and do not prepare real data, train, or launch the pilot."**
The user then binds the real Mix-01 artifacts, and reads the `recoverability`
and `input_bytes` review sections before authorizing.
