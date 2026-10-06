# C07 tokenize-selection / freeze: independent fast path (Opus)

Date: 2026-10-06. Branch `perf/tokenize-freeze-opus`, worktree
`F:\Project\xlm-tokenize-freeze-opus`, started from `622c14891090feee9959388ff0a2b00eb608f322`
(`perf/count-tokens-policy-v2`). This work was done independently: no other worktree or branch
was read.

Status: **IMPLEMENTED; VERIFIED on the authored chain; real-data kernels BENCHMARKED
(bounded, read-only). Production tokenize and freeze were NOT RUN** (no signing key; the
real roots were not created).

## 1. The current implementation (reference, kept as `tokenize-selection-reference`)

Current path, in order:

1. `open_gate`: SQLite import of all 14,927,848 kept-membership rows.
2. `SelectionGate`: SQLite import of 5,824,661 selected rows, with one membership lookup per row.
3. For each component (sorted): `iter_plan_documents`.
   - Every row of every file of the component is read, hashed and strict-parsed, and a
     `CanonicalDocument` is built (15.1M rows, 96.85 GiB).
   - `selected()` makes one SQLite query per row.
4. `TokenShardWriter`, per selected document:
   - `gate.verify`: one SQLite query, one content digest and one `seen` insert;
   - `expect`: one SQLite query and one more content digest;
   - `encode_with_offsets`: HF `encode`, then a Python loop over every token that builds its
     byte span;
   - a count check and truncation to `selected+1`;
   - a per-ID range check and `struct.pack`;
   - a third content digest;
   - `json.dumps` of the record **including `token_byte_spans` for every token**;
   - SHA-256 updates.
5. Per component: fsync, manifest, counters and the signed C05 attestation, published file by
   file into the component directory with the manifest last.
6. `freeze`, which does three things:
   - `open_gate` and `SelectionGate` imports again;
   - per shard, `verify_component_shard`: `verify_token_shard` (full hash, a `json.loads` of
     every line, SQLite lookups) and then a second index pass;
   - `compile_exposure_plan`: `verify_token_shard` **again**.

   `verify_freeze` and `verify_training_freeze` repeat all of it.

Bottlenecks, by kind:

| Stage | Bound by | Measured (single thread, real data) |
|---|---|---|
| HF `encode` / `encode_batch` with offsets | tokenizer library | 2.0 MiB/s (`encode_with_offsets`) |
| per-token span loop | Python | included above (ids-only fast encode: 3.3 MiB/s) |
| `offsets.jsonl` record JSON | JSON serialization | 12.0 MiB/s of text (14.85 B/token written) |
| `struct.pack` + range loop | Python | 75.8 MiB/s |
| strict JSON parse / `CanonicalDocument` | Python / JSON | 108 / 465 MiB/s of text |
| content digest (`asdict` + JSON + SHA) | Python / JSON | 83.5 MiB/s; done 3x per selected doc |
| SQLite gate import | Python + SQLite | 16.5 s per 1M rows: about 4.1 min for 14.93M |
| SQLite lookup + `seen` insert | SQLite | 5.3 µs/doc per pass |
| source hashing / reads | storage, hashing | G: reads at 370-500 MiB/s; SHA-256 at about 3 GB/s |
| fsync, manifest, attestation | storage | negligible (once per file) |

Measured reference writer, on 64 MiB of real selected documents with dict stubs (cheaper than
its SQLite): **1.57 MiB/s, 397 docs/s, 0.373 Mtok/s**.

**Reference production projection: about 4.8 h for tokenize-selection.**
- 24.58 GiB of selected text at 1.57 MiB/s is 4.45 h.
- Parsing all 96.85 GiB of physical rows adds about 15 min.
- The SQLite gate and selection imports add about 5.6 min, and per-row lookups about 1.5 min.

Freeze with the reference projects to about 10–11 min on v2 shards. That is about 4.1 min of
gate import, 1.5 min of selection import, two hashes of 16 GB and three index passes with
SQLite lookups. On v1 shards it projects to more than 45 min (three JSON passes over about
89 GB). These are estimates built from the measured unit costs above.

## 2. Output size (measured; blocking deliverable)

Content-free join of real membership and selection (`selected_rows`, 94 s):

- selected documents: 5,824,661; selected canonical text 26,393,670,917 B (24.58 GiB);
- Σ selected = **6,000,000,000**; Σ counted = 6,000,046,704; truncated = **17** (one per
  allocation);
- **emitted token IDs = Σ(selected+1) = 6,005,824,661** (proved over all rows: every selected
  row has `0 < selected ≤ counted` and stores `selected+1` IDs).

| File | v1 (current schema) | v2 (new default) |
|---|---|---|
| `tokens.bin` | 12,011,649,322 B (11.19 GiB), exact | identical bytes, exact |
| `offsets.jsonl` | 14.83 B/token measured: about **89.1 GB (83.0 GiB)** | 698–711 B/doc measured: about **4.1 GB (3.8 GiB)** |
| manifests, counters, attestations | under 20 KB | under 20 KB + 11 × 64 KiB byte tables |
| **total final** | about **101 GB (94 GiB)** | about **16.1 GB (15.0 GiB)** |
| peak temporary | = final (staged in place) | = final (hidden stage, one rename); about 2.3 MB C: scratch |

Per component (exact from the real plan; the v2 index bytes are a preflight upper bound):

| component | docs | valid targets | token IDs | tokens.bin B | index bound B |
|---|---|---|---|---|---|
| common_pile_prose | 162,253 | 300,000,000 | 300,162,253 | 600,324,506 | 269,010,070 |
| essential_practical | 458,396 | 600,000,000 | 600,458,396 | 1,200,916,792 | 790,090,976 |
| essential_prose | 268,659 | 300,000,000 | 300,268,659 | 600,537,318 | 463,060,692 |
| essential_science | 298,500 | 600,000,000 | 600,298,500 | 1,200,597,000 | 514,487,544 |
| finepdfs_en | 308,416 | 900,000,000 | 900,308,416 | 1,800,616,832 | 507,221,992 |
| finewiki_en | 245,684 | 300,000,000 | 300,245,684 | 600,491,368 | 405,219,722 |
| ifm_behaviors_general_planning | 89,455 | 300,000,000 | 300,089,455 | 600,178,910 | 169,191,370 |
| nemotron_wiki_rewrite | 472,537 | 480,000,000 | 480,472,537 | 960,945,074 | 814,655,698 |
| simple_stories | 422,096 | 120,000,000 | 120,422,096 | 240,844,192 | 740,945,186 |
| synth_en_explanations | 1,670,879 | 900,000,000 | 901,670,879 | 1,803,341,758 | 2,651,953,892 |
| ultrax_ultrafineweb | 1,427,786 | 1,200,000,000 | 1,201,427,786 | 2,402,855,572 | 2,631,931,490 |
| **total** | **5,824,661** | **6,000,000,000** | **6,005,824,661** | **12,011,649,322** | 9,957,768,632 |

## 3. Are per-token spans needed? (offsets necessity)

Spans are an exact function of the token IDs. `ByteLevelBPETokenizer._offsets_from_encoding`
accumulates `len(token_to_bytes(token))` from 0, with BOS = `(0,0)` and EOS = `(n,n)`.
Verified on 11,973 real documents and on the 18 special real documents:

- derived spans equal stored spans;
- EOS equals the canonical byte length.

Consumers:

| Field | Consumer | Class |
|---|---|---|
| `token_start`, `token_count` | stream windows, ordering, training validation, verifiers | SCIENTIFICALLY REQUIRED |
| `doc_id`, `c05_content`, `c05_receipt`, `c05_selection`, `c05_counted/selected_valid_targets`, `valid_targets`, `split` | C05 shard/freeze/training verification, quota report | CONTRACT/AUDIT REQUIRED |
| `byte_start`, `byte_end`, `byte_count` | ordering membership checks; packing | CONTRACT REQUIRED |
| `lineage_id`, `split_group`, `bos/eos_positions`, `covered_bytes`, `c05_canonical_source_id`, `source_id` | trace / provenance / counters | CONTRACT/AUDIT REQUIRED (small) |
| `token_byte_spans` | training stream: per-target trace digest, `canonical_bytes` exposure (bits-per-byte), `byte_coverage_complete`; training-index validation (optional) | **SCIENTIFICALLY USED, but DERIVABLE**: not needed on disk |

Spans are not used by model training math, batching, the mixture sampler, exposure planning,
freeze, checkpointing or evaluation. They are used by the training stream's provenance and
byte accounting, so dropping them silently would change exposure semantics (`canonical_bytes`
would become 0). Therefore:

**Schema `c07-offsets-v2`** (versioned in `shard_counters.json`; implicit v1 when absent):

- records are byte-identical to v1 lines with only `token_byte_spans` removed;
- `token_bytes.u16` holds the uint16 canonical byte length of every token ID; its SHA-256 is in
  the counters, and so is covered by the signed C05 attestation and the freeze record;
- `TokenShardReader.with_byte_spans` re-derives the spans exactly. It is wired into
  `MixtureBatcher._document_at` and `OrderedSourceIndex.document_at`, so v1 records are
  untouched;
- `verify_integrity` verifies the table. Old v1 shards read unchanged.

**Blocking finding for v1:** the largest real document (511,580 targets) produces a v1 index
line of **9,103,147 bytes**. That exceeds the 8 MiB per-line ceiling of `_validate_training_index`,
`MixtureBatcher._document_at` and `OrderedSourceIndex`. Mix-01 training would refuse a v1 shard
set. The same line under v2 is 706 bytes.

## 4. Optimizations implemented (`src/xlm/data/exclusion/tokenfast.py`)

- **No SQLite.** One authenticated parallel membership stream plus `reconcile` (reused
  `fitfast`). Then one hashed strict pass over `selected.jsonl`, merge-joined with membership,
  which reproduces every `SelectionGate.__init__` check.
- **One physical pass.** Every plan file of the produced components is hashed once by a scan
  task, which also locates the selected rows by their C05 membership `(file, row)`. Each file
  belongs to exactly one component and all 2,035 files hold selected rows, so the total pass is
  96.85 GiB. Non-selected rows are never parsed.
  - Selected lines are re-read in about 4 MiB tasks, and refused unless size and mtime are
    unchanged.
  - Scans run ahead of the writer, with at most 8 GiB scanned but unwritten, so re-reads come
    from the page cache.
- **Parallel exact tokenization.** Worker processes with single-threaded HF, a 100k BPE word
  cache, and ids-only `encode_batch_fast` (offsets are not needed).
  - Exact `BOS/EOS` framing.
  - A full-document recount (`len-1 == counted`).
  - Truncation to `selected+1`.
  - The byte-length identity `Σ len(id) == len(canonical text)` is checked for every document.
- **Packing:** `array('H')` (range-checked) viewed as `<u2`, byte-identical to `struct.pack`.
- **Index:** each record is assembled from worker-built fragments plus three positional
  integers, byte-identical to `json.dumps` (unit-tested with escapes, non-ASCII and non-string
  cluster ids). The content digest is computed from the validated parsed value, without the
  `asdict` deep copy.
- **Deterministic ordered writer.** Components sorted, plan-file order, row order. Bounded
  in-flight plus buffered results (3 × workers).
- **Freeze** (`freezefast.py`): streamed membership and selection, each shard verified once
  (hashes in parallel threads, `json.loads` of index blocks in workers), then the unchanged
  `exposure_plan` / `compile_exposure_plan` code over the verified proofs. A path that was not
  verified refuses.

Rejected or not done:

- A native tokenizer (section 13): HF is still about 85–88% of kernel CPU, but there is no
  Rust or C toolchain, and no tiktoken or numba is locked offline. A port would also need
  HF-exact merge semantics, so it is BLOCKED rather than attempted.
- In-process native threads: 20.2 MiB/s versus 21.4 MiB/s for 16 processes.

## 5. Exactness evidence

Authored chain (`scripts/c05_synthetic_flow.py`: 383 docs, 11 components, 17 allocations,
17 crossings):

- Fast **v1** output equals `tokenize-selection-reference` **byte for byte** at 1 (inline),
  2 and 4 workers. That covers `tokens.bin`, `offsets.jsonl`, counters, manifest and the signed
  attestation, for all 11 components.
- Fast **v2**: `tokens.bin` is identical; every record equals the reference record minus
  spans; re-derived spans are identical; counters equal the reference counters plus 2 keys.
- **Training stream:** 12 steps of `MixtureBatcher` over v1 vs v2 shards give identical batches
  (including `target_byte_spans`), state (trace digest, exposure counters) and share report,
  with `byte_coverage_complete` true.
- Every document is re-tokenized from source: `len-1 == counted`, the stored IDs are exactly
  the first `selected+1` IDs, and all 17 crossings drop EOS.
- **Freeze:** fast `freeze.json` is byte-identical to `freeze-reference` on both v1 and v2
  shard sets. Fast and reference `verify-freeze` both pass, and `verify-training-freeze`
  passes. The cleaned-proof chain test now runs both tokenizers and both freezes, and asserts
  that `freeze.json` is byte-identical.

Real data (read-only; no publication):

- 17 real crossing documents plus the largest document: fast v1 equals the reference writer
  byte for byte, and v2 is scientifically equal.
- 11,973 real docs: framed `encode_batch_fast` ids equal `encode_with_offsets` ids; derived
  spans equal the stored ones; `array`/NumPy packing is byte-identical to `struct.pack`.

## 6. Benchmarks (real data)

These used a benchmark-only harness. It makes an **unverified** read of the real
plan/completion/selection (the trust root needs the key), runs the production stream, scan,
tokenize and write code on bounded file subsets, and uses a dummy seal. Its output was deleted.

Tokenizer topology (ids only, 128 MiB of real normalized text; BPE cache 100k):

| batch | 1 | 8 | 16 | 32 | 64 | 128 | 256 | 512 |
|---|---|---|---|---|---|---|---|---|
| `TOKENIZERS_PARALLELISM=false`, MiB/s | 3.71 | 3.81 | 3.80 | 3.82 | 3.79 | 3.78 | 3.76 | 3.72 |
| `=true` (1 process, 16 rayon threads), MiB/s | 3.31 | 7.24 | 9.92 | 12.91 | 15.18 | 17.87 | 19.80 | 20.20 |

| processes (1 thread each) | 1 | 2 | 4 | 8 | 12 | 16 |
|---|---|---|---|---|---|---|
| MiB/s | 3.52 | 6.69 | 12.62 | 18.55 | 20.09 | **21.39** |

The default BPE cache (10k) is about 11% slower; 400k and 1M caches give no gain.

Full fast path (`source_pass`, v2):

- large subset: 946.9 MiB text, 387,347 docs, 2.62 GiB input, 13 files from 7 components;
- 1-worker run: SYNTH file, 307.6 MiB.

| run | text MiB/s | input MiB/s | docs/s | Mtok/s | tokens.bin MiB/s | index MiB/s | CPU | peak RSS |
|---|---|---|---|---|---|---|---|---|
| reference writer (1 proc) | 1.57 | n/a | 397 | 0.373 | 0.71 | 5.3 (v1) | 1 core | n/a |
| fast, 1 worker | 3.37 | 4.94 | 1,306 | 0.704 | 1.34 | 0.87 | 15% | 4.35 GiB |
| fast, 4 | 10.65 | 29.5 | 4,355 | 2.39 | 4.56 | 2.95 | 35% | 5.08 GiB |
| fast, 8 | 16.40 | 45.5 | 6,709 | 3.68 | 7.02 | 4.55 | 59% | 4.90 GiB |
| fast, 12 | 17.90 | 49.6 | 7,321 | 4.02 | 7.66 | 4.96 | 78% | 5.02 GiB |
| **fast, 16** | **18.65** (repeat: 18.61) | 51.7 | 7,630 | 4.19 | 7.98 | 5.17 | 86% | 4.65 GiB |

Pre-phase (16 workers): membership stream 42.6–43.6 s; reconcile 9.5 s; selection 11.2–11.4 s;
work plan 0.8 s. Fast kernel overhead over pure encode is 12–14%, including a cold BPE cache.

Freeze kernels (8 workers, cached files): SHA-256 at 3.0 GB/s; index parsing at 161k
records/s (109 MiB/s).

## 7. Chosen architecture and projection

16 single-threaded tokenizer processes, plus a parent that does the ordered merge and write.
Scan tasks hash and locate ahead of the writer. Results are written strictly in reference order.

| Production projection | value |
|---|---|
| tokenize-selection | about 1.1 min pre-phase + 24.58 GiB / 18.6 MiB/s ≈ 22.6 min ⇒ **about 24–26 min** |
| freeze | about 64 s streams + 20–35 s hashing 16 GB + about 36 s parsing 5.82M records ⇒ **about 2.5–3 min** |
| verify-freeze | about 2.5–3 min |
| **total (tokenize + freeze)** | **about 27–29 min (primary < 30 min: met; preferred 15 + 5: not met)** |
| final output | about 15.0 GiB on G: (v2); peak = final |
| scratch | about 2.3 MB on C: (tokenizer copy); C: reserve 4 GiB |
| peak RAM | about 5 GiB (plan ceiling 48 GiB, supervised) |

Limitation: the HF byte-level BPE on this 8C/16T CPU tops out at about 21.4 MiB/s even with zero
overhead, which is about 19.6 min for 24.58 GiB. A ≤ 15 min tokenize is not reachable without a
different, exact tokenizer backend.

## 8. Resume, failure, preflight, progress

- **Publication.** Each component is staged as hidden `.<component>.stage-<uuid>`: payloads
  fsynced, then table, counters, attestation, and the manifest last. It is published by one
  directory rename, so `<root>/<component>` exists only when complete.
- **Collisions.** A root lock (`.tokenize-selection.tokenize.lock`) is held for the whole run.
  Without `--resume`, the root must be fresh.
- **`--resume`** removes this tool's stale stage directories, provided they contain only known
  file names; anything else refuses. Each published component is **fully verified** before it
  is skipped: exact file set, schema, hashes and byte table, trusted attestation and binding,
  manifest component/tokenizer/selection, counters, and every record against the expected
  document, content, counts, contiguity and order. A failing component refuses and is never
  deleted. Missing components are regenerated from scratch, and nothing is ever appended.
- **Failure injection** (tests) at read, tokenization, packing, index serialization, final
  write, manifest, attestation and Ctrl-C. In every case: only complete components remain, no
  stage directory survives, the private tokenizer copy is removed, and a following `--resume`
  reproduces the uninterrupted bytes exactly.
- **Preflight.**
  - Before any membership or source read, a coarse refusal from the signed totals
    (tokens.bin + 32 GiB reserve on G:).
  - After the join, the exact tokens.bin plus a conservative index bound, with reserves for
    output and scratch.
  - The Supervisor watches RAM and both volume reserves live.
  - `--plan-only` prints the full plan and reads no source.
- **Progress.** Content-free `[TOKENIZE]` lines: documents of 5,824,661, text GiB, files,
  input GiB, token IDs, valid targets of 6B, output GiB, docs/s, MiB/s, Mtok/s, busy workers,
  tasks/buffered, RSS/peak, CPU, free space, elapsed and ETA. `[FREEZE]` stages work the same
  way.

## 9. Downstream finding (not changed here)

`src/xlm/data/input_limits.py` bounds frozen training inputs to 2 GiB per shard and 2 GiB in
aggregate. `resolve_training_input` enforces this **before** `verify_training_freeze`.
`tokens.bin` alone is 11.19 GiB, and several components exceed 2 GiB. So **Mix-01 training
resolution will refuse under either schema** until those reviewed bounds are raised; the module
says raising them is a separately reviewed change. `verify-training-freeze` itself verifies,
and is exposed as an operator command.

## 10. Tests and checks

Commands (from the worktree; `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1`,
`TOKENIZERS_PARALLELISM=false`):

- `uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_tokenize_freeze_fast.py -n 8 --dist=worksteal --max-worker-restart=0 -q`:
  **28 passed** (39 s). These are new.
- Related regression selection: every test file that references the flow, tokenize, the shard
  reader, `offsets.jsonl`, the ordered index or the batcher (43 files), `-n 16 -m "not serial"`:
  **849 passed, 3 skipped, 2 failed**.
  - `test_c05_cleaned_downstream` needed the new `--scratch` flag. It was updated, and now
    passes, exercising fast and reference with a byte-identical freeze.
  - `test_c06_fast_hardening::test_a1_stubborn_descendants_are_killed_and_reaped` is the known
    load-sensitive kill-timing test; it passed serially (`-n 0`).
- `tests/test_tokenize_freeze_fast.py tests/test_c05_selection.py tests/test_c05_cleaned_downstream.py`
  after formatting: **56 passed**.
- Serial selection of the same files (`-n 0 -m serial`): 1 passed, 1 skipped, 4 failed in
  `test_configurable_workflow.py`. This is **pre-existing**: the `authored_tree` bound of
  `< 400` source files is already exceeded at `622c148` (431 tracked `src` .py/.yaml files plus
  3 = 434). It is unrelated to this change.
- `ruff check` / `ruff format` on the changed files: clean. `mypy --strict` on the new and
  changed modules: clean.
- Not run: the full offline acceptance suite (final gate only), CUDA tests, and any production
  run.

## Requirement ledger

| Requirement | Status |
|---|---|
| Worktree/branch from `622c148`, clean start | VERIFIED |
| Audit and trace; bound-type classification | IMPLEMENTED (sections 1, 4) |
| Exact emitted IDs 6,005,824,661 | VERIFIED (real selection) |
| Output size, real-data estimate | VERIFIED (measured B/token and B/doc; tokens exact) |
| Span necessity; versioned v2 schema with exact re-derivation and v1 compatibility | IMPLEMENTED, VERIFIED (authored + real) |
| Tokenization profile, batch and topology sweeps | VERIFIED (real) |
| Parallel deterministic ordered architecture, bounded RAM | IMPLEMENTED, VERIFIED |
| One physical source pass | IMPLEMENTED (re-reads only located selected lines, page-cached) |
| SQLite-free selection lookup with all checks | IMPLEMENTED, VERIFIED |
| Byte-identical packing | VERIFIED |
| Index serialization: v1 byte-identical, v2 compact | VERIFIED |
| Native tokenizer | BLOCKED (no toolchain or locked backend offline) |
| Full recount and the 17 truncations | VERIFIED (authored; real special docs) |
| Freeze fast path, byte-identical; verify-freeze | IMPLEMENTED, VERIFIED (authored) |
| Resume with full verification | IMPLEMENTED, VERIFIED |
| Progress / ETA | IMPLEMENTED, VERIFIED (content-free test) |
| Disk preflight | IMPLEMENTED, VERIFIED |
| Failure safety (7 points + Ctrl-C) | VERIFIED |
| Production tokenize/freeze on the real chain | NOT RUN (no key; real roots not created) |
| Mix-01 training input byte limits | OUT OF SCOPE (reported, section 9) |
