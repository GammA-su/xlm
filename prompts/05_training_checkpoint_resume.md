# Prompt 05 — Working trainer, exact budgets and reproducible resume

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement the first complete trainer using local token data and the public registries. Training should work on CPU before any remote data. Implement deterministic batches with source/exposure metadata, causal target shifting exactly once, valid-token accumulation, clipping, schedule stepping, structured metrics and terminal progress. Include an explicit global target batch and variable microbatch size. Normalize by the total valid-target count, not an average of per-microbatch means.

Support token-budget completion with an exactly masked final update; padding never consumes the valid-target budget. Log model CE and optimized objective separately, training-only auxiliary work, processed versus committed targets, source/byte exposure, update counts and termination status. Respect smoke resource caps. Stop on non-finite gradients/loss with a useful failure report. Do not silently skip records, alter batch size or lower context to survive an error.

Implement checkpoint publication and resume as specified, including optimizer/objective/scheduler state, Python/NumPy/PyTorch RNGs, loader/sampler/packer committed state, precision settings and immutable artifact references. Handle SIGINT/termination by saving at the next safe optimizer boundary when possible. A hard interruption must leave the previous checkpoint readable and the current write uncommitted. Define exactly which state is saved at a partial final batch. Do not claim recovery of a mid-kernel computation.

Implement `xlm train`, `xlm resume`, `xlm run inspect` and a bounded `xlm demo` that imports local fixtures, fits a tiny tokenizer, trains a toy model, saves and resumes. Keep this CLI and artifact format; later stages extend it. Separate a resume from a fork with changed budget/config. A fork links its parent and invalidates inappropriate fairness assumptions.

Acceptance: tiny-batch overfitting; finite loss/gradients; uneven masked microbatch accumulation parity; exact token budgets including a nonmultiple of batch size; uninterrupted versus checkpoint-resumed CPU training agrees for weights, optimizer, counters and next data IDs; corrupt/incompatible checkpoints are rejected; interruption recovery works. Safetensors/metadata round trips preserve tied weights. Demonstrate one complete offline tiny training run and one interrupted/resumed run; do not launch a 50M production run yet.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
