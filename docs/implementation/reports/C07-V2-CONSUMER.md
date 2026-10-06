# Mix-01 C07-v2 training consumer review and fix

2026-10-06. **C07-OFFSETS-V2: SAFE for the exact frozen tokenizer. Consumer engineering readiness: YES.**
This permits the operator to run the reviewed chain; it is not a claim that production
shards, a production freeze, training, or a full-scale timing test have been completed.
No operator key was used. No production output root was created.

Source: `F:/Project/xlm-tokenize-freeze-opus`, `perf/tokenize-freeze-opus`,
`602cd3ff83a6495589b8d9d7608c9ebc77118488` (based on `622c14891090feee9959388ff0a2b00eb608f322`).
Review/fix: `F:/Project/xlm-training-input-v2`, `fix/training-input-policy-v2`.
The initial HEAD, branch and clean status were printed before review. The local commit
containing this report is the release identity; obtain it with `git rev-parse HEAD`.
The source worktree was left clean and unchanged. Nothing was pushed.

## Independent scientific proof

The independent review was recorded in [C07-V2-INDEPENDENT.md](C07-V2-INDEPENDENT.md)
before reading the earlier Astra report. No implementation was ported from that branch.

In `ByteLevelBPETokenizer.encode_with_offsets`, a content token's span is defined by
the UTF-8 bytes decoded from its vocabulary spelling, not backend character offsets.
For ordered token IDs `t_i`, let `L(t_i)` be their exact byte lengths and
`S_i = sum(j < i, L(t_j))`. Then its span is uniquely `[S_i, S_i + L(t_i)]`.
BOS has span `[0,0]`. EOS has span `[N,N]`, where N is the canonical normalized
document byte count. The producer checks the **full** content-length sum equals N
before applying a selected prefix of `chosen + 1` IDs. Therefore cumulative
reconstruction places EOS at N and preserves every truncated-prefix coordinate.
The original document byte count is retained; crossing truncation does not renumber
bytes or append an EOS that was not selected. Empty framed text has two zero spans.

For this pinned tokenizer the added-token table is empty, normalization and postprocessing
are absent, dropout/padding/backend truncation are absent, and ByteLevel has no prefix
space. Every ordinary vocabulary spelling decodes through the byte alphabet. Literal
special-token spellings are protected as ordinary text. This proof is **not** a general
codec for arbitrary tokenizers, unknown-token substitution, or character-offset schemes.

An important correction to the request's description: `token_bytes.u16` contains lengths
per **vocabulary ID**, not per emitted token. It is 65,536 bytes per component, bound by
SHA-256 `8527d59938ff3d32abffa682b3fb338c4aa99caebc220acc276f014b64b39342`.
The protected policy also pins the tokenizer fingerprint and complete upstream chain.

Independent local real-tokenizer probe: **10,150 generated/adversarial strings,
100,215 prefix comparisons, zero mismatches**. It compares reference `encode_with_offsets`
against reconstruction, framed and unframed, with empty text, Unicode, emoji, combining
marks, whitespace, punctuation, NUL, and literal control spellings. A separate authored
tokenizer runs the same property test offline. A 511,580-target authored long shape
has 511,581 IDs; its exact v1/v2 spans agree. Its v1 index is 8,986,549 bytes versus
294 bytes for v2. This is a shape test, not a copy of the real long document.

The authored signed chain resolves both v1 and v2, compares all batch fields over five
batches, JSON-round-trips committed checkpoint state, and compares four further batches
including spawned prefetch. IDs, masks, spans, attribution, trace IDs/digests, schedules
and committed batcher states agree. Optional ordered-document membership now reconstructs
spans before computing its scientific member digest. Physical manifest/input/freeze
identities intentionally differ between representations: a complete execution checkpoint
cannot silently switch input artifact identity. Existing checkpoint tests remain applicable.

After this proof, Astra's report at `65a9afa3173626f6bfa5ae6e3dbb52aed9c0afa4` was read
as an audit cross-check only. Its conclusion that spans are scientifically required is
correct. The stronger statement that they must be **stored explicitly** does not follow
for this frozen tokenizer: reconstruction is lossless. Its v1 size and old input-limit
concerns remain valid. Its implementation was not inspected or merged.

## Consumer and resource audit

The old per-shard and aggregate 2 GiB constants are engineering work bounds, not a
scientific limit. History `9ccccd2` extracted existing literals into named constants;
those constants remain unchanged for legacy inputs. Simply increasing them would have
missed table accounting, unbounded reconstructed records, optional eager ordering,
unsigned permission drift, and repeated legacy verification.

`TokenShardReader` loads bounded manifests/counters and the tiny vocabulary table.
Token payloads are mapped/read in windows. Index iteration is sequential and bounded.
The new startup validator checks v2 IDs and byte coverage in NumPy per document without
creating Python span arrays or an all-document list. Stream consumers reconstruct spans
only for active documents; at most two such records per source are cached in native order.
Optional document-order compilation creates O(documents) membership state and is therefore
explicitly excluded by this Mix-01 policy. Other existing small ordered v1/v2 inputs remain
supported and have identical scientific member digests.

Packing and trace/provenance need the reconstructed spans. Prefetch carries compact
per-target provenance and committed cursor/trace state. Its child now checks the counters
snapshot as well as manifests before reopening shards. Token cache admission watches
the counters and byte table for changes. Evaluation has no direct offsets-array consumer;
training evaluation uses the same batch path. There is no GPU representation change.

### Signed `training-input-policy-v2`

Policy digest: `b5a809bd5b0e36dc10f38251c55094654669041df51fd17f9662da0d23c587b0`.
The exact policy object/digest is signed in `freeze.json`, copied into `training-data.json`,
checked against the signed freeze, and included in training execution/input identity.
Overrides or unknown versions refuse. Protected use is pinned to the exact plan,
completion, tokenizer, count, selection and 6B target total in the request. Authored signed
fixtures are allowed for testing but cannot pass the production preflight.

| Bound | Exact value | Rationale |
|---|---:|---|
| tokens.bin per component | 3,221,225,472 B (3 GiB) | Largest exact payload 2,402,855,572 B; >34% margin |
| offsets.jsonl per component | 4,294,967,296 B (4 GiB) | Largest 2 KiB-record bound 3,421,960,192 B |
| Complete component shard | 6,442,450,944 B (6 GiB) | Largest conservative bound 5,327,223,444 B |
| Aggregate six-file shard inputs | 25,769,803,776 B (24 GiB) | Conservative selected-count bound 23,943,438,634 B; ~59% over point estimate |
| Each manifest/counters/attestation | 65,536 B | Small fixed-schema summaries, no document lists |
| Byte table | 65,536 B | Exact 32,768-entry uint16 vocabulary table |
| Metadata record, including newline | 2,048 B | Sample maximum 745 B; removes token-count-dependent JSON |
| Tokens per document / read window | 1,048,576 each | Largest selected document 511,581 IDs; >2x margin; at most 2 MiB raw uint16 per read |
| Components | 11 | Exact reviewed Mix-01 shape; signed selection verifies the complete set |
| Documents | 6,000,000 | Exact selected 5,824,661; bounded small headroom, no future corpus permission |
| Verification process-tree RSS | 17,179,869,184 B (16 GiB) | >3x the inherited ~5 GiB C07 process-tree measurements; separate from artifact bytes; bounded tables/workers/arrays |
| Verification deadline | 1,800 seconds | Refuses pathological startup; above the 5–8 minute estimate, not a promised measured SLO |
| Consumer disk scratch | 0 payload bytes | Streamed gate/verification; no legacy SQLite corpus copy |
| Order | shard-native-only | Avoids unreviewed eager order compilation at 5.8M documents |

All component sizes are admitted before any large hash. The exact regular file set is
required. Schema, uint16 little-endian encoding, positive-document token accounting,
document counts and byte table are admitted explicitly. V2 records cannot override spans.
Legacy v1 retains its 8 MiB record ceiling and 2 GiB artifact envelope; old fixtures remain
valid. The pathological long v1 line still refuses. Eager v2 `read_document_offsets()` is
limited to 8 MiB; production must use the iterator, as the training stream already does.

The inherited C07 memory figures are Opus's bounded measurements, not a new full-scale
consumer measurement. The RSS monitor is an operational, sampled process-tree bound, not a byte-exact allocator
reservation; an overage cancels verification. This limit is independent of current free
RAM and independent of model/optimizer memory. The normal full training resource planner
must still budget the model and prefetch queue. Keep `max_open_shards=0` (the default) for
this first run; a too-small nonzero LRU cache can repeatedly rehash evicted components.

## Independent storage estimate

The full 1,428,788,117-byte selected-membership SHA was independently checked against
`a4539473bdfcbcf0d52efc621dcfb8efa3fff1cbba8e0e7cc943cf31cad54be9`.
Counts/maxima below use all selected records. The index point estimate uses 2,124 selected
records from 23,586,153 bytes of bounded source-file prefixes across allocations.
It is a stratified convenience sample, not a random or exhaustive metadata audit.
No HMAC signatures were verified in this key-free real-data probe; the operator's
authenticated preflight and production resolver still perform that verification.

| Component | Selected documents | Exact token payload B | Projected complete shard GB (decimal) |
|---|---:|---:|---:|
| common_pile_prose | 162,253 | 600,324,506 | 0.716 |
| essential_practical | 458,396 | 1,200,916,792 | 1.532 |
| essential_prose | 268,659 | 600,537,318 | 0.793 |
| essential_science | 298,500 | 1,200,597,000 | 1.416 |
| finepdfs_en | 308,416 | 1,800,616,832 | 2.019 |
| finewiki_en | 245,684 | 600,491,368 | 0.772 |
| ifm_behaviors_general_planning | 89,455 | 600,178,910 | 0.667 |
| nemotron_wiki_rewrite | 472,537 | 960,945,074 | 1.306 |
| simple_stories | 422,096 | 240,844,192 | 0.543 |
| synth_en_explanations | 1,670,879 | 1,803,341,758 | 2.981 |
| ultrax_ultrafineweb | 1,427,786 | 2,402,855,572 | 3.446 |

Exact token bytes: **12,011,649,322** (11.187 GiB).
Projected index bytes: **4,176,714,731**.
Projected total: **16,189,805,845 B**, about **15.08 GiB**, including a small summary/table
allowance. The largest projected index is synth, 1,177,431,253 B. Maximum selected ID is
49 UTF-8 bytes; sample metadata maximum is 745 B. The maximum actual production metadata
line has not been exhaustively measured: the enforced 2 KiB ceiling is the conservative
bound, with early refusal for an outlier. At that cap, exact selected counts imply at most
23,943,438,634 shard bytes including full sidecar allowances. This is not 24 GiB of RAM.
Freeze/training-data summaries are additional small metadata, covered by the output reserve.

## Measurements and startup projection

[Diagnostic JSON evidence](../evidence/C07-V2-CONSUMER/) is content-free; retained pytest
failure logs contain authored fixture details only, never real corpus text or operator keys.
Windows 11 build 26200, Ryzen 7 5700X3D (8 cores/16 threads), 77,231,341,568 reported RAM
bytes; Python 3.12.13, uv 0.12.19, tokenizers 0.23.2, NumPy 2.5.3, PyArrow 25.0.1,
psutil 7.2.2, CPU torch 2.14.0. Existing `.python-version`, `pyproject.toml`, and `uv.lock`
were used unchanged with offline locked CPU/eval extras. CPU and CUDA extras remain
mutually exclusive; use the pinned `cu126` policy only in a separately planned training
environment. No CUDA or network work was performed.

| Bounded authored diagnostic | Result |
|---|---:|
| Reader initialization | 0.0072 s |
| Hash 161,880,966 payload B | 0.1372 s (warm C: cache) |
| Validate 50,000 docs / 70,150,000 IDs | 0.8152 s; 61,332 docs/s |
| Parent peak RSS for that probe | 128,684,032 B (~123 MiB) |
| Old / reviewed freeze parser, same 50k records | 0.3001 / 0.3428 s |
| Windows sparse file/window fixture | 2,147,483,776 B; mmap and NumPy read beyond 2 GiB; <64 MiB RSS delta assertion |
| Long-shape oracle + reconstruction peak | ~423 MiB, includes explicitly materialized v1 oracle spans |

The sparse fixture tests admission and real Windows mapping/addressing, **not** checksum
acceptance of fake data. It does not hash or certify a multi-GiB authored corpus.
The 50k diagnostic scratch payload was removed. These measurements cannot prove exact
full-run RSS. Active per-document arrays are bounded; no consumer needs 6B Python IDs or
5.8M eager metadata dictionaries. The actual selection's largest document is below half
the policy's reconstruction ceiling. The independent stage and resource tests are bounded.

For policy-bound training resolution, shard hashes are checked twice: reader admission
and authenticated freeze verification. A separate streaming coverage pass reads IDs+index;
the freeze verifier also parses the index against authenticated selected membership.
Thus payload reads are approximately **3T + 4I = 52.74 GB**, plus C05 membership/selection
and small metadata. Legacy training verification used the SQLite path and redundant
hashes; the policy path reuses Opus's authenticated streaming verifier. No signature is
treated as proof that current payload bytes are unchanged. No persistent verification
cache or skipped integrity pass was introduced.

At an assumed sustained G: 500 MiB/s, 52.74 GB has a ~101-second I/O floor. Sequential
index validation extrapolates to ~95 seconds from the warm-cache diagnostic; selected
membership parsing and freeze exposure verification add work. Allow **5–8 minutes for
resolver startup**, an estimate, not a measured cold-SATA guarantee. Starting a prefetch
producer performs one further T+I integrity pass (~16.19 GB, ~31 seconds at that rate),
plus imports. Its existing 300-second startup timeout remains; slow media can refuse.
Separate operator verify commands deliberately repeat verification and are not free.

C07 generation kernels were unchanged. The extra freeze parser checks cost 0.043 seconds
per 50k diagnostic rows (~5 seconds serially over 5.8M rows); small header checks have no
material throughput impact. Opus's **24–26 minute tokenize / 2.5–3 minute freeze** projections
remain inherited estimates, not independently repeated production measurements. The
consumer verification time is additional to tokenization/freeze. No sub-30-minute claim
is made for the combined generation plus training-startup workflow.

## Requirements and verification

| Requirement | Status | Evidence / limit |
|---|---|---|
| Independent frozen-tokenizer proof and adversarial properties | VERIFIED | Pinned local tokenizer + authored oracle, zero mismatches |
| v1/v2 tokens, spans, ordering, batching, trace and resumed prefetch | VERIFIED | Authored signed chain, all-field comparisons |
| Versioned finite envelope signed and execution-bound | IMPLEMENTED / VERIFIED | Policy drift, unsigned injection, protected-chain refusal, boundary tests |
| >2 GiB reader addressability without eager corpus RAM | VERIFIED | Real sparse Windows mmap and NumPy-window test |
| Production resolver entry point | IMPLEMENTED / VERIFIED | `input_preflight`; authored success and production refusal |
| Legacy small v1 and existing C07 behavior | VERIFIED | Focused shard, freeze, stream, trace, training, checkpoint and order regressions |
| Exact production signature verification and 6B materialization | NOT RUN | Operator-only; no key or production artifacts used |
| Full production RSS / cold-SATA startup / full 50M training | NOT RUN | Projections and bounded diagnostics only |
| Alternative document orders under this policy | OUT OF SCOPE | Explicit refusal; needs a separately reviewed envelope |
| GPU acceleration, full offline suite, CUDA tests | NOT RUN / OUT OF SCOPE | Consumer change, focused acceptance only |

See [command ledger](C07-V2-COMMANDS.md) for commands, exit statuses, failures and repairs.
No unrelated baseline changes were made. The complete offline acceptance suite was not run.
The selection -> tokenize -> freeze -> training-resolve chain is ready for the operator
with **c07-offsets-v2**, the explicit signed policy, and shard-native order. Actual output
must pass the production resolver before any model training is authorized.

Next commands: [C07-v2 operator runbook](../../runbooks/c07-v2-consumer.md).

**MIX-01 C07-V2 CONSUMER READY**
