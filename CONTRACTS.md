# Binding implementation contracts

These are proposed XLM v1 contracts, not external standards. A deliberate change requires a versioned migration and new comparison lineage.

**P35 scientific-contract migration (2026-09-25):** New research follows
[xlm-science-v1](docs/implementation/reports/P35-SCIENTIFIC-CONTRACT.md).
Its explicit endpoint-before-update LR policy, separate training RNG, statistical
reproducibility and variance-calibrated promotion rules require the versioned
[implementation handoff](docs/implementation/handoffs/P35-OPUS-HANDOFF.md).
P35 does not change existing execution defaults or reinterpret legacy checkpoints.
C09/C10 LR semantics and C12's suggested promotion thresholds remain historical
for legacy runs; the P35 specification governs new scientific comparisons once
implemented. Missing implementation is not permission to launch an ambiguous run.
Milestone 1 (LR policy, training RNG, runtime identity) is implemented as the
explicit opt-in described in [science-v1](docs/science-v1.md); schema-v1
configurations and checkpoints resolve to the legacy policy with unchanged hashes.

## C01 — Configuration and freeze

Use strict versioned Pydantic schemas for source, transform, pool, tokenizer, model, objective, optimizer, schedule, training, evaluation, comparison and campaign configurations. Allow YAML inheritance through a bounded `extends` mechanism and named preset references. Deep-merge maps, replace lists, reject duplicate YAML keys, reject non-finite values and unknown fields, and show the fully resolved configuration before execution. Reject path traversal, recursive interpolation and arbitrary code. Explicit environment interpolation is restricted to declared variables; secret values are never serialized.

Separate a **draft recipe**, which may refer to source candidates and tokenizer names, from an **executable resolved plan**, which contains immutable artifact IDs, code/dependency hashes and reviewed policies. Do not accept strings such as `latest`, `TODO` or fake example hashes as a frozen input. Run reproducibility hashes exclude cosmetic labels/timestamps but include every behavioral field. Hardware/runtime fingerprints are recorded separately and included in equivalence checks where relevant.

## C02 — Canonical document

A canonical record includes `doc_id`, source ID, pinned source revision, source file and row locator, raw hash, clean hash, text, canonical UTF-8 byte count, language/confidence, document kind, source metadata, provenance/parent IDs, license/admission reference, transformation log, quality reasons, duplicate/lineage cluster IDs, and split. Unknown metadata is explicitly unknown.

Keep raw identity separate from clean identity. Source locators plus raw content hashes identify original rows; clean hashes identify equal normalized strings. Avoid Python’s randomized built-in hash. Hash algorithms, canonical serialization and Unicode normalization versions are explicit. Decode errors and lossy repairs are counted, not hidden. Never silently turn a message list into a string representation of a Python object.

Synthetic examples preserve necessary passages, question, answer and provenance. Their render template and reasoning-inclusion policy are versioned. A missing required field rejects the record. Loss masks must distinguish any optional answer-only training track from ordinary full-sequence pretraining.

## C03 — Cleaning and safety

Default canonicalization preserves case, punctuation, paragraph boundaries, mathematical notation and code indentation. Use NFC and newline normalization as versioned transforms; do not lowercase, stem or aggressively collapse whitespace for model text. A separate normalized view may be used for duplicate/exclusion detection. Perform text extraction only when the record actually contains HTML; do not strip mathematical angle brackets from ordinary text.

Filters annotate pass/reject reasons and numerical features: language, length, broken extraction, excessive repeated spans, boilerplate, symbol/digit density, incomplete/self-referential generated answers and missing-context indicators. Thresholds are per-domain configuration, not one universal quality formula. Preserve an auditable sampled rejection set with restricted access. Basic secret/PII detection is not a certification that a corpus is free of personal information.

No OCR dependency or automatic raw-PDF ingestion is required. Use the extracted text in the selected PDF datasets. A future PDF/OCR extension is separate, opt-in, cost-bounded and documented.

## C04 — Source admission and bounded acquisition

A source candidate is not trainable until the required admission fields are resolved: real repository, immutable revision, subset/files/split, actual schema, tested adapter, source license/provenance review and explicit operator approval. Record candidate status separately from successful small-pilot status and production admission. A successful metadata probe is not a full adapter test.

No implicit FineWeb or FineWeb-Edu fallback. Maintain direct-source denylists and lineage review; unknown upstream provenance stays unknown and can be blocked by the selected policy. FineWiki/FinePDFs-Edu are allowed candidate names from the requested shortlist, subject to their own audits. Do not claim absolute absence of indirect source overlap when provenance cannot establish it.

Acquisition is resumable, host-allowlisted and bounded by transferred bytes, cache/scratch disk, record size, documents, requests, duration and output size. Prefer selected immutable shards/row groups over entire repository snapshots. Record selected files and row ranges before a production run. A streaming sample describes its sampling frame and bias; do not call the first N rows a uniform sample of a trillion-token corpus. Deny remote code and executable archives; guard traversal and decompression bombs. Never accept access terms on the user’s behalf.

**C04 benchmark-risk amendment `c04-benchmark-risk-v2` (2026-09-30).**
Source admission is acquisition/pretraining eligibility, never evidence of zero
benchmark contamination. The legacy `clean` value had no precise contamination
definition; retaining it for legacy decisions does not retroactively certify a
corpus. Known possible contamination in the three Essential-Web views must use
`suspect_with_mitigation`, with this contract version, SHA-256 bindings to the
benchmark-risk and source/license/provenance reviews, an explicit C05 mitigation
binding, the unchanged resource contract and operator approval. `suspect`,
`disabled_pending_audit`, arbitrary strings, missing review and missing mitigation
do not admit. Old Essential `clean` decisions require resealing; other legacy
decision defaults and historical checkpoint identities are unchanged.

The bound mechanism is `xlm.data.exclusion`: full-example and informative-span
screening of the eventually frozen Essential/Mix-01 canonical training pool
against all BLiMP, ARC-Easy, HellaSwag and PIQA splits. C05 still requires exclusion
before tokenizer fitting and gradient training. Admission is not proof that this
step ran. Official benchmark claims additionally require a protected, trusted C05
receipt matching frozen input and kept membership, exclusion policy/index, and
the evaluation's checkpoint/suite lineage. Receipt verification establishes only
screening under that policy, with its limitations; never universal zero
contamination. Missing final-pool integration or receipt leaves benchmark claims
blocked. This amendment changes admission lineage only: no source, selector,
tokenizer, mixture, quota or scoring changes.

**C04 raw-artifact amendment `essential-web-raw-artifact-v2` (2026-09-30).**
For source `essential_web` only, the raw acquisition artifact may be the
`verified_source_parquet`: the unmodified upstream Parquet file of a selected
shard, identified by repository, immutable revision, source path, strong ETag,
remote length and the locally computed SHA-256 of every byte, stored once and
never replaced. The strong ETag is an opaque remote validator used for resume
and drift detection; it is never assumed to be a content hash, whatever its
shape. The local SHA-256 must equal the independent expected SHA-256 whenever
the plan or the repository declares one for the pinned path; a storage hash
such as `X-Xet-Hash` is kept as its own field and never substituted. Adaptation then reads the retained file directly; the selected-record
stream is produced in memory with the certified serialization and its SHA-256 is
recorded per file, so it is reproducible offline and is not stored. Immutable
rejection ledgers may be stored zstd-compressed and stay identified by the
SHA-256 of their uncompressed version-1 JSONL bytes. Row accounting, campaign
membership and an acquisition receipt are kept per file. Transfer stays
resumable, host-allowlisted and bounded as above, now including a byte cap on
fast scratch that counts partial downloads. Nothing retained may be deleted; a
second raw representation is not kept. The historical `selected_records`
artifact and every other source are unchanged. This amendment changes physical
transport and storage only: no source, revision, selector, membership, mixture,
quota or stop-target changes.

## C05 — Duplicate clusters, exclusions and splits

Deduplicate across all selected source families before assigning final train/validation splits. Implement exact hashes and a scalable near-duplicate method such as shingled MinHash/LSH with bounded candidate verification; do not use an all-pairs comparison. Algorithm, seeds, thresholds and deterministic survivor selection are frozen. Retain all source aliases and provenance of the surviving document.

Keep known derivatives, books/chapters, conversations, URL/article versions and paraphrases sharing a seed in one lineage group where available. Near-duplicate detection has false positives/negatives: report estimates and sampled audits, not a perfect-clean guarantee. If new sources later merge formerly separate groups, refreeze the affected pool and invalidate comparisons; never quietly reuse now-leaking splits.

Reserve diagnostic validation independently of candidate mixture weights. Initial target: 50 MB of canonical English text and a fixed 5 MB quick subset; exact sizes may differ at document boundaries and must be recorded. All tokenizer fitting, data adaptation and gradient training use only the training partition. Validation sampling is deterministic and group-safe.

Exclude all benchmark splits from gradient and tokenizer training, including benchmark `train` splits used as development evaluations. Match full examples and sufficiently informative spans; avoid deleting ordinary English merely because it shares common short phrases. Protected final data are accessed only by an isolated exclusion/evaluation process. Its ordinary output is an opaque receipt, aggregate counts and corpus keep/drop decisions—not benchmark labels or matching snippets. See EVALUATION_POLICY.md.

## C06 — Tokenizer contract

Baseline: reversible byte-level BPE, vocabulary 32,768 including special tokens. Use a toy byte tokenizer/small BPE for offline tests only. Freeze a balanced tokenizer-training sample from the admitted training pool before mixture search; never retrain the tokenizer for each mixture. Do not adapt it using validation or benchmark text.

Required API: fit when applicable; encode; decode; batch_encode; byte-span/offset convention; vocabulary size and special IDs; save/load; fingerprint; deterministic evaluation behavior; capability flags. Persist exact serialization, fit input hash, implementation/version, normalization identity and seed. Reconstruction is relative to canonical NFC text, not the pre-cleaning bytes. Test arbitrary Unicode, combining marks, emoji, non-English proper names, literal special-token-looking strings and malformed input rejection. Ordinary corpus strings must not become control tokens accidentally.

For stochastic/adaptive tokenizers, require a specified deterministic scoring convention or a justified string-probability method, all side-information costs and explicit treatment of context/continuation boundaries. Standard BPE is not globally prefix-stable: do not incorrectly reject it for that. Instead, reproduce the frozen harness boundary convention and ensure the scored prompt is not adaptively re-encoded using a candidate answer in a way unavailable at generation time. When an interface cannot support meaningful likelihood evaluation, mark that benchmark capability unsupported.

## C07 — Frozen token shards and mixture accounting

Store per-source token arrays with document boundaries, source/lineage IDs, byte-span metadata, special-token markers, token dtype, endianness, tokenizer/pool hashes and checksums. uint16 is allowed only when every ID fits; otherwise use uint32 or a validated wider format. Chunk all processing; never load a whole corpus or giant Python list of all token IDs into RAM.

The default research budget unit is **valid next-token target positions** contributing to training, excluding padding, ignored labels and BOS; include EOS targets and report their share separately. Record content-token counts, unique/repeated canonical text exposure, and source shares as additional counters. Mixture weights refer to these valid target-token exposures under the frozen tokenizer—not documents and not publisher token estimates. Attribute EOS to its document’s source. The planning report shows any difference between content and structural-token shares.

Compile mixture recipes into deterministic exposure plans over reusable source shards. Implement token-quota/deficit scheduling, stable seeded order and carry-over state. Report observed shares and bounded drift; do not use document sampling weights as though they guaranteed token weights. Cap document/window lengths for a tractable drift bound. Handle an exhausted source by the configured explicit `error` or bounded-repeat policy. No silent renormalization, substitution or unreported repetition.

The baseline packing policy is a causal stream with EOS document boundaries and cross-document attention explicitly enabled. Isolated-document packing is a separate supported policy with correct segment masks and position reset. Source-local/other packing policies are also distinct experiment fields. Never change packing to make a candidate fit without creating a new comparison contract.

For a stream window of T+1 IDs, feed the first T and target the next T. On continuation, overlap only the last context token so target positions are neither dropped nor double-counted. Handle final partial windows with explicit masks. Preserve byte coverage and document offsets. Carry sampler, packer and partial-window state across checkpoints. Prefetch advances a speculative cursor; checkpoints commit only consumed data or serialize the queue so resume cannot skip examples.

For tokenizer comparisons, make matched-document/byte exposure plans as well as matched-compute plans. Same bytes, normalization, source attribution and comparable raw-text context must be reconstructible. Bits per byte needs precisely defined scored bytes and exclusions; never divide loss by bytes belonging to unscored context. Count each covered byte once under the documented coding convention.

## C08 — Model contract and counts

Every architecture supplies `forward(input_ids, attention_mask, position_ids, state, requested_outputs)` → a typed output with logits or a declared distribution interface, optional named auxiliary outputs, and new state. Models do not receive gold labels through the inference API. Each plugin defines init/reset/detach/clone state operations where needed, serialization and capabilities. Full forward without cache is the correctness reference. A model without a tested cache may generate slowly without cache; it must not pretend to have a correct cache.

Reference Transformer: bias-free, pre-RMSNorm, RoPE, full multi-head causal attention, SwiGLU, tied token embedding/output weights; zero dropout initially. Reference shapes (V=32,768):

| name | blocks | width | heads | FFN width | expected unique parameters |
|---|---:|---:|---:|---:|---:|
| 50m | 10 | 512 | 8 | 1,472 | 49,883,648 |
| 150m | 18 | 768 | 12 | 1,984 | 149,942,016 |
| 300m | 24 | 1,024 | 16 | 2,240 | 299,418,624 |

Formula: `V*d + L*(4*d*d + 3*d*f + 2*d) + d`. These are initial shapes, not measured optimal choices. Verify module counts, aliasing and sizes at runtime. Tied storage is saved and counted once. Include all deployed parameters, even inactive experts or routers; separately report active/non-embedding/training-only parameters and inference state. Changing vocabulary changes the count; refuse an exceeded cap unless an explicit new size contract authorizes it. Parameter-matched reshape proposals require approval, never an unnoticed width change.

## C09 — Objectives and optimizers

Objective plugins receive model outputs and targets after causal inference. They return a differentiable optimization quantity, named unscaled diagnostics, normalization requirements, extra resource use and trainable state where applicable. Ordinary next-token CE is independently evaluated regardless of the objective being optimized.

A token-additive loss returns summed valid-token losses and a valid-token denominator. The trainer divides the global accumulated sum by the actual number of valid targets once, not the mean of microbatch means. A non-additive/global-batch objective declares an alternative accumulation protocol; reject unsupported microbatching rather than silently changing its mathematics. Request optional hidden states explicitly to avoid retaining every activation by default.

Baseline AdamW: betas (0.9, 0.95), epsilon 1e-8, weight decay 0.1, norm gains excluded, gradient clip 1.0. Declare the tied embedding decay policy explicitly. Initial LR anchors are 1e-3 / 6e-4 / 3e-4 by size; they need calibration. Count optimizer state, auxiliaries, closures and extra forward/backward passes. Equal optimizer tuning trial counts are a configurable fairness policy.

Schedules use a declared counter (default committed valid targets). Warmup plus cosine decay is baseline, with an explicit total horizon and warmup amount. Do not extend a short completed cosine schedule and call it identical to a long-horizon baseline. Resume preserves horizon; extending budget creates a fork with an explicit extension policy.

## C10 — Trainer and checkpoint

Single-device CPU and CUDA implementations share the same loop. Starting research context 512 and global batch 65,536 valid targets; microbatch chosen by a preflight profile, with accumulation preserving the global batch. BF16 autocast where verified, FP32 master parameters/optimizer state by default; loss/log-prob reductions are numerically stable. FP16 is an explicit alternative with scaler state. Compilation and fast attention are opt-in verified performance modes, never correctness dependencies.

Stop token-budget runs at exactly the allowed target count using a masked partial final batch. Also support secondary wall-time/disk limits and explicit matched-compute experiments. Track consumed targets, targets contributing to successful updates, padding, repeats, skipped/failed attempts, optimizer updates, training wall time, evaluation/checkpoint time and startup/compile overhead separately. Non-finite loss/gradients fail the default run at a safe point; do not silently burn data and continue.

Checkpoints are atomic, checksummed and resumable at optimizer boundaries. Save model and objective parameters, optimizer slots and groups, schedule horizon/state, token/byte counters, sampler/packer committed cursor, Python/NumPy/CPU/CUDA RNG state, scaler, configuration, environment and code/input hashes. Use safetensors plus explicit metadata/tensor tables where practical; never unpickle an untrusted checkpoint. Restore tied aliases. Resume refuses incompatible model/tokenizer/data/precision policy changes; a deliberate fork records lineage.

Guarantee exact data replay in the same frozen plan. Demonstrate deterministic CPU continuation. CUDA numeric reproducibility is claimed only for tested deterministic environment/backend combinations; record tolerance/mode and do not promise bitwise identity across drivers, devices or releases. Do not use a live shuffled remote iterable as the controlled training source.

## C11 — Evaluation and cache identities

Implement native conditional likelihood and same-text diagnostics, then a pinned lm-evaluation-harness adapter. Use the harness’s actual versioned prompt and continuation handling rather than reimplementing its named tasks approximately. Empty contexts, continuation whitespace, multi-token answers, long targets, left truncation, BOS/EOS, masks and branch state reset have explicit tests.

Cache identity includes checkpoint weights, tokenizer, dataset revision and split IDs, task source/scorer version, template, metric normalization, context/truncation policy and precision. Never reuse scores solely by run name or prompt text. Record all candidate scores and margins for exposed development examples. Do not leak protected per-item outputs into the ordinary workspace.

For the baseline text diagnostic, define `BPB = text_token_NLL_sum / (ln(2) * scored_canonical_UTF8_bytes)`. BOS is context only; padding and structural EOS/BOS targets are excluded from this named text-BPB metric. An EOS-inclusive variant needs its own metric name and stated document-framing convention. Score the first text token using the declared BOS context, count rolling-window targets once, and verify byte-span coverage. This is a canonical encoding/code-length diagnostic; it is not automatically a marginal string likelihood for adaptive or stochastic tokenization.

## C12 — Comparisons and run plans

A run captures resolved config, immutable source snapshot including tracked changes and allowlisted untracked plugin files, lockfile, data/tokenizer/evaluator manifests, environment, seed policy and comparison track. Secrets and unrelated user files must not enter snapshots. The hash of the code actually executed must match the plan; modifying a working tree cannot alter a queued run retroactively.

Architecture comparison: fixed data/tokenizer/order/packing/objective and defined resource/parameter bounds. Loss comparison: same architecture/data with extra supervision and compute disclosed. Optimizer comparison: equal tuning allowance and state accounting. Tokenizer comparison: matched canonical bytes and matched compute, total parameters including vocabulary, context policy explicit. Violations create an ineligible comparison with reasons, not a false winner.

Promotion uses development/confirmation evidence, declared materiality and uncertainty, plus seed/resource checks. It produces a new model-size config from initialization, not an unexplained checkpoint resize. Suggested materiality: +1 internal suite point, or ≥10% less measured compute to a predeclared target. Thresholds are policy choices, not automatic proof of novelty or state of the art.

## C13 — Dependencies, command conventions and resource authorization

Use uv CPU/CUDA extras with a tested mutually exclusive index policy; resolve the actual compatible CUDA wheel rather than hard-coding a remembered version. Commands for compute include the selected extra, for example `uv run --locked --extra cpu xlm demo` or `uv run --locked --extra cuda xlm train ...`. Ensure later `uv run` calls cannot silently remove the intended accelerator packages. Dashboard may use an additional optional extra. Pin the evaluator to an immutable supported revision and record it.

Implement network-free unit tests and authored fixture demos. Default implementation smoke cap: 200,000 valid targets, ten training minutes, one GPU process, 2 GiB new artifacts; the toy model must be much smaller than 50M. Explicit real-data pilot default proposal: at most 256 MiB total transferred, 25,000 retained records, and 2 GiB local output; metadata/sample budgets may be lower. These are guardrails, not throughput promises. A genuine large data/train plan needs an operator-supplied authorization bound to the plan hash and limits. CLI flags are an operational safeguard, not an OS security boundary.
