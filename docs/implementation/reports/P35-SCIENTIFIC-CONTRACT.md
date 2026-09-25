# P35 — Final scientific training contract and XLM research program

Date: 2026-09-25. Contract ID: **xlm-science-v1** (specified here; runtime
enforcement awaits the handoff). Audited branch: `research/p35-scientific-contract`.
Audited engineering HEAD: `febbf8b98e2e2e216cfdab62eef911e7b6d91d09`, clean on entry.
Work and outputs are confined to `G:\Project\xlm-p35-scientific-contract`.

**Decision:** use controlled statistical reproducibility, select a microbatch by
paired quality/cost experiments, and use an explicit positive warmup LR on the
first update of NEW research runs. Establish and calibrate the 50M training
recipe, compare data mixtures, then freeze data before novel-model research.
No trained-model improvement, optimal mixture, optimal LR or optimal batch is
established by this review.

This is a scientific specification, not a production-code release. P34 remains
engineering-frozen. No production defaults, recipes, dependencies or training
code changed. No network, installation, acquisition, useful-model training,
push or merge occurred. One synthetic attention diagnostic was run; it does not
constitute a training experiment. See [implementation handoff](../handoffs/P35-OPUS-HANDOFF.md).

## A. Current recipe audit and scientifically significant gaps

Authoritative sources: [model presets](../../../recipes/models/50m.yaml),
[50M draft](../../../recipes/experiments/baseline_50m.yaml),
[150M draft](../../../recipes/experiments/baseline_150m.yaml),
[300M draft](../../../recipes/experiments/baseline_300m.yaml),
[C01–C13](../../../CONTRACTS.md), and the executed code listed below.

| Field | 50M | 150M | 300M |
|---|---:|---:|---:|
| Unique deployed parameters, meta-module count verified in P35 | 49,883,648 | 149,942,016 | 299,418,624 |
| Blocks | 10 | 18 | 24 |
| Width | 512 | 768 | 1,024 |
| Heads / head dimension | 8 / 64 | 12 / 64 | 16 / 64 |
| SwiGLU intermediate width | 1,472 | 1,984 | 2,240 |
| Context / vocabulary | 512 / 32,768 | 512 / 32,768 | 512 / 32,768 |
| Base LR (uncalibrated anchor) | 0.001 | 0.0006 | 0.0003 |
| Warmup valid targets | 10,000,000 | 30,000,000 | 60,000,000 |
| Cosine horizon valid targets | 1,000,000,000 | 3,000,000,000 | 6,000,000,000 |
| Actual draft stopping budget | **128,000,000** | **1,000,000,000** | **6,000,000,000** |
| Minimum/base LR ratio | 0.1 | 0.1 | 0.1 |
| Nominal valid targets/update | 65,536 | 65,536 | 65,536 |
| Microbatch in draft | null, unresolved | null, unresolved | null, unresolved |
| Checkpoint interval in draft | 16,000,000 targets | same | same |
| Declared evaluation interval | 16,000,000 targets | same | same |

All three: bias-free pre-RMSNorm decoder, RoPE, SwiGLU, tied input/output
embedding, zero dropout; AdamW, betas `(0.9, 0.95)`, epsilon `1e-8`, weight decay
`0.1`, norms exempt, embeddings decayed, global gradient clip `1.0` after actual
valid-target normalization. CE has no trainable auxiliaries. Parameter count is
`V*d + L*(4*d*d + 3*d*f + 2*d) + d`; module counts, not rounded size labels, are
authoritative. Optimizer implementation flags such as `foreach`/`fused` are not
explicitly passed by `optimizers/adamw.py`; record resolved behavior/runtime.

Draft precision is `bf16_fp32_master`; compile and activation checkpointing are
false in all three drafts. This is a recipe setting, not a certificate that all
large-model shapes fit. Attention presets say `profile_required`, not a selected
kernel. P34's certified 50M execution is B8/SDPA, with memory-efficient attention
observed, and explicit `process_depth1` giving 45,679 targets/s. Producer default
remains `off`; new plans select it explicitly with content verification enabled.
B8 corresponds to 16 accumulation calls only when all 128 windows have 512 valid
targets. Partial/source-boundary windows can require more calls.

The drafts are **not executable frozen experiments**: pool, tokenizer, exposure
plan, profile, evaluation policy, disk bounds and authorization remain unresolved.
The historical 1B/3B/6B numbers are schedule horizons, not evidence of training or
unique corpus availability. The P33/P34 actual-size synthetic smokes do not fill
these artifact gaps.

Current controls and limitations:

| Concern | Actual implementation / implication |
|---|---|
| Initialization | `training/components.py` seeds Python, torch and NumPy from `init_seed` (101 in drafts), constructs on CPU then moves to device. `initialization.py`: normal std 0.02, residual projections scaled by `1/sqrt(2L)`, norm gains 1, tied weights initialized once. No independent training-RNG field exists. |
| Data order | Draft `data_seed=20260918` must agree with mixture/source seed. `QuotaScheduler` hashes seed/source for deterministic deficit tie order. `MixtureBatcher` reads increasing token offsets within each frozen source shard; epoch repetition restarts at offset zero. **Changing data_seed does not establish a shuffled within-source document order.** |
| Packing | The executed mixture stream builds windows within a selected source; EOS document boundaries within those windows may be crossed. Source-local windows, carry, source visit caps and exposure-plan block sizes are part of identity. The phrase `causal_stream_eos` alone is insufficient to imply a globally interleaved document stream. |
| Sampler/checkpoint | Committed per-source cursors, epochs, carry, scheduler deficits, counts and trace digest are serialized. P34 prefetch is speculative; only consumed state commits. Exact data replay is required even when CUDA weights are not bit-exact. |
| Resume | Checksummed publication; model/objective/optimizer/schedule/scaler and Python/NumPy/CPU/CUDA RNG restored; frozen execution/inputs checked. Changed horizons/inputs require explicit lineage. Numerical replay depends on environment/backend. Periodic next checkpoint is currently recalculated from resumed committed count, so cadence can shift. |
| LR | `trainer.py` calls optimizer before applying schedule at new committed count. First update uses full base LR. `TrainingStepMetrics.learning_rate` records the **next** LR, not the one just used. This is a scientifically misleading log field; preserve historical interpretation and fix by versioned metadata. |
| Evaluation | Native read-only likelihood, summed validation NLL, perplexity, text BPB, per-source diagnostics; pinned harness 0.4.13 with declared input/coverage receipts, task tiers and protected-final workflow. `evaluation.every_valid_targets` exists in config but no trainer callback consumes it. A declared cadence does not prove evaluations happened. |
| Existing comparison | Track checks, paired cluster bootstrap, learning curves, reports and promotion exist. Bootstrap v2 headline uses the smallest seed pair, with seed spread separately. It is **not** the multi-seed mean-effect CI required here. Promotion defaults (+1 suite point / 10% compute; minimum 2 seeds) are old policy, not P35 confirmation. |

These are the scientific blockers for a NEW baseline, not a reason to reopen
P34 acquisition, IPC, trace, gradient-transfer or checkpoint engineering.

## B. Experiment identity and valid comparisons

An experiment is a preregistered question plus a frozen arm configuration,
replicate tuple, exposure plan, evaluation policy and stopping/decision rule.
A run is one execution attempt of an arm/replicate. Retries are linked attempts,
never independent seeds. A comparison is an immutable manifest mapping these
runs to a parent baseline, permitted differences and an estimand.

`experiment_id = SHA256(canonical scientific manifest)`; execution receipt IDs
bind captured source, environment, data and actual checkpoint. Reuse existing
artifact digests/canonicalization, not a second store. Git SHA alone cannot
identify uncommitted or captured code. A label such as `baseline50` is not an ID.

| Classification | Fields / rule |
|---|---|
| MUST MATCH within a pair unless the named intervention allows a difference | Model config/count/initialization policy; init and training seed; actual initial tensor hash for same architecture; data membership and canonical content; tokenizer serialization/hash/vocabulary; mixture/quota and ordered exposure stream; source-local packing/position/mask policy; context; global valid-target batch and final partial-batch rule; target budget and update count; objective/auxiliary supervision; optimizer/groups/betas/eps/decay/clip; entire LR curve/horizon/first-step policy; microbatch/accumulation; precision/TF32/reduction modes; attention/determinism; compile/checkpointing; evaluator/checkpoint selection, splits, score normalization and inference precision. |
| INTENTIONALLY VARIED | Only an enumerated intervention: e.g. B8/B16/B32 grouping; mixture weights and consequent selected data/order; architecture and corresponding parameter count within a declared cap; objective; optimizer plus equal tuning allowance; tokenizer under a separate byte/compute track. Changed source code is expected for architecture work: freeze the common harness/base SHA, hash each arm and whitelist the reviewed intervention diff. No claim that different architectures have identical initial tensors. |
| RECORDED BUT MAY DIFFER | Run IDs/timestamps, physical artifact locations, wall time, measured VRAM/RSS/storage/energy, throughput, checkpoint IDs and legitimate interruption history. Git commit labels may differ when executed code is otherwise identical. Different hardware/runtime is allowed for external replication, **not pooled into the primary same-machine comparison** without a preregistered blocked design. |

PyTorch, lockfile, CUDA/cuDNN/driver, GPU, OS and optimizer-kernel settings MUST
MATCH for this single-4090 primary paired series. Across an independent
replication they are recorded differences and results form a separate stratum.
Resume history may differ only under the same frozen semantics, verified exact
data accounting and documented numeric policy; report uninterrupted-only
sensitivity if interruptions correlate with arm. More resumed attempts are not
more replicates. Missing values, undeclared differences and unmatched target
checkpoints yield **INELIGIBLE**, not a statistical loss or win.

## C. Reproducibility standard

**Do not require universal bit-for-bit replay for ordinary or confirmatory
research.** Require exact experiment identity, exact consumed-data replay,
controlled randomization and replicated statistical effects. Numerical equality
is a powerful debugging test, not the definition of learning improvement.

| Use | Policy |
|---|---|
| Ordinary 50M search | Statistical mode, explicit memory-efficient SDPA and seed tuple; no silent kernel-policy changes. |
| Confirmatory 50M | Same declared statistical mode in both arms, fresh paired seeds, training-seed uncertainty. Same-seed reruns measure nondeterministic variance, not extra seed replicates. |
| Debugging/recovery validation | Strict deterministic algorithms (errors, not warn-only), fixed environment, exact CPU tests and bounded CUDA replay wherever supported; isolate unsupported operations rather than claim determinism. |
| 150M/300M | Statistical mode and new multi-seed confirmation at each scale; reprofile capacity/backend, report any resource-motivated mode changes in both arms. No inheritance of 50M bitwise claims. |

Kernel noise is not guaranteed negligible or unbiased. Before confirmation,
repeat two baseline seed tuples once at the 128M screen budget, with unchanged
inputs, to estimate same-seed variance separately. If that variation is comparable
to the intended gain or pairing collapses, improve the deterministic configuration
or increase the preregistered replication budget. Do not subtract estimated noise
from observed effects or use a same-seed replay as an independent training seed.

## D. Deterministic attention: measured diagnostic and recommendation

P35 [probe](../evidence/P35/probe.py) and [raw JSON](../evidence/P35/probe.json)
use synthetic BF16 Q/K/V and upstream gradients at actual attention dimensions:
50M `(8,8,512,64)`, 150M `(16,12,512,64)`, 300M `(8,16,512,64)`.
Projected/transposed layout, zero dropout; causal flag and an explicit causal
mask with one partial sequence. Three warmups and 12 repetitions per case, forced
`SDPBackend.EFFICIENT_ATTENTION`, with deterministic algorithms off/on. No model
optimizer steps, model weights, training loss or useful-model training.

Both modes execute `aten::_scaled_dot_product_efficient_attention` and
`aten::_efficient_attention_backward` (full operator list in JSON). Strict
deterministic mode is available without a math/flash backend switch in this
installation. The profiler identifies the operator family, not an exact compiled
CUDA subkernel symbol. All six deterministic cases had one forward and one
backward digest; their forwards exactly matched ordinary mode. Ordinary 300M
causal backward had two distinct digests; the other cases had one in this small
sample. One digest in 12 trials never proves universal determinism.

| Shape / mask | Ordinary forward+backward ms | Deterministic ms | Observed latency increase | Peak allocated MiB, both modes |
|---|---:|---:|---:|---:|
| 50M B8 causal | 0.659 | 0.729 | 10.5% | 46.51 |
| 50M B8 partial mask | 0.753 | 1.088 | 44.4% | 50.51 |
| 150M B16 causal | 0.902 | 0.964 | 6.8% | 137.03 |
| 150M B16 partial mask | 1.145 | 1.448 | 26.4% | 145.03 |
| 300M B8 causal | 0.945 | 1.295 | 37.0% | 90.77 |
| 300M B8 partial mask | 2.031 | 2.389 | 17.6% | 94.77 |

Medians of 12 CUDA-event measurements; isolated operator throughput reductions
are approximately 9.5%, 30.8%, 6.4%, 20.9%, 27.0%, 15.0%, respectively. There was
zero observed allocated-memory penalty within each matched case. These peaks
include retained input/gradient buffers, not full model VRAM. Peak reserved was
62/166/116 MiB for the three sizes. End RSS was 839.72 MiB; total probe wall time
14.891 s. Budget: 120 s, fixed 12 cases, allocator cap 10% of GPU RAM, tiny JSON
output. No corpus storage.

**Limit:** preflight GPU utilization was 24%, 8,895 MiB already occupied by desktop
applications. No foreign research job was identified, but exclusivity is not
established. Modes ran in fixed order; this diagnostic establishes availability
and sampled equality, **not a reliable end-to-end throughput penalty**. It also
does not cover 50M B16/B32, all Q/K strides from real RoPE, all masks, full models,
cuBLAS determinism prerequisites or cross-process replay.

Recommendation: retain statistical mode for the first pilot. Before expensive
batch selection, run one explicitly bounded, interleaved full-update calibration
at each intended shape with the strict mode and actual execution route; record
backend, all deterministic errors, at least three timing blocks, VRAM and repeated
updates. If paired full-step timing intervals establish a cost below **5%** with
no capacity loss or unsupported operations, prefer strict mode for the NEW
baseline and refreeze all arms. Five percent is an operational cost allowance,
not a scientific success threshold. Otherwise retain statistical mode. Even
cheap deterministic attention cannot replace multi-seed experiments. No current
default was changed; full-model cost remains **NOT RUN**.

## E. B8/B16/B32: the quality–cost baseline selection experiment

Treat each grouping as a new numerical treatment. B8's engineering equivalence
tolerance cannot adjudicate learning quality. Faster B32 also does not establish
equal quality.

1. After the pilot, freeze M0 artifacts, tokenizer, LR semantics and attention
   mode. Verify on authored masked/partial windows that each global update has
   identical ordered input/label/loss-mask/position/source/byte coverage and
   target trace across B8/B16/B32. **Do not compare final parameter hashes.**
2. For full windows, 128 sequences/update gives accumulation 16/8/4. Global
   target sum 65,536, single normalization and single global clip/AdamW update
   remain identical. Variable masks imply measured, not assumed, call counts.
   Call grouping, summation order, rounding, kernel shape and utilization vary.
   Do not scale LR by microbatch: global batch did not change.
3. Run all three arms for **128M targets on each of three paired exploratory
   tuples** E0/E1/E2 (§H), on a 1B horizon/10M warmup. Same initial tensor file per
   tuple, exact target order/update boundaries, optimizer and precision. All
   arms finish the same 1,954 updates including the 8,192-target last update.
   Counterbalance execution order by seed (8/16/32, 16/32/8, 32/8/16); retain
   contention/clock metadata. No trajectories from a completed short cosine.
4. Use primary held-out CE, domain losses, learning-curve area, stability and
   complete frozen development BLiMP as secondary evidence. Other MC benchmarks
   are contextual diagnostics if demonstrably responsive. Measure end-to-end
   successful targets/s, total time including evaluation, and peak VRAM.
5. Select at most one challenger against B8 for **five fresh paired 1B runs per
   arm**, fixed in advance; include the ordinary baseline control even if the
   screen favors B32. This expensive stage is performed once to establish the
   training baseline, not per hypothesis. Screening controls may be shared only
   when all identities and budgets match; their outcomes are development-exposed.
6. Choose a challenger if its simultaneous upper confidence bound on
   `CE_challenger - CE_B8` is below the frozen practical noninferiority margin,
   critical domain/BLiMP regressions pass their limits, all runs are stable, and
   measured total-cost savings exceed the preregistered operational minimum
   (5% with timing uncertainty excluding zero). A CE-superior challenger can win
   on a declared quality/cost Pareto rule even without that speed saving. A
   nonsignificant quality difference is **not equivalence**. Margins must be set
   before confirmation using §P; none can be honestly supplied from P34 timings.

Evaluation points follow §K. If no challenger qualifies, retain B8 as the
unresolved comparison control, not a scientifically proven best batch. Do not
launch the three-arm training study during agent implementation. B16/B32 capacity,
full quality and chosen baseline are empirical open questions.

## F. Canonical first-update LR policy

Let `f(t)` be the current warmup/cosine function, `C` successfully committed
targets before an update and `N` that update's actual valid targets. Set the NEW
policy to **`target_endpoint_before_update_v1`: apply `f(C+N)` immediately before
the optimizer update; advance committed counters only after success**. For fixed
full batches this is schedule-for-update-1, not counter-zero. For partial batches
it uses the actual endpoint. It avoids a zero-LR first optimizer step while
making the warmup argument explicit. It is a convention, not a claimed optimum.

| 50M first updates | Update 1 LR | Update 2 LR |
|---|---:|---:|
| Existing `legacy_base_then_postcommit_v1` | 0.001 | 0.0000065536 |
| Pre-update start counter `f(C)` | 0 | 0.0000065536 |
| **NEW canonical endpoint `f(C+N)`** | **0.0000065536** | **0.0000131072** |

Actual W=10,000,000, N=65,536: first legacy step is **152.587890625 times** the
canonical first-step LR (1/0.0065536), a one-step peak preceding warmup. Zero LR
at update zero still advances Adam moments/step count and consumes targets; it
is not an omitted update. AdamW's direct decay factor also follows LR (legacy
first factor `1-0.001*0.1=0.9999`). Actual model-quality harm is unmeasured.

Version this as a deliberate C09/C10 migration; the current schedule docstring
describes start-boundary semantics. Preserve old checkpoint behavior under the
legacy policy and reject ordinary resume into the new policy. Both new arms use
the same policy. Record **`lr_used`** per group and its counter, separately from
optional `lr_next`; historical `learning_rate` means post-update scheduled LR.
Restore LR/counters on safe pre-update failure; an in-doubt optimizer failure
still requires a fresh checkpoint reload. No silent reinterpretation of logs.

Keep 1e-3 as the pilot anchor. Before claiming a strong baseline, use equal-budget
128M screens at 0.5x/1x/2x this anchor, one E0 seed followed by E1 replication of
the top two; hold batch fixed during this LR comparison. Lock the selected LR
before fresh confirmation. The batch screen then holds LR fixed; if batch–LR
interaction is suspected, register an equal-size factorial study for all arms,
not extra tuning only for the favored batch. Repeat calibration on the selected
mixture if necessary, recording it as baseline development.

## G. Token budgets, unique text and repetition

Use decimal M/B. A target is a valid next-token position contributing to a
successful update; exclude padding/BOS/ignored targets, include and separately
report EOS. Report attempted/replayed targets independently. `C` stops **exactly**
at the hard declared budget; the final update masks the remainder. Schedule
horizon is a separate scientific field; neither budget nor horizon is unique
text. The 1B/3B/6B family is a scaling-inspired planning default of about 20
exposures/parameter, not an empirically established optimum or requirement to
invent unavailable data.

| Stage | Hard target exposure cap per run | Schedule horizon / warmup | Interpretation |
|---|---:|---:|---|
| Agent correctness smoke | <=200,000, toy model | explicit fixture schedule | C13: <=10 min, one GPU process, <=2 GiB new artifacts |
| User first 50M pilot | 32,000,000 | 1B / 10M | integration and training-health check |
| Cheap idea falsification (user) | up to 32M if needed | 1B / 10M | development only |
| 50M exploratory screen | 128,000,000 | 1B / 10M | learning prefix, not converged 128M model |
| Optional finalist triage | 256,000,000 | 1B / 10M | preregister before starting; no confirmation claim |
| Full 50M control / confirmation | 1,000,000,000 | 1B / 10M | five paired seeds for robust promotion |
| 150M scale screen / confirmation | 384M / 3B | 3B / 30M | one pair to falsify; three fresh full-budget pairs |
| 300M scale screen / confirmation | 768M / 6B | 6B / 60M | one pair to falsify; three fresh full-budget pairs |

Do not splice an exact-ended 128M run into a 1B trajectory and call it identical
to uninterrupted 1B: the partial update changes an intermediate boundary.
Confirmations initialize afresh. Optional continuation requires a declared fork
or a preplanned full-update prefix ending at an aligned boundary, and remains
development-exposed.

Before a plan is executable, tabulate per-source canonical bytes, unique admitted
documents/lineages, unique token positions available under this tokenizer,
planned valid/content/EOS targets, expected visits and repeated targets/bytes.
If source i has usable targets U_i and quota Q_i, exposure ratio Q_i/U_i is a
planning summary, not proof each document is visited equally. Tokenizers change U_i.
Cross-source aliases must not double-count unique text.

M0 currently errors on exhaustion (maximum document exposures 1). Retain this for
the first pilot. For later budgets either acquire/prepare separately authorized
data, lower **all arms'** budget before launch and label it a different study, or
freeze explicit bounded repetition for all arms. Set per-source maximum passes
and aggregate repeated-target ceiling in the resolved exposure plan. Never
renormalize or replace an exhausted component. Keep the existing deterministic
within-source epoch order unless a separately versioned order policy is adopted;
do not claim reshuffling. Report realized repeat shares and per-source coverage,
not just “trained on 3B tokens.” Repetition changes the hypothesis and may overfit;
more exposures are not guaranteed useful.

## H. Seed policy and what actually varies

Separate initialization, post-construction training RNG, source scheduling,
within-source order manifest, tokenizer-fitting sample, split allocation and
analysis RNG. Freeze tokenizer/split seeds across the entire research family.
Pair init/training/order seeds between arms; vary replicate tuples independently
across runs. Same architecture uses identical initialization bytes. A changed
architecture uses its frozen initialization distribution with matched seed
labels, without claiming tensor-level pairing of unrelated weights.

Explicit roster (new training seed support is required):

| Tuple | init | training RNG | source/data seed | Use |
|---|---:|---:|---:|---|
| P0 / E0 | 101 | 10001 | 20260918 | pilot; exposed exploratory control |
| E1 | 211 | 10002 | 20260919 | second screen |
| E2 | 307 | 10003 | 20260920 | third batch-screen tuple |
| C0 | 401 | 20001 | 20261001 | fresh confirmation |
| C1 | 503 | 20002 | 20261002 | fresh confirmation |
| C2 | 601 | 20003 | 20261003 | fresh confirmation |
| C3 | 701 | 20004 | 20261004 | fresh confirmation |
| C4 | 809 | 20005 | 20261005 | fresh confirmation |

Ordinary idea screen: one pair E0; replicate shortlisted ideas on E1 before
spending full-budget compute. Batch selection uses three exploratory pairs.
50M confirmation is a **fixed five fresh paired tuples**; first three may be
reported as provisional progress but cannot trigger formal early success. A
first baseline inventory may start with C0–C2, then complete C3–C4; confirmation
decisions wait for all five. Independent 150M and 300M confirmation each uses
three fresh tuples recorded before the scale run, derived from a preregistered
scale-specific namespace; do not tune against them.

Known C-seed results cannot remain “untouched” for an unlimited adaptive search.
Retire each confirmation block after its declared family; create a fresh seed
block for the next adaptively chosen family. Reused baseline controls reduce
cost but disclose shared-control dependence and loss of seed blindness.

**Scope of data-order noise:** current `data_seed` varies source scheduling;
within-source order is fixed by shards. That is a valid conditional comparison
if stated. Before calling a result robust to document order or promoting to
150M, confirm using at least two independently frozen document-order manifests
over the same corpus membership, assigned across the five C tuples (e.g. C0/C2/C4
order A, C1/C3 order B). Source seeds still vary independently. These manifests
need a narrow, versioned ordering implementation or separately prepared artifacts;
they are not present merely because a seed field exists. This is a scientific
capability gap, not a request to optimize the loader. Paired arms use identical
order within a tuple. Larger-scale three-tuples cover both order strata.

## I. Evaluation hierarchy and primary endpoint

Primary at fixed tokenizer: **endpoint held-out text cross-entropy in nats per
scored text token**, on a frozen validation distribution at the declared target
budget. Define domain weights before mixture search: equal weight across the
admitted M0 source domains for source-balanced validation, with source aliases
grouped where they describe the same domain. Freeze the exact domain membership
and weights, not candidate mixture weights. Aggregate NLL/token sums within each
domain before weighting. Also report micro-averaged NLL/token over the full
validation inventory and every domain separately. A candidate cannot change its
primary distribution to match its training mix.

Text CE excludes structural EOS/BOS and padding. Report EOS-inclusive training
CE separately; do not compare it numerically as the same validation metric.
Perplexity `exp(CE)` is a presentation of the same information, not a second
independent endpoint. Cross-tokenizer research uses **text BPB** on identical
canonical bytes and scored-byte coverage as primary, with matched byte/context
and separate matched-compute tracks. BPC, if requested, must declare the character
unit (Unicode code points, for example); do not label UTF-8 BPB “BPC.” Raw token
perplexities from different vocabularies are ineligible as a quality comparison.

| Purpose | Ranking / usage |
|---|---|
| Early health | Finiteness, training CE and fixed quick LM validation CE, gradient/clip/target counters; no capability claim. |
| Mixture choice | Fixed-distribution CE first; per-domain CE/BPB and repeat exposure second; BLiMP development macro accuracy and margins as syntax guardrail; MC tasks only supportive. |
| Architecture/loss | Independent ordinary CE (never substitute the candidate objective), domain metrics and learning curve; BLiMP secondary. Auxiliary losses/teachers/extra targets counted. Tokenizer track switches to BPB. |
| Final capability report | All four benchmark components and coverage, chance references, CIs and declared suite index, alongside CE/BPB and costs; no popular-task-only winner selection. |

BLiMP/PIQA chance references 0.5; HellaSwag 0.25; ARC uses actual mean `1/n_choices`,
not a blanket 0.25. Prompt/answer biases mean an untrained model need not hit
these references. At 50M, ARC/HellaSwag/PIQA may be near chance; large item noise,
correlated groups and tiny margins can make them insensitive to useful LM gains.
For illustration, an independent 100-item binary accuracy at chance has standard
error about 5 percentage points; real clustered uncertainty may be larger.
Measure responsiveness across baseline checkpoints before assigning these metrics
selection power. Retain all metrics even when unhelpful; do not postselect tasks.
The four-task chance-adjusted index remains a secondary reporting statistic;
P35 does not change its weights or scorer after results.

## J. Validation/test hygiene

Preserve C05 and [evaluation policy](../../../EVALUATION_POLICY.md): deduplicate
and group related documents before splitting; keep all benchmark splits out of
gradient training and tokenizer fitting. Freeze a training pool and a 50 MB
canonical-English LM validation target, including a nested fixed 5 MB quick
subset. Record actual bytes at document boundaries and per-domain coverage.
Training-sample selection must not use validation scores per document. Add a
separate group-disjoint LM confirmation inventory before the first search; its
membership and byte cap must be frozen, never carved out after observing wins.
Proposal: another 50 MB if inventory supports it; otherwise preregister the
smaller measured inventory and its reduced precision before running.

Quick/development LM metrics may be viewed at scheduled points in every run.
Full development LM is the screen endpoint. Confirmation LM and confirmation
benchmarks are opened only for frozen finalists; they then become exposed for
future development, with access history retained. A separately isolated final
store is reserved for the final reporting release, not the 50M tuning loop.

| Task | Search | Confirmation | Final reporting |
|---|---|---|---|
| ARC-Easy | train | validation | test |
| HellaSwag | grouped half of train | disjoint grouped half of train | validation |
| PIQA | grouped half of train | disjoint grouped half of train | validation |
| BLiMP | ~20% whole subdatasets | ~20% whole subdatasets | remaining ~60% |

Use actual frozen IDs, revisions, split receipts, group membership and coverage.
An item limit is a partial diagnostic, never a full benchmark. Public final data
are not inherently secret; same-user directories do not create isolation. If an
operator-side protected evaluator and decontamination receipts are unavailable,
mark final reporting **development-exposed**, not sealed. No corpus or benchmark
content was read by P35. Contamination checks report methods and residual
uncertainty; an exclusion receipt is not proof of zero overlap.

## K. Evaluation cadence by targets

Use absolute planned token thresholds, evaluated at the first **natural completed
optimizer boundary at or above** the threshold. Record actual C. Do not split
updates merely to evaluate or checkpoint. For identical budgets/batches these
actual points match across arms; otherwise align explicitly or reject pairing.
Endpoint runs stop at the exact budget including its partial final update.

| Run | Quick LM validation thresholds (M targets) | Full development LM | Development benchmarks |
|---|---|---|---|
| 32M pilot | 0, 1, 4, 8, 16, 32 | 0, 32 | 0 and 32: frozen small search subset, marked partial |
| 128M screen | 0, 1, 4, 8, 16, 32, 64, 96, 128 | 0, 32, 128 | complete frozen search BLiMP at 128; MC diagnostic at 128 for finalists |
| 1B 50M | 0, 1, 4, 8, 16, 32, 64, 128, 256, 512, 768, 1000 | 0, 128, 256, 512, 768, 1000 | frozen search suite at 256 and 1000; confirmation suite at 1000 only for registered finalists |

Full confirmation LM is an endpoint-only primary for the frozen confirmatory
decision. Screen learning-curve area uses quick LM at common points; freeze the
trapezoidal integral versus linear target count from 16M to budget, divided by
that interval. Exclude the untrained point from this area so early scale does
not dominate. Report endpoint and area separately; do not choose whichever wins.

Evaluation time goal: <=5% of training wall time, measured in the pilot. This
is a planning goal, not permission to silently skip metrics or shorten item
coverage. If it fails, refreeze the quick subset/cadence before comparisons;
keep confirmation endpoints. Evaluation must restore train mode and RNG/state,
or run from a saved checkpoint in a separate bounded process. A single local
queue serializes GPU work; no concurrent train/eval jobs on the 4090.

## L. Checkpoint policy

Use verified fast NVMe storage selected by the operator. Normal recovery
checkpoint every **64M targets** (~23.4 raw minutes at P34 B8 rate), rounded to
the next update boundary; maintain the two most recent verified recovery
checkpoints. The 32M pilot overrides this with 8/16/32M. Immutable milestone
checkpoints for a 1B run: initial model/seed receipt, 128/256/512/1000M; optionally
768M only if named analysis needs it. Validation can score in memory without
retaining a full optimizer checkpoint at every quick point.

Retain full resumable state at milestones and terminal/interrupted boundaries;
initial weights need no empty optimizer payload. Temporary evaluation checkpoints
are recyclable after their score/coverage receipts and checksums are durable.
Never remove the last good recovery checkpoint, a comparison input, or a lineage
parent still needed by dependent artifacts. Retention uses explicit bounded
cleanup through existing artifact mechanisms; immutable IDs are never overwritten.

At 50M, six retained full states at roughly 600 MB plus one in-flight publication
already need several GB. Reserve **8 GiB new output** for pilot/baseline operational
checkpoint handling, plus separately measured input/cache/scratch space; shared
corpus storage is not zero cost. Recalculate from measured sizes and inventory,
especially at 150M/300M. Counts are caps, not a promise that eight GiB always fits.
If publication plus evaluation exceeds the plan, stop safely and revise the plan;
do not introduce async checkpoint redesign in this milestone.

## M. Data-mixture research protocol and data freeze

Registered anchor `recipes/mixtures/mix01.yaml` is **draft_unvalidated**:

| Component | Valid-target share |
|---|---:|
| essential_science / essential_practical / essential_prose | 10% / 10% / 5% |
| nemotron_organic_high / nemotron_organic_medium_high | 15% / 5% |
| finepdfs_en / synth_en_explanations | 15% / 15% |
| nemotron_wiki_rewrite / finewiki_en | 8% / 5% |
| ifm_behaviors_general_planning / common_pile_prose / simple_stories | 5% / 5% / 2% |

M1 less synthetic (5%, Essential-Web 35%); M2 more synthetic (30%, Essential-Web
10%); M3 PDFs 25% and organic Nemotron 10%; M4 substitutes verified TxT360 web
for organic Nemotron 20%; M5 shifts five points from Essential science to
practical. `mix01_no_ifm` and `mix01_views` are additional named alternatives,
not automatic substitutes for missing anchor components. A registered name does
not prove admission, license permission, sufficient quantity or adapter validity.

First calibrate a provisional training recipe on M0. Then screen M0–M5 (only
admitted feasible arms) at 128M with E0, identical model/tokenizer/LR/batch/horizon,
repeat the top two against M0 on E1, and freeze one finalist for fresh full-budget
confirmation. Rank on fixed-domain CE with per-domain/BLiMP guardrails and compute
accounting (§P), not a validation distribution resampled to each mix. Count all
attempted arms for multiplicity; absence of M4 data means M4 is BLOCKED, not M0.

Hold target exposure, tokenizer fit, initial tensors and seed structure fixed.
Different mixture weights necessarily change which tokens and their order are
seen; **do not require identical global token sequences across mixtures**.
Pair the same within-source order manifests and scheduling algorithm so common
prefixes share document identities wherever the quotas allow. Freeze cap policy
and per-source admission bounds before seeing scores. Token-proportional weights
are the experiment; capped weights describe a different precomputed mixture.
Exhaustion cannot silently implement a cap by redistribution.

Fit one balanced 32,768-vocabulary byte-level BPE on admitted training data only,
before mixture search. Freeze fit membership/normalization/seed/serialization.
Count repeated text and source-specific loss/contamination separately; gains
from memorizing a small source must not be presented as broad generalization.

**“Data frozen”** means a signed/hashed manifest of source revisions/admission,
canonical train membership and lineage/dedup graph, filtering/exclusion/split
versions and receipts, clean text/byte identities, tokenizer fit and serialization,
per-source shards/index/span checksums, mixture weights, target quotas, order
manifests/seeds, repeat/cap policy, packing/cross-document rules, and fixed
development/confirmation evaluation inventories. A changed member, filter,
tokenizer, source weight or repetition rule creates a new data baseline version.
Architecture/loss/optimizer work uses this frozen bundle; tokenizer research
reuses canonical data but is explicitly a new tokenizer comparison track.

## N. XLM-50M BASELINE v1

Baseline-v1 is an immutable **bundle**, not a single lucky checkpoint: chosen
50M architecture/count, admitted frozen winning data, tokenizer, calibrated LR
and new first-step policy, scientifically selected microbatch, global target
batch, AdamW/groups/clip, precision/attention mode, frozen code/lock/environment,
five seed run receipts at 1B targets, learning curves and failures, independent
evaluation/coverage receipts, resource profiles, statistics and decision record.
Include selected checkpoints at the declared endpoint, not best validation step.
Baseline v1 is not created by this report.

Before novel work: show scoring/initial-model sanity, exact target accounting and
resume, pilot health, LR neighborhood calibration, batch selection, mixture
comparison, frozen evaluation hygiene, baseline variance/order coverage and no
unresolved correctness issue on the claimed path. Baseline may still be imperfect;
publish its tuning allowance and known limitations. Any later retuning gets an
equally tuned control or a new baseline version.

## O. Novel-idea funnel

| Boundary | Evidence needed to proceed |
|---|---|
| Idea to falsification | One mechanism/hypothesis, predicted failure, novelty card with dated prior-art evidence or explicit uncertainty, no-op control, inference interface/capability and resource accounting. No prior-art network search occurred in P35. |
| Falsification to 128M screen | Offline authored tests show objective/gradient/state semantics, no gold-label leakage, correct accounting, finite bounded execution; gain is not a broken baseline fix. |
| Screen to replicated screen | E0 learning health, primary/curve evidence plausibly favorable, costs admissible; no isolated noisy benchmark selection. |
| Replication to full 50M | E1 supports the mechanism; freeze candidate implementation/hyperparameters, family size, primary estimand, margins, five fresh tuples and analysis before runs. |
| Full 50M to promotion | Complete §P decision, order robustness, no missing failures, equal tuning budget, independent CE and ablations identifying the active contribution. |

Architecture counts include deployed inactive parameters, routers and auxiliary
inference state; separately count training-only parameters, optimizer slots and
loss/teacher compute. Match parameter/resource bounds, and report both matched
target and matched-compute views where appropriate. Extra teachers/inputs/targets
are a supervision intervention, never an undisclosed architecture win. Optimizer
research receives equal search budgets. Novelty and effectiveness are distinct;
a no-op plugin demonstrates a control only.

## P. Effect sizes, uncertainty and decision rules

The confirmatory primary is the paired endpoint difference
`d_s = CE_candidate,s - CE_control,s` on fixed confirmation LM (lower is better).
Report all `d_s`, their mean, sample SD and a **two-sided 95% Student-t interval
over independent paired training tuples**, df=n-1. This is a parametric small-n
interval, not a distribution-free proof; five seeds remain imprecise. Show a
paired sign summary and leave-one-pair-out sensitivity. With five pairs the
smallest two-sided exact sign/randomization p-value is 2/32=0.0625, so do not
claim a distribution-free p<0.05 confirmation from five signs alone.

Training seeds are the independent replication units. A million validation tokens
cannot turn one training seed into a narrow between-training-seed interval.
Separately bootstrap aligned validation document/lineage clusters for finite-text
uncertainty; benchmark bootstrap resamples aligned example groups and whole BLiMP
subdatasets, preserving pairing. Default analysis RNG 350035, 10,000 bounded
resamples. Report seed and evaluation intervals separately; optionally a frozen
hierarchical bootstrap (seed pairs then clusters) as sensitivity, not replacement
for the primary interval. Preserve missing/partial-coverage refusal.

Before confirmation, use baseline screens, held-out calibration data and same-seed
replays to estimate scale of paired variance `s_d`; a rough normal-approximation
80%-power detectable effect is `(z_(1-alpha/2)+z_0.8)*s_d/sqrt(n)` (about
2.8*s_d/sqrt(n) for alpha .05). Small-n t critical values and variance-estimation
uncertainty make actual requirements larger. Label this a planning approximation.
That expression concerns detecting a difference from zero; clearing a practical
improvement margin requires roughly that margin plus the detectable difference,
not merely the zero-null calculation.
Also tabulate baseline endpoint SD; it is not automatically the paired SD.
Same-seed baseline replay differences estimate only a numerical-noise component;
they cannot estimate candidate-by-seed interactions. Use exploratory paired
candidate differences conservatively for power planning and retain their
selection-bias caveat; confirmatory confidence intervals use only fresh pairs.

Define two quantities **before candidate confirmation results**:

- `delta_practical`: the smallest CE improvement worth the added compute/complexity,
  calibrated using baseline learning-curve gains per measured hour and downstream
  responsiveness. It may be larger or smaller than the statistically detectable
  effect; if smaller, increase replication or call the study underpowered.
- `delta_NI` and per-domain/BLiMP regression margins: largest quality loss tolerable
  for an explicitly priced efficiency gain. Baseline variance informs feasibility,
  **not permission to accept a larger quality loss**. Freeze domain-owner or
  operator rationale in a short decision record. No valid numerical margins can
  be inferred from the synthetic P34 runs. Unresolved margins block a formal win.

Clear quality win: eligible, completed fixed-budget pairs, upper primary CI
`< -delta_practical`, guardrails within preregistered simultaneous bounds, stable
across order strata and no unexplained catastrophe. Report weaker “positive mean,
uncertain practical gain” separately. Clear loss: lower CI `> delta_practical`,
or a scientifically fatal guardrail/stability failure. Efficiency win: upper
quality CI `< delta_NI` plus measured cost benefit above its frozen threshold and
guardrails; absence of significant degradation is insufficient. Ambiguous:
interval crosses decision boundary or evidence conflicts; retain control, archive,
or register a new adequately powered replication. Never keep adding seeds until
a favorable p-value appears. Crossings in learning curves motivate a new horizon
study, not cherry-picked checkpoint selection.

Primary family at confirmation contains at most two candidates against the frozen
control. For k candidates use Bonferroni simultaneous two-sided intervals at
`1-0.05/k` (or one frozen equivalent procedure); do not combine an unadjusted
primary CI with adjusted p-values. Shared controls and guardrail families are
explicit; correct familywise guardrail bounds separately. Secondary benchmark
effects remain descriptive unless preregistered in the claim family. Reusing the
same final set for many “confirmations” does not restore nominal coverage.

## Q. Compute-aware costs on one RTX 4090

Planning arithmetic uses **45,679 successful targets/s**, P34 B8 synthetic
production measurement, not a new live-data timing. All figures exclude
evaluation/checkpoint/startup/interruptions and may be optimistic on cold real
data. Use measured pilot overhead before booking a campaign; 10–20% scheduling
reserve is an assumption, not measured overhead.

| Work | Targets | Raw hours at B8 rate |
|---|---:|---:|
| Pilot | 32M | 0.195 (11.7 min) |
| One 128M screen arm | 128M | 0.778 |
| One fresh control/candidate screen pair | 256M | 1.56 |
| Three batches × three 128M tuples | 1.152B | 7.01 |
| Optional 256M triage arm | 256M | 1.56 |
| One 1B run | 1B | 6.08 |
| Three-seed baseline inventory | 3B | 18.24 |
| Five-seed baseline | 5B | 30.41 |
| Five-pair 1B confirmation, both arms fresh | 10B | 60.81 |
| Candidate confirmation with five valid reusable controls | 5B new | 30.41 incremental |

This makes full confirmation deliberately selective; most ideas cost a pair of
screens, not five full pairs. B16/B32 rates are **not assumed** in this budget.
For 150M/300M do not extrapolate 50M throughput: cost is
`arms * seeds * target_budget / measured_scale_targets_per_second`, plus measured
overhead. P33 larger-size smokes are preliminary capacity evidence, not a campaign
rate forecast. Require a fresh bounded profile before scale authorization.
Report CPU RSS, VRAM allocated/reserved, input/cache/temporary/final disk, read/write
bytes, successful/attempted targets and wall times, not only GPU kernel speed.

## R. 50M → 150M → 300M promotion

150M entry requires one frozen innovation (or explicitly preregistered bundle),
five fresh paired 1B 50M results satisfying §P, order robustness, no unresolved
fairness issue, ablation/control support and a scale-specific resource plan.
Train the larger control and candidate **from initialization** under matched 3B
horizons and equal LR-tuning allowances. One 384M pair may falsify a severe
scale failure; passing it only licenses three fresh paired 3B confirmations.
All three pair effects should support the direction and the primary CI/materiality
and critical-domain gates must pass; otherwise evidence is provisional. Three
seeds can be inconclusive even with all signs favorable.

300M entry requires that complete 150M confirmation, acceptable measured memory/
cost, a scaling rationale and no regression hidden by pooled sizes. Run only the
strongest one or two candidates, one 768M diagnostic pair if useful, then three
fresh paired 6B confirmations. 300M superiority is claimed only from the 300M
comparison. Both arms use the same necessary checkpointing/microbatch policy
unless that difference is explicitly the efficiency treatment.

A sign reversal, meaningful domain loss, instability, disproportionate resource
cost or failure of the frozen primary rule is scale-dependent failure or
inconclusiveness. Report the useful smaller-scale result without “scales up”
language. Combining positive 50M results with negative 150M results into a pooled
win is prohibited. No requirement forces every idea to reach 300M.

## S. Multiple-hypothesis discipline

Maintain one lightweight append-only idea/attempt ledger: intent, parent, changed
variables, tuning budget, every attempted run and failed/rejected result. Freeze
a batch of exploratory hypotheses, shortlist at most two for a confirmation
family, lock code/hyperparameters before fresh seeds, and use the adjusted rule
in §P. Search results rank hypotheses; they do not supply confirmatory p-values.
Report both selected and rejected trials to expose winner's curse. Confirmatory
holdouts and seeds have an exposure ledger. A result used to revise a candidate
becomes development evidence; the revision needs a new confirmation block.
Final protected benchmarks are queried only at a frozen release, with request
quotas and no iterative tuning on returned scores.

## T. Provenance schema (specification, not a new implemented schema)

Reuse execution envelopes, artifact manifests, checkpoint provenance and evaluation
receipts. Add a small versioned scientific manifest with these required groups:

| Group | Required fields |
|---|---|
| Intent | science version, experiment/arm/comparison IDs, parent baseline/version, hypothesis/novelty-card ID, intervention allowlist, track/estimand, preregistration timestamp/hash, family and tuning-attempt counts |
| Code | Git SHA, dirty/captured source hash and included-file manifest, plugin/component/serializer versions, Python pin, lock hash, command and resolved config hash |
| Model/objective | full architecture/init config, initial-weight hash, deployed/active/nonembedding/training-only parameter counts, auxiliary inputs/targets/teacher identity, optimizer state/resource accounting |
| Data | admission/pool/mixture/exposure IDs and checksums, per-source weights/quotas, tokenizer fit/serialization/hash, split/exclusion receipts, order manifest and source seeds, epoch/repetition/cap/packing policy, committed trace/cursor digests |
| Randomness | init, training RNG, source order, document-order manifest, tokenizer fit/split/analysis seeds; RNG state hashes and scope; same-seed repeat ID distinct from replicate ID |
| Training | exact target/byte budget and horizon, valid/content/EOS/ignored/attempted/replayed counts, global targets, microbatch and observed accumulation/update counts, final partial counts, full optimizer/groups/clip settings, LR function and first-step policy, lr_used/counter per update |
| Execution | precision/master/reduction/TF32 settings, requested/resolved attention/determinism and sampled operator receipt, compile/checkpointing/producer verification, actual torch/CUDA/driver/GPU/OS, contention and resource limits |
| Recovery | attempt ID, parent checkpoint/plan, resume/fork reason and timestamps, committed counters, interruptions/failures/skips/in-doubt events, last verified checkpoint digest |
| Evaluation | checkpoint/model/tokenizer hash, native/harness/task/scorer revisions, inference precision/context/truncation/BOS/normalization, inventory and item/group IDs (only where exposed), score denominators, coverage/partial label, split tier/exposure status, primary/secondary policy and cache ID |
| Result | endpoint and curve records, per-seed/cluster evidence, CI method/seed/repetitions/multiplicity/margins, measured train/eval/checkpoint/startup times, throughput definition, peak VRAM/RSS/disk, completion/failure reason, eligibility diff and decision |

Null means unknown, never zero; scientific-required unknowns make the comparison
ineligible. Secrets and sealed examples stay outside normal provenance. Raw
training corpus/log text cannot instruct the experiment runner. Authorization
hash covers behavior, sources, GPU/resource ceilings and stopping rules; any
behavioral change invalidates that authorization.

## U. Standard result table

Use one per-run evidence table and one comparison summary; include a machine-readable
JSON record behind each Markdown row. Suggested summary columns:

| Experiment ID / parent | Changed variable / eligibility | Model / params | Committed targets / unique bytes / repeats | Paired tuples / order strata | Primary endpoint Δ [CI] / curve area | BLiMP / ARC / HS / PIQA + scope | Successful targets/s / total hours | VRAM / RSS / disk | Failures / completed-planned | Decision |
|---|---|---|---|---|---|---|---|---|---|---|
| No research run in P35 | N/A | N/A | N/A | N/A | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | Design only |

Link each row to full identity; show mismatched budget/tokenizer/schedule/backend
as **INELIGIBLE: field diff**, not a footnote below a winner badge. Include
noninferiority margins and resource denominator. Missing benchmarks are `NOT RUN`
or `PARTIAL n/N`, never zero. Comparative throughput requires matching hardware/
contention/cadence; rows with different protocols cannot share an unqualified rank.

## V. Stopping and early termination

Catastrophic stop immediately on nonfinite loss/gradients, target/source/accounting
or checksum violation, unsafe/in-doubt optimizer state, violated byte/time/storage/
GPU bound, or unauthorized input/backend change. Preserve reason and last safe
checkpoint; an OOM is a resource failure, not a zero-quality score. Never silently
skip bad updates/examples. A repaired candidate is a new version; retain failure.

Dominated-screen stopping may save cost only at preregistered 64M and 96M looks,
after warmup: candidate is worse than a frozen baseline prediction envelope at
both successive looks in primary and curve metrics, has no credible compensating
cost benefit, and lies beyond a conservative deterioration margin calibrated
before the screen. If no baseline envelope exists, disable this rule. These are
heuristic **futility** stops, not inferential evidence of superiority or definitive
poor asymptotic quality. Delayed-learning hypotheses must declare a later minimum
budget in advance; unexpected crossings justify a separately budgeted study.

Confirmatory arms normally complete all seeds and the full budget. Only safety/
resource catastrophe stops them early; any incomplete pair makes the registered
confirmation incomplete. Do not discard failed seeds or compare survivors to all
controls. There is no early success rule. Administrative stops retain all targets,
schedule and resume history; missing endpoints cannot be manufactured by curve
extrapolation.

## W. Exact first real 50M pilot — specified, NOT RUN

Purpose: validate the new scientific identity/LR/logging/evaluation path on real
already-prepared local text, including cold source transitions, safe checkpoint
recovery, finite training and endpoint scoring. This is **not** batch selection,
mixture validation as optimal, or a useful-model campaign.

| Field | Pilot specification |
|---|---|
| Model | 49,883,648-parameter 50M preset, ctx 512, vocab 32,768, zero dropout |
| Data | M0/mix01 weights in §M, all 12 admitted components, no repeat/exhaustion error; source-local causal windows, EOS and cross-document policy fixed; actual artifacts required, none invented here |
| Tokenizer | Frozen reversible byte-level BPE 32,768, balanced training-only fit manifest and actual hash |
| Seeds | P0: init 101, training 10001, source 20260918; supplied fixed order manifest, explicitly not an independent document-order replicate |
| Batch | B8, 65,536 actual valid targets/update, 16 calls only for full windows |
| Optimizer/LR | AdamW §A, 1e-3 anchor, 10M warmup, 1B horizon, min ratio .1, endpoint-before-update v1 |
| Execution | BF16 autocast / FP32 parameters and optimizer state, SDPA memory-efficient statistical mode, compile/checkpointing off, process_depth1 explicitly on and verification on |
| Hard budget | **32,000,000 successful targets**; 489 updates, final 18,432 targets; at most one GPU process, 60 minutes total job wall time including evaluation/startup/recovery, plus explicit memory/storage limits |
| Checkpoints | Initialized weights/identity, 8M and 16M first-crossing boundaries, exact 32M final; verified fast NVMe, rolling recovery retention |
| Validation | Quick 0/1/4/8/16/32M, full development LM 0 and 32M; inference precision/scorer frozen |
| Benchmarks | At 0 and 32M: the same frozen search-only 100 items/task maximum and declared BLiMP subdataset coverage; strictly partial, no full-suite claim |
| Cost | 11.7 raw training minutes from historical rate; initially book 20–30 minutes, 60-minute hard total cap. Actual evaluation/cold-loader overhead unknown. |
| Limits | GPU allocated cap <=20 GiB with desktop headroom checked, process-tree RSS <=16 GiB, <=8 GiB new output including partial files; all existing inputs/caches and scratch separately inventoried and bounded before approval |

The resolved pilot must verify its first 32M exposure plan crosses at least two
actual source/shard transitions and exercises at least one cold open on each
available component. Do not alter mixture quotas to force a cache experiment.
If real layout lacks that coverage, report cold-shard coverage NOT RUN and plan
a separate bounded diagnostic; do not claim that 32M automatically tests storage
scale. Verify resume once from the 16M boundary in a fresh process; all replayed
work counts against the wall/attempted-target allowance and preserves the frozen
run identity. Retain continuous data trace and exact final counters.

Success requires identity resolved and frozen, expected LR actually used/logged,
finite CE/gradients, exact targets/update/quota/byte accounting within declared
drift, improving full validation CE from initialization (otherwise investigate),
all declared evaluations/coverage receipts present, no train-state mutation by
evaluation, valid checkpoint/load/resume traces, resource caps respected and
honest cold-open evidence. No absolute CE, BLiMP gain or 45.7k/s pass threshold is
invented. An infrastructure pass does not imply a competitive baseline.

Retain resolved plan and authorization, initial/final model and 16M recovery state,
optimizer/RNG/sampler state, checkpoint hashes, LR/target logs, source exposure
table, timing/resource traces, score/coverage receipts and a pilot report including
all failures. Missing actual input hashes are **BLOCKED prerequisites** to execution.
No substitute corpus or toy tokenizer may masquerade as this pilot.

## X. Before the first full 1B baseline

The user launches it only after: P35 handoff steps 1–4 verified; real artifacts/
admission/exclusion/splits supplied; pilot passes; throughput/eval/storage costs
measured; first-step LR fixed and calibrated; attention policy frozen after its
bounded profile; B8/B16/B32 exploratory study selects at most one challenger;
seed roster and statistical margins/family registered; full 1B per-source capacity
or explicit repetition established; checkpoint retention and plan-hash-bound GPU
authorization approved by the user. The initial 1B runs may be baseline/batch
confirmation development; they are not yet the final data-frozen Baseline-v1.
Data-mixture selection and independent confirmation precede the final v1 bundle
and architecture/loss research. Independent within-source order coverage is
required before a robustness/promotion claim, not to run the single pilot.

## Y. Empirical questions still open

No optimal batch, LR, mixture, minimum meaningful CE margin, baseline seed variance,
benchmark sensitivity at 50M, real-source throughput, full-model deterministic
cost, independent-order effect or large-scale superiority has been measured here.
Resolve these with the named pilot, bounded attention calibration, LR neighborhood,
paired batch study, mixture funnel, variance calibration and scale confirmations.
Finite prepared-text inventory may force explicit repeats or a smaller study;
its size is unknown here. Lack of these observations is not a reason to invent
thresholds or run an unapproved campaign.

## Z. Prioritized next actions and requirement ledger

1. Opus implements the versioned LR/seed/provenance migration, preserving legacy
   runs, then integrates evaluation/cadence and bounded retention (§handoff 1–3).
2. Implement fail-closed experiment planning/comparison and the exact statistical
   schema; generate the unresolved pilot plan, never launch it automatically.
3. User supplies admitted local artifacts and approves the fully resolved pilot
   hash/limits, then personally runs the 32M pilot.
4. User executes variance/LR/batch calibration and data-first search; freeze v1.
5. Agents implement one hypothesis at a time with toy/synthetic bounded checks;
   user authorizes and runs the research funnel and larger-model campaigns.

Next prompt: **“Implement P35 handoff milestone 1 only, preserve all legacy LR
semantics, use focused offline tests, and do not launch the real pilot.”**

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| A–Z scientific decisions, protocols and handoff | IMPLEMENTED | This report and separate handoff; documentation specification |
| Current recipe counts and scheduler audit | VERIFIED | Meta counts in probe.json; code audit; 7 existing schedule tests pass |
| Deterministic attention availability/sample equality | VERIFIED | 12 synthetic cases, 12 repetitions each; qualified scope §D |
| Deterministic full-model cost / all-shape replay | NOT RUN | Need bounded interleaved full-update profile; no training-quality inference |
| Runtime enforcement of new LR, separate training RNG, cadence and multi-seed rules | NOT RUN | P35 changes no product code; handoff specifies implementation/tests |
| Real pilot launch and valid empirical margins | BLOCKED | Actual admitted artifacts/identities, implementation and user plan authorization required |
| B8/B16/B32 study, mixture/novel-model campaigns, final evaluation | NOT RUN | User-run scientific work, not agent acceptance tests |
| Network, acquisition, performance-system redesign, push/merge | OUT OF SCOPE | Not performed |

## Evidence, environment and exact commands

Windows 11 build 26200, Python 3.12.13, torch 2.14.0+cu126, CUDA runtime 12.6,
driver 596.49, RTX 4090; existing CUDA venv read without sync/install. `.python-version`,
`pyproject.toml`, `uv.lock` and mutually exclusive CPU/CUDA index policy unchanged.
CPU test execution below used the CUDA wheel; no CPU-only environment certificate.
All numerical inputs were authored synthetic tensors, never live corpus examples.

From the P35 worktree, `R` means
`& docs/implementation/evidence/P35/env.ps1`, whose checked-in source spells out
all environment variables and calls `uv run --offline --locked --no-sync --extra cuda`.
The probe was executed with those same uv/source/thread settings before this
convenience wrapper was added. Python bytecode writes disabled, no environment
sync. The borrowed interpreter is outside this tree; no files there were edited.

| Exact command | Exit / observation |
|---|---|
| `git status --short`; `git branch --show-current`; `git rev-parse HEAD` (separate read-only commands) | 0; clean, expected branch and audited SHA above |
| `nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total,utilization.gpu --format=csv` | 0; RTX 4090, 596.49, 8895/24564 MiB, 24% utilization |
| `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv` | 0; desktop processes, WDDM per-process memory unavailable; no exclusive-use claim |
| `R python docs/implementation/evidence/P35/probe.py` initial attempt | 1; model-preset envelope fields rejected by component schema, before attention work; diagnostic corrected to strip only schema_version/kind/id |
| Same probe command after that correction | 0; 3 exact parameter counts, 12 attention cases, 14.891 s; immutable probe.json |
| `R python -m pytest tests/test_schedules.py -n 0 -q` | 0; **7 passed in 6.53 s**; existing schedule function tests, not a test of the proposed LR migration |
| `R python -m ruff format docs/implementation/evidence/P35/probe.py` | 0; formatting only after execution, no semantic diagnostic changes |

Final document/link/JSON checks and lint are recorded in [P35 milestone record](P35.md).
Full offline acceptance, training tests, new-policy tests, research training and
live dataset compatibility tests were NOT RUN. Discovery searches included
missing guessed paths; they were corrected by reading actual files, not treated
as absent capabilities. The initial diagnostic failure is preserved here; no
retry-until-green numerical selection occurred.

Key audited source paths (repository-relative): `src/xlm/training/trainer.py`,
`training/components.py`, `training/checkpoint.py`, `training/inputs.py`,
`models/attention.py`, `models/initialization.py`, `models/backends.py`,
`schedules/cosine.py`, `schedules/anchors.py`, `optimizers/adamw.py`,
`data/sampling/{stream,scheduler,mixture,plan}.py`,
`evaluation/{diagnostics,likelihood,suites,coverage,harness_runner}.py`,
`comparison/{bootstrap,promotion,tracks}.py`, `config/schemas.py` (all abbreviated
paths after the first are under `src/xlm/`).
Authoritative [recipes](../../../recipes/README.md),
[model 50M](../../../recipes/models/50m.yaml),
[draft 50M](../../../recipes/experiments/baseline_50m.yaml),
[draft 150M](../../../recipes/experiments/baseline_150m.yaml),
[draft 300M](../../../recipes/experiments/baseline_300m.yaml),
[contracts](../../../CONTRACTS.md),
[P34 final evidence](P34.md), [P33 evidence](P33.md).
