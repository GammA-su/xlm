# count-tokens: parallel exact fast path and live progress (2026-10-05)

Branch `perf/count-tokens-parallel-progress`, from `720fbf6`. Offline, authored
fixtures and bounded local benchmarks only. No G:, X:, real proof, real tokenizer,
operator key or real counting.

## Result

| | |
|---|---|
| exact artifact equivalence | **yes**: `counts.jsonl` and signed `counts.json` byte-identical to the reference at workers 1/2/4/8/16 and on the chunked path |
| measured speedup (authored 120k-doc corpus) | **9.0x** end to end at 16 workers (275 s -> 30.5 s); 8.0x at 8 workers |
| real production SOURCE COUNT at 16 workers (operator run, `b5eb4f8`) | **51 min 31 s** (see below) |
| projected real production at 16 workers | **about 53-55 min** in total. The synthetic projection had been about 45 min. |
| <= 30 min target | **not met.** The exact tokenizer backend is CPU-bound at this machine's ceiling (below). The operator accepted the runtime. |

## Production run 1 (`b5eb4f8`): measured, then refused at AGGREGATE

The operator's first real run (16 workers) completed the whole source count. Its
final telemetry:

| field | value |
|---|---|
| kept train docs counted | 12,613,085 / 12,613,085 |
| kept train text | 60.78 GiB |
| physical input hashed | 96.85 GiB in 2,035 / 2,035 files |
| throughput | 4,079 docs/s avg; 20.1 MiB/s of text |
| SOURCE COUNT elapsed | 00:51:31 (run 00:52:19 at the start of AGGREGATE) |
| process tree RSS | peak 5.2 GiB (ceiling 48 GiB) |
| CPU | about 90 % |

It then refused with `{"refused": true, "error_type": "FileNotFoundError"}` right
after `AGGREGATE | started`.

**Root cause.** It was reproduced on the authored smoke corpus, and the traceback was
captured privately. The fast path created its staging directory
`<output>.partial-<uuid>` only after counting, with `OwnedPaths.directory`, which
calls `Path.mkdir()` without `parents=True`. The reference creates its staging
directory with `stage.mkdir(parents=True)`. With an output path whose parent did not
exist yet (for example a new `G:/XLM/counts/`), `os.mkdir` raised `WinError 3`
(`FileNotFoundError`). That happened at the first filesystem operation after
`aggregate()`, a pure in-memory step.

No worker result file, unit or temporary file was involved, because there were
none. The 6,881 task results were integrated into one in-memory array as they
arrived. Worker teardown deletes nothing; the only scratch file is the private
tokenizer copy.

**Salvage: not possible.** Every count lived in the parent process's memory. When
the job refused, `finally` removed its only owned scratch file (the tokenizer copy),
and the process exited. Nothing durable or verifiable remains. The scratch directory
is empty and may be reused. The output never existed, and no `.partial-*` directory
could have been created.

**Fix (commit following `b5eb4f8`).** Every late filesystem operation is now probed
at the start, in seconds (stage `OUTPUT PREFLIGHT`, before the tokenizer, membership
or any source byte):
- the output parent is created as the reference does;
- the owned staging directory is created and kept for the whole job;
- both artifact names pass the same link/junction check as `write_once`;
- a probe file is written, fsynced and hard-linked, because `write_once` publishes
  by hard link;
- the staging directory is renamed away and back, because publication is a
  directory rename.

After the membership stream and before SOURCE COUNT, the job refuses only when the
export is *guaranteed* to fail: the exact minimum `counts.jsonl` size exceeds the
plan's output ceiling or the free space on the output volume. Any other failure now
reports a fixed stage literal and its numeric errno, never a path:
`{"refused": true, "error_type": ..., "stage": ..., "errno": ...}`.

**Resume: not added.** The fast path has no durable result units. Adding a safe
resume needs signed per-unit result files that bind plan/completion, tokenizer,
file/range, rule and row digests, a resume-check/resume protocol, and stale/mixed
refusal. That is a redesign of the result path, so it would delay this fix. The
preflight removes the failure class that occurred: every late filesystem dependency
now fails within seconds of starting.

**Regression evidence.** `tests/test_count_tokens_lifecycle.py` (9 tests, about 40 s):
- the exact production scenario through the CLI (missing output ancestors, workers
  1 and 4). It fails on `b5eb4f8` with this refusal; it is byte-identical to the
  reference now;
- preflight refusal before any tokenizer, membership or source work: output parent
  is a file, hard links unsupported;
- guaranteed export failures refused before SOURCE COUNT, and the lower bound is
  proven true and tight;
- aggregation runs after every worker is reaped and needs no scratch file;
- late failure and KeyboardInterrupt remove the early staging directory;
- a 2,500-doc generated chain (production vocab) with every kept row its own chunk:
  more than 2,000 result units through the full lifecycle at workers 1, 4 and 16,
  byte-identical to the reference.

## Why it was slow, and what changed

Profile of the reference over 2,998 generated docs of about 6 KB (`count_tokens`
under cProfile, plus component micro-timings):

| component | us/doc |
|---|---|
| `encode_with_offsets` (the reference count rule) | 2,009 |
| `count_valid_targets` (native batch, no offsets; equal counts) | 1,288 |
| membership `gate.lookup` (SQLite) | 39 |
| `canonical.digest(doc.to_dict())` | 34 |
| strict JSON parse | 31 |
| everything else (hash, dataclass, SQLite insert, ORDER BY) | < 10 each |

Tokenization is about 95 % of the work, and the reference runs it on one core.

The fast path (`count-tokens`, module `xlm.data.exclusion.countfast`) works as follows:

1. **Membership.** `membership.jsonl` is streamed once and authenticated exactly as
   the C06 fast fit does: SHA-256, size and row count equal the signed completion;
   rows are strictly ascending (unique); every row is bound to its frozen plan file,
   row and allocation; the stream is reconciled against the completion's accounting.
   There is no SQLite import, no per-record lookup and no SQLite at all.
2. **Tokenizer.** The files are read once and must equal the identity that the
   reference records (`tokenizer_identity`). They are copied into private job
   scratch, and workers load only that copy and must reproduce the fingerprint.
   Before publication, both the original directory and the copy must still equal
   the snapshot.
3. **Source.** Every plan file is hashed completely, once, sequentially (size,
   SHA-256 and rows equal the plan; growth refuses at once).
   - A file with at most 24 MiB of kept text is verified and counted in that same pass.
   - A larger file's pass records the live byte location of each kept row. Its kept
     rows are then counted in chunks of at most 16 MiB by other workers. Those
     workers re-read exactly those lines and refuse if the file's size or mtime
     changed. This matters because the real corpus has ten files of 2-5 GB.
4. **Per kept row.** Every kept row, train or not (as in the reference), goes through:
   - a strict JSON parse;
   - `CanonicalDocument` construction;
   - the membership doc id at that location;
   - the exact C05 content digest.

   A C05-train row whose canonical split is not `train` refuses. Train rows are
   counted with `count_valid_targets`; its equality with the reference rule is tested.
5. **Integration.** Results are placed by membership position, never by completion
   order. Rows are exported in membership (doc-id byte) order, which equals SQLite
   `ORDER BY id` (BINARY collation). The row serializer is tested byte-equal to
   `canonical_bytes`. The envelope is built by the same code fields as the reference.
6. **Publication.** The parent alone writes, into a staging directory: fsync,
   re-read hash, signature check, tokenizer re-check, then one rename.
   Interruption or failure terminates and reaps every worker and removes exactly the
   job's own files.

The C06 kept index (`--kept-index`) is **not used**. Its offsets cannot remove any
source read, because every file must still be hashed in full. Locations are instead
derived live in the same hashing pass, so no stale index can be trusted. Its content
and split columns are a C06 copy of membership that the signed membership stream
already supplies.

## Benchmarks (bounded, authored)

The corpus is `scripts/count_tokens_benchmark.py build`, seed 20261005:
- 119,995 docs, 774 MB physical, 704 MB canonical text, 32 files, largest 280 MB;
- 3 % exact duplicates and planted benchmark contamination;
- diagnostic and audit partitions; 115,358 kept train docs;
- a BPE fitted on screened train records, with vocabulary 32,768 (production size).

Machine: Ryzen 7 5700X3D (8C/16T). Corpus and scratch were on NVMe C:.

| configuration | wall s | kept docs/s | MB/s (physical) | speedup | CPU (cores) | peak tree RSS | scratch |
|---|---|---|---|---|---|---|---|
| reference (`count-tokens-reference`) | 275.2 | 419 | 2.8 | 1.00 | 0.99 | 121 MiB | 22 MiB (SQLite) |
| fast, 1 worker | 166.5 | 693 | 4.7 | 1.65 | 1.04 | 562 MiB | 2 MiB |
| fast, 2 workers | 88.7 | 1,300 | 8.7 | 3.10 | 2.0 | 611 MiB | 2 MiB |
| fast, 4 workers | 50.0 | 2,305 | 15.5 | 5.50 | 3.8 | 830 MiB | 2 MiB |
| fast, 8 workers | 34.3 | 3,366 | 22.6 | 8.03 | 7.0 | 1.5 GiB | 2 MiB |
| fast, 16 workers | 30.5 | 3,782 | 25.4 | 9.02 | 11.3 | 2.6 GiB | 2 MiB |

All six runs produced `counts.jsonl` sha256 `09a516cc…` and `counts.json` sha256
`affa9348…`. Raw data: [matrix-120k.json](../evidence/COUNT-TOKENS-FAST/matrix-120k.json).

That matrix ran on the working tree before two small parent-side hardening edits:
- task identity is kept in the parent and every result echo must match it;
- an `assert` became a refusal.

The final code was re-run once at 16 workers (27 s wall). It produced identical
digests, at 4,733 kept docs/s and 26.5 MiB/s of kept text inside SOURCE COUNT:
[progress-example-w16.txt](../evidence/COUNT-TOKENS-FAST/progress-example-w16.txt).
A repeat matrix was cancelled on request. Run-to-run variance is therefore NOT
measured.

### Ceiling of the exact tokenizer

The benchmark tokenizer is the production configuration: `tokenizers` 0.23.2, BPE
with a ByteLevel regex pre-tokenizer, no normalizer and no added tokens. It was
measured on 24 MB of benchmark text:

| measurement | MB/s |
|---|---|
| `count_valid_targets`, 1 thread | 4.2 |
| `encode_batch_fast` only, 1 thread | 4.2 (Python overhead is negligible) |
| same, BPE word cache 100k / 1M entries (rebuilt model, `to_str()` identical, counts identical) | 4.6 / 4.5 |
| native Rayon in one process, 8 / 16 threads | 22.5 / 18.0 |
| fast path, 8 / 16 worker processes (steady-state kept text) | 22.3 / 25.4 |
| real English prose (repo docs), tokenizer fitted on it, 1 thread | 3.2 |

A Python-side split was also tried. Character classes were derived by probing this
tokenizer's own pre-tokenizer over all 1.1M scalar values, with per-word cached BPE.
It was exact (0 mismatches over 4,400 docs) but slower: 2.1 MB/s. Faster exact
backends such as `tiktoken`, Cython or numba are not installed, and adding a
dependency is out of scope.

The fast path therefore already runs at the machine's ceiling for this backend:
about 25 MB/s of kept text with 16 workers. SMT adds only about 14 % over 8 workers.

## Production projection (synthetic, before the real run)

Superseded by the real measurement above: kept text was 60.78 GiB, not about 68 GB,
and real text ran at 20.1 MiB/s, not 26.5 MiB/s. Real text tokenized about 24 %
slower than the generated text, as the repo-prose sample had suggested.

The real corpus has 81.37 GB of canonical text over 15,087,207 docs. That gives
about 68 GB of kept text for 12,624,198 kept docs, assuming kept docs have average
size; the real completion holds the exact `train_bytes`.

| stage | estimate |
|---|---|
| membership stream (12.6 M rows at about 80 k rows/s measured; C06 parsed the same file in production) | about 3 min |
| source count: 68 GB / 26.5 MiB/s | about 41 min |
| export (321 k rows/s measured) plus verify | about 1 min |
| **total, 16 workers** | **about 45 min** (8 workers: about 52 min) |

Source reads total about 0.5 TB/h at most, far below the measured 0.5 GB/s of the
SATA disk. Disk is not the limit.

The estimate is credible because it uses the steady-state kept-text rate of the
final code at production vocabulary size and scales by bytes. It is optimistic if
real text tokenizes like the repo prose: the backend ran 25 % slower per thread on
that sample, which would mean about 55-60 min. The live ETA line prints the measured
value within the first minute.

## Correctness and integrity (tests)

`tests/test_count_tokens_fast.py` has 32 tests. They cover:
- **Equivalence:**
  - spawned workers 1/2/4/8/16 against the reference (artifact bytes, stdout JSON,
    no residue);
  - the chunked large-file path, inline and spawned;
  - the mixed whole-file/chunked path with small batches;
  - exact per-row counts against `encode_with_offsets` over every document;
  - only kept train rows present: excluded, duplicate, diagnostic and audit rows
    absent;
  - allocation totals;
  - native-count API equality;
  - the row serializer against `canonical_bytes` for adversarial ids.
- **Refusals:**
  - changed, appended, truncated and duplicated-id source;
  - changed content, wrong id and non-train split (row check);
  - a file changed after its verified pass, and line-boundary drift (chunks);
  - a tokenizer bound to another C05, and a tokenizer changed after start;
  - a worker fingerprint mismatch;
  - a wrong proof plan or completion digest, and changed membership;
  - protected volume mounted;
  - output exists (write-once);
  - scratch/output overlapping the data root, completion or tokenizer, or each other;
  - an untrusted signer, and an invalid worker count.
- **Failure handling:**
  - a spawned-worker failure: CLI exit 1, workers reaped, owned files removed,
    unrelated scratch kept;
  - an abrupt worker exit;
  - KeyboardInterrupt during source and during export;
  - reference staging removed on failure.
- **Progress:**
  - text format: stage order, fields, stderr only, stdout exactly one JSON object;
  - jsonl format; `--no-progress` gives empty stderr;
  - ETA only after the minimum observation window, driven by the work rate;
  - content-free: no ids, digests, paths or corpus words.
- **Compatibility:** the command without the new options (default 8 workers)
  produces identical bytes.

## Progress

Stages, in order: PROOF VERIFY, TOKENIZER VERIFY, MEMBERSHIP VERIFY, SOURCE COUNT,
AGGREGATE, EXPORT COUNTS, FSYNC, VERIFY, PUBLISH, COMPLETE. Label `[COUNT]`; stderr
only; `--progress-format text|jsonl`, `--progress-interval`, `--no-progress`.

SOURCE COUNT shows:
- kept train docs / total and %;
- kept text GiB / total;
- files / total;
- hashed input GiB / total;
- rolling and average docs/s;
- rolling and average text MiB/s;
- busy workers, in-flight tasks / capacity, results, queued chunks;
- process-tree RSS, peak and ceiling;
- scratch, free disk, machine CPU %;
- elapsed and ETA.

The ETA uses the rolling rate of kept text bytes over a 30 s window. It needs at
least 10 s of samples and resets at every stage. Otherwise it shows `--:--:--`.
Text bytes are steadier than documents, whose size varies by component.

`RunProgress` gained an optional per-stage `work_total` for this. C05/C06 output is
unchanged unless a stage opts in.

## Commands and results

All commands ran on Windows 11, CPython 3.12.13, `uv` offline and locked
(`--extra cpu --extra eval`), with `OMP/MKL/OPENBLAS/NUMEXPR_NUM_THREADS=1` and
`TOKENIZERS_PARALLELISM=false` for tests.

| command | result |
|---|---|
| `pytest tests/test_count_tokens_fast.py -n 0` | 32 passed |
| `pytest tests/test_count_tokens_fast.py tests/test_c05_selection.py tests/test_c06_tokenizer_fit.py tests/test_c06_fast.py tests/test_c06_fast_hardening.py tests/test_c05_cleaned_downstream.py tests/test_c05_detached_volume.py tests/test_c05_control.py tests/test_c05_progress.py -n 16 --dist=worksteal --max-worker-restart=0` | 290 passed, exit 0 (`b5eb4f8`) |
| aggregate fix: the same selection plus `tests/test_count_tokens_lifecycle.py` | 298 passed, 1 failed, exit 1. The failure was the pinned stage list, which lacked the new `OUTPUT PREFLIGHT`. After updating it, both count-tokens modules passed: 41 passed, exit 0. |
| `tests/test_count_tokens_lifecycle.py::test_missing_output_parent_is_created_like_the_reference` against the `b5eb4f8` `countfast.py` | 2 failed, with the production refusal `FileNotFoundError` (expected: proves the regression test) |
| `ruff format --check`, `ruff check` (`src/xlm/data/exclusion`, new tests, script) | clean |
| `mypy --strict src/xlm/data/exclusion tests/test_count_tokens_fast.py tests/count_workers.py scripts/count_tokens_benchmark.py` | `src` and the new files are clean. 6 errors are in the imported `tests/test_c06_tokenizer_fit.py`; the same file already has 93 strict errors at base `720fbf6`. |
| `git diff --check` | clean |

Not run:
- the full suite and the serial selection;
- any real production count.

## Limitations

- **The 30-minute target is not met on this machine with the exact backend.**
  Reaching it would need one of:
  - about 1.6x more CPU (another host);
  - a reviewed faster exact backend (for example a count-only native BPE), proven
    equal and adding a dependency;
  - an authorized change of contract.
- No resume: an interrupted run starts over.
- The benchmark text is generated. Real-text throughput may differ by about ±25 %.
- Per-file scan concurrency is `workers // 4`. The first scan of the four 5 GB files
  is sequential I/O at disk speed (about 10 s each). That cost is not measured on G:.
