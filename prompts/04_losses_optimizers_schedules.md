# Prompt 04 — Objective, optimizer and schedule plugins

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement next-token cross-entropy as the baseline objective, with an independent diagnostic CE computation. The objective sees already-shifted labels and the mask from the batch. Return summed valid-target loss, denominator, named diagnostics and state according to the contract. Test that padding, BOS and ignored targets never enter the denominator. An all-masked batch is an explicit invalid/no-update case, not a NaN or invented zero-quality score.

Implement objective capabilities for token-additive versus global-batch normalization, optional auxiliary outputs/parameters and extra computation. Supply one pass-through/no-op objective plugin used solely to verify extensibility. Do not call it a novel loss. Registering a genuinely new objective should require adding its plugin and config/tests, not adding a branch to the trainer.

Implement the AdamW optimizer factory, unique-parameter grouping, explicit norm/embedding weight-decay rules, configurable hyperparameters, optimizer state reporting and serializer hooks. Include objective-owned trainable parameters without double-counting tied model weights. Detect omitted trainable parameters and duplicate parameter groups. Reject optimizer plugins requiring unsupported closures or normalization rather than silently ignoring them.

Implement a schedule registry with token-based warmup/cosine as baseline and a simple constant fixture schedule. Horizon, warmup, minimum ratio and counter identity are explicit. Add per-size LR anchors as draft starting values; no claim of calibration. Schedules must reload exactly and must reject a changed horizon on ordinary resume.

Acceptance: CE agrees with a direct hand-sized PyTorch calculation; sum/count accumulation equals an unsplit batch for uneven masks; optimizer parameter grouping is complete and duplicate-free; no-op objective matches baseline gradients; scheduler values at start/warmup/midpoint/end are correct; schedule save/load continuity; objective auxiliary parameters update and serialize. Confirm a global-batch objective cannot be used with unsupported microbatching. Record object/state memory estimates as estimates, not measured GPU memory.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
