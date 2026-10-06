# C07-v2 consumer command and evidence ledger

All commands below ran locally from `F:/Project/xlm-training-input-v2` unless stated.
Environment and production limitations are in [the report](C07-V2-CONSUMER.md).
No network operation, operator-key access or production materialization was performed.

Initial worktree creation from the shared repository (`F:/Project/xlm-c05-policy-v2`):

```powershell
git worktree add -b fix/training-input-policy-v2 F:/Project/xlm-training-input-v2 602cd3ff83a6495589b8d9d7608c9ebc77118488
```

Exit 0. Initial commands `git rev-parse HEAD`, `git branch --show-current`,
`git status --short`: exit 0 each, exact source SHA, new branch, empty status.
Offline environment setup: `uv sync --offline --locked --extra cpu --extra eval`, exit 0.
The existing Python 3.12.13/dependency lock was retained.

Key-free local real-data diagnostics (each exit 0):

```powershell
uv run --offline --locked --extra cpu --extra eval python scripts/c07_consumer_probe.py properties
uv run --offline --locked --extra cpu --extra eval python scripts/c07_consumer_probe.py sizes
uv run --offline --locked --extra cpu --extra eval python scripts/c07_consumer_probe.py resources
```

`properties` was repeated after adding explicit tokenizer-file/configuration binding:
4.516 seconds, 10,150 strings, 100,215 prefix comparisons, zero mismatches;
`properties-pinned.json` is the final result. The earlier 4.485-second result is retained.
`sizes`: 24.578 seconds, bounded canonical prefixes plus one full bounded selected-metadata
hash; no signatures or token shards. `resources`: 2.375 seconds, authored text encoded with
the local frozen tokenizer, private <=512 MiB C: payload removed on completion.
None of these is a full-scale performance or production-signature acceptance test.

Test environment: `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`,
`NUMEXPR_NUM_THREADS=1`, `TOKENIZERS_PARALLELISM=false`. All runs use one pytest controller,
explicit `-n 0`, authored fixtures and authored signing keys. Test settings do not modify
production configuration. The selected existing training tests run tiny authored CPU
fixtures, not the real baseline or research campaign.

| Command suffix after `uv run --offline --locked --extra cpu --extra eval python -m pytest` | Exit | Result / evidence |
|---|---:|---|
| `tests/test_c07_consumer_v2.py -n 0 -q` | 1 | 14 passed, 1 failed, 28.97 s; initial prefetch comparison treated tensor transport as a Python list |
| `tests/test_c07_consumer_v2.py::test_resolved_v1_v2_policy_chain_batches_trace_and_resume -n 0 -q` | 1 | 1 failed, 15.39 s; prefetch deliberately stores compact provenance separately |
| same exact node after rehydrating the public provenance object | 0 | 1 passed, 17.28 s; every primitive field and committed state compared |
| focused selection below | 1 | 168 passed, 2 failed, 112.97 s; retained `focused-tests.log` |
| repair selection below | 0 | 3 passed, 72.33 s; `repair-tests.log` |
| earlier exploratory resource checks | 0 | 4 passed, 6.78 s; `resource-tests.log`; superseded by the explicit final resource command below |
| final consumer/C07 selection below | 0 | 50 passed, 71.90 s; `final-consumer-tests.log` |

Focused selection:

```powershell
uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_c07_consumer_v2.py tests/test_tokenize_freeze_fast.py tests/test_token_shards.py tests/test_mixture_stream.py tests/test_sampling_trace.py tests/test_configurable_training.py tests/test_prefetch.py tests/test_checkpoint.py tests/test_p35_readiness_planner.py tests/test_p35_m5_order.py -n 0 -q
```

The planner failure exposed a legacy helper monkeypatch that accepts one argument;
the policy-aware caller now retains that legacy call signature when no policy is present.
The public CLI authored objective fixture failed on a 261-character filesystem-capture
path. Windows `LongPathsEnabled=0` was independently confirmed. A short fresh pytest
base directory fixed the test environment; no assertion or capture check was weakened.

```powershell
uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_configurable_training.py::test_public_cli_executes_registered_objective tests/test_p35_readiness_planner.py::test_input_check_blocks_without_raising_the_cap tests/test_c07_consumer_v2.py::test_readiness_planner_uses_explicit_policy -n 0 -q --basetemp C:/XLM-scratch/c07-v2-repair-1
```

Resource/record checks:

```powershell
uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_c07_consumer_v2.py::test_sparse_over_2gib_admission_and_mmap_use_bounded_ram tests/test_c07_consumer_v2.py::test_511580_target_shape_stays_small_and_exact tests/test_c07_consumer_v2.py::test_metadata_record_boundary -n 0 -q -s --basetemp C:/XLM-scratch/c07-v2-resource-2
```

Exit 0: **5 passed in 6.63 seconds**. The metadata node has three parameter values;
see `final-resource-tests.log`. Sparse fixture output expressly says hash
verification was NOT RUN; it proves bounded admission and actual Windows window reads.

Final consumer/C07 selection, after production-preflight refusal and little-endian checks:

```powershell
uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_c07_consumer_v2.py tests/test_tokenize_freeze_fast.py -n 0 -q --basetemp C:/XLM-scratch/c07-v2-final-1
```

Additional explicit document/shard/aggregate boundaries and signed reference/fast policy
freeze comparison:

```powershell
uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_c07_consumer_v2.py::test_document_admission_boundary tests/test_c07_consumer_v2.py::test_shard_aggregate_component_boundaries tests/test_c07_consumer_v2.py::test_signed_policy_reference_fast_freeze_agree -n 0 -q --basetemp C:/XLM-scratch/c07-v2-policy-1
```

Exit 0: **3 passed in 13.85 seconds**, retained in `policy-tests.log`. Evidence files are normalized to UTF-8/LF for
review; only encoding, newlines and trailing whitespace are normalized, not outcomes.
A final diff review restored original non-ASCII source comments affected by PowerShell's
legacy text encoding; no scientific code was changed by that restoration.

Static checks: **all exit 0**, Ruff clean, strict mypy clean on 15 source files,
and both working/staged diff checks clean (details in `static-checks.log`):

```powershell
uv run --offline --locked --extra cpu --extra eval ruff check src/xlm/data/input_policy.py src/xlm/data/input_validation.py src/xlm/data/tokens.py src/xlm/data/token_cache.py src/xlm/data/sampling/stream.py src/xlm/data/sampling/prefetch.py src/xlm/data/ordering/index.py src/xlm/data/ordering/membership.py src/xlm/training/inputs.py src/xlm/training/input_preflight.py src/xlm/data/exclusion/freeze.py src/xlm/data/exclusion/freezefast.py src/xlm/data/exclusion/control.py src/xlm/experiments/science_pilot.py scripts/c07_consumer_probe.py tests/test_c07_consumer_v2.py
uv run --offline --locked --extra cpu --extra eval mypy --strict src/xlm/data/input_policy.py src/xlm/data/input_validation.py src/xlm/data/tokens.py src/xlm/data/token_cache.py src/xlm/data/sampling/stream.py src/xlm/data/sampling/prefetch.py src/xlm/data/ordering/index.py src/xlm/data/ordering/membership.py src/xlm/training/inputs.py src/xlm/training/input_preflight.py src/xlm/data/exclusion/freeze.py src/xlm/data/exclusion/freezefast.py src/xlm/data/exclusion/control.py src/xlm/experiments/science_pilot.py scripts/c07_consumer_probe.py
git diff --check
git diff --cached --check
```

Intermediate Ruff findings (formatting, line length and an import order) were corrected.
Mypy reports the existing unused lm_eval config-section note, not a source error.
No full offline suite or CUDA tests were run. Individual diagnostics stayed below the
requested three-minute hard bound. Operator commands are documented but **NOT RUN**:
[production runbook](../../runbooks/c07-v2-consumer.md).
