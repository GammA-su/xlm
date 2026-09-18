# Prompt 18 — Research idea workflow and safe extensibility

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement versioned idea cards and plugin scaffolding. Required fields: bottleneck; mathematical mechanism; precise allegedly original element; closest prior art with dated sources/search terms; predictions; minimal falsification experiment; allowed information; parameter/compute costs; baselines; ablations; tuning allowance; kill/promotion rules; result status. Statuses distinguish proposed, prior-art-unreviewed, possible rediscovery, no-close-prior-art-found, implemented, inconclusive, promising and replicated. Do not certify that nobody has ever tried an idea.

Create `xlm research idea new`, `xlm research idea validate`, `xlm research scaffold` and `xlm research check-plugin`. Accept a mathematical specification supplied by the user or an external idea-generating model. The core application must not depend on one named model provider. Provider calls are optional future adapters; do not make paid calls, invent literature references or construct an automatic sealed-benchmark optimization agent.

Scaffolds cover architecture, objective, optimizer and tokenizer. Generate typed config, registry wiring, parameter/state/resource accounting, serialization hooks, causal/no-future test template, tiny-overfit test, no-op/disabled-mode test where meaningful, and a comparison recipe. Unimplemented generated scaffolds are disabled and fail explicitly; they are not registered as functioning mechanisms. A working no-op plugin for every category is required to verify the extension seam and clearly labeled nonnovel.

Add capability checks: recurrent state reset, auxiliary-output requirements, accumulation protocol, optimizer closures, deterministic tokenizer scoring, parameter caps, cache support and inference-only input availability. Reject an unsupported combination at plan time. Research plugins must not modify evaluation code, sealed manifests or baseline settings; verify file-scope diffs and behavioral regression tests. A disabled mechanism must not consume extra hidden teacher data or alter token order.

Use CHANGE_EXPERIMENT_TEMPLATE.md as the reusable prompt for later ideas. Build an idea→patch→smoke→matched experiment→comparison evidence chain. Novelty review and positive training results remain separate statuses.

Acceptance: add/load four no-op plugins without editing generic trainer/scorer; baseline parity; unsupported capability errors; objective-owned state survives resume; plugin changes alter code fingerprints; proposed scaffold cannot pretend to run; idea card with missing falsification/allowed-information fields fails; attempt to change protected evaluator during an architecture experiment is detected. Do not invent a breakthrough as part of infrastructure delivery.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
