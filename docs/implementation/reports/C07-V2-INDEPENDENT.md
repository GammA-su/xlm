# C07-v2 independent semantics review (before Astra cross-check)

Reviewed 2026-10-06 from `602cd3ff83a6495589b8d9d7608c9ebc77118488`, in the new
`fix/training-input-policy-v2` worktree. The earlier Astra implementation was not
inspected or ported during this review.

The representation is exact for the frozen protected ByteLevel BPE. For content
IDs t_i and byte lengths L(t_i), v1 defines span i as [sum(j<i)L(t_j), sum(j<=i)L(t_j)].
It does not use backend character offsets. BOS contributes [0,0]. V1 appends EOS
at [N,N], where N is the normalized UTF-8 byte count. The fast producer explicitly
requires sum L == N before truncation. Thus cumulative reconstruction also puts EOS
at N. Keeping a prefix of chosen+1 tokens preserves every prefix sum; it does not
relocate EOS to the truncated byte count. A crossing prefix normally excludes EOS.
Empty text gives BOS/EOS at zero. Literal controls are protected normal text by the
frozen tokenizer's empty added-token table. Unknown or lossy tokenizers cannot be
assumed equivalent; a vocabulary-length table alone is not a universal offset codec.

Independent pinned-tokenizer probe: 10,150 generated/adversarial strings, framed and
unframed, 100,215 prefix comparisons, zero mismatches (4.485 seconds). Includes
Unicode, combining marks, emoji, whitespace, NUL, control spellings and empty text.
The table is per **vocabulary ID**, 65,536 bytes, not per emitted corpus token.
Maximum vocabulary token length is 2,048 bytes. Results are content-free.

Consumer review: sampling and ordered views call `with_byte_spans`; packing feeds
reconstructed coordinates into exact byte exposure and target trace hashing.
Prefetch transports primitive per-target spans; checkpoints store committed trace,
cursor and scheduler state. Evaluation has no direct shard-span dependency.
The 2 GiB input limits constrain hashing/scan work, not science or resident corpus
memory. History `9ccccd2` extracted unchanged literal limits into named constants.

Gaps to address: no per-document reconstruction bound; training accepts absent spans
without v2-specific validation; v2 files omitted from input size accounting; old
training verification still creates the SQLite gate/repeats full verification;
policy is not explicitly frozen. Optional document-order membership hashes raw v2
metadata without reconstructing spans, changing scientific member identity. Cache
admission does not watch the v2 table/counters. Large corpora must not inherit a
global limit increase without an explicit signed policy binding.

Independent size audit: full selected metadata SHA checked, bounded 23,586,153-byte
source prefix sample. All 11 component counts and maximum token counts measured;
sample metadata lines <=745 bytes. `sizes.json` contains projections and finite
2 KiB-per-record bounds. These are not production artifacts or signature verification.

Representation verdict: **SAFE under the frozen tokenizer/coverage contract**.
Consumer readiness at this review point: **NOT YET READY**. No real C07 run or key use.

This historical pre-fix review is followed by [the completed consumer review](C07-V2-CONSUMER.md).
