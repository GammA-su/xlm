# P35 — Opus 5.5 implementation handoff

Implement [xlm-science-v1](../reports/P35-SCIENTIFIC-CONTRACT.md) in small sequential
milestones. This is sufficient specification to proceed without another Astra
review. Do **not** launch real-data training, a research campaign, acquisition or
protected evaluation. The USER executes the real 32M pilot and longer experiments.
Preserve P34's data/IPC/checkpoint/gradient/trace engineering. Work from a clean
reviewed checkout; do not discard user changes, push or merge.

This handoff is a specification, not a claim the commands/fields below already
exist. P35 implementation baseline is `febbf8b98e2e2e216cfdab62eef911e7b6d91d09`;
P35 itself adds only documentation and a synthetic attention evidence script.
Follow AGENTS.md's focused test policy. A full offline gate belongs to an explicit
release gate, not every submilestone. Keep Python 3.12.13 and existing uv.lock /
CPU–CUDA extras policy unless a separately justified dependency change is necessary.

## What exists and should be reused

| Capability | Existing seam |
|---|---|
| Strict data-only configs/component construction | `src/xlm/config/schemas.py`, `training/components.py`, local registry and plugin capability checks |
| Captured execution and immutable inputs | `experiments/execution.py`, `experiments/evaluation_execution.py`, existing artifact manifests/ledger; direct/queue/resume paths |
| Actual-target accumulation/partial budget/global clip | `training/trainer.py`, objective token-additive protocol |
| Data exposure/sampler/resume | `data/sampling/{mixture,plan,scheduler,stream,prefetch}.py`; target traces and committed-only state |
| Safe checkpoint publication/RNG/optimizer restore | `training/checkpoint.py`, artifact durability substrate |
| Likelihood/BPB/per-source validation | `evaluation/{likelihood,diagnostics}.py` |
| Official harness/input verification/coverage | `evaluation/{suites,inputs,harness_runner,coverage,evidence}.py`; `cli/eval_cmd.py`; harness 0.4.13 |
| Comparison/curves/bootstrap/promotion | `comparison/{tracks,bootstrap,curves,promotion}.py`; extend with a version, preserve old semantics |
| Local queue/CLI/reports | Existing campaign/experiment CLI, `reports/{collect,render}.py`, operator plan-hash authorization |
| Current recipes | `recipes/models`, `recipes/experiments`, `recipes/mixtures`; these remain historical drafts until new versioned recipes are added |

Do not create a second experiment store, scheduler, dashboard, plugin loader or
distributed service. Keep ordinary same-user mode explicitly development-exposed.

## Milestone 1 — LR semantics, independent training RNG and identity

Deliver a versioned migration with no silent default change:

1. Add explicit `first_update_lr_policy` (or equivalently scoped schedule-policy
   field) values `legacy_base_then_postcommit_v1` and
   `target_endpoint_before_update_v1`. Existing schema-v1 configs/checkpoints
   resolve to legacy; new science-v1 recipes must explicitly select endpoint.
   Put the resolved policy in the execution envelope/config hash, checkpoint
   metadata and ordinary-resume compatibility check. Historical records retain
   their original hashes; normalize through a version-aware reader, never rewrite.
2. NEW path computes actual N after masking, sets group LRs from f(C+N) before
   optimizer execution, advances counters only after the existing successful
   CUDA/commit boundary. Zero-valid updates do not advance schedule/optimizer.
   Preserve group LR multipliers if supported; otherwise reject unsupported
   semantics. `lr_used` records what AdamW used, with its counter; `lr_next` is
   separate and optional. Legacy `learning_rate` remains documented as next LR.
   Do not change the math function under old schedule version/doc semantics.
3. Save/recover LR policy with optimizer/schedule; safe pre-step failure restores
   policy state; in-doubt step still refuses checkpoint/retry until fresh reload.
   No scheduler counters advance on skipped/failed updates. Standard BF16 policy
   fails on nonfinite values, does not silently skip. Test the supported scaler
   path if the migration touches it.
4. Add explicit `training_seed` for NEW runs. Keep init seed exactly responsible
   for construction; reseed Python/NumPy/torch CPU/CUDA training RNG after all
   model/objective construction. Record train-start RNG receipt. Resume restores
   saved states and must not reseed afterward. Legacy behavior couples global
   RNG to init seed and construction consumption; preserve it under legacy
   schema, not by pretending training_seed=init_seed is necessarily equivalent.
5. Bind attention determinism/backend policy, precision/reduction/TF32 choices
   and producer mode into scientific identity. New statistical mode selects and
   verifies the intended efficient SDPA path; strict mode uses error-on-nondeterminism.
   Record actual operator probe receipt. Do not alter process-global settings
   without scoped restoration. Do not certify whole-model determinism from an
   attention-only probe or automatically switch unsupported backends.

Required focused tests (authored fixtures; names below describe cases to add):

- Captured optimizer.step sees legacy first LR .001 and second .0000065536;
  endpoint policy sees .0000065536 then .0000131072 for N=65,536/W=10M.
  A test must observe optimizer mutation/captured LR, not just call get_lr.
- Actual partial N determines endpoint LR; warmup crossing, exact horizon, no
  warmup and no-valid-target boundaries; lr_used accurately matches each group.
- Resume at update 1 and after partial updates reproduces CPU weights, Adam slots,
  scheduler/LR and data trace. Legacy checkpoint remains legacy; policy change
  is rejected without an explicit fork/new experiment.
- Injected pre-step and in-doubt failures preserve current fail-closed behavior;
  no checkpoint exposes ambiguous state. Use directly relevant P34 boundary tests.
- Training seed changes stochastic training while same init seed preserves
  initial weights; changing init affects initial weights. Separate objective
  construction consumption cannot silently redefine NEW training RNG. Resume
  restores Python/NumPy/CPU and optional CUDA RNG, not constructor reseeding.
- Unknown policy, unresolved attention choice or precision mismatch fails early;
  direct/queue/resume resolve one identical behavior hash. Old configs remain
  executable through the legacy path.

Acceptance: updated schema/contract migration notes, focused tests and exact
commands/statuses, no production recipe switched. Next milestone begins only
after these semantics are reviewable and verified.

## Milestone 2 — Evaluation cadence and state-preserving scoring

Add one small absolute-threshold planner over committed target counts; reuse the
trainer boundary/checkpoint/evaluator seams. It must distinguish quick LM, full
LM, search benchmarks and endpoint confirmation events. Thresholds are data,
not executable YAML. In-memory read-only scoring or a serialized checkpoint job
is acceptable; no concurrent GPU train/eval. Do not infer successful evaluation
from `evaluation.every_valid_targets` alone.

Event identity binds run/attempt, actual checkpoint or model-state hash, planned
threshold, actual committed C, evaluator identity, inventory and tier. Events
fire after successful updates at first crossing; no artificial partial optimizer
step at an eval threshold. Persist completed receipts to avoid duplicate scoring
on resume. If evaluation is rerun, preserve both attempt records and one declared
canonical receipt. Missing/failed events invalidate completeness rather than
defaulting to zero scores. Preserve mode, RNG, gradients and recurrent/plugin
state, or use a separate verified loader of immutable weights.

Integrate real local LM manifests into native scoring: summed NLL/tokens/UTF-8
bytes per source/domain, text-only CE/BPB, EOS-inclusive diagnostic separately;
equal fixed domain weights from the frozen validation policy. Empty required
domains fail rather than silently renormalize. The inference precision, rolling
window/context/BOS/normalization rules and scored-byte coverage are frozen. If
the native scorer is too slow, measure and state it; do not redesign it or shorten
validation silently. Benchmark runs use declared local inputs, pinned task specs,
coverage and split firewall. No downloads or default final-task lookup.

Required tests:

- 1M threshold after a 65,536-target update fires at C=1,048,576; endpoint budget
  fires exactly once; multiple crossed events, interruption/resume, completed
  checkpoint and missing event recovery behave deterministically.
- Scoring cannot mutate weights/optimizer/RNG/train mode/committed cursor. An
  intentionally mutating fixture exposes the guard. Separate process path binds
  the checkpoint hash actually loaded.
- Hand-computed synthetic corpus checks CE/BPB and domain weighting, no mean of
  per-document means, structural-token exclusion and zero-domain refusal.
- Eval receipt/cache identity changes with checkpoint/tokenizer/task revision/
  precision/items; mismatched or partial coverage cannot issue a complete score.
- Training integration proves declared cadence actually invokes the evaluator;
  evaluation error is recorded and cannot yield a completed scientific result.
- Search/confirmation paths never resolve final split; authored benchmark-shaped
  fixtures only. Protected final mechanism remains separate.

## Milestone 3 — Bounded retention and pilot plan generation

Implement absolute target-based checkpoint thresholds in the same boundary
planner, with resumable next-event state. Keep synchronous publication and all
P34 guards. Retention is a small explicit policy over verified artifact IDs:
latest two recovery states plus pinned milestones/parents. Plan peak disk for
old states + staging + new states + caches, not only retained files. Existing
cleanup/artifact reference checks must protect live inputs and last-good state.

Add **new science-v1 example drafts**, do not mutate historical baseline recipes.
Pilot draft specifies every field in §W of the contract and keeps unresolved
artifact/hash/resource-profile references null. Label it nonexecutable. A planner
resolves only actual supplied artifacts; it must never generate fake hashes,
invent a source field, accept “latest,” fit on heldout data or replace a missing
M0 component. Check per-source 32M quotas/exhaustion, exact tokenizer size/count,
evaluation inventories, path/capacity bounds and cold-transition coverage before
requesting user authorization. 32M pilot is outside ordinary agent smoke scope.

Pilot computation: B8, global 65,536; 488 full updates plus 18,432-target final
update = 489 updates. 1B horizon/10M warmup, endpoint LR policy, P0 seeds,
statistical efficient SDPA, BF16/FP32, process_depth1 with verification on.
Checkpoint 8/16/32M; evaluation points and resource bounds exactly §W. Annotate
that actual first-crossing checkpoints can exceed 8M/16M slightly.
Enforce the 60-minute total job deadline through the existing outer job/resource
boundary, including startup/evaluation/checkpoint/recovery time; the trainer's
`max_train_seconds` alone does not express this limit. Persist elapsed allowance
across a resumed pilot attempt. A shorter run stopped by that cap is incomplete,
not a successful 32M pilot.

Required tests: publication failure retains last good state, retention cannot
delete milestone/parent, capacity includes staging, resumed cadence does not
drift, paths stay within declared store, missing sources/capacity/hashes or
authorization reject before GPU allocation. One synthetic end-to-end toy
train/checkpoint/evaluate/resume flow, <=200k targets / 10 minutes / 2 GiB outputs.

## Milestone 4 — Scientific comparison and result reports

Add a small science-v1 manifest/validator that wraps existing frozen execution:
experiment/question, parent baseline, arm/seed roster, intended differences,
primary endpoint/distribution, budget/horizon/LR semantics, fixed final checkpoint,
seed/order/exposure identities, runtime policy, margins, CI/multiplicity and
stopping rules. Hash with existing canonical serialization. Every table row
links to the execution/evaluation receipts that substantiate it. Unknown or
unmatched required fields yield INELIGIBLE plus a field-level diff.

Extend comparison tracks narrowly for (a) microbatch grouping and (b) mixture
research; use explicit allowlists. B8/B16/B32 compare flattened global-update
target/input/mask/position/source traces, not weight hashes. Mixture comparisons
must allow quota/token-order changes caused by weights while requiring the same
within-source order policy and frozen tokenizer. Architecture code diffs are
allowed only for the declared intervention; do not require identical Git SHA
across intentionally different model code. Match hardware/runtime for primary
paired runs; external replication is a separate stratum.

Implement training-pair mean/SD/t intervals independently of current bootstrap-v2
headline selection. Pair by explicit init/training/data/order tuple. Repeated
same-seed executions share a replicate ID and cannot inflate n. Confirmatory
sample size is frozen (50M five pairs; 150M/300M three); a first-three view is
provisional, not early success. Use verified Student-t critical values or an
existing locally available implementation; do not introduce an unpinned network
dependency. Support the Bonferroni family size declared in the manifest. Keep
paired cluster/item uncertainty separate; preserve existing bootstrap coverage
refusals. Do not call the old minimum-seed + suite-point promotion function the
P35 rule. Version new promotion logic and require resolved practical/NI margins.

Report endpoint difference and fixed linear-target curve area, all pairs,
uncertainty method/limitations, per-domain and benchmark guardrails, eligibility,
failed runs, missing coverage, timing/storage/VRAM and the exact table in §U.
No placeholder success rows. JSON + Markdown/CSV through existing reports is
sufficient; HTML uses existing escaping. No new dashboard needed.

Required tests:

- Identical scores yield zero delta; swapped arms reverse direction; pair order
  does not change mean; mismatched/missing/duplicate seed tuples reject.
- Known small synthetic t-interval cases including zero variance and n<2;
  family-size adjustment widens intervals; one-seed result has no seed CI.
- Same-seed repeats cannot count as fresh seeds; partial pairs/failures block
  complete confirmation. A narrow item bootstrap cannot override a wide seed CI.
- Meaningful mean with CI crossing margin remains ambiguous; NI requires upper
  bound below margin, not a nonsignificant difference. No adaptive extra-seed
  success. Old promotion records stay old, cannot be silently upgraded.
- Parameter/tokenizer/data/schedule/first-LR/attention/precision/backend mismatches
  reject unless expressly varied in that track. Missing margins cannot promote.
- Curve area uses identical actual target points/range; missing endpoints stay
  missing. Different-tokenizer CE/perplexity cannot issue a quality win.
- Reports show NOT RUN/PARTIAL and INELIGIBLE prominently, escape external text,
  and preserve failed/tuned attempts in machine-readable provenance.

## Milestone 5 — Independent document-order evidence (before robust promotion)

This is not necessary for the first P0 pilot. Current source seed only changes
deficit/tie scheduling; it does not shuffle documents within shards. Do not
mislabel multi-init/source-seed evidence as robust to within-source document order.

Implement a narrow versioned way to bind two independently ordered manifests
over **identical canonical train membership**. Prefer existing preparation/shard
artifacts with explicit ordered-document IDs and seed. If regeneration is needed,
it is bounded, separately authorized local preparation; no source acquisition or
loader optimization. Preserve document internal token order, lineage/split, source
attribution, byte spans, quotas and repeat policy. Carry the ordered-artifact
identity through stream/checkpoint/exposure receipts. New order cannot ordinary-
resume an old plan. Avoid arbitrary per-step runtime shuffling that changes
P34 speculative/committed cursor semantics.

Tests: membership equal but order different, repeatable seed/order hash, no split
leakage, whole-document content/byte preservation, exact resume per order,
different order rejected on ordinary resume. Paired arms share order, C0/C2/C4
use order A and C1/C3 order B; larger confirmation blocks cover both. Baseline
v1 bundle reports order-stratified effects and actual initialization counts.
This does not require rewriting the producer or benchmarking acquisition.

## CLI contract to expose (proposed syntax, NOT currently executable)

Use a thin `xlm experiment` facade or extend existing commands with equivalent
behavior; publish the actual selected syntax before handoff. No second runner.
The following is the target user-facing flow; unresolved file paths below are
operator-created outputs, not fabricated existing artifacts:

```powershell
# Implement and verify these verbs before using them. Planning never trains.
uv run --offline --locked --extra cuda --extra eval xlm experiment plan --config pilot-science-v1.yaml --output pilot-plan.json
uv run --offline --locked --extra cuda --extra eval xlm experiment validate pilot-plan.json
uv run --offline --locked --extra cuda --extra eval xlm experiment compare --comparison comparison.json --output comparison-report
uv run --offline --locked --extra cuda --extra eval xlm experiment report --experiment experiment.json --output experiment-report
```

`plan` resolves local data/evaluator artifacts, all behavioral fields, target and
resource ceilings, scientific code identity and authorization hash; fails closed
on nulls needed for execution but may emit a clearly marked unresolved draft.
`validate` emits machine-readable reasons and nonzero exit on ineligibility;
it cannot approve or start a GPU job. `compare` reads immutable run/eval evidence;
it never retrains. `report` surfaces missing fields/attempts. Existing `xlm train`,
resume and local queue remain execution backends. Output the exact user-only
launch command using those real APIs and existing plan-hash authorization after
implementation; **do not invent a working `--authorize` flag or launch it here**.

The complete plan records all provenance groups in §T, including init/training
RNG distinction, ordered-document manifest, budget versus horizon, LR actually
used, requested versus observed attention and evaluation scope. Authorization is
the final step after the plan is concrete and reviewable. The USER decides the
actual artifact/storage limits and supplies authorization for persistent GPU work.

## Verification and execution ownership

Use focused exact tests and directly related regressions. Example template
(replace the node with an implemented test name):

```powershell
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_schedules.py -n 0
```

Select CUDA extras for separately marked CUDA checks; do not use a CPU-environment
certificate with a borrowed CUDA wheel. No install/network fallback. One xdist
controller only if justified for a focused group; explicit worker count coordinated
with other sessions. A skip is NOT RUN, not a pass. No full acceptance audit unless
the user explicitly requests the release gate. No automatic whole-campaign smoke.

Agents may implement software, authored offline tests and ordinary C13 toy smokes.
The P35 attention-only diagnostic authorization does not authorize future agents
to repeatedly run actual-size training benchmarks. A bounded actual-shape
deterministic full-update profile requires its own concrete budget/authorization
unless a later user request already supplies it. The user runs real-data pilot,
LR/batch/mixture studies, all full-budget replications and scale promotions.

Every milestone updates STATUS.md, its report, new fields/CLI user docs, exact
commands/exits/environment, fixture versus live distinction, resource use,
IMPLEMENTED/VERIFIED/BLOCKED/NOT RUN/OUT OF SCOPE ledger and next prompt. Commit
coherent work when authorized; never claim a future spec is implemented because
it appears in a YAML example.

## Completion gate and next prompt

Milestones 1–4 plus concrete admitted local inputs must pass before the user runs
the first 32M pilot. Milestone 5 is required before claiming order-robust 50M
promotion. Before the first full 1B baseline, apply report §X; a passing toy smoke
alone is insufficient. The pilot plan must remain visibly blocked if actual
tokenizer/data/evaluator IDs, margins needed for comparative decisions, or user
GPU authorization are unresolved. Pilot health does not require comparative
effect margins; formal batch/idea promotion does.

Next prompt: **Implement milestone 1 only. Read the P35 scientific contract and
current status first. Preserve legacy execution, use focused offline tests, do
not change production defaults or launch any real-data training. Report actual
LRs captured at optimizer updates, resume compatibility and exact test evidence.**
