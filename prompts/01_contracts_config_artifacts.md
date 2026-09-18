# Prompt 01 — Typed configuration, contracts, registries and artifacts

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement the public schemas/protocols before specialized code. Read CONTRACTS.md and make these stable interfaces the common dependency of the rest of the package.

Create versioned strict configuration models, explicit preset selection and an `extends` composer with cycle detection, duplicate-YAML-key rejection, map deep merge, list replacement, typed command-line overrides and environment interpolation restricted to declared fields. Draft recipes may contain unresolved source names; executable plans may not. Implement `xlm config validate`, `xlm config resolve` and `xlm config diff`. All commands must show useful paths for invalid values. Export JSON Schema and document the exact composition order. Unknown keys such as `tokens_buget` must fail instead of being ignored.

Define CanonicalDocument, TokenShardManifest, TrainingBatch, LMOutput, LossResult, ModelCapabilities, RunPlan, RunStatus, EvaluationReceipt and ComparisonContract. The batch has explicit input IDs, already-shifted labels, masks, optional position/segment IDs, source attribution and exposure metadata. Its inference view must omit labels. Plugin configurations are validated by the selected registered type; no generic unvalidated `kwargs` escape hatch.

Build registries for architectures, objectives, optimizers, tokenizers, source adapters, transforms and schedules. Record entry version/capabilities/serializer. Duplicate names and unsupported capabilities are errors. A controlled local Python plugin is supported; arbitrary code in YAML or downloaded dataset scripts is not.

Implement the artifact store: canonical config hashing, input/output checksums, immutable manifests, pending/completed distinction, atomic publication, file locks, schema/version checks, lineage lookup and safe path containment. Store the local ledger in SQLite with transactional run states; allow rebuilding the index from manifests. `xlm artifact inspect` and `xlm artifact verify` should work without PyTorch.

Acceptance: equivalent configs hash identically; behavior changes alter hashes; list merge/cycle/error fixtures pass; an interrupted artifact cannot be consumed; two writers cannot corrupt one artifact; corrupted files fail verification; a malicious relative path is rejected; schema upgrades are explicit; secret values are absent from resolved reports. Round-trip a real tiny artifact and its lineage. Include tests on platform-neutral paths and document Windows rename/lock behavior.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
