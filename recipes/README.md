# Recipe language to implement

These are **draft configuration examples**, not configs for an existing installed XLM package. Most are JSON-formatted YAML so keys and numbers can be checked using only Python’s standard library. They can be edited as normal YAML after the strict loader is implemented. Validation must accept YAML/JSON syntax while rejecting duplicate keys and invalid values.

Named `preset` and `mixture_preset` values resolve through the local versioned preset catalog built in prompt 01. The model preset supplies only model fields; the experiment deliberately contains its optimizer/schedule separately. Moving to a different size must also select/calibrate its training preset rather than silently reuse inappropriate hyperparameters. The three experiment examples already carry the proposed per-size LR anchors and horizon.

`null` means unresolved. Required immutable artifacts, microbatch/profile choice, storage authorization and final plan hash must be filled by discovery, admission, profiling and planning. `draft` validation checks structure; `executable` validation must reject these unresolved references. Do not replace nulls with invented hashes or unmeasured defaults to make a run launch.

`attention_backend: profile_required` is a draft marker; execution requires an actual supported selected backend. `max_train_seconds` can remain absent only when another authorized finite compute/token stop exists; `max_new_disk_gib` must be bounded in a production authorization.

The scheduled horizon may exceed the early screening stop. For example, the 50M 128M-target screen is an early prefix of a declared 1B-target horizon, not a completed 128M cosine schedule. Both arms of a comparison use the same rule. A standalone short-horizon experiment is a different recipe.

All mixture shares count valid next-token target positions, including attributed EOS and excluding PAD/BOS/ignored targets. Content-token shares and structural-token overhead are reported separately. Default source exhaustion is an error, not automatic repeats. A repeated-data experiment requires a named explicit repetition policy and new hash.

Implemented interface (tested in prompt 22; run from the repository root):

```bash
uv run --locked xlm config validate recipes/experiments/baseline_50m.yaml --mode draft
uv run --locked xlm experiment plan recipes/experiments/baseline_50m.yaml --output plan.json --snapshot-dir snaps/xx
uv run --locked --extra cuda xlm train plan.json --device cuda --max-targets 128000000
```

`plan.json` and ticket files are user/runtime-generated, not literal paths.

`experiments/draft_science_v1_*.yaml` are non-executable drafts that opt into
[xlm-science-v1](../docs/science-v1.md): an explicit endpoint LR policy,
`training_seed` and runtime block. The historical `baseline_*` drafts stay
schema v1 and keep legacy semantics; none of the drafts is a production default.
`xlm train` takes the plan path positionally; training authorization is checked
at `xlm experiment submit` time via `--ticket`, not at `xlm train` time.
The offline commands above are executed verbatim by the P22 runbook tests;
`xlm train` needs a resolved plan plus an accelerator extra (`cpu` or `cuda`).
