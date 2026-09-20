# Bounded baseline pilot after D03 core

This handoff covers configurable local training within C13 smoke limits. It is
not acceptance of acquisition, official benchmark selection, tokenizer research,
large-model training or the full platform. The remediation agent ran authored CPU
fixtures only. The operator runs real-data preparation and any real-data pilot.

## Prerequisites

1. Use Python **3.12.13** and the unchanged `uv.lock`. Select exactly one of `cpu`
   or `cuda`; verification here used Windows CPU with the optional `eval` extra.
   Dependencies must already be available locally for `--offline --locked`.
2. Independently review/admit the actual source revisions, licenses, exclusions,
   train split and frozen tokenizer. Supply already prepared local per-source
   shards and their tokenizer. D02 acquisition/preparation accounting is still
   OPEN: do not treat the current fetcher or preparation workflow as a verified
   production resource boundary. No real acquisition/preparation was performed
   for this handoff.
3. Each shard directory must contain `shard_manifest.json`, `tokens.bin` and
   `offsets.jsonl` with matching checksums; retain `shard_counters.json` when
   present. Training mixture indices must be contiguous, explicitly `train`,
   and source-bound. New tokenization records actual token byte spans. Historical
   indices without spans remain explicit incomplete byte coverage, never inferred
   matched-byte evidence. Bind the actual tokenizer fingerprint and vocabulary.
4. Inputs are currently bounded to **64 sources / 2 GiB aggregate shard inputs**,
   **8 MiB per index entry**, and **64 MiB / 4,096 entries per tokenizer/plugin
   asset tree**. The complete packed optimizer step is capped at **1,048,576
   positions**. Reduce the declared batch/context or change the reviewed visit
   cap if needed; these guards do not silently truncate the corpus.
5. Keep original inputs, the captured snapshot, locked environment and checkpoint
   artifacts accessible for continuation. Changing mixture, packing, tokenizer,
   component or exposure policy changes execution identity. Legacy unresolved
   checkpoints are inspectable but do not acquire frozen execution provenance.

## Concrete CPU pilot configuration

Create an isolated home and use the project's installed environment. This example
uses the environment that tested D03; it is a local path, not a portable install
requirement. All following commands run from `D:\Project\xlm`.

```powershell
$env:UV_PROJECT_ENVIRONMENT='D:\Project\xlm\data\audit\p23-remediation\stage01\after01\.venv'
$env:UV_OFFLINE='1'
$env:PYTHONPATH='D:\Project\xlm\src'
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:XLM_HOME='D:\Project\xlm\data\operator-baseline-pilot'
```

Save the following as `pilot-mixture.json`. **Replace `source_a` and `source_b`
with the actual manifest source IDs**, and use those same names in `pilot.yaml`.
Each source must have enough unique targets for the requested budget; exhaustion
is an error and shares are never redistributed. This is a template, not a claim
that these source artifacts exist or are admitted.

```json
{
  "mixture_id": "operator_baseline_pilot",
  "components": [
    {"source_id": "source_a", "weight": 0.5},
    {"source_id": "source_b", "weight": 0.5}
  ],
  "exhaustion": {"repeat": false, "max_epochs": 1},
  "packing": {"mode": "causal_stream", "cross_document_attention": true,
              "max_document_tokens": 16},
  "data_seed": 20260919,
  "model_seed": 20260920,
  "max_share_drift": 0.02
}
```

New token exposure plans use version 2 identity: deterministic integer quotas
sum exactly to the declared target budget, including odd budgets. Training
enforces these per-source quotas and the plan's `block_size` as a maximum target
count per visit. Context/document caps can shorten visits. The block preview is
not a literal packed document trace. Historical plans are preserved; regenerate
a new plan instead of rewriting a frozen v1 plan under its old identity.

Place reviewed shard directories at `$env:XLM_HOME/shards/<actual-source-id>` and
the fitted tokenizer at `$env:XLM_HOME/tokenizer`. Generate the token exposure
projection from those actual shards; JSON is accepted by the recipe loader:

```powershell
uv run --offline --locked --extra cpu --extra eval xlm mixture validate --recipe pilot-mixture.json --shards "$env:XLM_HOME/shards"
uv run --offline --locked --extra cpu --extra eval xlm mixture plan --recipe pilot-mixture.json --shards "$env:XLM_HOME/shards" --budget-targets 1025 --block-size 128 --output "$env:XLM_HOME/exposure.json"
```

Save `pilot.yaml` below. Copy the **same mixture object** from `pilot-mixture.json`
under `data.mixture`; the inline example shows the corresponding fields. Set
`model.vocab_size` to the actual tokenizer vocabulary and the tokenizer config to
its saved target vocabulary. A mismatch is refused, not padded or guessed.

```yaml
id: operator_baseline_pilot
model:
  architecture: transformer_baseline
  vocab_size: 32768  # replace with actual fitted vocabulary
  hidden_size: 96
  intermediate_size: 256
  num_layers: 2
  num_attention_heads: 4
  context_length: 128
  attention_backend: eager
  tie_embeddings: true
objective: {type: cross_entropy}
optimizer: {type: adamw, lr: 0.001}
data:
  tokenizer_artifact: '${XLM_HOME}/tokenizer'
  tokenizer: {type: bpe, target_vocab_size: 32768} # saved target vocabulary
  sources:
    source_a: '${XLM_HOME}/shards/source_a'
    source_b: '${XLM_HOME}/shards/source_b'
  exposure_plan: '${XLM_HOME}/exposure.json'
  mixture:
    mixture_id: operator_baseline_pilot
    components:
      - {source_id: source_a, weight: 0.5}
      - {source_id: source_b, weight: 0.5}
    exhaustion: {repeat: false, max_epochs: 1}
    packing: {mode: causal_stream, cross_document_attention: true, max_document_tokens: 16}
    data_seed: 20260919
    model_seed: 20260920
    max_share_drift: 0.02
training:
  device: cpu
  precision: fp32
  init_seed: 20260920
  data_seed: 20260919
  context_length: 128
  global_batch_valid_targets: 128
  microbatch_sequences: 4
  checkpoint_every_valid_targets: 256
  budget: {max_valid_targets: 1025, max_train_seconds: 600}
  schedule: {type: warmup_cosine, warmup_valid_targets: 128, horizon_valid_targets: 1025}
```

Validate inputs and the actual model shape without numerical training, then use
either direct training or the public queue workflow. Do not execute both under
the same intended run identity merely to repeat training.

```powershell
uv run --offline --locked --extra cpu --extra eval xlm train pilot.yaml --device cpu --dry-run
# Direct alternative:
uv run --offline --locked --extra cpu --extra eval xlm train pilot.yaml --device cpu
# Queued alternative, with a new private snapshot/output path:
uv run --offline --locked --extra cpu --extra eval xlm experiment plan pilot.yaml --smoke --snapshot-dir "$env:XLM_HOME/snapshot" --output "$env:XLM_HOME/plan.json"
uv run --offline --locked --extra cpu --extra eval xlm experiment submit "$env:XLM_HOME/plan.json" --snapshot-dir "$env:XLM_HOME/snapshot" --device cpu
uv run --offline --locked --extra cpu --extra eval xlm queue run --once --device cpu
uv run --offline --locked --extra cpu --extra eval xlm queue status --json
```

`--smoke` uses the existing queue, envelope, authorization and worker. It refuses
models with **5 million or more unique parameters**, more than **200,000 targets**,
more than **600 training seconds**, final evaluation or multiple GPU processes.
It reserves the existing **2 GiB artifact cap** and explicitly records unmeasured
cost; it does not manufacture a throughput estimate or production readiness.
The ordinary production planner keeps its artifact/profile/policy and plan-bound
authorization requirements. Its public CLI currently does not load a measured
`profile_lookup`; full-size campaign planning remains blocked pending that
integration and actual operator evidence. A 50M/150M/300M model is not made ready
by this smoke path; those shapes were checked on meta only.

Use the checkpoint path printed by the command for an ordinary frozen resume:

```powershell
uv run --offline --locked --extra cpu --extra eval xlm resume 'ACTUAL_CHECKPOINT_DIRECTORY' --device cpu
```

The resume target and execution configuration remain the original plan's. A
completed checkpoint is verified and preserved. Intermediate continuation
restores the model, objective auxiliaries, optimizer, schedule, Python/NumPy/Torch
RNG and committed data state. Mixed precision/CUDA requires separate operator
validation; this stage did not test CUDA scaler state or cross-platform parity.

Inspect `data_state.json` for committed per-source counters/cursors/epochs,
deficits, real document/lineage IDs, byte coverage and the target trace digest.
`last_step_trace` retains at most 256 actual targets and marks truncation. The
example's 128-target updates fit fully. The cap ends a source visit and emits its
shorter padded training window; the next visit is selected from current deficits.
Only the last context token carries between visits, as in the existing stream.
A visit cap inserts no EOS and is not a document boundary. Isolated-document
mode additionally requires `cross_document_attention: false`; positions and
attention reset at real document boundaries only.

## Gates that remain open

- **D03 tokenizer comparisons: OPEN / DEFERRED.** Token exposure quotas and visit
  limits execute; matched-document/byte execution across tokenizers is not verified.
- **D02: OPEN.** Durable acquisition accounting, cache integrity, record/selected
  row limits and aggregate preparation bounds need separate approval and repair.
- **D04/D05 and D08: OPEN.** Official evaluation coverage and statistical comparison
  are not ready for benchmark-based model selection. Synthetic diagnostic receipts
  are partial fixture evidence, not official benchmark scores.
- **D07: OPEN.** Tied-weight portable export remains a separate unresolved stage.
- Real data, full-size numerical profiling, official benchmarks, protected
  deployment and research campaigns are **NOT RUN — OPERATOR ACTION REQUIRED**.
  Offline tests cannot establish live compatibility or remove these gates.
