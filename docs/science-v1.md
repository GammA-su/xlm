# Science-v1 training semantics (P35 Milestone 1)

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

## Resume and forks

Ordinary resume requires an identical policy (version, LR policy, RNG policy,
training seed and runtime block). The frozen envelope check already enforces
this. The domain API also compares `science.json`, whose absence means legacy,
before restoring any state. A legacy checkpoint cannot be resumed as
science-v1, or the reverse, without `--fork` or a new experiment. A fork across
policies does not adopt the parent's LR/RNG receipt history.
