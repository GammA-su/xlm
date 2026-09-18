# Final acceptance matrix

Every requirement below needs an implementation reference and actual test evidence. Record results as VERIFIED, IMPLEMENTED BUT NOT RUN, BLOCKED, or FAILED. CPU fixtures, mocked provider schemas, real-source pilots, real GPU tests and operator-isolated final tests are different evidence classes and cannot substitute for each other.

| ID | Requirement | Prompts | Evidence required |
|---|---|---|---|
| A01 | Environment and CLI | 00 | Real uv lock, CPU setup, metadata-only doctor, lazy optional imports |
| A02 | Configuration correctness | 01 | Unknown/duplicate keys, cycles, typed overrides, draft/executable distinction |
| A03 | Immutable artifacts | 01 | Atomic publish, checksums, corrupt/partial rejection, lock contention |
| A04 | Local canonical data | 02 | UTF-8 fixtures, train-only tokenizer fit, provenance and round trips |
| A05 | Reference architecture | 03 | Causality/masks, exact three parameter counts, tied weights |
| A06 | Objective normalization | 04,05 | Uneven microbatch masks equal global-batch token-normalized gradient |
| A07 | Optimizer and scheduler | 04,05 | Complete unique param groups, state reload, horizon continuity |
| A08 | Exact token budget | 05,12 | Nonmultiple final target count, structural/padding exposure accounting |
| A09 | Safe resume | 05,12 | Same committed sample IDs and deterministic CPU continuation |
| A10 | Native scoring | 06 | Boundary/whitespace/Unicode/long-target tests and byte-denominator correctness |
| A11 | Offline vertical slice | 00–06 | Tiny import→tokenizer→train→resume→evaluate→generate with real artifacts |
| A12 | Twenty-source discovery | 07 | Candidates not presumed admitted; real schemas/revisions and status |
| A13 | No FineWeb substitution | 07,13 | Denied direct source, no silent fallback or unknown-source renormalization |
| A14 | Acquisition bounds | 08 | Network bytes/cache/temp output/retries all bounded and observable |
| A15 | Source live verification | 07,08,13 | Real small pilot distinguished from transport/schema mocks |
| A16 | Normalization and cleaning | 09 | Idempotence, math/code/paragraph preservation, explicit rejection reasons |
| A17 | Deduplication and lineage | 10 | Cross-source exact/near duplicates; deterministic bounded clustering |
| A18 | Corpus split integrity | 10,11 | Grouped disjoint splits, independent diagnostics, no tokenizer validation leak |
| A19 | Benchmark exclusion | 10,21 | Development matches, protected receipt contract, no hidden examples leaked |
| A20 | Pool/tokenizer freeze | 11 | Reused across mixtures; correct invalidation on source/filter changes |
| A21 | Token-shard format | 12 | ID dtype, offsets, byte coverage, checksums, bounded memory |
| A22 | Mixture scheduling | 12 | Token rather than document shares, exhaustion/repeats/drift/carry state |
| A23 | Initial mixture and treatments | 13 | M0–M5 valid and explicit no-IFM alternative, admitted-source blocking |
| A24 | CUDA correctness | 14 | Real dtype/mask/update parity only on tested available hardware |
| A25 | CUDA performance | 14 | Real per-size microbatch/VRAM/throughput reports or NOT RUN |
| A26 | Official harness parity | 15 | Pinned task/scorer API and native parity; no accidental final default |
| A27 | Evaluation tiering | 15,21 | Search/confirmation IDs stable, partial scores labeled, final protected |
| A28 | Queue and code freeze | 16 | Actual executed snapshot immutable, single GPU lease, recovery/cancel |
| A29 | Campaign cost limits | 16,22 | Trial totals/horizons fixed and unauthorized large jobs blocked |
| A30 | Comparison fairness | 17 | Allowed-differences contracts reject inappropriate causal comparisons |
| A31 | Statistical analysis | 17 | Paired/cluster-aware bootstrap tests; seed and item uncertainty separated |
| A32 | Promotion and ablations | 17 | From-scratch next-size draft, no auto-launch/final, factorial plans |
| A33 | Research extension seams | 18 | Four no-op plugins; no trainer/scorer edits; disabled scaffolds honest |
| A34 | Reports and dashboard | 19 | Real artifact data, partial/failure visibility, HTML escaping, optional UI |
| A35 | Export and generation | 20 | Fresh-process native parity, safe deserialization, no secret/raw-data bundle |
| A36 | Protected deployment | 21 | Actual OS separation/operator tests or explicitly NOT RUN |
| A37 | Release audit | 21 | Rights/provenance evidence and limits, final exposure, model/code hashes |
| A38 | Complete runbooks | 22 | Executed clean offline workflow, current uv CLI examples and recipe checks |
| A39 | Independent audit | 23 | Requirement mapping, adversarial failures, no fabricated research results |

## Final audit must answer

Can an operator reproduce the offline path from a clean install? Can all sources be audited without pretending they are admitted? Does changing only a mixture reuse the tokenizer/shards and preserve the intended token weighting? Can a new component be plugged in without altering the evaluator? Are token/byte exposure and optimizer accumulation correct? Does interruption replay the same committed data? Are all large operations bounded and separately authorized? Can the software honestly distinguish implemented infrastructure from unrun training campaigns and unverified final isolation?

A final report may say “offline platform verified; CUDA/source/operator checks pending” when that is what the evidence supports. It may not say “complete live production verification” or “SOTA models trained” without corresponding evidence.
