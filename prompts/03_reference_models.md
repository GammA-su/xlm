# Prompt 03 — Reference Transformer and size-independent model API

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement the bias-free pre-RMSNorm/RoPE/SwiGLU causal Transformer using the model interface in CONTRACTS.md. Include a clear eager reference attention path and a PyTorch scaled-dot-product-attention path behind a backend switch. Confirm boolean/additive mask polarity, causal semantics, dropout behavior and fully masked padding rows with the installed PyTorch API. Do not add a third-party fused extension as a mandatory dependency.

Keep the architecture separate from model size. Register `transformer_baseline`, a tiny test preset and 50m/150m/300m shape presets. Use the provided parameter formula and exact expected counts with V=32,768. Implement `xlm model inspect` with meta-device/shape-only inspection where supported, deployed/active/non-embedding/training-only counts, tied-storage detection and model-cap checks. Record parameter estimates versus instantiated counts separately. A tokenizer vocabulary mismatch is an error; never truncate logits to hide it.

Implement forward returning LMOutput, optional hidden-state requests, and explicit no-cache generation support. State capability may initially be stateless/no-cache; later cache support must pass parity tests before enabled. Weight initialization is a named versioned policy with deterministic seeding and documented residual-projection scaling. No trainable absolute-position table unless the configuration explicitly selects one.

Implement cross-document causal-stream masks and an isolated-document mask reference. The model must not attend to future tokens, padded positions or another segment under isolated mode. Position IDs are explicit; the RoPE implementation must behave correctly under offsets and padding. RMSNorm/loss numerical reductions must be stable in supported dtypes. Do not let an `attention_mask` silently disable causality in a fast path.

Acceptance: full numerical gradient flow on a tiny model; future-token perturbations do not change earlier outputs; padding invariance; segment isolation; head/shape validation; reference versus SDPA parity within declared tolerance; tied embedding/output tensors remain tied after load. Verify all three parameter counts using meta or actual instantiation without claiming they were trained. Add finite-gradient and model serialization tests. Include one ordinary deterministic tiny model forward executed through the public API.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
