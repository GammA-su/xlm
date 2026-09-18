# Prompt 09 — Normalization, extraction, quality filters and audit reports

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement composable, bounded, versioned transforms on canonical records. Each transform returns a transformed record or a reasoned rejection plus metrics; log input/output counts and bytes at each stage. Register strict transform schemas. Pipeline configs may change filters without trainer edits, and altered transform code/config must invalidate downstream artifacts.

Implement UTF-8 handling, NFC/newline normalization, optional actual-HTML extraction, paragraph-aware boilerplate handling, repeated-line/span detection, language filtering, min/max size, symbol/repetition/encoding-noise indicators and basic secret/PII pattern detection. Preserve capitalization, punctuation, math syntax, code indentation and coherent paragraph structure by default. Quality thresholds differ for educational prose, code, historical OCR and structured synthetic text. No vague universal `quality > 0.8` threshold without a defined score/model.

Support optional locally pinned language/quality classifiers through plugins, with explicit model artifact, license, compute and version. No paid LLM judge and no implicit network download. A deterministic heuristic-only mode remains available and clearly labeled. Missing classifier weights produce a blocked capability, not a fake English confidence.

Implement structured-example rendering for explicit passage/question/answer and message schemas. Preserve necessary context. Reasoning inclusion/exclusion is a render-policy experiment, not an arbitrary regex deletion of anything between think tags. Preserve provenance and report incomplete/missing-context examples. Full-sequence pretraining and optional answer-only masks are distinct tracks.

Produce `xlm data clean`, `xlm data quality-report` and review samples with pass/reject reasons, domain distributions, length statistics, projected yield and paired before/after displays. Reports escape HTML and redact protected values. Quarantine has configurable retention/access controls and never includes sealed evaluation matches or raw labels.

Acceptance: golden clean-text tests; canonicalization idempotence; Unicode preservation; no accidental math stripping; code indentation retained; excessive repetition rejected; valid short/high-density educational text not indiscriminately discarded; synthetic required-context tests; version/cache invalidation; bounded-memory processing over many fixture shards. Do not claim classification precision without a labeled audit sample. Include measurable yield and rejection examples from the allowed fixture/pilot.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
