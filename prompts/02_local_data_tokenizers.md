# Prompt 02 — Local data adapters and reversible tokenizer vertical slice

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Build a small working local-data path now, not the complete remote pipeline. Implement adapters for UTF-8 plain text and JSONL and a canonical-document writer/reader. Define a tiny source manifest with author-written English, non-English names, code, equations, paragraphs, emoji and literal special-token-looking text. Assign training and validation by explicit fixture IDs; no evaluation benchmarks or user-private content in fixtures.

Implement the tokenizer registry using a deterministic byte-tokenizer fixture and a real Hugging Face Tokenizers byte-level BPE implementation. Baseline real vocabulary target is 32,768, including declared PAD/BOS/EOS/UNK policies. Tiny tests use a smaller feasible vocabulary; never report that artifact as the production tokenizer. Apply canonical normalization before tokenization, not a second hidden tokenizer normalizer that changes the canonical text. Unknown Unicode must remain representable through bytes.

Implement `xlm data import-local`, `xlm tokenizer train`, `xlm tokenizer inspect`, `xlm tokenizer encode` and `xlm tokenizer verify`. A tokenizer fit manifest includes only admitted training document IDs and sample weights/seed. Save tokenizer bytes, metadata, implementation version and its training-input hash, and integrate with the artifact store. Fit in bounded chunks; do not concatenate the entire training corpus in memory.

Define exact reconstruction and byte-offset conventions. Ensure corpus text resembling `<eos>` is not converted into a control token without explicit policy. Deal with empty documents and invalid byte sequences consistently. Changing special tokens or normalization changes the fingerprint. The BPE vocabulary cap may be unattainable on a tiny fixture: expose the actual count and refuse to mislabel it as a 32,768 baseline artifact.

Create minimal per-document token records used by the next milestones; these are the first implementation of the eventual token-shard interface, not a disposable alternative. Include stable source IDs, document boundaries and canonical byte counts. Large-scale storage and mixture planning are expanded in prompt 12.

Acceptance: exact round-trip of canonical Unicode; train-only fitting enforced; save/load identity; deterministic fit in the tested environment; invalid ID and malformed-record errors; vocabulary and special-ID integrity; byte offsets validated on multibyte characters. Run a real tiny BPE fit through the CLI and publish its artifacts and command transcript.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
