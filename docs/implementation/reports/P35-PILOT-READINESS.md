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
