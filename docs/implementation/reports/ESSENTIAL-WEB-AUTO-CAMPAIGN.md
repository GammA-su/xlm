# Essential-Web automatic campaign runner — 2026-09-30

**READY FOR AUTOMATED ESSENTIAL-WEB ACQUISITION**

This adds an outer runner over the proven fast campaign. It runs Batch 3, 4, 5, …
while the campaign says CONTINUE. It stops before another batch when every
first-pass target is met, and immediately when a batch needs a human.

Nothing below the runner changed: transport, local processing, B-normal selector,
adapters, ledgers, dashboard, recovery dispatcher, Windows publication lock,
artifact layout, mixture, quotas, stop targets, gates, disk bounds and the C05
obligation are byte-identical. No batch ran, nothing was fetched and nothing was
pushed. Commands and outputs are in
[`evidence/ESSENTIAL-WEB-AUTO-CAMPAIGN/COMMANDS.md`](../evidence/ESSENTIAL-WEB-AUTO-CAMPAIGN/COMMANDS.md).

## Authoritative state (recomputed read-only from the receipts)

| | Value |
|---|---|
| Campaign | `8e42ba31b0ef9fcbae1079cebf88d2449b88a9b37ec52b68c7a408eb2546bb8c` |
| Complete batches | 3 (0, 1, 2); next batch **3**, `CLEAN_NOT_STARTED` |
| Sealed files | 96 |
| Rows scanned | 7,956,430 |
| Durable footprint | 32,970,294,061 bytes (cap 429,496,729,600) |
| Science | 475,576,675 canonical bytes = 118,894,168.75 est. tokens / 660M (18.0%) — TOP_UP |
| Practical | 1,203,463,447 B = 300,865,861.75 / 660M (45.6%) — TOP_UP |
| Prose | 3,360,566,812 B = 840,141,703 / 330M (254.6%) — SUFFICIENT |
| Decision | CONTINUE; C05 NOT RUN; training not permitted |

Measured batch execution time (all runs of the batch, pauses excluded): 243.8 s,
265.0 s and 221.2 s. Science yield per batch: 40.17M, 39.87M and 38.86M tokens.

## Architecture

- **`src/xlm/data/sources/essential_web_campaign_runner.py`** holds pure, offline
  logic:
  - envelope identity, construction and self-digest check;
  - bounds, and structured differences between two identities;
  - the classification rules for a started batch;
  - the rolling projection, the campaign header and the completion banner;
  - the append-only JSONL log, fsynced per record.
- **`scripts/essential_web_campaign.py`** is the orchestrator. It imports the
  unchanged `essential_web_fast.py` as a module and calls its functions directly:
  - `load_campaign`, `campaign_state` and `evaluate_gate`;
  - `mint_plan`, `batch_record`, `cmd_plan` and `cmd_authorize`;
  - `check_admission`, `prepare_units` and `restart_plan`;
  - the recovery module's `resume_state`, `unit_scope` and `code_identity`.

  Only the network stage runs as a child process: `essential_web_fast.py run
  --batch N`, on the operator's terminal, so the existing per-batch dashboard is
  unchanged. Nothing parses console text. Decisions come from the returned
  structures, the batch record, receipts and event logs, and the child's exit
  code.
- **`scripts/operator_essential_web_campaign.ps1`** provides the stages
  `Status`, `PrepareAuto` and `RunAuto`. It applies the same root checks as the
  fast driver.

Per iteration, `run-auto` does the following:

1. Reload the campaign; any science or code drift refuses.
2. Compare the full identity with the envelope.
3. Recompute the state and the stop decision; if the targets are met, print the
   banner and stop.
4. Take the next batch from the complete-batch count and require it to lie
   inside the envelope.
5. Classify that batch.
6. Check the automation guards.
7. Run the campaign gate; it must answer `RUN`.
8. Re-derive the child's plan and compare it with the envelope.
9. Run `plan` if the batch has no record yet, and compare the record with the
   envelope.
10. Run `authorize` with the bound child digest.
11. Run the C04 admission check.
12. Run the batch process.
13. Reload the campaign, require complete batches = N + 1, and re-hash every
    unit of the batch with `resume_state`.
14. Log the cumulative sufficiency.

## Bounded auto-authorization: possible, and how it is bound

The per-batch contract did allow a one-time envelope without weakening anything.
A batch's authorization digest is `digest(campaign, batch, membership, plan_hash,
limits)`. `plan_hash` is the behavioural hash of a plan that `xlm data plan`
mints offline and deterministically. `prepare-auto` therefore mints every child's
dry plan in a temporary directory outside the operator store. It computes each
authorization digest exactly as `plan` does and binds the list.

Proof on the real store: the same derivation reproduces the recorded
authorization digests of batches 0, 1 and 2 byte for byte, and `prepare-auto`
refuses if it ever does not.

The envelope (`essential_web_auto_campaign_authorization`, version 1) binds:

- **Identity**: the campaign digest; source id, repository and revision; the
  selector id and its freeze, evaluator and policy digests; the adapter id,
  canonicalization and frozen plus running adapter/selector code hashes.
- **Code**: the frozen transport code, plus the running code of transport, local
  processing, monitor, progress, recovery, the fast driver and the runner itself.
- **Recovery**: the recovery amendment in force (Batch 0/f00026 only) and the
  digest of each code-compatibility record.
- **Campaign contract**: the inventory identity, batch size and benchmark plan
  digest; the first-pass targets; the per-file and per-batch limits, scratch,
  concurrency, disk reserve and footprint cap; the campaign ceiling; C05 status
  and obligation.
- **Range**: start batch = the authoritative next batch, maximum batches (1..20),
  the exclusive end, and whether the campaign ceiling shortened it.
- **Children**: for every batch in range, its membership digest, plan hash,
  selection hash, limits digest, first inventory rank and authorization digest.
- **Operator**: the automation guards and the operator identity.

`run-auto --authorize <digest>` loads the envelope written by `prepare-auto`. It
refuses in any of these cases:

- the file's self-digest or the requested digest differs;
- the operator differs;
- any identity field differs from what the running code and campaign produce now;
- a child's re-derivation differs;
- a batch's written record differs from its bound child.

It records a write-once authorization file and then authorizes each batch
through the normal `authorize` path, using only the bound child digest. The
batch's `authorization.json` names the operator and `auto <envelope prefix>`.

It never approves:

- a recovery amendment;
- an oversized-record exception or any changed record bound;
- a changed source identity or code-compatibility contract;
- a top-up after the targets are met (the runner never passes `--top-up-reason`).

A new amendment changes the identity, so the envelope stops working.

## Range and ceilings

- Default: from the next batch (3), at most 20 batches, so batches 3–22. The
  campaign ceiling is 33.
- One envelope may never exceed 20 batches, and there is no "run forever" mode.
- When the envelope runs out while targets are insufficient, the runner stops
  with `ENVELOPE_EXHAUSTED` (exit 5); a new `PrepareAuto` is required.
- At the campaign ceiling it says that a new reviewed campaign version is
  required.

## Stop conditions

| Outcome | Exit | Causes |
|---|---:|---|
| `FIRST_PASS_COMPLETE` | 0 | every component SUFFICIENT under the existing stop decision |
| `REFUSED` | 1 | campaign, source, revision, selector, adapter, inventory, limits or code identity drift; a campaign that refuses to load (science or code drift); wrong or altered envelope; operator mismatch; child derivation or record mismatch; any gate result other than RUN (disk reserve, footprint cap, order, ceiling, benchmark, superseded campaign); C04 admission failure |
| `ENVELOPE_EXHAUSTED` | 5 | the next batch is outside the envelope, or the campaign ceiling is reached |
| `HUMAN_REVIEW_REQUIRED` | 6 | classification (below); nonzero batch exit; the batch process raising; batch not complete or not re-verifiable after exit 0 |
| `AUTOMATION_GUARD` | 7 | `G:` free below the guard, `C:` free below the guard, scratch occupancy above the campaign scratch cap |
| `INTERRUPTED` | 130 | Ctrl+C |

Everything the executor already refuses is surfaced as a nonzero batch exit, and
then as human review:

- a file above 512 MiB, or a record above 8 MiB;
- the malformed-row threshold;
- Parquet or schema corruption;
- a SHA-256 or declared-digest mismatch, ETag or source drift, or revision drift;
- a publication or Windows replace failure;
- receipt reconciliation.

## Resume semantics

Classification reads only authoritative artifacts:

- **COMPLETE**: the batch is below the complete-batch count. The runner simply
  advances.
- **CLEAN_NOT_STARTED**: no execution evidence. That means no events,
  performance records, top-up record, canonical units, staging, scratch
  checkpoints or retained sources. A dry plan or an authorization alone is
  clean.
- **RESUMABLE**: every check below passes, and no unexplained failure was
  recorded.
  - The batch record and the authorized plan verify.
  - No recovery amendment is scoped to the batch.
  - `resume_state` re-hashes every sealed unit and refuses a unit without a
    receipt.
  - `prepare_units` accepts every retained source and checkpoint identity (the
    same read-only path as `resume-check`).

  A crash, power loss or kill leaves no terminal event, so the batch resumes
  through the existing idempotent executor:
  - sealed units are skipped;
  - retained sources are reused;
  - verified Range prefixes continue.
- **HUMAN_REVIEW_REQUIRED**: any of the following.
  - The latest attempt in the batch's `events.jsonl` has a `failed` event (the
    unit and exception type are reported) or a `fatal` event.
  - A recovery amendment is scoped to the batch.
  - An artifact check above raised.
  - The runner's own log has a `batch_failed` mark for this batch. The mark
    applies only while the batch's artifact fingerprint (event and performance
    counts) is unchanged.

The runner's log never overrides the receipts; it only records what the runner
itself saw:

- **Ctrl+C during a batch**: after the child has finished its own cleanup, the
  runner writes `batch_interrupted` with the fingerprint. That lets the next
  start resume the batch the operator interrupted, despite its `fatal` event.
- **Nonzero exit**: the runner writes `batch_failed`, which keeps the batch in
  review even when the failure left no trace.

Either mark lapses as soon as the batch's artifacts change. For example, an
operator's manual `operator_essential_web_fast.ps1 -Batch N -Stage Run` after
review adds events and lifts the mark. A completed batch is simply COMPLETE.

## Disk safeguards

The campaign's own guards are untouched:

- the gate's 64 GiB reserve after the 32 GiB per-batch ceiling;
- the 400 GiB footprint cap;
- the executor's 64 GiB scratch cap and 32 GiB scratch reserve.

On top of them, the envelope binds operator automation guards, checked before
every batch:

- `G:` free ≥ **128 GiB** by default;
- `C:` free ≥ **96 GiB** by default (scratch cap + scratch reserve);
- scratch occupancy ≤ the scratch cap.

Both guards are configurable, stricter than the campaign's effective 96 GiB
durable floor, and never replace it. Nothing is ever deleted to make space.

Projection:

- **Worst case**: 20 batches at the 32 GiB per-batch ceiling. That exceeds the
  footprint cap, so the gate would refuse the batch that no longer fits.
- **Measured**: about 14 batches × 10.99 GB, about 153.9 GB more, about 187 GB
  in total.

## Campaign progress and ETA

The header is printed before and after each batch, and by `Status`. The live
per-batch dashboard is unchanged. It shows:

- the campaign digest, next batch, complete batches, sealed files and cumulative
  rows;
- per component: estimated tokens against the target, the percentage and the
  status;
- durable footprint and cap, `G:` free, campaign reserve and automation guard;
- scratch used and cap, `C:` free and guard;
- campaign elapsed time (from the first `automation_start` of the envelope) and
  the last batch's duration.

The projection uses the rolling mean of the last three complete batches, and
needs at least two with measured durations; otherwise it shows "ETA
calculating...". Remaining batches are the maximum over deficient components of
ceil(deficit / recent yield). Now it reads:

- science yield 39.6M tokens per batch, so about **14 batches** remain (practical
  4, prose 0);
- about 243 s per batch, so **ETA about 57 min**.

This is labelled projected and "not a promise"; it covers execution time only.
The executable stop condition remains the campaign's stop decision.

## Structured interface

- `status --json` returns the full state: decision, complete and next batch,
  views, disk, per-batch history, projection, next-batch classification with
  its restart plan, C05 and training flags.
- `prepare-auto --json [--dry]` returns the envelope, the membership proof, the
  re-derivation of existing plans, status, space and stop conditions.
- `run-auto --outcome <path>` writes the outcome: kind, envelope, outcome, exit
  code, reasons, batch and the batches completed this session.
- The log `G:\XLM\plans\ew-fast\auto\campaign-runner.jsonl` records these events:
  `operator_authorization`, `automation_start` and `automation_resume`,
  `batch_start` (with its classification and authorization digest),
  `batch_complete` (with per-component sufficiency), `batch_interrupted`,
  `batch_failed`, `safe_stop` and `fatal_stop`. It holds metadata only.
- Envelopes are stored at `…\auto\envelopes\<digest>.json`, authorizations at
  `…\auto\authorizations\<digest>.json`, and a runner lock at `…\auto\runner.lock`.

## Dry run against the real store (offline, read-only)

Under a wrapper that refuses every socket connection:

- `status` and `prepare-auto --max-batches 20 --operator gamma --dry` exited 0
  with **0 network attempts**;
- the file snapshot of `plans`, `canonical`, `acq-raw` and `C:` scratch was
  identical (1,196 files).

`prepare-auto` found:

- complete batches 3, next batch 3, CONTINUE;
- science 118.894M, practical 300.866M, prose 840.142M SUFFICIENT;
- range 3–22, not clamped.

It proved each envelope batch equal across three sources: the fast campaign's
members, the historical campaign's members and the raw inventory slice.

**Proposed Batch 3** (identical to the unchanged fast driver's `show --batch 3`):

- 32 files, inventory ranks 96–127;
- membership `bb7fd547ede2aeb36cb6ede5f72c49459451749148a3d50cfb50d4bbac264480`;
- plan `242b0cf4592b75149357b5beed1ca159d39b2981348186360fa2da708da432e0`;
- selection `d1401b051f82bca9f49e265b9bbbc33b0da24403b1bd4e5a9aa560d10b096137`;
- batch authorization digest
  `f25da8735fecea374935c05b1a0727fe77f8855ed91e4d12420da40ed6ccf56c`.

**Envelope digest** for operator `gamma` with default options and the committed
code: `21cbad8ca0de96148b094c14753187145ba0558481a17b4b9ab6a2bd4527ad9e`.

## Tests

`tests/test_essential_web_campaign_runner.py` has 26 tests, all offline.

- **Normal and sufficiency**: start at the authoritative next batch after batch 0.
  Prose is already sufficient, practical becomes sufficient after the first
  automated batch, and science last. The runner stops with the completion banner
  and never plans the next batch. A restart and `prepare-auto` then do nothing.
- **Authorization**:
  - wrong digest, altered envelope and operator mismatch are refused;
  - a changed campaign, changed source revision or changed running code is
    refused;
  - max-batches 0 or 21, a start that is not the next batch and an empty
    operator are refused;
  - the envelope stops at its end with exit 5;
  - each bound child equals what the manual `plan` writes.
- **Recovery**:
  - a complete batch is skipped;
  - a crash-left retained source resumes without re-download;
  - a unit without a receipt needs review;
  - a recovery amendment in scope needs review and changes the identity;
  - an oversized record (`RecordLimitError`) stops for review and is never
    retried.
- **Failure**:
  - a gate refusal happens before planning;
  - a nonzero exit stays in review until the batch changes;
  - both automation guards stop the runner, and the campaign disk reserve still
    applies;
  - a SHA-256 mismatch, revision drift or canonical publication `PermissionError`
    stops for review;
  - the subprocess executor reports the child's exit code.
- **Restart**:
  - Ctrl+C between batches, and Ctrl+C during a batch, resume at the exact unit,
    with no duplicate files or rows;
  - marks apply only to the exact batch fingerprint.
- **UI**: projection calculating, projected and unbounded; header percentages;
  ETA calculating; completion banner; the envelope bounds and log.
- **Other**: `status` is read-only; the PowerShell driver parses and refuses
  without operator storage.

Results:

- the new module: 26 passed;
- the related regression selection: **786 + 3 serial passed, 0 skipped**;
- ruff, format and strict mypy pass.

## Requirement ledger

| Requirement | Status |
|---|---|
| Outer runner reusing production APIs; transport, selector, mixture, gates unchanged | IMPLEMENTED, VERIFIED (empty diff of core files; 786 regressions) |
| Structured state and JSON; no console parsing | IMPLEMENTED, VERIFIED |
| One-time bounded auto-authorization binding identity, code, children, guards, operator | IMPLEMENTED, VERIFIED (fixtures; real dry derivation) |
| No automatic approval of recovery, record bound, source identity or code contract | IMPLEMENTED, VERIFIED |
| Stop on every listed human-review condition | IMPLEMENTED; VERIFIED on fixtures for the conditions tested above |
| Resume classification and restart after Ctrl+C or crash | IMPLEMENTED, VERIFIED (fixtures) |
| Automation disk and scratch guards in addition to campaign guards | IMPLEMENTED, VERIFIED |
| Campaign header, projection and ETA, completion banner | IMPLEMENTED, VERIFIED |
| Append-only JSONL log without document text | IMPLEMENTED, VERIFIED |
| Real-store dry run, no network, Batch-3 membership proof | VERIFIED (read-only) |
| `PrepareAuto` without `--dry`, `RunAuto` and Batch 3 on the real store | NOT RUN (operator action) |
| Live dashboard behaviour under the runner's child process on a TTY | NOT RUN live; the child inherits the terminal exactly as `operator_essential_web_fast.ps1 -Stage Run` does |
| Power loss mid-batch | VERIFIED only as "no terminal event, then resume" on fixtures, not by cutting power |
| Tokenizer, exact count, C05, training | OUT OF SCOPE |
| Full offline acceptance selection, CUDA tests | NOT RUN |

## Limitations

- The runner is sequential and has one lock. A manual driver run started at the
  same time makes the batch process fail on `run.lock`, and the runner then
  stops for review. It does not wait.
- Ctrl+C: the runner waits up to 600 s for the batch process to finish its own
  cleanup before it records the interruption.
- A failure the runner cannot explain is never retried automatically. That
  includes a transient network failure that exhausted its retries. After review,
  resume it with the fast driver, then run `RunAuto` again with the same digest.
- The ETA uses measured execution time of complete batches. It excludes pauses
  and does not model endpoint variance.

## Operator commands

```powershell
. .\scripts\operator_storage.ps1
.\scripts\operator_essential_web_campaign.ps1 -Stage Status
.\scripts\operator_essential_web_campaign.ps1 -Stage PrepareAuto -MaxBatches 20
.\scripts\operator_essential_web_campaign.ps1 -Stage RunAuto -Authorize <AUTO AUTHORIZATION DIGEST>
```

`PrepareAuto` prints the envelope and `AUTO AUTHORIZATION DIGEST`. With operator
`gamma`, default options and this commit, the digest is `21cbad8c…ad9e`. After
an interruption or a stop, rerun the same `RunAuto` command; it resumes from the
authoritative state.

When all targets are met, the runner prints **ESSENTIAL-WEB FIRST-PASS
ACQUISITION COMPLETE** and reports:

- batches, files and rows;
- canonical bytes and estimated tokens per component;
- the durable footprint;
- `C05 status NOT RUN` and `TRAINING NOT YET PERMITTED`.

It starts no further batch and no tokenizer, C05 or training.

**Remaining after acquisition**:

1. Freeze the canonical pool.
2. Produce the C05 exclusion receipt (`c04-benchmark-risk-v2`,
   `suspect_with_mitigation`, `xlm.data.exclusion`).
3. Tokenize.
4. Take the exact token count.
5. Deterministically top up deficient components (a new reviewed authorization).
6. Only then consider training.
