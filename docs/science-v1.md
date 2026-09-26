# Science-v1 training semantics and comparisons (P35 Milestones 1–4)

`xlm-science-v1` is an explicit, versioned training policy defined by the
[P35 scientific contract](implementation/reports/P35-SCIENTIFIC-CONTRACT.md).
It changes three things for **new** runs only: which learning rate the first
and every later optimizer update uses, which seed drives stochastic training,
and which runtime settings are part of the run's identity. Nothing changes for
a configuration that does not select it.

## Selecting it

All five fields are required together, in the `training` section:

```yaml
training:
  science_version: xlm-science-v1
  lr_policy: target_endpoint_before_update_v1
  training_seed: 10001
  runtime:
    attention_policy: statistical_efficient_v1   # or strict_deterministic_v1
    matmul_tf32: disabled                        # or enabled
    bf16_reduced_precision_reduction: allowed    # or disallowed
```

There are no defaults. A missing field, an unknown value, or any of these
fields without `science_version` is a validation error. An existing (schema-v1)
configuration names none of them and resolves to the legacy policy. Its
resolved configuration, envelope and plan hashes are byte-for-byte unchanged.
[`recipes/experiments/draft_science_v1_50m.yaml`](../recipes/experiments/draft_science_v1_50m.yaml)
is a non-executable draft showing the fields. No production recipe uses them.

## Learning rate per update

`f` is the existing warmup/cosine function (unchanged). `C` is the number of
targets committed before an update, and `N` is that update's actual valid
targets after masking and final-budget truncation.

| Policy | Update 1 | Later updates | Zero-valid update |
|---|---|---|---|
| `legacy_base_then_postcommit_v1` (implicit for schema v1) | optimizer base LR | `f(C)`, installed after the previous update commits | no step, no schedule change |
| `target_endpoint_before_update_v1` (science-v1) | `f(0 + N)` | `f(C + N)`, installed immediately before the optimizer step | no step, no schedule change |

For the 50M draft (base 0.001, warmup 10,000,000, N = 65,536), `optimizer.step`
sees 0.001 then 0.0000065536 under the legacy policy, and 0.0000065536 then
0.0000131072 under science-v1. A final partial update of 1,000 targets after
65,536 uses `f(66,536)`. Per-group `lr_multiplier` values are preserved
(`group LR = f(C + N) × multiplier`). A group built with a distinct LR and no
multiplier is refused, because its endpoint rate would be ambiguous.

Metrics fields per committed update:

- `learning_rate`: **historical meaning, unchanged.** The schedule evaluated at
  the post-update committed count. Under legacy this is the LR the next update
  will use. Under science-v1 it equals `lr_used`.
- `lr_used`: the per-group LR that `optimizer.step` actually read.
- `lr_schedule_counter`: `C + N` for science-v1; `None` under legacy.
- `lr_next`: the LR already installed for the next update (legacy), or `None`
  when it depends on the next update's actual `N` (science-v1).

Science-v1 checkpoints add `science.json`. It holds the policy identity, the
train-start RNG receipt, every committed update's LR receipt (`step`,
`committed_before`, `valid_targets`, `schedule_counter`, `lr_used`), and one
runtime receipt per training attempt. A receipt is appended only after the
data cursor commits, so failed or in-doubt updates never publish one. Legacy
checkpoints keep their historical file set.

## Training RNG

Legacy runs seed Python, NumPy and torch from `init_seed` before construction
and train on whatever state construction leaves behind. Science-v1 does the
same for construction, then reseeds Python, NumPy, torch CPU and (on CUDA) all
CUDA generators from `training_seed` after the model, objective, plugins,
optimizer, schedule and batcher exist. Changing a constructor's RNG
consumption therefore cannot change the training stream. The reseed happens
only for a fresh run: ordinary resume restores the checkpoint's RNG states and
never reseeds.

## Runtime identity

The runtime block is applied by the trainer around each update and restored
afterwards. The process-wide worker setting of historical envelopes is not
used.

- `statistical_efficient_v1`: CUDA and `attention_backend: sdpa` only.
  Deterministic algorithms are off, and SDPA is restricted to the
  memory-efficient kernel, so an unsupported input raises instead of falling
  back. Same-seed reruns may differ numerically; data accounting may not.
- `strict_deterministic_v1`: `torch.use_deterministic_algorithms(True)` in
  error mode, cuDNN deterministic. On CUDA, SDPA is restricted to the efficient
  kernel and `CUBLAS_WORKSPACE_CONFIG=:4096:8` is required at process start;
  the frozen launcher sets it for strict envelopes. On CPU, SDPA is restricted
  to the math kernel. A nondeterministic operation fails the update.
- `matmul_tf32` and `bf16_reduced_precision_reduction` set the corresponding
  `torch.backends.cuda.matmul` flags. `disabled`/`allowed` are the torch
  defaults the P34 path already used. If a flag reads back differently from
  what was requested, the run fails.

At trainer start, a small probe records the SDPA operators actually executed
under the scope (for example `aten::_efficient_attention_forward/backward`),
the observed flags, and the cuBLAS workspace setting. It fails if the declared
kernel is not observed. The probe covers attention only; it is **not** a
whole-model determinism certificate.

The runtime block, `training_seed`, `lr_policy`, precision, `producer_prefetch`,
the model's `attention_backend`, and all existing fields are part of the
resolved configuration. They therefore enter the execution envelope, plan hash
and `seeds` tuple (`training_seed` is added for science-v1 only). Science-v1
envelopes carry the runtime policy
`{"torch_threads": 1, "scientific_runtime": "trainer_scoped_v1"}`. Legacy
envelopes keep `{"torch_threads": 1, "deterministic_algorithms": true}`.

## Evaluation cadence (P35 Milestone 2)

`evaluation.every_valid_targets` is a legacy declaration and has never triggered
evaluation. Science-v1 runs declare `evaluation.science`. Every field is
required, and the block is data only:

```yaml
evaluation:
  science:
    version: xlm-eval-cadence-v1
    cadence: pilot_32m            # screen_128m | full_1b | authored_fixture
    fixture_thresholds: null      # explicit ints, authored_fixture only
    confirmation_registered: false
    quick_lm: {manifest: /abs/quick/manifest.json, manifest_id: <sha256>}
    full_lm: {manifest: /abs/full/manifest.json, manifest_id: <sha256>}
    search_benchmark: {inputs: /abs/search/manifest.yaml, manifest_id: <sha256>, blimp_universe: [...]}
    endpoint_confirmation: null   # {lm: {...}, benchmark: {...} | null}
    scoring: {forward_precision: fp32, logprob_dtype: fp64, rolling_stride: 256}
```

- Contract tables are bound to their budgets (32M/128M/1B). An event fires after
  the first *committed* update with `C >= threshold`. Its receipt records both the
  planned threshold and the actual `C`; updates are never shortened to hit a
  threshold. The endpoint fires once, after the exact-budget (possibly partial)
  update.
- Every planned tier needs pinned local inputs, and inputs without a planned
  event are refused. Nothing is downloaded; `latest` is never accepted.
- Primary LM metric is `equal_domain_text_ce_nats_per_token`: nats per scored
  text token, with BOS/EOS/padding excluded and equal fixed domain weights over
  per-domain sums. Also reported: micro CE, `text_bpb` (bits per canonical UTF-8
  byte) and the separate `eos_inclusive_ce_nats_per_token_diagnostic`.
- Event state is kept in `science.json`. Attempts are immutable `evaluations`
  artifacts. The canonical receipt is the first complete attempt. A failed,
  partial or unreached event leaves the run evaluation-incomplete.
- A changed plan or evaluator refuses ordinary resume. A fork re-originates the
  plan at the fork's committed count.
- Scoring uses a digest-verified replica of the model, never the live one.
  Evaluation that changes live training state stops the run until it is resumed
  from a checkpoint.

## Checkpoint cadence and bounded retention (P35 Milestone 3)

Science-v1 runs may declare an absolute checkpoint cadence. It replaces the
relative `checkpoint_every_valid_targets` cadence, which is then inert (a
trainer given both refuses to start):

```yaml
training:
  checkpoint_cadence:
    version: xlm-checkpoint-cadence-v1
    cadence: pilot_32m          # full_1b | authored_fixture
    fixture_milestones: null    # explicit ints, authored_fixture only
    fixture_recovery: null      # explicit ints, authored_fixture only
    retention: latest_two_recovery_plus_pinned_v1
    protected_references: []    # verified artifact ids never to retire
```

- **Thresholds.** `pilot_32m` = milestones 0 (initialized weights/identity),
  8M, 16M and the exact 32M endpoint (§W). `full_1b` = milestones 0/128M/256M/
  512M/1000M plus recovery every 64M (§L). Checkpoints use the M2 planner's
  first-crossing rule: a threshold is satisfied at the first committed boundary
  at or above it, and updates are never split. At 65,536 targets per update the
  8M checkpoint lands at C = 8,060,928 (update 123) and 16M at C = 16,056,320
  (update 245); both the planned threshold and the actual count are recorded.
  The 32M endpoint is reached exactly by the masked 489th update.
- **One boundary, fixed order.** Evaluation crossings are recorded, then one
  checkpoint is published for every checkpoint event first crossed there (it
  therefore owes that boundary's evaluations), then evaluations run. The final,
  interrupted, cancelled and time-limit states reuse an already published
  checkpoint of the same state instead of publishing a duplicate. The
  exact-budget endpoint checkpoint consequently shows its endpoint evaluations
  as owed; their completion is the durable attempt artifacts (see below).
- **Identity.** Each publication record binds run/plan, event identities,
  planned thresholds and actual C, step, model-state digest, committed
  data-cursor digest, scientific identity, artifact id
  `{run}_ckpt-t{threshold}-a{attempt}`, verified manifest SHA-256/content hash
  and creation attempt. Records live in `science.json`; immutable receipts
  (kind `checkpoint_events`) record every published or failed attempt.
- **Resume.** The ledger is restored, never recomputed from the resumed count,
  so cadence does not drift and a completed milestone is never republished. A
  replay that reaches an already published boundary adopts that artifact only
  if run, counters, data cursor and weights digest are identical; otherwise it
  publishes the next attempt and records the earlier one as a lost lineage. A
  failed publication is recorded, receipted and stops training.
- **Retention** (`latest_two_recovery_plus_pinned_v1`) runs only after the new
  checkpoint is published and verified. It keeps every pinned milestone, the
  two newest recovery states, the last good state, every protected reference
  (including a fork parent) and every state an unresolved evaluation event may
  need (matched by model-state digest). Every decision is logged with reasons.
  Retirement re-verifies the artifact and its decided manifest hash under the
  store lock, renames it atomically into `<root>/.retired`, deletes only that
  tombstone and marks the run-ledger row `retired`. Nothing is deleted by
  filename, and a failed publication retires nothing.

## Rescoring a failed evaluation from its exact checkpoint

An unresolved event (failed, interrupted, partial or due) whose live boundary
state is gone can be completed only from the retained checkpoint of exactly
that state. The checkpoint is located by model-state digest, C and step, never
"the latest". Its stored weights must hash to the event's digest, and the
rebuilt evaluator, device and scientific runtime must reproduce the crossing's
computation identity. That means the same tokenizer, inventory, scoring
policy, scorer source, device and runtime. Otherwise the request is refused and
nothing is published. The rescore is a new immutable attempt (route
`retained_exact_checkpoint_rescore_v1`) joined to the event's lineage; earlier
attempts stay visible and the canonical receipt is still the first complete
attempt.

It runs automatically at the exact budget (train, queue and at-budget resume)
under the training-state guard, bounded by the M2 per-lineage attempt limit,
and retention runs again afterwards. For a finished run,
`xlm.evaluation.rescore.rescore_from_run_checkpoint(<run checkpoint>, <event>,
inputs=..., device="cuda")` does the same offline, and
`load_run_evaluation_state(<checkpoint>)` reconciles completeness from the
durable attempts. Only LM tiers have a verified checkpoint route; a failed
search-benchmark event stays incomplete, and its checkpoint stays retained
with an explicit reason. Events at boundaries without a planned checkpoint
cannot be rescored (in the pilot: `quick_lm@1M` and `quick_lm@4M`). If one of
them fails live and training moves past it, the pilot stays
evaluation-incomplete. Whether to accept that, add 1M/4M recovery checkpoints,
or require an immediate retry is an open pilot policy decision for the user
([P35-M3 §17.8](implementation/reports/P35-M3.md#178-real-pilot-readiness)).

## Science pilot plans

[`recipes/experiments/draft_science_v1_pilot_32m.yaml`](../recipes/experiments/draft_science_v1_pilot_32m.yaml)
is the non-executable 32M pilot draft. It states every §W scientific value:

- model and seeds;
- LR, precision, runtime and producer;
- checkpoint and evaluation cadence;
- the 12 mix01 components;
- limits: 3,600 s total wall time, ≤20 GiB GPU, ≤16 GiB process-tree RSS,
  ≤8 GiB new output.

It leaves every real artifact, root, capacity input, pin and the frozen scoring
policy null. States:

| State | Meaning |
|---|---|
| DRAFT | No bindings; lists every unresolved field; never launchable. |
| BLOCKED | Bindings supplied, but something is missing, invalid, unpinned or unverifiable. No plan file is written. |
| RESOLVED | Every preflight passed; a frozen `ExecutablePlan` with a concrete `plan_hash` is written. Not authorized. |
| EXECUTABLE | `xlm experiment validate` re-verified it and an operator ticket covers exactly that hash, with new output ≤ the §W limit. |

```powershell
uv run --offline --locked --extra cuda --extra eval xlm experiment plan recipes/experiments/draft_science_v1_pilot_32m.yaml `
  --bindings <bindings.json> --profile <profile.json> --output <plan.json> --review <review.json> --snapshot-dir <snapshot>
uv run --offline --locked --extra cuda --extra eval xlm experiment validate <plan.json> [--ticket <ticket.json>]
```

Bindings (`xlm-science-pilot-bindings-v1`) take absolute local paths and
pinned identities only. `latest`, wildcards and relative paths are refused.
The planner verifies:

- the contract values exactly, and the 488 + 18,432 = 32,000,000 arithmetic;
- that every input and output lies inside its declared root, and that the
  queue's `XLM_HOME/runs` lies inside the checkpoint and temp roots;
- the tokenizer: artifact digest, fit-input hash, 32,768 vocabulary, distinct
  special tokens;
- every mix01 component, the frozen exposure plan and per-source exposure
  without repetition;
- the pinned inventories: nesting, domains covering the mixture, frozen scope;
- search inputs: tier firewall, BLiMP universe, ≤100 items per task, and the
  HellaSwag/PIQA group halves against an operator item→group mapping;
- that no validation document is in training membership;
- cold coverage: source transitions and first visits within the budget;
- the measured profile, and peak disk: retained + new checkpoint + staging
  copy + receipts, evidence, caches, logs and margin, from a measured
  checkpoint size.

Planning and validation never train, allocate a GPU or authorize anything.
The generic planner refuses pilot drafts. A RESOLVED review renders the
existing user-only `authorize`, `submit` and `queue run` commands. A BLOCKED
one renders only `LAUNCH BLOCKED` and its reasons.

## Total wall allowance and resource ceilings

`resources.total_wall_seconds` bounds the whole queue job across attempts:
startup, training, checkpoints, evaluation and recovery. It is not the
trainer's per-attempt `max_train_seconds`. The queue persists consumed time in
`<job>/wall_allowance.json` about once a second while the worker runs, so a
crashed runner under-records at most about a second. A resumed attempt gets
only the remaining time as its kill limit. When the allowance runs out before
the exact budget, the job is `FAILED` with completion `INCOMPLETE`, never a
success, and a spent allowance refuses any further launch.

`resources.max_process_tree_rss_gib` is enforced by the launcher's sampling.
`resources.max_gpu_allocated_gib` is checked against free device memory
(desktop headroom) at worker start and caps the CUDA caching allocator. Both
are enforced only when declared.

## Resume and forks

Ordinary resume requires an identical policy (version, LR policy, RNG policy,
training seed and runtime block). The frozen envelope check already enforces
this. The domain API also compares `science.json`, whose absence means legacy,
before restoring any state. A legacy checkpoint cannot be resumed as
science-v1, or the reverse, without `--fork` or a new experiment. A fork across
policies does not adopt the parent's LR/RNG receipt history. A changed
checkpoint plan also refuses ordinary resume. A fork re-originates the
checkpoint plan at its committed count and protects its parent checkpoint from
retention.

For a science pilot, resume through the queue. Submit with `--max-retries 1`.
After an interrupted runner, `xlm queue run` recovers the stale job and resumes
from the newest verified checkpoint with the remaining wall allowance. The CLI
`xlm resume` keeps its C13 200,000-target smoke cap.

## Scientific comparisons (P35 Milestone 4)

A science-v1 comparison answers a single question: are these runs
scientifically comparable, and if so, what is the paired effect? It computes
from immutable evidence. It never trains, schedules or authorizes anything.
The legacy `xlm compare` / `xlm promote` (P17: suite index, two seeds) are
unchanged and keep their historical meaning; their outputs are never
re-evaluated under P35. See [P35-M4](implementation/reports/P35-M4.md) for the
full rules.

```powershell
uv run --offline --locked --extra cuda --extra eval xlm experiment compare --comparison <manifest.json> --runs <runs.json> --output <dir> [--prerequisite <prior comparison.json>]
uv run --offline --locked --extra cuda --extra eval xlm experiment report --record <dir>/comparison.json --output <dir2>
```

### The manifest

The manifest (`xlm-science-comparison-v1`) is a preregistration with 36
required keys. Nothing has a default, and an unknown key is refused. It
freezes:

- the question and track;
- the control and candidate arms with their declared intervention;
- the paired replicate roster (init, training and data seed, plus order
  manifest);
- the intended differences and required invariants, plus `fixed_values`;
- the budget and LR schedule;
- the primary endpoint (the exact budget) and primary metric with its
  verified direction;
- guardrail and descriptive metrics, and the curve metric;
- practical and NI margins with a rationale, and an efficiency threshold;
- the Bonferroni family id and size, and `ci_level` 0.95;
- the stopping, failure, final-checkpoint and promotion-rule versions;
- the M5 order-evidence slot and the scale-promotion intent.

Its hash is `identity_digest(manifest)`. Changing a margin, family size or
roster makes a new manifest. Examples, which carry no runs or results:
`recipes/science_comparisons/`.

### The runs file

`xlm-science-comparison-runs-v1` lists every attempt:

`{label, arm_id, status, failure, checkpoint (absolute | null), measurements {name: {value, source}}, synthetic}`

Failed attempts stay listed. Evidence comes from each checkpoint's receipts:
the frozen envelope, M1 LR and runtime receipts, M2 attempts under
`first_complete_attempt_v1`, the M3 checkpoint ledger and the committed data
state. Labels are never trusted.

### Rules

- **Eligibility.** Each track classifies every field as `MUST_MATCH`,
  `INTENTIONALLY_VARIED` (only if declared) or `RECORDED_MAY_DIFFER`.
  - `microbatch_grouping_v1` varies only `microbatch_sequences`. It requires
    identical global batch, update boundaries, committed per-target trace,
    per-source exposure and initialization. Final weights are *not*
    compared.
  - `data_mixture_v1` varies the mixture weights, and as their consequences
    the exposure plan, trace and per-source counts. Model, tokenizer,
    optimization, within-source order and evaluation stay fixed.
  - Any undeclared or unknown material difference is **INELIGIBLE**, with a
    field-level diff (field, control, candidate, class, reason) and no
    effect estimate.
  - CE across different tokenizers is always refused.
- **Pairing.** Pairs are keyed by the replicate identity (seeds plus order
  manifest), never by position, time or name. More than one at-budget
  attempt of the same replicate is a duplicate, not a replicate. Missing or
  incomplete pairs make the comparison INCOMPLETE.
- **Statistics.**
  - The raw delta is `d_i = candidate − control`.
  - The improvement is oriented so that positive means candidate better.
  - The interval is the mean ± `t_{1−α/(2m), n−1}·s/√n`, with
    `α = 1 − ci_level` and `m` the frozen family size.
  - With one pair there is no seed CI.
  - Zero variance never decides.
  - Item-level (benchmark bootstrap) uncertainty is reported separately and
    never decides.
- **Decisions.** In improvement terms with interval `[L, U]`:

  | Question | Result | Condition |
  |---|---|---|
  | Superiority | CLEAR_WIN | `L > δ_practical` |
  | Superiority | CLEAR_LOSS | `U < −δ_practical` |
  | Non-inferiority | NON_INFERIOR | `L > −δ_NI` |
  | Either | AMBIGUOUS | otherwise |

  A nonsignificant difference is not non-inferiority. A missing margin gives
  no decision.
- **Promotion (`xlm-p35-promotion-v1`)** has these states:

  | State | Meaning |
  |---|---|
  | `NOT_ELIGIBLE` | ineligible, or a required margin missing |
  | `SCREEN_ONLY` | screen or replication evidence |
  | `INCOMPLETE` / `PROVISIONAL` | partial confirmation, e.g. 3 of 5 pairs, or missing order robustness |
  | `CONFIRMED_50M` / `150M` / `300M` | 5, 3 and 3 fresh full-budget pairs |
  | `PROMOTE_TO_150M` / `300M` | confirmed, plus order robustness and a declared resource plan and ablations |
  | `REJECT` | clear loss or guardrail regression |
  | `AMBIGUOUS` | the interval crosses its boundary |

  A 150M or 300M confirmation needs the verified prior-scale record.
- **M5 dependency.** Until M5 exists, every run carries
  `order_manifest_id = shard_native_no_order_manifest`. Any rule requiring
  order robustness therefore reports it BLOCKED (NOT RUN); source-seed
  variation cannot satisfy it.

### Outputs

`comparison.json` (hashed record), `report.md` and `summary.csv` hold the §U
columns: eligibility, pairs, delta, SD, CI, multiplicity, curve area,
guardrails, declared throughput and VRAM, failures, completeness, decision
and promotion state. Missing values show as `n/a`, `NOT RUN` or `PARTIAL`,
never 0. A partial confirmation's cells are prefixed `PROVISIONAL n/N`.
`report` re-renders a verified record; it refuses an altered or legacy one.
