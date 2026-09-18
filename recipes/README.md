# Recipe language to implement

These are **draft configuration examples**, not configs for an existing installed XLM package. Most are JSON-formatted YAML so keys and numbers can be checked using only Python’s standard library. They can be edited as normal YAML after the strict loader is implemented. Validation must accept YAML/JSON syntax while rejecting duplicate keys and invalid values.

Named `preset` and `mixture_preset` values resolve through the local versioned preset catalog built in prompt 01. The model preset supplies only model fields; the experiment deliberately contains its optimizer/schedule separately. Moving to a different size must also select/calibrate its training preset rather than silently reuse inappropriate hyperparameters. The three experiment examples already carry the proposed per-size LR anchors and horizon.

`null` means unresolved. Required immutable artifacts, microbatch/profile choice, storage authorization and final plan hash must be filled by discovery, admission, profiling and planning. `draft` validation checks structure; `executable` validation must reject these unresolved references. Do not replace nulls with invented hashes or unmeasured defaults to make a run launch.

`attention_backend: profile_required` is a draft marker; execution requires an actual supported selected backend. `max_train_seconds` can remain absent only when another authorized finite compute/token stop exists; `max_new_disk_gib` must be bounded in a production authorization.

The scheduled horizon may exceed the early screening stop. For example, the 50M 128M-target screen is an early prefix of a declared 1B-target horizon, not a completed 128M cosine schedule. Both arms of a comparison use the same rule. A standalone short-horizon experiment is a different recipe.

All mixture shares count valid next-token target positions, including attributed EOS and excluding PAD/BOS/ignored targets. Content-token shares and structural-token overhead are reported separately. Default source exhaustion is an error, not automatic repeats. A repeated-data experiment requires a named explicit repetition policy and new hash.

Expected interface after implementation:

```bash
uv run --locked xlm config validate --config configs/experiments/baseline_50m.yaml --mode draft
uv run --locked xlm experiment plan --config configs/experiments/baseline_50m.yaml
uv run --locked --extra cuda xlm train --plan PLAN_PATH --authorization AUTHORIZATION_PATH
```

`PLAN_PATH` and `AUTHORIZATION_PATH` are user/runtime-generated files, not literal paths. Every example command must be tested and kept aligned with the implemented CLI during prompt 22.
