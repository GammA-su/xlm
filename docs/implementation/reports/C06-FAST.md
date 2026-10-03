# C06 FAST tokenizer fit

**Date:** 2026-10-03
**Branch:** `feat/c06-fit-fast` (from `bb886bd`; `9ee7409`, `e0608ac` and `bb886bd` are kept)
**Status:** IMPLEMENTED and VERIFIED on authored fixtures. The 20-minute SLO is
SUPPORTED BY PROJECTION from authored measurements only. The real production fit was
NOT RUN.

`fit-tokenizer` now runs the fast path (`c06-fast-v1`). On every input that the
bb886bd reference accepts, it produces the same scientific result:
- the same policy, budgets, eligibility, rank, 1 MiB cap and crossing rule;
- the same selected IDs and bytes, and the same sample file bytes;
- the same BPE feed order (plan file order, then row order) and `training_input_hash`;
- the same `tokenizer.json`, vocabulary, merges, fingerprint and `c05-binding.json`.

The reference remains available as `fit-tokenizer-reference`.

Nothing on the real system was touched: no network, no `G:` production data, no `X:`,
no real fit, no training and no push.

## 1. Architecture

| Stage | Work |
|---|---|
| PROOF VERIFY | `guard_proof` refuses while the protected root is mounted and refuses forbidden paths. `open_streamed` then checks the proof, plan, manifest and completion **signature and bindings** without hashing membership and without SQLite. |
| MEMBERSHIP STREAM | `membership.jsonl` is read **once** in 16 MiB blocks. The parent hashes each block and hands exactly those bytes, cut at line boundaries, to workers. Workers strict-parse rows and check the schema and types, decision `kept`, split, content hex, size, row, the frozen plan file, and the allocation/source identity. They also check strictly ascending doc-id byte order (so no repeated IDs) and compute the rank for eligible rows. Nothing is used until the final SHA-256, byte size and row count equal the signed completion. Every completion aggregate is then reproduced: per-allocation kept count, train bytes and non-kept count, per-component totals and the document total. No `(file, row)` may repeat. |
| SAMPLE SELECT | The exact shortest rank prefix per allocation, vectorized with a `lexsort` on the SHA-256 rank, tie-broken by membership position, which is doc-id byte order. Then bb886bd's own `deficit_report`, `select_sample` and `export_sample` run, so the reports and sample bytes come from the same code. A deficit refuses here, before any source byte is read. |
| SOURCE PASS | Each plan file is read **once** in 8 MiB blocks, by parallel independent-file workers whose results are consumed in plan order. Every byte is hashed, and LFs are located with the same row and ceiling semantics as `iter_plan_documents`. SHA-256, size and row count must equal the plan. **Kept rows** get the strict canonical JSON parser only: their doc id, ORIGINAL split and byte counts are checked, with no dataclass, `asdict`, re-serialization or digest. **Selected rows** additionally get a full `CanonicalDocument` and the exact C05 content digest. **Non-kept rows** are hashed, never parsed. The parent screens every selected record (exact selected membership, allocation, content, size, `train`, fed once), then spools it in feed order and fills the kept index. |
| INDEX WRITE | The signed kept-membership index (section 4) is written into staging. |
| TOKENIZER FIT | `fitfast_child` (`ByteLevelBPETokenizer.train_from_spool`) runs in a child process. The child environment forces `TOKENIZERS_PARALLELISM=true` (overriding an inherited `false`) and an explicit `RAYON_NUM_THREADS`. The deadline and the process-tree RSS ceiling can terminate it. `train_from_spool` and `train_from_documents` share `_train_protected`, which is the unchanged trainer configuration. |
| SAVE / VERIFY | The parent writes `c05-binding.json` and runs the reference `_verify_tokenizer` (identity, training-input hash, production certification, reversibility probes). It writes the signed manifest (`fit_record`, shared with the reference) plus the fast-path provenance, then publishes with one atomic rename. |

**Corpus passes:** 1 over membership plus 1 over the sources.
**Application bytes read:** about 6.36 GB of membership plus 104.51 GB of sources,
about 110.9 GB in total, plus about 0.55 GB of spool written and read. bb886bd reads
about 222 GB: membership twice and the sources twice.
**Full JSON decodes:**
- 12.63M small strict membership-row decodes;
- 12.63M strict decodes of kept source rows, with no dataclass and no digest;
- full `CanonicalDocument` construction plus digest only for the selected rows (about
  10⁵; hard ceiling 4,000,000);
- the 2.47M non-kept rows are never parsed.

bb886bd does about 30.2M full decodes plus 12.63M full digests and 12.63M SQLite inserts.
**SQLite queries:** 0. bb886bd does about 27.7M lookups.

## 2. Original-split equivalence (the Astra adversary)

Membership supplies C05's *assigned* split. During the one source pass, every kept
row is strict-parsed with the same `canonical.loads_bytes_strict` that the reference
uses. A row assigned to train refuses with the reference's exact message, `record split
differs from C05 membership`, unless its original `CanonicalDocument.split` is
`train`. A split value outside the allowed set also refuses.

The authored adversary fixture is a C05 run in which some canonical records carry
`split: diagnostic_val` while C05 assigns them to train. Both implementations refuse
with that message, at worker counts 1 and 4, and nothing is published.

Deliberate difference: for kept rows that are not selected, the fast path does not
recompute the full C05 content digest. A mismatch between an unselected row and its
signed membership digest, given matching file SHA-256s, would be a C05-internal
inconsistency. bb886bd would refuse in that case; the fast path relies on C05's signed
computation together with the file hashes, as the operator's prompt allows.

## 3. Defaults and environment

- **`--workers 8`** (one per physical core). Steady-state CPU throughput with files
  served from the OS cache:

  | Workers | GB/s |
  |---|---|
  | 1 | 0.104 |
  | 2 | 0.223 |
  | 4 | 0.434 |
  | 8 | 0.679 |
  | 16 | 0.864 |

  `G:` is a Samsung 870 EVO **SATA** SSD at about 0.50–0.55 GB/s, so 8 workers
  already exceed the read floor. 16 adds CPU headroom but cannot beat the disk. The
  setting is operational only.
- **`--bpe-threads 16`.** At 512 MiB: 47.4 s with 16 threads, 49.2 s with 8.
  `tokenizer.json` is byte-identical across settings (and 1/8/16 in tests).
- **Scratch.** Use the NVMe `C:` for `--scratch` (the spool).
- **Ceilings.** Process-tree RSS ceiling 32 GiB (target 24 GiB); optional
  `--deadline-seconds`. Both are fail-closed and nothing publishes after them.

## 4. Post-C05 kept-membership index (`<output>/kept-index/`)

The index covers public **kept** membership only. It contains no benchmark matches,
matcher facts, excluded or duplicate ledgers, group arrays or C05 scratch. It is
signed (`c05_kept_membership_index_v1`) and binds:
- the format;
- the plan digest, completion digest, input-manifest digest, membership SHA-256/bytes
  and kept count;
- the ordered plan-file identities (path, SHA-256, size, rows);
- every section's size and SHA-256;
- the producing implementation.

Sections:
- `rows.bin`: 64 B per row, in doc-id order. Fields: file, row, raw offset, raw line
  length, canonical bytes, content digest, assigned split, original split, allocation.
- `ids.bin` and `ids.off`: doc ids, binary-searchable.
- `by_location.u4`: rows ordered by `(file, row)`.
- `tables.json`: string tables.

The `KeptIndex` API provides `find(doc_id)`, `doc_id(i)`, `file_positions(ordinal)`
and `iter_locations()` over memory-mapped arrays. Expected size at 12.63M rows is about
0.81 GB of rows plus the id bytes (0.81 GB for 64-character ids) plus 0.15 GB of
offsets and location order, roughly 1.8 GB. Consumers must still hash each source file
when they read it.

## 5. Count-only API (prepared for `count-tokens`)

`BaseTokenizer.count_valid_targets` is the exact reference rule.
`ByteLevelBPETokenizer.count_valid_targets` uses the native `encode_batch_fast`, which
skips offsets. It applies the same normalization and the same BOS/EOS framing.

Tests show it equals `max(0, len(encode_with_offsets(t, True)[0]) - 1)`:
- on 18 adversarial texts plus 2,000 random Unicode texts, for both the fitted fixture
  tokenizer and a 1,200-vocabulary BPE: empty, whitespace, ASCII, accents, combining
  marks, emoji/ZWJ, CJK/Arabic, code, multiline, literal special-token strings, 1-char
  and 20k-char texts;
- for a backend whose content can begin or end with the BOS/EOS ids;
- for `ByteTokenizer`.

`count-tokens` itself is unchanged (not optimized yet).

## 6. Tests and checks

Environment: Windows 11, Ryzen 7 5700X3D, Python 3.12.13, tokenizers 0.23.2,
`uv run --offline --locked --extra cpu --extra eval`, thread variables set to 1,
`TOKENIZERS_PARALLELISM=false` in the test runner (the BPE child overrides it).

| Selection | Exit | Result |
|---|---|---|
| `tests/test_c06_fast.py` (new) | 0 | 47 passed |
| Regression `-m "not serial" -n 8`: C06 fast + reference, C05 selection/detached/control/progress/engine, tokenizer, fit-stream, P11 regime, pool freeze, CLI pool freeze, configurable workflow, P35 science workflow | 0 | 332 passed |
| Same files, `-m serial -n 0` | 1 | 1 passed, 4 skipped, 2 failed: `test_configurable_workflow::test_public_two_source_prepare_direct_queue_and_resume[False, producer_cpu]` with `FileNotFoundError` (Errno 2) in an eval subprocess under the long default pytest temp path |
| Those nodes with `--basetemp=C:/t6` | 0 | 3 passed, 1 skipped. This is the known long-temp-path environment issue, not a regression. |
| `ruff format --check`, `ruff check` (17 files) | 0 / 0 | clean |
| `mypy --strict` (12 source modules) | 0 | no issues |
| `git diff --check` | 0 | clean |

New C06 fast tests, exact equivalence against bb886bd:
- sample bytes, all three tokenizer files, vocabulary, merges and every scientific
  manifest field;
- worker counts 1/2/4/8 (identical kept-index sections too) and BPE threads 1/8/16;
- policy variants: other seed, 620-byte cap, small target, a 3-byte target;
- identical deficit reports, including a cap-induced deficit;
- vectorized selection against the reference heaps and brute force, with forced
  SHA ties, zero sizes and shuffled order.

Refusal and mutation tests:
- the Astra adversary;
- membership byte changed, and membership changed during the stream;
- source truncated, extended, line inserted, line deleted, same-size row split, byte
  changed;
- a selected row changed, and an unselected row changed;
- a source changed during its own scan;
- a deadline before any work, and a deadline killing a sleeping BPE child (refused well
  inside 60 s, nothing published, scratch cleaned);
- a failed BPE child and the RSS ceiling;
- inherited `TOKENIZERS_PARALLELISM=false` is overridden;
- a stale index against the membership SHA, plan, completion, files digest or count,
  a tampered section, and a changed source;
- `verify-tokenizer-fit --sources`, and a tampered sample refusing.

Index, progress and CLI tests:
- the index locates and re-reads every kept record;
- progress is content-free and staged, with SLO lines;
- operator CLI plan → fit → verify (fast with sources, reference) → `verify-kept-index
  --sources` → `count-tokens` → `select` (EXACT).

## 7. Performance evidence (authored; files in OS cache)

Commands:
- `python -m scripts.c06_fast_benchmark`
- `python -m scripts.c06_fast_scaling`
- `python -m scripts.c06_bpe_benchmark`

All use generated data in NVMe scratch, which was deleted afterwards. These are
measurements of authored data, not of the production corpus. The raw results are in
[evidence/C06-FAST](../evidence/C06-FAST/): `benchmark.json`, `scaling.json` and
`bpe.json`. In `benchmark.json`, the `bpe` RSS (sampled from the venv launcher) and the
64 MiB BPE time are superseded by `bpe.json`.

| Measurement | Result |
|---|---|
| Membership stream: 1.5M rows / 638 MB, 1/2/4/8 workers | 46.7k / 87.7k / 138k / 165k rows/s (19.9 / 37.3 / 58.7 / 70.1 MB/s, spawn included); peak tree RSS 1.48 GiB at 8 |
| Exact selection, 1.5M rows | 0.68 s |
| Raw block scan + SHA-256, steady state | 0.74 GB/s (1 worker), 2.90 GB/s (8 workers) |
| Source pass with strict kept-row parse, steady state over 5.56 GB | 1 / 2 / 4 / 8 / 16 workers: 0.104 / 0.223 / 0.434 / 0.679 / 0.864 GB/s; tree RSS 0.87 GiB at 8 |
| Same, short fixture (spawn included) | 8 workers 0.42 GB/s; 16 workers 0.34 GB/s |
| Earlier probe, per core | strict parse 134 MB/s vs bb886bd full decode + digest 46.5 MB/s (2.9×) |
| BPE feed overhead (spool frame read) | 415 MB/s |
| BPE, 128 MiB heavy-tailed text (2.64M word types), 16 / 8 threads | 25.7 / 25.9 s; 2.7 GiB peak; identical `tokenizer.json` |
| BPE, 512 MiB (3.0M word types), 16 / 8 threads | **47.4 / 49.2 s; 3.65 GiB peak; identical `tokenizer.json`** |
| Index sections, NVMe | write 770 MB/s, verify-read 817 MB/s |

## 8. Production projection (arithmetic, not a measurement)

Inputs: membership 6.36 GB / 12,632,103 rows; sources 104,506,534,003 B / 15,097,174
rows; `G:` SATA SSD at 0.50–0.55 GB/s (assumed from the drive class, not measured on
this volume).

| Stage | Optimistic | Likely | Pessimistic | Basis |
|---|---|---|---|---|
| Proof / plan / identity | 1 s | 2 s | 5 s | metadata only |
| Membership stream | 77 s | 91 s | 120 s | 165k rows/s and 70 MB/s at 8 workers; above the disk floor (12 s) |
| Reconcile + select | 6 s | 8 s | 15 s | 0.68 s per 1.5M rows |
| Source pass | 190 s | 209 s | 250 s | SATA floor 104.5 GB / 0.55–0.50 GB/s; 8-worker CPU capacity 154 s; spawn-inclusive 249 s |
| Index write | 3 s | 4 s | 8 s | about 1.8 GB |
| BPE (512 MiB) | 47 s | 100 s | 240 s | 47 s measured on synthetic text; real-text merge cost NOT measured (2× and 5× allowances) |
| Save / verify / publish | 3 s | 5 s | 10 s | |
| **Total** | **~5.5 min** | **~7 min** | **~11 min** | the target of 1200 s holds with about 1.9× margin even in the pessimistic column |

The SLO is an operational target. It is reported live through the `SLO |` progress
lines, the projected pre-BPE time and a `SLO WARNING`, and as `measured` values in
`tokenizer_fit_resource_plan.json`. `--deadline-seconds` enforces it fail-closed.

## 9. Limitations

- No real-corpus run: real JSON shapes, real SATA throughput under 8 concurrent
  readers, and the real-text BPE cost are unmeasured.
- The fit manifest differs from bb886bd only in provenance: `fit_path`, `kept_index`,
  `resource_plan_digest` (the fast plan) and `implementation`. Its signed digest
  therefore differs. The tokenizer directory consumed downstream is byte-identical.
- `verify-tokenizer-fit` re-streams membership. `--sources` adds one more full source
  pass, which is about as long as the source stage of the fit.
- `count-tokens`, `select` and `tokenize-selection` are unchanged.

## 10. Next: `count-tokens`

Proposed design, not implemented:
- open the kept index and one source pass, with each file re-hashed;
- parse only the kept-train rows at their recorded offsets;
- count with `count_valid_targets` in batches, using 8 file workers with one Rayon
  thread each;
- emit rows in membership (doc-id) order.

The current `counts.jsonl` is sorted by id and carries C05 content digests, and the
signed `counts.json` has no implementation field. A count-tokens built this way can
therefore be byte-identical to the current artifact. The open decision is whether to
keep the full per-record content digest check.
