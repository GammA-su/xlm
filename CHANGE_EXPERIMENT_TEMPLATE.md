# Reusable prompt — implement one new XLM research mechanism

You are modifying the existing XLM repository to test ONE specified hypothesis. Read `AGENTS.md`, the active contracts, frozen regime, baseline config, comparison policy and current implementation status.

## User-supplied fields

- Idea ID and version: [fill]
- Category: architecture / objective / optimizer / tokenizer.
- Mathematical definition or exact algorithm: [fill]
- Claimed original element and closest known mechanisms: [fill]
- Current prior-art review status and source record: [fill]
- Predicted behavior and scale dependence: [fill]
- Minimal falsification experiment: [fill]
- Parameter, information, compute and state differences: [fill]
- Allowed tuning budget and stage: [fill]

## Implementation task

Validate the idea card before coding. Missing definitions are blockers, not permission to invent a different mechanism. Implement the smallest faithful registered plugin plus strict config, serialization and capability metadata. Do not modify the generic evaluator, protected data, baseline settings, mixture, tokenizer or optimizer unless that change is the explicitly declared research variable. Never smuggle future labels into the causal inference path.

Add a correctness reference, gradient/causality tests, state/reset tests, tiny-overfit test, parameter/resource accounting, save/load/resume tests and a no-op/disabled-mode test where mathematically meaningful. Compare against the closest known control and include a component-removal ablation. A no-op control is not evidence of novelty.

Create a matched 50M experiment draft with the same frozen training exposure and declared schedule as its baseline. For tokenizer changes create matched-byte and matched-compute plans, not a simple equal-token comparison. Count any extra objective/teacher calls or optimizer-internal work. Reject unsupported combinations explicitly.

Run only bounded correctness/smoke tests authorized for this session. Produce a diff review, actual test evidence and a resource estimate based on available measurements. Do not launch the full sweep or query sealed evaluations. Give the exact next plan/compare command and keep the outcome status proposed/implemented/inconclusive until real experiments support a stronger claim.

Do not describe the method as universally new or superior based on its name or explanation. Distinguish prior-art search status from measured quality.
