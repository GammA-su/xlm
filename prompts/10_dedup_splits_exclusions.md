# Prompt 10 — Deduplication, lineage-safe splits and contamination controls

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement exact clean-text deduplication across source families and a scalable near-duplicate path with deterministic shingling/MinHash/LSH and bounded verification. Use a disk-backed partitioned index rather than loading every hash and document into RAM. Persist duplicate edges/clusters, stable survivor rules and all retained source aliases. Document hash-collision handling and false-positive/negative limits.

Keep known document families together: source document/page IDs, book chapters, conversation IDs, URL variants and synthetic examples sharing a seed. Use deterministic cluster IDs and order-independent split assignment. If a new snapshot adds bridge duplicates, record changed clusters and generate a new split/pool lineage instead of silently modifying an existing frozen pool. Test both incremental and full rebuild behavior.

Create frozen corpus diagnostic-validation and text-audit partitions independent of trial mixture weights. Tokenizer fitting and gradient training must reject nontraining IDs. Use document boundaries for validation-size targets, record actual bytes, and keep a fixed quick-validation subset nested inside the full diagnostic set.

Implement development benchmark exclusion matching with full-example hashes and informative normalized spans. Avoid removing all ordinary sentences matching a common short n-gram. Include prompt plus all candidate-answer content and answer-bearing duplicates where available. Paraphrase detection is an optional imperfect check, not a guarantee. Report algorithm/thresholds/coverage and sampled audits.

Create the interface for operator-side final exclusion receipts without loading final examples in the coding workspace. Dev mode uses synthetic protected-fixture data only. A final exclusion receipt binds corpus inputs, exclusion policy/index identity, output membership and trusted issuer. Real protected ingestion/evaluation is completed in prompt 21. No default final labels, raw final hashes/snippets or label-bearing cache may be written here.

Acceptance: exact/near duplicates across sources; deterministic cluster survivor across worker counts; shared-seed grouping; group-safe disjoint splits; no validation tokenization; bounded index behavior; synthetic contamination hits and nonhits; no raw protected text in ordinary reports; late duplicate bridge invalidates the affected freeze; malformed/unsigned/untrusted receipt rejected under protected policy. Be honest that contamination detection cannot prove perfect absence of paraphrased benchmark material.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
