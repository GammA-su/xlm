# Prompt 14 — Single-4090 execution and correctness-preserving performance

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Add the production CUDA path without breaking the CPU reference. Verify installed torch/CUDA wheel, device capability, BF16 behavior and attention backend using `xlm doctor`. Do not install a guessed driver or system CUDA toolkit automatically. Detect and explain unsupported configurations.

Implement BF16 autocast with FP32 master model parameters and optimizer slots by default, stable loss/log-softmax reductions, optional activation checkpointing and optional torch.compile. Expose a tested attention-backend policy and record the actual selected backend/fallback. Arbitrary document masks may select a slower backend; report it rather than pretending flash attention is always active. FP16 is a separate mode with scaler/resume tests, not an implicit fallback.

Create `xlm profile --config ...` that runs bounded synthetic/local-data dry steps on 50m/150m/300m presets, searches feasible microbatch sizes, measures tokens/sec, peak allocated/reserved VRAM, CPU memory, startup/compile time, loader wait, optimizer state and checkpoint size. Calibrate in a separate process so an OOM does not poison the production run. Preserve the intended global valid-token batch through accumulation. Do not change context, precision or architecture silently to get a run to fit.

Resource plans use measured throughput ranges and documented uncertainty, not theoretical peak FLOPs as a promised training time. Separate training execution from evaluation/checkpoint overhead. Report profiler-supported operation counts and labeled estimates where exact counting is unavailable. An unfamiliar architecture’s compute cannot default to the ordinary Transformer formula unnoticed.

Implement graceful preflight rejection when disk/VRAM/headroom estimates fail. Freeze chosen microbatch/accumulation/backend settings before matched runs. OOM recovery during a production run requires restart from a safe checkpoint and an explicit mode/contract decision; it cannot skip data or change the comparison without a record.

Acceptance: CPU reference versus CUDA supported-mode numerics; finite backward/update; activation-checkpointing parity; compile/eager parity within declared tolerances; token-normalization correctness with accumulation; interrupted-resume state continuity; separate real GPU results for each tested size. If no RTX 4090/CUDA is available, implement/tests run on CPU and mark all GPU performance claims NOT RUN. Do not fabricate timing or certify the 300M profile from a tiny-model test.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
