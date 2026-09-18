# Prompt 12 — Production token shards, mixture scheduling and packing

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Extend the earlier token-record implementation into bounded per-source token shards and a deterministic mixture planner. Do not create a second unrelated dataset format. Use compact memory-mapped arrays with validated token-ID dtype, document offsets, source/lineage metadata, canonical-byte coverage, structural-token markers and checksummed manifests. Keep source shards independent of mixture ratios so ratio changes do not force retokenization.

Implement `xlm data tokenize`, `xlm mixture validate`, `xlm mixture plan`, `xlm mixture inspect` and `xlm mixture preview`. The planner resolves only admitted nonempty views, computes available unique exposure, repetition risk and storage needs, and freezes source/record order with separate data and model seeds. Large plans use compact block/range descriptions and deterministic generators rather than a Python list of billions of positions.

Implement a token-quota/deficit scheduler whose observed valid-target token shares match configured weights within a documented bound. Long-document windows, end-of-stream partials, EOS attribution, ignored BOS/padding and source exhaustion must all be accounted for. Report content-token shares too. No silent source-weight renormalization. `repeat: false` fails on exhaustion; an explicit repeat policy has maximum epochs/exposures and separate repeated-token/byte counters. The scheduling policy is part of the regime hash.

Implement cross-document causal packing and the isolated-document alternative using the mask/state semantics already tested. Validate the T+1 shift and one-token overlap; no document tail or first token disappears unnoticed. Preserve source ownership and byte spans through windows. Resume must restore source deficits, document cursors, partial pack, RNG and committed-versus-prefetched data. Multiworker prefetch must not change committed order; use indexed deterministic batches or replayable queue state, not a live shuffled iterable.

Add matched-canonical-byte/document plans for tokenizer comparisons. Changes in vocabulary/context length must not silently change raw-text exposure. Ensure the accounting can distinguish exact interval-based unique bytes from any approximate sketch and label estimates appropriately.

Acceptance: statistical/deterministic share checks on unequal-length source fixtures; exact token stopping; repeat/exhaustion tests; worker-count/order tests; interrupted and resumed batches have identical IDs and target masks; no double-counted next-token targets; isolated segment masks correct; dtype overflow caught; memory scales with configured buffers rather than corpus size. Verify a second mixture reuses token shards and a second tokenizer invalidates them. Connect the real trainer to this loader and rerun the offline demo/resume tests.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.
