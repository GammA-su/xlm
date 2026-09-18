# Prompt 06 — Native likelihood, diagnostics and complete offline demo

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement native conditional log-likelihood using the common model/tokenizer contracts. Score all tokens in a supplied continuation while correctly handling the boundary between context and continuation. Follow a documented tokenization convention compatible with the later harness adapter; test leading spaces and merges crossing a naïve separately-tokenized boundary. Empty prompts use the declared BOS/EOT policy. Padding and context targets are excluded. Long continuations must not be silently shortened; use a tested rolling/context policy or report a clear unsupported case.

Implement rolling document likelihood, same-tokenizer perplexity, bits per byte on precisely tracked canonical text spans, per-source validation losses and configurable fixed diagnostic samples. Count UTF-8 bytes, not Python codepoints. Do not include byte counts of unscored prompt tokens. Denominator/marginalization conventions for different tokenizers must be explicit. Avoid counting overlapping rolling-window targets twice.

Add bounded autoregressive generation with greedy and temperature/top-k/top-p sampling, max-new-token and stop/EOS handling, deterministic seed options and no-cache fallback. Treat it as base-model text completion, not an instruction-following chatbot. Correctly switch train/eval modes and restore them. Never use future gold continuation tokens as prompt input for their own predictions.

Create an offline benchmark-shaped fixture scorer for multiple-choice and minimal-pair tests. It must be visibly labeled synthetic/offline and must not register itself as official BLiMP/ARC/HellaSwag/PIQA. Add cache identities including checkpoint, tokenizer, prompt/scorer/policy/dtype; a changed model cannot retrieve another model’s cached likelihood.

Acceptance: compare likelihood against brute-force token-by-token scoring on a toy model; batch/single parity; correct answer-mask positions; empty/long/Unicode cases; stable scores after save/load; no state sharing between examples/answer branches; changing a weight or tokenizer invalidates cache. Validate bits-per-byte against an authored hand-computable example. Extend `xlm demo` through train → resume → evaluate diagnostic fixture → generate → report. This completes the offline vertical slice.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
