# select: exact fast path without SQLite, and live progress (2026-10-05)

Branch `perf/select-parallel-progress`, from `aa38904` (the count-tokens commit that
produced the real exact-count artifact). Offline, authored fixtures and bounded local
benchmarks only. No G:, X:, real proof, real counts, real tokenizer, operator key or
real selection. The real count artifact (`G:/XLM/counts/mix01-clean-v1`) is untouched
and is the unchanged input of this command.

## Result

| | |
|---|---|
| exact artifact equivalence | **yes**: `selected.jsonl`, signed `selection.json`, stdout and deficit reports byte-identical to `select-reference`, at workers 1/2/4/8/16, on the cleaned-proof chain and on the 1M-row benchmark |
| SQLite | **removed** from the whole command: no membership import, no candidate table, no rank index, no chosen table, no `ORDER BY` |
| counts-file passes | **one** sequential pass (hashed while consumed) |
| measured, 1M-row authored chain | reference **176.0 s**; fast **6.95 s at 8 workers (25.3x)**; 7.37 s at 16; 8.69 s at 4; 21.7 s at 1 |
| projected real production, 8 workers | **about 1.5 min** (linear scaling of the whole measured wall; 3-4 min if real membership rows are twice as large) |
| projected real production, reference | **at least 37 min** (linear; SQLite index growth makes it worse) |
| recommended `--workers` | **8** (16 is slower here and uses 60 % more RSS) |

## Current reference (`select-reference`, unchanged oracle)

`open_gate` imports all 12.6M kept membership rows into a scratch SQLite table, then
`select()`: hashes `counts.jsonl` (2.67 GB) once, then per count row strict-parses
JSON, does a `MembershipGate.lookup`, checks allocation, computes the SHA-256 rank and
inserts into a `candidates` table keyed `(allocation, rank, id)`; per allocation walks
`ORDER BY rank,id` until the quota and inserts into `chosen`; exports
`ORDER BY id`; signs. Measured on the 1M-row chain: 176.0 s on one core
(about 176 us per count row, everything included). Linear projection to 12,613,085
rows: about 37 min, a lower bound.

## Fast path (`select`, module `xlm.data.exclusion.selectfast`)

1. **PROOF VERIFY**: `open_streamed` (signed plan, manifest, completion envelope;
   detached-volume guard), signer trust check, root-overlap checks, write-once output.
2. **OUTPUT PREFLIGHT** (seconds, before any heavy work; the count-tokens lesson):
   output parent created as the reference does; owned staging directory created for
   the whole job; link/junction checks on both artifact names; probe write + fsync +
   hard link (`write_once`) + directory rename and back (publication); scratch
   write + fsync probe; the deficit report path must be a plain, absent path under a
   directory (so a deficit can always be recorded). Probes are content-free.
3. **TOKENIZER VERIFY**: the reference `tokenizer_identity` (files digest, C05 fit
   binding, fingerprint, vocab size).
4. **COUNTS VERIFY**: the reference `verify_counts` except the content hash: signature,
   C05 binding, counting rule, tokenizer identity, size. Quota requirements (cleaned
   proofs recover lineage through the admission, as before) and vocab size.
5. **MEMBERSHIP VERIFY**: the authenticated `membership.jsonl` stream shared with
   count-tokens and C06 (SHA-256, size and row count equal the completion; strictly
   ascending doc ids; plan file/row/allocation binding; completion accounting). Every
   kept train allocation must be a frozen quota allocation.
6. **RANK PASS**: one sequential read of `counts.jsonl`, every byte hashed, never past
   the signed size, cut into 4 MiB blocks of whole lines. Each block goes to a worker
   with the membership span that must match it. **Positional proof (ordered merge):**
   count row `k` must equal, byte for byte, the canonical count row of the `k`-th kept
   *train* membership row (doc id, C05 content digest, allocation) followed by a
   canonical non-negative integer. Because membership is strictly ascending, this
   proves no row is missing, extra, repeated, reordered, changed or moved, and no kept
   non-train row is present. Workers return the count and the first 64 bits of the
   reference rank `sha256(C([seed, allocation, doc_id, content]))`, built from the same
   canonical bytes. Results are integrated strictly in submission order. Nothing is
   used until the final SHA-256, size and row count equal the signed envelope.
7. **QUOTA CROSSINGS** and **CROSSING SORT**: exact rank-prefix buckets (below).
8. **EXPORT SELECTION**, **FSYNC**, **SIGN**, **VERIFY** (re-read hash, signature,
   counts file size/mtime unchanged, tokenizer files digest unchanged), **PUBLISH**
   (one directory rename), **COMPLETE**. Rows are serialized in parallel and written
   in membership (doc-id byte) order, which equals SQLite `ORDER BY id` (BINARY =
   UTF-8 byte order). No second read of `counts.jsonl` is needed: membership
   supplies id, content and allocation; the pass supplies counts.

### Exact selection by rank-prefix buckets (`select_by_buckets`)

Reference semantics per allocation: walk rows by (rank, doc id); skip zero-count rows;
take `min(count, quota - total)` until `total == quota`.

Let `E` be the allocation's positive-count rows and `b(r)` the top 16 bits of `r`'s
rank. `b` is monotone in the (rank, doc id) order, so all rows of a lower bucket
precede all rows of a higher bucket. Let `C[j]` be the summed counts of `E` with
`b <= j` (exact int64 sums).

* If `C[last] <= quota`, the walk takes every row of `E` whole (each step has
  `quota - total >= count`): EXACT if equal, otherwise DEFICIT.
* Otherwise let `j*` be the first bucket with `C[j*] >= quota`. Rows below `j*` sum to
  `C[j*-1] < quota`, so the walk takes each of them whole and has not stopped. Rows
  above `j*` come after the walk reaches `C[j*] >= quota` inside `j*`, so none is taken.
* Inside `j*`, the walk is replayed exactly on (full 256-bit rank, row index) with
  `quota - C[j*-1] > 0` remaining; the last taken row is the crossing document,
  truncated to the remainder. Row index is membership order, i.e. doc-id order.

Prefix collisions, and even identical full ranks, are ordered by that same key, so
nothing is approximated or probabilistic. The full rank of each crossing-bucket row is
recomputed in the parent and must reproduce the worker's 64-bit prefix. On the 1M-row
chain, 31 rows in total were fully sorted for all 17 allocations (about
rows / 65,536 per allocation).

### Accepted inputs

On every count artifact the fast path accepts, its outputs equal the reference's. It
is stricter in exactly two ways, which no count-tokens path produces (both write
canonical rows in doc-id order, bound by the signed SHA-256): rows not in doc-id order
and rows that are valid but non-canonical JSON. A trusted signer's artifact of that
kind is refused, never interpreted differently (tested: the reference accepts both and
produces the unchanged selection). `select-reference` remains available.

### Deficit

Unchanged: exit 2, content-free report written write-once by the CLI, no publication,
no borrowing, redistribution, renormalization or repetition. The report is built from
the same per-allocation results and is byte-identical to the reference's (tested
through both CLIs and in-process at 2 spawned workers).

## Progress

`[SELECT]` lines on stderr only (`--progress-format text|jsonl`,
`--progress-interval`, `--no-progress`); stdout is exactly the final JSON result
(`{"digest","mode"}`, `{"deficit": true, ...}` or a refusal). Stages: PROOF VERIFY,
OUTPUT PREFLIGHT, TOKENIZER VERIFY, COUNTS VERIFY, MEMBERSHIP VERIFY, RANK PASS, QUOTA
CROSSINGS, CROSSING SORT, EXPORT SELECTION, FSYNC, SIGN, VERIFY, PUBLISH, COMPLETE.
Fields: rows done/total, input GiB done/total, rows/s rolling and average, MiB/s,
workers busy, tasks in flight/capacity, results, written MiB, selected and truncated
document counts, crossing rows, process-tree RSS and peak against the plan RAM
ceiling, scratch, free space, CPU, stage and run elapsed, ETA. The ETA is stage-local
(the shared `RunProgress`): rolling rate over 30 s, shown only after 10 s of samples,
`--:--:--` otherwise; every stage resets it. No ids, ranks, digests, text or paths are
printed (tested with a 32-hex-digit scan and every membership doc id). Refusals print
`{"refused": true, "error_type", "reason"}` (C05Error, fixed literals) or the
type, fixed stage literal and errno.

Example (1M-row chain, 8 workers; full transcript in
[progress-example-w8.txt](../evidence/SELECT-FAST/progress-example-w8.txt)):

```text
[SELECT] MEMBERSHIP VERIFY | 623,211/1,000,102 rows (62.31%) | 0.53/0.53 GiB input | 208,781 rows/s avg | 180.6 MiB/s | RSS 0.1/16.0 GiB (peak 0.1) | scratch 0.00 GiB | free 661.8 GiB | elapsed 00:00:02 (run 00:00:03) | ETA --:--:--
[SELECT] RANK PASS | 718,244/999,530 rows (71.86%) | 0.22/0.22 GiB input | 695,973 rows/s avg | 215.7 MiB/s | workers 8/8 busy | tasks 15/16 results 40 | RSS 0.9/16.0 GiB (peak 1.2) | scratch 0.00 GiB | free 661.8 GiB | CPU 41% | elapsed 00:00:01 (run 00:00:05) | ETA --:--:--
[SELECT] CROSSING SORT | done | 17/17 allocations (100.00%) | 362 allocations/s avg | crossing_rows 31 | selected_docs 395,743 | truncated_docs 16 | RSS 0.9/16.0 GiB (peak 1.2) | ...
[SELECT] EXPORT SELECTION | done | 395,743/395,743 rows (100.00%) | 1,102,348 rows/s avg | workers 0/8 busy | tasks 0/16 results 13 | written 102.0 MiB | ...
```

Each stage of the bench finished within 10 s, so no ETA appears there; in production
the membership and rank stages last long enough to show one.

## Benchmark (bounded, authored)

Corpus: `python -m scripts.select_benchmark build --root C:/t/selbench-1m --documents
1000000`. It is the generated Mix-01-shaped flow (the same 17 allocations, IFM views,
Common Pile upstreams, planted duplicates and contamination) scaled to 1,000,136
short documents. Authored C05 kept 1,000,102 (999,530 train; membership 565,362,539
B). Counts: 999,530 rows, 233,468,792 B (234 B/row; real: 211 B/row), 106,048,785
valid targets for a 42,000,000 quota (about 40 % of train docs selected). Build:
5 min 18 s (C05 91 s). Machine: Ryzen 7 5700X3D (8C/16T), 72 GB RAM; corpus, counts
and scratch on NVMe C:. Each configuration ran once, alone, in a fresh process
(`matrix --modes reference 8 16`, then `--modes 4 1`). The code was this branch's
working tree before commit (identity recorded in the JSON).

| configuration | wall s | count rows/s | counts MiB/s | CPU (cores) | peak tree RSS | scratch | speedup |
|---|---|---|---|---|---|---|---|
| reference (`select-reference`) | 176.02 | 5,679 | 1.26 | 0.99 | 97 MiB | 372 MiB (SQLite) | 1.00 |
| fast, 1 worker | 21.69 | 46,078 | 10.26 | 1.14 | 396 MiB | 0 | 8.1 |
| fast, 4 workers | 8.69 | 114,968 | 25.61 | 3.58 | 742 MiB | 0 | 20.3 |
| **fast, 8 workers** | **6.95** | **143,761** | **32.02** | 5.34 | 1.17 GiB | 0 | **25.3** |
| fast, 16 workers | 7.37 | 135,661 | 30.22 | 7.59 | 1.89 GiB | 0 | 23.9 |

Stage seconds at 8 workers: MEMBERSHIP VERIFY 4.22, RANK PASS 1.11, QUOTA CROSSINGS
0.05, CROSSING SORT 0.03, EXPORT SELECTION 0.34, FSYNC 0.20 (includes pool
shutdown), VERIFY 0.12, everything else below 0.05. All five runs produced
`selected.jsonl` `74ffccc1…`, `selection.json` `938f8e05…` and selection digest
`7a340a9d…`. Raw data: [matrix-1m.json](../evidence/SELECT-FAST/matrix-1m.json).
Run-to-run variance was not measured (one run each).

## Production projection (8 workers)

Real inputs: 12,613,085 count rows, 2,667,110,172 B; 12,624,198 kept membership rows
(membership bytes not read here; assumed about 565 B/row like the bench, about
7.1 GB). Scale factor 12.6 on rows, 11.4 on counts bytes.

| stage | bench 8w | projection |
|---|---|---|
| membership stream (parse-bound, about 145 MB/s) | 4.22 s | about 53 s |
| rank pass (about 900k rows/s, 200 MiB/s) | 1.11 s | about 13-14 s |
| crossings + crossing sort | 0.08 s | about 1 s |
| export + fsync + verify (about 5M selected rows if 40 % again) | 0.66 s | about 8 s |
| fixed (start, proof, tokenizer, spawn, publish) | about 1 s | a few s (32k-vocab tokenizer load) |
| **total** | **6.95 s** | **about 1.5 min** |

The rates are far below the G: SATA read rate (about 0.5 GB/s), so the disk is not
the limit. If real membership rows are twice as large, the total is 3-4 min. Even one
worker projects to about 4.6 min. Memory: the parent holds the membership columns
(about 200 B/row) plus 16 B/row of counts and rank prefixes; projected peak tree RSS
about 5 GiB (count-tokens, with the same membership stream, peaked at 5.2 GiB). The
plan RAM ceiling enforced by the supervisor applies. Scratch: none beyond a probe.

## Correctness evidence (tests)

`tests/test_select_fast.py`, 54 tests, about 3 min single process:

* **Serializers**: count-row prefix, selected row, doc-id JSON and rank digest equal
  `canonical_bytes`/`canonical.digest` for 10 adversarial ids (quotes, backslash,
  control characters, DEL, CJK, U+2028/2029, astral emoji, tab).
* **Property tests** of `select_by_buckets` against a straightforward full
  (rank, row) sort-and-walk: 1,500 generated datasets at prefix widths 1, 2, 3, 8, 16
  and 24 bits; 1-17 allocations; zero-count rows; counts up to 1e9; deliberate
  shared 64-bit prefixes and identical full ranks; quotas 0, 1, half, exact total,
  total + 1 and random. Selected targets per row and every statistic must match.
* Crafted case: one shared prefix for all rows, identical full ranks (doc-id
  tie-break), zero-count lowest rank, crossing truncation, and the deficit walk.
* A 200,000-row allocation: under 50 rows fully sorted, selection equals a numpy
  lexsort walk.
* **Reference equivalence** on the 17-allocation authored chain (kept non-train rows
  present, truncated crossings present): inline with tiny blocks/export tasks, and
  spawned at workers 1, 2, 4, 8, 16; CLI `select` vs `select-reference` stdout and
  bytes; zero-count documents (re-signed counts) identical; deficit report
  byte-identical via both CLIs (exit 2, no selection, no staging residue).
* **Refusals** (re-signed by the trusted issuer, so only content checks apply; the
  reference refuses each too): missing row, missing last row, duplicate row, extra
  trailing row, excluded record, kept non-train record, changed content, wrong
  allocation, negative count, extra field, invalid JSON; unsigned digit change
  (hash) and size change; unordered and non-canonical rows (reference accepts,
  unchanged selection); wrong completion digest, byte tokenizer, rebound tokenizer
  binding, untrusted signer, invalid worker count.
* **Lifecycle**: missing output ancestors created; output parent is a file and hard
  links unsupported refuse at OUTPUT PREFLIGHT before tokenizer/membership/count
  work; existing deficit report refused early; existing output refused (write-once);
  OSError and KeyboardInterrupt in RANK PASS and EXPORT remove every owned file;
  KeyboardInterrupt with 4 spawned workers reaps them; counts changed during
  selection refused at VERIFY.
* **Progress**: text and JSONL through the CLI, stderr only, exact stage sequence,
  content-free; `--no-progress` leaves stderr empty.

`tests/test_c05_cleaned_downstream.py::test_cleaned_proof_c06_fit_verify_count_select_tokenize_freeze`
now also requires the fast `select` to equal `select-reference` byte for byte on the
cleaned proof (the production shape: lineage through the admission).

## Commands and results

All with `uv run --offline --locked --extra cpu --extra eval`, Python 3.12.13,
numpy 2.5.3, Windows 11, test threads pinned to 1 (`OMP/MKL/OPENBLAS/NUMEXPR`,
`TOKENIZERS_PARALLELISM=false`), `--basetemp` on a short `C:/t/...` path.

| command | result |
|---|---|
| `pytest tests/test_select_fast.py -n 0` | 54 passed (one progress assertion corrected and re-run: 2 passed) |
| `pytest tests/test_c05_selection.py -n 0` | 15 passed (its CLI `select` is now the fast path; its deterministic test compares the reference `select()` against it) |
| `pytest tests/test_count_tokens_fast.py tests/test_count_tokens_lifecycle.py tests/test_c05_cleaned_downstream.py tests/test_c05_progress.py -n 4 --dist=worksteal -m "not serial"` | 68 passed |
| `pytest tests/test_c05_cleaned_downstream.py::test_cleaned_proof_c06_fit_verify_count_select_tokenize_freeze -n 0` (after the reference comparison was added) | 1 passed |
| `ruff check` / `ruff format --check` on changed files | clean |
| `mypy --strict` on `selectfast.py`, `countfast.py`, `control.py` | no issues |
| `git diff --check` | clean |
| `scripts.select_benchmark build --documents 1000000`, `matrix --modes reference 8 16`, `matrix --modes 4 1` | exit 0, all artifacts identical |

Not run: the full offline acceptance selection, the C06 suites (`fitfast`/`fitscan`
unchanged), any real-source or CUDA test, the real selection.

## Requirement ledger

| requirement | status |
|---|---|
| byte-identical `selected.jsonl`, `selection.json`, digest, statistics, deficit report vs reference | IMPLEMENTED, VERIFIED (authored) |
| SQLite removed from the hot path (and the whole command) | IMPLEMENTED, VERIFIED |
| streaming ordered-merge proof against authenticated C05 membership | IMPLEMENTED, VERIFIED |
| exact radix/prefix selection, collisions and ties | IMPLEMENTED, VERIFIED (property tests) |
| doc-id output order without a global sort; one counts pass | IMPLEMENTED, VERIFIED |
| `--workers {1,2,4,8,16}`, ordered parent integration, identical at every count | IMPLEMENTED, VERIFIED |
| `[SELECT]` progress text/jsonl, `--no-progress`, stderr only, content-free, ETA | IMPLEMENTED, VERIFIED |
| output preflight (parents, staging, fsync, link, rename, write-once, scratch, deficit path, exact space bound before export) | IMPLEMENTED, VERIFIED |
| failure and KeyboardInterrupt cleanup, worker reaping | IMPLEMENTED, VERIFIED |
| production runtime below 30 min (target 10-15) | projected about 1.5 min; real run NOT RUN (operator only) |
| real counts artifact untouched, no source corpus reread | VERIFIED by construction (select reads only proof, membership, counts, tokenizer, quotas) |
| resume | OUT OF SCOPE (a rerun takes minutes) |

## Open limitations

* The projection assumes real membership rows of about the bench size; the live
  MEMBERSHIP VERIFY line shows the real rate within the first 10 s.
* Count artifacts in non-doc-id order or non-canonical JSON are refused by `select`
  (use `select-reference` only if such an artifact were ever legitimately signed;
  none of the count paths writes one).
* The deficit report path must not already exist (refused in the preflight, so the
  report can always be written); choose a fresh path per run.
