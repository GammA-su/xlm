# count-tokens on the C05 production-v3 / C06 policy-v2 chain (2026-10-06)

Branch `perf/count-tokens-policy-v2` in `F:\Project\xlm-count-tokens-policy-v2`, started
exactly at `488273e965aab66fc5718626901866f8fc1063b1` (the C06 transition commit that
fitted the real policy-v2 tokenizer). Donor/reference: `aa3890457d82fc37a30dab3aade44ae56b7ff4cc`
(`perf/count-tokens-parallel-progress`).

Authored fixtures plus bounded, read-only, content-free benchmarks. Nothing was written
under `G:/`. No production counting, no signing with a real key, no secret was read, and
nothing was pushed.

## Result

| question | answer |
|---|---|
| fast counter already present at `488273e`? | **yes, completely.** `aa38904` is an ancestor of `488273e` (`git merge-base` = `aa38904`), so nothing needed porting |
| v3 compatibility at `488273e` | already contract-aware: `count_tables` passes `contract=view.plan.output_contract`, and the parser accepts `c05_membership_v3` |
| what was missing | binding to the **C06 fit and kept index**, operator **pins** (including the expected document count), a read-only **verifier** for a published count, a token-rate progress field, and tests on a v3 chain |
| semantics | unchanged. Same rows, same rule, same signed fields; `c06_fit` is one added payload key, present only with `--c06-fit` |
| exactness | fast at workers 1/4/8/16 is byte-identical to the reference on the v3 chain, bound and unbound; it equals an independent oracle row by row |
| projected production | **about 64 min** in total at 16 workers (range 51-67 min); SOURCE COUNT is about 61 min |
| <= 30 min | **not achievable** with this exact backend on this CPU (measured below) |

## 1. What was already present (`488273e`)

Everything from `aa38904`. That is the whole fast path:

- parallel source processing over 16 independent readers, with large files split into
  located chunks;
- no SQLite on the hot path;
- full SHA-256 of every source file in the same pass;
- results placed by membership position, so worker count cannot change output;
- progress with ETA;
- OUTPUT PREFLIGHT, which includes the fix for the AGGREGATE publication-parent bug;
- bounded memory and clean worker shutdown.

`488273e` also had the v3 membership parsing (`MEMBERSHIP_SCHEMAS`, `contract=`).
Diff `aa38904..488273e` over `countfast.py`: only the `contract=` line and the generic
`preflight_output` parameters.

## 2. What this branch adds

All of it is new code. Nothing is cherry-picked.

- **`countbind.py`.** With `--c06-fit <fit dir>`, the job proves that the counts belong
  to exactly that C06 fit:
  - The signed fit manifest verifies against the C05 trust root and names this
    C05:
    - plan, completion and input manifest;
    - kept membership and source seals;
    - `fit_path` `c06-fast-v1`.
  - The counted tokenizer's identity equals the signed fit's `tokenizer` block:
    fingerprint, files digest, vocabulary and C05 binding.
  - The fit's kept index verifies (`open_index`: signature, C05 binding, private
    snapshot, structure) and is the one the fit signed.
  - Fast path: after the membership stream and before SOURCE COUNT, every kept-index
    column must equal authenticated membership. The columns are file, row, bytes,
    content, assigned split, allocation (mapped by key), id offsets and id bytes. The
    1.7 GiB index is then released.
  - Both paths: counted train documents per allocation must equal the kept index's
    train rows.
  - Before publication, the fit manifest and every kept-index section are re-hashed.
  - The payload records `c06_fit: {fit_digest, kept_index_digest}`.
- **Pins.** These flags apply to `count-tokens`, `count-tokens-reference` and
  `verify-counts`:
  - `--expect-c05-plan-digest`;
  - `--expect-c05-completion-digest`;
  - `--expect-tokenizer-fingerprint`;
  - `--expect-c06-fit-digest`;
  - `--expect-kept-index-digest`;
  - `--expect-documents`.

  When the pins refuse:
  - C05, tokenizer, fit and kept-index pins: before any membership or source read.
  - The document pin: right after the membership stream, before SOURCE COUNT, and
    again after counting.
  - Fit or kept-index pins without `--c06-fit`: at once.
- **`verify-counts`** (`countverify.py`). Read-only and content-free, with no SQLite.
  It checks:
  - the proof;
  - the tokenizer identity;
  - the signed envelope (kind, C05 binding, rule, tokenizer);
  - the exact directory contents;
  - the `c06_fit` record;
  - every `counts.jsonl` row: canonical form, four fields, plan allocation, count >= 0,
    strictly ascending ids, and, with the fit, equality with the kept index's next
    train row (id, content, allocation);
  - SHA-256 and size;
  - recomputed per-allocation totals against the signed totals, and the pins.

  It prints `counts_digest`, documents, allocations and every binding.
- **Worker BPE word cache** (`with_bpe_cache`, `COUNT_BPE_CACHE` = 100,000; stock is
  10,000). The model is rebuilt from the tokenizer's own serialization, and the job
  refuses unless `to_str()` is byte-identical afterwards. Dropout refuses. It is a
  speed knob only; the gain is in section 4.
- **Progress** adds `mtokens_per_s`, the exact valid targets counted per second.
- **Stage literals** add `C06 BINDING VERIFY` and `C06 KEPT-INDEX COMPARE`.
- **`scripts/count_tokens_backend_benchmark.py`.** A bounded, content-free probe of
  the exact backend on a real-text sample (`single`, `pool`) and of disk reader
  concurrency (`disk`).

The selector is unchanged. `verify_counts_envelope` and the reference `verify_counts`
check named binding fields, the rule and the tokenizer identity, so the added key is
compatible. `tests/test_select_fast.py` passes unchanged.

## 3. v3 membership and exactness (authored, `tests/test_count_tokens_policy_v2.py`)

The tests use the old/new chains of `test_c06_policy_v2`, built through the real
operator flow. The new chain is `c05-production-v3`, `c05_membership_v3`, with
diagnostic_val and audit partitions and excluded and duplicate rows.

| requirement | evidence |
|---|---|
| `c05_membership_v3` accepted | reference + fast at workers 1/4/8/16 publish; `count_tables(...).contract == "c05_membership_v3"` |
| worker count never changes output | `counts.jsonl` + signed `counts.json` byte-identical: reference, w1, w4, w8, w16 (bound) and reference vs w4 (unbound) |
| only C05 `split == train` counted | row-by-row equality with an oracle that reads only kept rows' identity fields and recounts every source text with `encode_with_offsets` |
| diagnostic_val, audit, excluded and duplicate absent | asserted against membership and decisions |
| `split_group` / `exclusion_group` ignored | arbitrary rewritten group values parse to identical counting columns; a v2-shaped row (`lineage_group`) refuses |
| totals | per-allocation documents and valid targets equal the oracle; documents == kept index `assigned_splits.train` |
| tokenizer fingerprint, kept-index identity | `c06_fit` == {fit digest, kept-index digest}; fingerprint equals the fit's `c05-binding.json` |
| source hashes | unchanged full-file hashing; source refusals stay covered by `test_count_tokens_fast.py` |
| stale combinations refuse | old fit (new proof and tokenizer); new fit plus old tokenizer; old proof plus new fit; new fit with the old kept index swapped in; existing output (write-once, bytes unchanged) |
| wrong pins refuse, nothing published | 6 pins x {fast, reference}; no output, no `.partial-*` |
| comparator | any changed split, content, id or row column refuses; a released index refuses |
| verify-counts | accepts (with and without fit); refuses a changed count, an extra file, an unbound artifact claimed as bound, and a wrong document pin |
| BPE cache | identical serialization, fingerprint and counts (stock vs cached, two passes, edge texts), and equality with the reference rule |

## 4. Benchmarks (bounded)

Machine: Ryzen 7 5700X3D (8C/16T), 72 GB RAM; G: SATA SSD, C: NVMe. Raw data is in
[evidence/COUNT-TOKENS-POLICY-V2](../evidence/COUNT-TOKENS-POLICY-V2/). Every run was
a single run; run-to-run variance is **not measured**.

### End to end, authored corpus (reference vs fast)

The corpus is `scripts/count_tokens_benchmark.py build --documents 30000`:
- 29,989 docs, 185 MiB physical, 32 files;
- synthetic text and a production-size BPE (32,768);
- corpus and scratch on C:.

| configuration | wall s | docs/s | physical MiB/s | CPU cores | peak tree RSS | scratch | speedup |
|---|---|---|---|---|---|---|---|
| reference (`count-tokens-reference`) | 72.9 | 411 | 2.54 | 0.99 | 116 MiB | 5.5 MiB (SQLite) | 1.00 |
| fast, 1 worker | 45.0 | 667 | 4.12 | 1.03 | 527 MiB | 2.2 MiB | 1.62 |
| fast, 4 workers | 18.6 | 1,616 | 9.98 | 3.36 | 856 MiB | 2.2 MiB | 3.93 |
| fast, 8 workers | 12.6 | 2,387 | 14.74 | 5.55 | 1.5 GiB | 2.2 MiB | 5.81 |
| fast, 16 workers | 13.4 | 2,231 | 13.78 | 6.95 | 2.3 GiB | 2.2 MiB | 5.43 |

All five runs produced `counts.jsonl` `506f0ee1…` and `counts.json` `64f26168…`.

This corpus is too small to rank 8 against 16 workers. SOURCE COUNT lasts about 10 s,
so 16 worker start-ups and the tail dominate. 12 workers is not a CLI choice;
steady-state 12 is measured below.

### Exact backend on real text (read-only sample, steady state)

The sample is 160 MiB of complete lines from the first plan files of every component,
in proportion to component size: 127 MiB of text and 30,803,527 tokens.
`count_valid_targets` ran in N spawned processes on equal-byte shards. MiB/s is text
over the makespan; identical token totals in every run.

| workers | stock MiB/s | 100k cache MiB/s | gain | cores busy | CPU-time rate per core (cache) |
|---|---|---|---|---|---|
| 1 (24 MiB sample) | 2.92 | 3.22 | +10 % | 1 | 3.22 |
| 4 | 8.36 | 9.20 | +10 % | 3.8 | 2.39 |
| 8 | 14.55 | 15.42 | +6 % | 7.4 | 2.09 |
| 12 | 19.20 | 19.40 | +1 % | 10.7 | 1.83 |
| **16 (chosen)** | 20.17 | **20.88** | +3.5 % | 12.6 | 1.65 |

- **The bottleneck is tokenization**, inside the native backend. On real text, NFC
  normalization runs at 286 MiB/s; the per-row reference checks (strict parse,
  document, content digest) cost about 6 % of the per-row time. A fully warm BPE word
  cache gains only about 15 %, so the ByteLevel regex pre-tokenizer and the encoding
  construction dominate. None of these is reachable through the `tokenizers` API.
- **SMT.** Per-core throughput falls from 2.4 to 1.65 MiB/s at 16 workers. 16 is still
  the fastest setting. With perfect balance, 16 x 1.65 is about 26 MiB/s; the
  production scheduler balances dynamically over about 2,000 files and chunks.
- **Storage is not the limit.** Full-speed sequential SHA-256 reads of G: ran at
  500-537 MiB/s with 1, 4 or 16 concurrent readers (8 MiB blocks). The OS cache was
  not controlled. The counter needs about 28 MiB/s on average, so 16 independent file
  readers do not cause harmful random I/O. A sequential reader pool feeding a
  tokenizer pool cannot help, and was not built.
- **Rejected.**
  - GPU: no bit-identical tokenizer.
  - Python-side pre-tokenization with a word-count cache: about 2 MB/s in the donor
    study, slower.
  - Other backends (tiktoken etc.): not installed; exactness unproven.
  - Approximation.

### Real C06 binding cost (read-only; no signature checked: the key was not used)

| step | measured |
|---|---|
| kept-index snapshot (read + SHA-256 of 1.7 GB) | 1.4 s |
| re-verify sections before publication | 1.5 s |
| RSS of the snapshot (released before SOURCE COUNT) | +1.7 GiB |
| structural validation | not measured end to end. It needs the verified view; a partial run stopped after 5.2 s. Estimate: under 30 s |

These reads give the content-free facts below. They match the C06 verification:
- kept rows 14,927,848 = train 14,917,655 + diagnostic_val 9,297 + audit 896;
- train bytes 80,103,244,612;
- 17 allocations.

## 5. Production projection

Train text is 80,103,244,612 bytes = 74.60 GiB, against 60.78 GiB in the historical
run. That run took 51 min 31 s SOURCE COUNT at 20.14 MiB/s with the stock cache, and
used the same 2,035 files and 96.85 GiB physical input.

| stage | estimate |
|---|---|
| proof, tokenizer, OUTPUT PREFLIGHT | seconds |
| C06 BINDING VERIFY (signed fit + kept-index open/validate) | under 1 min |
| MEMBERSHIP VERIFY (8.29 GB, 14.93 M rows; historical pre-count stages took about 48 s) | about 1-2 min |
| C06 KEPT-INDEX COMPARE | seconds |
| SOURCE COUNT: 76,392 MiB at 20.1 (stock) to 20.9 (cache) MiB/s | **61-63 min**; 48 min at the balanced ceiling |
| AGGREGATE, EXPORT (14.9 M rows at about 360 k rows/s), VERIFY, PUBLISH | about 1 min |
| **total, 16 workers** | **about 64 min** (51-67) |

- **Peak RAM.** The historical peak was 5.2 GiB. Membership is 18 % larger, the kept
  index adds 1.7 GiB during the compare, and the cache is small. Expect about 7-8 GiB
  against the plan ceiling of 48 GiB.
- **Scratch (C:).** The private tokenizer copy, about 2.3 MB.
- **Output (G:).**
  - `counts.jsonl` is about 3.15 GB (historical 211.5 B/row), plus `counts.json`
    (about 5 KB).
  - Both are staged in `G:/XLM/counts/mix01-policy-v2.partial-<uuid>` and published
    by one rename.
  - G: had 523 GB free.
- **<= 30 min would need at least 42.4 MiB/s sustained.** That is 1.6x the balanced
  16-worker ceiling of this exact backend on 8 cores. It is not realistically
  achievable without a different exact tokenizer implementation or more cores.

## 6. Resume

Not supported, and unchanged. Counts live only in parent memory; there are no durable
result units, and adding them safely is a redesign (signed per-unit results bound to
plan, completion, tokenizer, range and rule). After interruption:

- **Ctrl-C.** The job reaps workers and removes its staging directory and tokenizer
  copy. It prints `{"refused": true, "error_type": "KeyboardInterrupt"}` with exit 130.
  The output is never created. Rerun the same command.
- **Crash or kill.** No `finally` runs. With no count process running, check the
  output root is still absent. Delete only the job's own leftovers:
  - `G:/XLM/counts/mix01-policy-v2.partial-*`;
  - `C:/XLM-scratch/count-tokens-policy-v2/count-tokenizer-*`.

  Then rerun. A leftover `.partial-*` does not block a rerun (each job has its own
  uuid); it only costs space.

## 7. Requirement ledger

| requirement | status |
|---|---|
| worktree/branch from `488273e`, donor read-only | VERIFIED |
| fast counter present / ported | VERIFIED present (ancestor); nothing to port |
| v3 membership accepted, groups ignored, train only | VERIFIED (authored) |
| C06 fit and kept-index binding, pins, stale refusal | IMPLEMENTED, VERIFIED (authored) |
| worker-count invariance 1/4/8/16 vs reference | VERIFIED (authored v3 chain and 30k corpus) |
| selector schema compatibility | VERIFIED (`test_select_fast.py` unchanged and passing) |
| progress (stage, docs, %, files, GiB, docs/s, MiB/s, tokens/s, RSS, elapsed, ETA) | VERIFIED ([example](../evidence/COUNT-TOKENS-POLICY-V2/progress-example-w16.txt)) |
| `verify-counts` | IMPLEMENTED, VERIFIED (authored) |
| <= 30 min | BLOCKED (exact backend CPU ceiling; section 5) |
| real kept-index signature/structure on the real chain | NOT RUN (needs the operator key) |
| production count of 14,917,655 docs | NOT RUN (operator) |
| resume | OUT OF SCOPE (not supported; documented) |
| selector port | OUT OF SCOPE |

## 8. Commands run (agent)

All of these ran under `uv run --offline --locked --extra cpu --extra eval`, with
`OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1` and `TOKENIZERS_PARALLELISM=false` for pytest.

| command | exit | notes |
|---|---|---|
| `python -m pytest tests/test_count_tokens_policy_v2.py -n 0` | 0 | 23 passed, 67 s; the comparator test, added next, then passed alone (24 in total) |
| `python -m pytest tests/test_count_tokens_policy_v2.py tests/test_count_tokens_fast.py tests/test_count_tokens_lifecycle.py tests/test_c06_policy_v2.py tests/test_select_fast.py tests/test_tokenizer_fit_stream.py -n 16 --dist=worksteal --max-worker-restart=0 -m "not serial"` | 0 | 135 passed, 3 min 52 s (focused; not the full suite) |
| same selection, final tree (after the `mtokens_per_s` field) | 0 | 135 passed, 3 min 44 s; no `serial` tests in this selection |
| `ruff check src tests scripts`; `ruff format --check` | 0 | |
| `mypy src` | 1 | 5 errors, all pre-existing and identical at `488273e` (none in changed files) |
| `python -m scripts.count_tokens_backend_benchmark single/pool/disk ...` | 0 | each < 1 min, read-only |
| `python -m scripts.count_tokens_benchmark build --documents 30000` / `matrix --workers 1 4 8 16` | 0 | 40 s / 2 min 44 s total (longest single run 73 s) |

The full offline acceptance suite was not run; it is the release gate only.
