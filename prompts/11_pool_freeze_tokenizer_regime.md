# Prompt 11 — Reusable corpus pools and frozen tokenizer regime

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Assemble reusable immutable clean-text pools from admitted, cleaned, deduplicated and split-safe source views. Distinguish source families from views and record overlap/lineage so multiple views do not give a duplicate document hidden extra sampling weight. A view is a deterministic selector over actual source fields; overlapping membership has an explicit exclusive-assignment or multi-view treatment policy.

Implement `xlm data pool build`, `xlm data pool inspect`, `xlm data pool verify` and `xlm data freeze`. A frozen pool manifest binds source revisions, selected raw files/row IDs, cleaning/dedup/exclusion/split policies, accepted document IDs, byte counts, license/provenance receipts, code and schemas. Failed or missing required components prevent publication. The CLI distinguishes production-ready pools from tiny demo/pilot pools.

Create a tokenizer-fit sampling manifest over the train partition, balanced by declared raw-byte shares across the candidate pool. Freeze it before mixture search. Fit the real 32,768 BPE through the existing tokenizer module and run reconstruction/offset tests on multilingual proper names, code and unusual symbols. A new mixture uses this same tokenizer; a later tokenizer-research track starts a new declared artifact.

Freeze the independent diagnostic corpus and its quick subset once for the campaign; their weights do not follow the winning mixture. A source trial cannot improve its validation metric simply by replacing the validation set. Confirm all duplicate/parent IDs are train-disjoint. Record pool sufficiency for planned budgets, unique available bytes/tokens and potential repeated exposures.

Create a research-regime manifest that refers to the cleaned pool, tokenizer, validation/exclusion policies and reference training/evaluation settings. Separate it from a specific mixture recipe, so selecting a different mixture during data search does not claim the full regime was already frozen. After selection, a final campaign freeze binds the winning mixture/exposure plan as well.

Acceptance: changing a mixture reuses the pool/tokenizer; changing a normalization rule or source revision invalidates downstream inputs; no train/validation membership leak; known overlaps are not counted as independent sources; insufficient distinct text is visible; the artifact can be verified offline. Demonstrate the full pool→tokenizer freeze workflow on local/pilot data. Do not run a large remote tokenizer fit without a resource plan.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
