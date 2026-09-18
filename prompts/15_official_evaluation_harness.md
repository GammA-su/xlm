# Prompt 15 — Pinned official evaluator and tiered benchmark protocols

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Integrate the current supported lm-evaluation-harness through an immutable pinned revision and XLM adapter, using the documented likelihood, rolling likelihood and generation interfaces. Prefer a supported external backend registration rather than editing installed third-party code. Keep native scoring tests as the oracle for indexing and implement the real installed API signature, which may differ between versions.

Create explicit XLM task variants for search/confirmation/final policies. Pin official dataset revisions, split membership and preprocessing. Freeze ARC-Easy search=train, confirmation=validation, final=test; grouped HellaSwag/PIQA train subdivisions for search/confirmation and official validation for final; whole BLiMP subdataset partitions. The ordinary developer command must never default to a final split. Do not download final labels into an ordinary HF cache to create the search configuration.

Primary metrics are BLiMP macro accuracy, ARC-Easy/HellaSwag `acc_norm`, PIQA `acc`. Inspect and pin the exact normalization implementation and tie behavior; save `acc` and `acc_norm` for all applicable tasks. Calculate the four-task index only with complete declared coverage and correct chance references. Save exposed per-item likelihoods, choices/margins and identifiers with model/tokenizer/evaluator context. Record truncation, omitted items, errors and limits; no silent score on the easy subset that happened to fit.

Implement `xlm evaluate --suite search|confirmation` and an operator-only final-request path completed in prompt 21. Batch scoring must be invariant to ordering and branch state. Handle whitespace/continuation merges, empty context, long answers and context-window policy with regression tests. Cache all relevant fingerprints; stale third-party caches must not override XLM identity checks.

Create reference-checkpoint evaluation registration, with exact model/tokenizer revisions, actual parameter counts and common scoring/context policy. Reevaluate rather than copy model-card numbers. Any model download requires a separate bounded plan. The default unit test oracle is a tiny authored/reference-model fixture, not a secretly downloaded public checkpoint.

Acceptance: native versus harness likelihood parity on saved development fixtures; actual task-format and normalization tests against the pinned implementation; initialized-model evaluation; deterministic score reload; split firewall; masked/truncated long example tests; missing tasks prevent aggregate; no-op model scores match. Run small official **search** evaluations only when network/data authorization exists, clearly labeled limited smoke results. Final/real public-comparator verification may remain operator-blocked without falsifying implementation status.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
