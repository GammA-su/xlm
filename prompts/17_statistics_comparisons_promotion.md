# Prompt 17 — Fair comparisons, uncertainty and size-promotion gates

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement comparison-contract checking before any winner calculation. Separate architecture, objective, optimizer, tokenizer, data-mixture and unconstrained-system tracks. Each has an explicit allowed-differences schema and requirements for shared data, tokenizer, normalization, raw exposure, parameter limits, context, initialization pairing, tuning allowance and resource budget. An incompatible pair must be marked ineligible with a readable diff, not given a misleading causal-improvement badge.

Load aligned per-item development predictions and compute task components, BLiMP macro score and the internal suite index. Implement paired cluster-aware bootstrap with deterministic analysis seeds, including BLiMP subdataset grouping and source clusters for other tasks where frozen. Report point differences and uncertainty, plus separate between-training-seed results. With one seed, say seed variability is unmeasured. Mismatched IDs, partial task coverage or missing predictions invalidate the paired analysis.

Implement learning-curve comparisons against valid tokens, canonical bytes and measured compute. Define target-crossing interpolation and no-extrapolation rules; a model that never reaches a target does not get invented compute-to-target. Include training-only auxiliaries/teacher costs where applicable and clearly labeled operation estimates.

Implement `xlm compare` and `xlm promote`. Gate rules are versioned and frozen in advance: suggested +1 suite point or at least 10% lower measured compute to a declared target, minimum required seeds, material-regression warnings and the configured CI condition. Search-stage CIs are decision support, not confirmatory proof after adaptive selection. Record multiplicity/tuning exposure and explicit scale-hypothesis exceptions; do not produce a formal significance claim from an arbitrary adaptive sweep.

Promotion creates a draft 150m/300m experiment initialized from scratch, with comparison lineage and projected resource plan. It does not resize weights, increase training budget silently, grant authorization or run the final suite. Add generators for data×architecture factorial and two-idea ablation matrices.

Acceptance: identical predictions give zero paired difference; known synthetic winners and swapped arms behave correctly; cluster-resampling test; seed/item uncertainty separated; incomplete suite rejected; changed tokenizer fails architecture-track comparison; byte-matched tokenizer track can pass; materiality threshold is not rewritten by output scores; promotion requires evidence and emits a valid draft without launching a job. Include clearly synthetic report fixtures only.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
