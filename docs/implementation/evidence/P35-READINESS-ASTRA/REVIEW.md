# ASTRA adversarial findings

This accompanies PRETEST.md, written before tests/product edits. All observations
are offline authored fixtures. No benchmark result or live-data compatibility is
claimed. Historical cloud evidence is preserved. Certification results and command
statuses are recorded in the report appendix and XML/log artifacts.

## Findings and disposition

| ID | Severity | Finding | Disposition |
|---|---|---|---|
| P1 | SHOULD FIX BEFORE PILOT | Stat diagnostic followed order-reader/index validation and did not gate resolution | Move stat check before both and return on its blockers; focused regression |
| P2 | SHOULD FIX BEFORE PILOT | RequiredEvaluationIncompleteError lost its typed completion across worker boundary | Worker emits verified FAILED/EVALUATION_INCOMPLETE result; queue persists classification |
| P3 | SHOULD FIX BEFORE PILOT | Queue trusted failed/incomplete worker results as success internally | Reject FAILED and declared required-evaluation missing/incomplete results before success transition |
| P4 | SHOULD FIX BEFORE PILOT | Successful result arriving after remaining wall time marked allowance completed | Persist expired and raise WallAllowanceExpired at final clock completion |
| E1 | certification tooling | Exact requested synthetic-flow script imported Unix-only resource | Platform-specific measured peak RSS; no dependency addition |
| M1 | SHOULD FIX BEFORE MICRO-BATCH STUDY | Prepared receipt hashes pending arrays even after consumed tensor is replaced | Reproduced; require full handoff binding or validated immutable views |
| M2 | SHOULD FIX BEFORE MICRO-BATCH STUDY | Chain and LR history can both claim C=16 while checkpoint/model/data stay C=0; load accepts | Reproduced valid checksummed authored checkpoint; cross-check all counters before restore |
| M3 | SHOULD FIX BEFORE MICRO-BATCH STUDY | Chain commit failure after data commit leaves LR row and unpoisoned boundary | Reproduced; preflight bounds and poison any partial receipt commit |
| M4 | SHOULD FIX BEFORE MICRO-BATCH STUDY | Evaluator guard does not fingerprint new chain | Reproduced live-chain mutation receives COMPLETE; add chain/staging state to guard |
| M5 | SHOULD FIX BEFORE MICRO-BATCH STUDY | Duplicate compact table strings can represent equal decoded values with different hashes | Reproduced false inequality; canonicalize by decoded value or validate unique table |
| M6 | SHOULD FIX BEFORE MICRO-BATCH STUDY | Changed-policy fork bypasses receipt pre-restore validation and can reset receipt lineage | Source-derived; add explicit fork semantics and before-restore regression before study |
| F1 | FOLLOW-UP ONLY | 9P assumes ordinary retention succeeds; repeated retirement errors or external protected references can exceed nominal count | Distinguish planning bound from runtime storage fail-stop; no claim of universal 9P |
| F2 | FOLLOW-UP ONLY | Absent/None masks conflate in receipt; explicit None is rejected by runtime; uint64 overflow/invalid shapes are not a universal typed input contract | Restrict evidence claim to valid stock input domain; strengthen validation before supporting alternate providers |

P1/P4 and queue state error were independently demonstrated by four initially
failing focused regressions (exit 1). No receipt source was changed, as instructed
for issues confined to the future microbatch study. The pilot draft is unchanged.

## Recovery and durability

PRETEST.md gives the ten-event table. Initial failed, invalid, partial, planned-only
events close the barrier: zero steps and zero LR rows. Immutable started/outcome
artifacts preserve attempt count across fresh Trainer reconstruction. An initial
checkpoint before attempts can therefore resume with all three attempts already
exhausted and still block update 1. A crash before scoring or after a failed outcome
does not invent a COMPLETE event.

Natural boundaries: 1M→1,048,576/16, 4M→4,063,232/62,
8M→8,060,928/123, 16M→16,056,320/245, endpoint→32,000,000/489
(488 full updates plus 18,432 targets). Evaluation due state is recorded before
checkpoint serialization. The checkpoint binds due event, digest, step and cursor.
First-crossing, publication/adoption, outcome reconciliation and retention tests
exercise interruptions around these transitions; they are synthetic fault injections,
not a claim to test arbitrary filesystem/controller hardware failure.

Unresolved due/failed/partial/interrupted LM events pin exact model digests. A
successful canonical attempt releases only that dependency; last-good remains
protected until a later verified publication. Exact rescore does not substitute
the newest checkpoint. Search has no checkpoint-rescore route: C0 and endpoint
retry while live, then fail. Three-attempt exhaustion is terminal for that exact
lineage. Interior rescore can fail-stop with an attempt still available; neither
three attempts nor a checkpoint promises eventual successful evaluation.

The queue repair carries only RequiredEvaluationIncompleteError as a structured
scientific failure. RecoveryRequiredError remains a stronger exception, never
converted into successful or ordinary incomplete evaluation. The endpoint checkpoint
has already been published when the required-completeness call occurs.

The total allowance encloses worker startup, all scoring/retries/rescore and checkpoint
work. Resume loads consumed time; it does not get another 3600 seconds. Heartbeats
persist at one-second intervals, so lost-runner accounting retains the documented
sub-second/approximately-one-interval uncertainty. No 60-minute test is needed.

## Independent capacity derivation

Immediately before endpoint publication the failure-retention case can hold t0,
t1M, t4M, t8M, t16M: five scientific states. Two interrupted terminal checkpoints
at other boundaries can survive as latest-two rolling recovery: seven P total.
New serialization plus ArtifactStore staging add two P: peak nine P. At publication
the new endpoint replaces transient staging and ordinary retention retires anything
eligible. Resolved 1M/4M states can reduce actual occupancy; the planner does not
assume that success. Receipts, snapshots, evidence, caches, logs and margin are
additional terms, not part of P. The measured checkpoint-size input remains required.

Deletion failures retain bytes; publication attempt bounds and actual output guards
must stop work instead of pretending those bytes vanished. This is an operational
failure case, not a promise of completing within nine P after arbitrary filesystem
faults. Explicit protected foreign/fork references require their own capacity.

## Payload meaning and limits

The versioned digest binds field presence, [sequence, context] shape, packing mode,
ordered input ids/labels/loss masks/positions/segments/attention masks and full
source/doc/lineage/span/offset provenance. Span shape is [N,T,2]; string provenance
is [N,T] or [N,1]. Integers use little-endian int64; attention normalizes boolean
truth; strings use first occurrence tables and UTF-8 JSON. Signed provenance offsets
and empty/Unicode strings are significant. Dtype/container changes within the valid
integer domain are incidental. Sequence vs per-token provenance remains explicitly
different; context/padding/masked positions are not discarded. BOS/EOS target masks
and isolated-document segment/packing policies are included. Empty update is refused.

The authored property check starts with one authoritative ordered payload and
regroups it 1/2/4/8/16/32. Heads match, and every named semantic field mutation and
sequence reordering differs. Real process_depth1 output matches synchronous Torch
mixture output, including the partial final update and row-aligned provenance.
A one-row doc-provenance shift changes both IPC and receipt digests. The detached
consumer replacement counterexample is distinct: pending data stays unchanged while
the Trainer's consumed values differ. It survives the receipt's row-count check.

Failed forward/nonfinite/scaler skip/optimizer/data commit do not create receipt
rows. Successful ordinary updates align LR and payload rows. The partial receipt
commit failure itself is the reproduced gap. Both receipt-presence mismatches and
historical chain-digest tampering reject ordinary resume before model/data restore.
The fresh-process authored resume check includes dropout and a partial final update;
equal input receipts make no assertion of equal dropout draws across different
microbatch groupings. Stochastic objective state and all computation/runtime contracts
remain separate invariants.

M4 v2 requires receipt version and chain and rejects missing/different values; v1
meaning remains historical. Old evidence reads without invented receipt fields.
Final weight hashes are not a grouping equality criterion. This machinery is useful
but its static eligibility is not a scientific certification of the adversarially
incomplete receipt implementation.

## Pilot receipt recommendation

Keep the draft's receipt opt-out. A 32M diagnostic pilot is not automatically a
formal comparison arm; a preregistered study creates its own B8 control with matching
budget, seeds/order, contracts and fixed implementation. Enabling now changes plan
identity and spends part of the 60-minute allowance while retaining the reproduced
pre-study gaps. The bounded CUDA measurement is a tiny-model diagnostic and cannot
establish acceptable pilot-sized cost. If reuse as a formal arm becomes an explicit
objective, repair and recertify the receipt, measure the actual shape, then freeze
that new plan before any run. No practical/non-inferiority margin is chosen here.
