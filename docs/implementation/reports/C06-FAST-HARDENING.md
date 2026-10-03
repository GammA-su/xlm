# C06 FAST hardening (independent-audit findings A1–A8)

**Date:** 2026-10-03
**Branch:** `feat/c06-fit-fast`. One hardening commit on top of `03242cf`. Historical
commits `9ee7409`, `e0608ac`, `bb886bd` and `03242cf` are unchanged.
**Input:** [independent acceptance](C06-FAST-INDEPENDENT-ACCEPTANCE.md) of `03242cf`:
SAFE AFTER SPECIFIC FIXES (A1–A8).
**Status:** A1–A8 IMPLEMENTED and VERIFIED on authored fixtures. Ready for
independent re-audit. The real fit is NOT RUN, and nothing here proves the 20-minute
target on real data.

The scientific architecture is unchanged:
- one membership pass and one source pass;
- full decoding of selected rows only;
- the kept index;
- 8 source workers and a controlled BPE child at 16 threads;
- atomic publication.

On authored inputs the hardened fast path still produces byte-identical scientific
artifacts to bb886bd. This was checked against the git-exported bb886bd code (Astra's
baseline export) and in the repository tests.

No network, `G:` data, `X:` access, real fit, training or push. Astra's uncommitted
audit files (acceptance report, evidence, and banners in STATUS, `C06-FAST.md` and the
runbook) are not part of this commit.

## 1. Fixes

| ID | Fix |
|---|---|
| **A1** total deadline and cancellation | `--deadline-seconds` (default 1200, finite, in (0, 86400]) is the **total command** budget. Its monotonic clock starts in `control.main` immediately after argument parsing, before proof verification. One `Supervisor` owns the whole command. `OrderedPool` polls every future with a 50 ms timeout against the supervisor. On any abnormal exit (deadline, RAM, error, interrupt, early consumer exit) it terminates the worker processes and their descendants, waits a 2 s grace, kills and reaps; it never drains running tasks. The BPE child wait polls every 50 ms, and a breach kills the child's whole tree. Inline mode (tests only, never the CLI) checks cancellation every source block and every 4,096 membership rows. Publication is the parent's single rename, allowed only after every verification, a passing supervisor check and at least 1 s of remaining deadline. A refusal reports how long after the deadline cleanup finished. `Deadline` rejects NaN, infinity and non-positive values. |
| **A2** RAM independent of display | The supervisor's monitor thread samples every 0.25 s, whatever the progress settings. It sums RSS over the whole owned tree (parent, scan workers, the BPE launcher, the BPE interpreter and any descendants), counting shared pages conservatively. On a breach it records the reason and kills all descendants. If inspection fails, the run fails closed. Reviewed ceiling: **24 GiB** default (`--rss-ceiling-gib`, maximum 32). Measured authored peaks: about 1.6 GiB tree at 16 workers, 3.64 GiB for the 512 MiB BPE. `_TreeReporter` is display only now. |
| **A3** authenticated spool | `HashedSpool` hashes the exact framed bytes as written, with a byte ceiling and a frame ceiling of 3× the 1 MiB cap. It then fsyncs, closes and marks the spool read-only. The job gives the child `spool_sha256`, `spool_file_bytes`, frames, payload bytes and the frame bound. In the child, `train_spool` hashes every header and payload byte consumed and checks each frame length before reading. It refuses **before** the tokenizer is saved unless SHA-256, bytes, frames and payload are equal. It then re-hashes the file and writes a bounded content-free result. The parent re-compares the consumed values and re-hashes the spool itself. The signed manifest binds `bpe_spool` (sha256, file bytes, frames, payload bytes), alongside the unchanged `training_input_hash` and selected-membership SHA. `verify-tokenizer-fit --sources` re-derives the spool digest from the sources. |
| **A4** index snapshot | There is no memory map any more. Each section is read exactly once, bounded to its signed size, into a private in-memory snapshot. The bytes that are hashed are the bytes that are consumed. `KeptIndex.reverify()` re-hashes the on-disk sections, so a later change refuses any reported success. Both verify commands call it last. The fit re-verifies its staged sections immediately before the rename. A directory with unaccounted files also refuses. |
| **A5** structural validation | See the list after this table. |
| **A6** storage, reads and cleanup | The plan lists every owned growth item with its volume role: spool, BPE job and result, kept index (bound: kept × 76 B + membership bytes + tables), sample (256 MiB), tokenizer (64 MiB), manifest plus resource plan, deficit report, and 64 MiB of allocation slack per device. Items are grouped by actual device (`st_dev`). The run refuses unless each volume has growth plus `--free-reserve-gib` free (default 16), and the supervisor re-samples free space every 0.25 s. Hard writers cover the spool, index, sample and tokenizer; manifests are capped at 8 MiB. Child output goes to the null device. Source reads never request more than one byte past the frozen size and refuse at once on extra bytes or rows; membership reads are capped the same way. The plan binds `membership_read_bytes` and `source_read_bytes`, and the parent ledger refuses any excess. `OwnedPaths` registers every path the job creates and deletes only those. An unregistered file leaves its directory in place and is reported as `cleanup residue in N owned paths` in the refusal; it never becomes success. Scratch is cleaned *before* publication, and any residue there refuses. |
| **A7** total projection | `Projection` estimates the total as: elapsed + remaining membership + 20 s selection reserve + remaining source + **240 s BPE reserve** + **60 s finalization reserve**. Stages without measurements use conservative planning rates (membership 40 MB/s, source 0.2 GB/s) until at least 10 s and 2 % have been measured. The supervisor evaluates it on every sample and synchronously at stage boundaries, so the very first planning-rate projection is checked before any membership byte is read. When the projection exceeds the deadline it prints `SLO WARNING \| projected total …` to stderr (even with `--no-progress`, refreshed every 30 s, numbers only). Once measured, a projection above 1.25× the deadline aborts early. Progress lines carry `projected_total_s` and `deadline_s`. |
| **A8** bound envelope | `OperationalEnvelope` covers workers, BPE threads, deadline, RAM ceiling, free reserve, monitor interval, shutdown grace, worker queue bound (2× workers), membership chunk and source block sizes, the BPE, selection and finalization reserves, planning rates, early-abort ratio and publication margin. It is part of the `--plan-only` digest, together with the storage plan and the scratch, output and deficit-report locations. The run rebuilds the plan from its own flags and refuses unless the digest is identical. Scientific identity is unchanged by these settings. |

A5 structural validation checks all of the following, so an index that is correctly
re-signed but internally inconsistent still refuses:
- rows equal the C05 kept count;
- exact schema lengths for `rows.bin`, `ids.off` and `by_location`;
- id offsets start at 0, end at the length of `ids.bin` (no trailing bytes) and are
  strictly increasing;
- ids are UTF-8 and strictly ascending (no duplicates);
- each row's file ordinal is in range, its row number is legal for that plan file,
  `offset + length` is within the file, and its length is within (0, line ceiling];
- canonical bytes are less than the line length;
- split codes are valid, and an assigned `train` row has an original `train` split;
- allocation references are valid;
- `by_location` is an exact permutation, with strictly increasing `(file, row)` and no
  overlapping lines;
- per-allocation kept counts and train bytes equal the signed completion;
- the tables match the plan files and split names.

## 2. Proposed operational envelope (bound by the plan digest)

| Setting | Value |
|---|---|
| Source workers / queue bound | 8 / 16 tasks |
| BPE threads | 16 (`TOKENIZERS_PARALLELISM=true`, `RAYON_NUM_THREADS=16` in the child) |
| Deadline | 1200 s total from dispatch; publication margin 1 s; shutdown grace 2 s |
| Process-tree RAM | 24 GiB (reviewed maximum 32) |
| Free-space reserve | 16 GiB per owned volume |
| Monitor interval | 0.25 s |
| Membership chunk / source block | 16 MiB / 8 MiB |
| Projection | BPE reserve 240 s; selection 20 s; finalization 60 s; planning 40 MB/s membership, 0.2 GB/s source; early abort at 1.25× |
| Reads | membership exactly its signed bytes; sources exactly the plan's file bytes (one pass) |

## 3. Tests

Environment: Windows 11, Ryzen 7 5700X3D (8C/16T), Python 3.12.13, tokenizers 0.23.2.
Commands used `uv run --offline --locked --extra cpu --extra eval`, with
OMP/MKL/OPENBLAS/NUMEXPR threads set to 1 and `TOKENIZERS_PARALLELISM=false` in the
test shell (the BPE child overrides it). Short base temps `C:/t9*`. All fixtures are
authored.

| Selection | Exit | Result |
|---|---|---|
| `tests/test_c06_fast_hardening.py` (new, A1–A8 plus equivalence) | 0 | 75 passed |
| `tests/test_c06_fast.py` (updated helper) | 0 | 47 passed |
| Regression: C06 fast, hardening and reference; C05 selection, detached, control, progress, engine; tokenizers; fit stream; P11 regime; pool freeze; CLI pool freeze; configurable workflow; P35 science workflow (`-m "not serial and not network and not cuda" -n 8`) | 0 | **407 passed** |
| Same files, serial complement (`-m serial -n 0`) | 0 | 3 passed |
| Astra's independent probes, **unmodified** (`test_independent.py`, `-p conftest -n 0`) | 1 | **43 passed, 2 failed**. Both failures are probes coupled to the old API (see below). |
| The same two probes adapted (`evidence/C06-FAST-HARDENING/test_independent_adapted.py.txt`) | 0 | 2 passed: deadline enforced in 0.157 s; consumed offset authentic (1238, not 123456789) and `reverify()` refuses |
| Astra's actual git-exported **bb886bd** fit against the hardened FAST fit (`assert_equivalent`) | 0 / 0 | identical sample, `tokenizer.json`, tokenizer manifest, c05-binding, vocabulary, merges and every scientific manifest field |

The two unmodified probe failures are not defects:
- `test_requirement_deadline_bounds_pool_wait` never hands its `Deadline` to
  `OrderedPool`. In the hardened API the pool takes the supervisor. With that one
  argument added and the assertion unchanged, it refuses after 0.157 s (it took
  2.093 s before).
- `test_requirement_index_hash_and_mapping_same_snapshot` hooks `_section_sha`, which
  index loading no longer calls; loading now uses a single hashed read, `_snapshot`.
  With the same mutation injected right after that read, the consumer still sees the
  authentic bytes and `reverify()` refuses.

The originals are kept unmodified.

Equivalence: workers 1, 2, 4 and 8, both inline and spawned, and BPE threads 1, 8 and
16 all produce byte-identical results to bb886bd. Two fast-path fields differ by
design: the manifest adds `bpe_spool`, and its signed digest differs only through
provenance (`fit_path`, `kept_index`, `bpe_spool`, `resource_plan_digest`,
`implementation`). The tokenizer directory consumed downstream is identical.

Per finding:
- **A1:** CLI proof-stage deadline; NaN, infinity, 0 and negative deadlines; a tiny
  deadline; the deadline expiring during the pre-publication re-verification
  (refused, nothing published); blocked workers killed within the bound; an interrupt
  that kills rather than drains; a stubborn grandchild killed and reaped; a BPE child
  tree killed on deadline.
- **A2:** a 512 MiB child and grandchild with progress disabled, at 0.001 s, and at
  3600 s intervals; the CLI ceiling with `--no-progress`; tree aggregation.
- **A3:** before-BPE mutations (same-length payload, frame length, truncation,
  extension, reorder, duplicate, missing frame); mutation while the child reads; the
  bound digest independently re-derived from plan-order frames and by `--sources`
  verification; trainer refusal of an unauthenticated or oversized frame.
- **A4:** mutation before the snapshot; after it (before the first lookup, during
  lookups, after the last lookup); before fit publication; truncate, extend, section
  swap, table edit, and a foreign same-size file.
- **A5:** 14 correctly re-signed inconsistencies: count, section length, trailing
  bytes, duplicate ids, bad offsets, permutation, offset or row outside the file, file
  ordinal, split enum, train with audit original, allocation enum, accounting, zero
  length.
- **A6:** insufficient space before work, a reserve violation during the run, index,
  spool, tokenizer and sample ceilings, a source read past its frozen size even with a
  lying `stat`, and visible cleanup residue (an unregistered file is never deleted).
- **A7:** reserves present in the total, a warning followed by early abort on a
  measured hopeless rate, and an early content-free warning without abort.
- **A8:** a changed worker count, BPE threads, deadline, RSS ceiling or free reserve
  each reject the reviewed digest; the full envelope and storage are in the plan; a
  matching envelope gives equivalent CLI success.

Static checks:
- `ruff format --check` and `ruff check` on 13 changed Python files, exit 0.
- `mypy --strict` on the 7 changed or new source modules, exit 0.
- `git diff --check`, exit 0.

## 4. Performance (authored, bounded; files served from the OS cache)

Raw results are in [evidence/C06-FAST-HARDENING](../evidence/C06-FAST-HARDENING/).

| Measurement | Result |
|---|---|
| Supervised source pass with strict kept-row parsing, steady state (5.56 GB), 1/2/4/8/16 workers | 0.104 / 0.173 / 0.337 / **0.596** / 0.871 GB/s; tree RSS 0.88 GiB at 8 workers |
| A/B supervised vs unsupervised, 8 workers, alternating | 0.620, 0.605 vs 0.606, 0.604 GB/s: **no measurable supervision overhead**. The earlier 0.679 GB/s was run-to-run variance. |
| Raw scan plus SHA-256, supervised | 0.78 GB/s (1 worker), 2.07 GB/s (8) |
| Membership stream, 1.5M rows, 8 workers | 157.6k rows/s, 67.0 MB/s; tree 1.47 GiB |
| Exact selection, 1.5M rows | 0.74 s |
| **Authenticated** BPE, 512 MiB heavy-tailed synthetic (3.0M word types), 16 / 8 threads | **50.2 / 55.7 s**; 3.64 GiB; byte-identical `tokenizer.json` (+≈3 s over unauthenticated, for consumed-byte and post hashing) |

## 5. Production projection (arithmetic, not a measurement)

Inputs: membership 6.36 GB / 12,632,103 rows; sources 104,506,534,003 B; `G:` SATA
assumed at 0.50–0.55 GB/s (not measured).

| Stage | Optimistic | Likely | Conservative | Basis |
|---|---|---|---|---|
| Proof, plan, admission | 3 s | 4 s | 5 s | metadata only |
| Membership | 77 s | 95 s | 158 s | 165k / 67 MB/s / 80k rows/s (Astra's conservative) |
| Reconcile and select | 10 s | 12 s | 15 s | 0.74 s per 1.5M rows plus a unique sort |
| Source pass | 190 s | 230 s | 523 s | SATA floor; 8-worker CPU capacity 154–174 s; Astra's conservative 0.2 GB/s |
| Index write and re-verification | 10 s | 12 s | 15 s | about 1.8 GB |
| BPE (authenticated) | 50 s | 125 s | 250 s | 50 s synthetic; 2.5× and 5× real-text allowances (not measured) |
| Verify, manifests, cleanup | 10 s | 15 s | 20 s | |
| **Total** | **≈ 5.8 min** | **≈ 8.2 min** | **≈ 16.4 min** | all within 1200 s |

In an adverse case such as Astra's 34-minute scenario (about 0.1 GB/s source), early
abort fires once the source rate is measured, a few minutes in. The run refuses
without spending another 20 minutes, and nothing is published. The 20-minute target
is **plausible but not proven**.

## 6. Commands (after approval; not executed here)

The commands are in the
[runbook](../../runbooks/c05-global-preparation.md#tokenizer-fit-c06). The plan-only
command and the run must use identical operational flags.

## 7. Limitations

- Real-corpus throughput, real-text BPE cost, cold-cache and concurrent SATA reads,
  physical Ctrl+C and power loss are NOT RUN.
- Process-tree inspection covers descendants of this process. An unrelated process
  started by the operator is not counted.
- Read-only marking of the spool is defence in depth. Authentication rests on the
  consumed-byte hash.
- `count-tokens` and other downstream commands are unchanged.
