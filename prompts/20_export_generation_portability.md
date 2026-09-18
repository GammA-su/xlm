# Prompt 20 — Model export, reproducible loading and inference

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement native XLM export/load for the baseline and every supported plugin family. Include safe tensor weights, tied-weight mapping, model/tokenizer/config hashes, canonical-text normalization policy, plugin/capability versions, parameter accounting, training-data manifest references, context limit and generation defaults. Record training budget and evidence status. Do not bundle full copyrighted corpora, hidden labels, credentials, optimizer states by default or unrelated run files.

Implement `xlm export`, `xlm generate` and a bounded text-completion session command. A base model remains a completion model; do not attach an invented chat template that changes benchmark behavior. Use max-new-token, stop/EOS handling and state reset. Cached inference may be added for the baseline only after parity with full recomputation, including position offsets, padding and long-context/window behavior. Models without a valid cache use the no-cache path and are labeled accordingly.

A Hugging Face-compatible export for the **baseline** is a useful optional interoperability path once architecture mapping and tokenizer special IDs can be verified exactly. It must have native/HF logits and likelihood parity tests; do not claim arbitrary novel architectures can be exported as an existing GPT family. Custom-code exports are labeled and never automatically loaded with trusted remote code. Native export is the guaranteed interface.

Support CPU load and supported CUDA dtype/device load. Reject incompatible serializer/plugin versions with migration guidance. Check all file hashes before loading. Protect against untrusted pickle/object deserialization. A released checkpoint excludes training-only objective heads unless the inference design actually requires them, with accounting explaining the difference.

Acceptance: train toy native model → export → clean process load → identical supported-mode logits, likelihood and greedy completion; tokenizer reconstruction unchanged; tied parameters restored; corrupted/incomplete export rejected; missing plugin fails usefully; no-cache/cache parity when implemented; max context and stop strings work; sensitive data absent. Do not publish artifacts to any remote hub without explicit authorization.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
