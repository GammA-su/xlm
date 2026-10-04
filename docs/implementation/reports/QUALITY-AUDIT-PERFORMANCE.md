# Global quality audit: operator visibility and throughput

Date: 2026-10-04. Base `382ab90` (implementation `c517fe0`; `382ab90` adds evidence
only). One focused commit, not pushed.

**The real audit was NOT run.** There was no `G:` read, no `X:`, no real C05, no
cleaning, no tokenizer fit and no training. Every measurement below is on AUTHORED
data in a 4-vCPU Linux container; Windows numbers quoted from earlier reports are
labelled as such. Exact commands:
[evidence/QUALITY-AUDIT-PERFORMANCE/COMMANDS.md](../evidence/QUALITY-AUDIT-PERFORMANCE/COMMANDS.md).

## 1. Was `--workers 8` really using eight worker processes?

Yes. The execution path of `python -m xlm.data.quality audit --workers 8` is:

| step | process / thread | parallel? |
|---|---|---|
| manifest, C05 proof signature, plan, code identity | parent main thread | no (seconds) |
| **C05 overlay: stream + strictly parse every `membership.jsonl` row** | parent main thread | **no; minutes; printed nothing before this change** |
| resume re-hash of committed files (only on resume) | parent, 2 threads | 2 |
| file read + first SHA-256 + line framing + chunk tasks | parent main thread | overlaps the workers |
| task pickling + pipe transfer | parent feeder thread | overlaps |
| chunk parse / verify / detectors | **8 spawned worker processes** (`ProcessPoolExecutor`, spawn) | 8 |
| result merge, per-file commit | parent main thread | overlaps |
| second full SHA-256 of each finished file | parent, 2 threads (GIL released) | overlaps the workers |
| aggregation, artifacts, receipt | parent main thread | no (tens of seconds) |

So there are 1 parent + 8 worker processes (+ a resource tracker on POSIX); at most
`2 x 8 = 16` chunk tasks in flight; results are consumed in task order. The new
`activity` facts prove it per run: `worker_processes_used` (distinct PIDs that
executed chunks) and `max_concurrent_tasks` (overlap of the measured execution spans).
`test_workers_really_execute_chunks_concurrently` asserts both.

### Why the operator saw "one thread", then about 50 %

1. **The first stage.** With `--c05-proof`, before any worker exists, the parent
   verifies the proof and then reads the whole C05 kept-membership file (about 565
   bytes per kept row, so several GB for ~13-15 M kept rows), hashing it and parsing
   every row with strict JSON in ONE thread. Earlier reports measured 184k rows/s on
   the Windows machine, so 70-90 s per 13-15 M rows. Nothing was printed during
   this stage: progress output only existed for the scan. This stage is
   inherently serial: the membership is ordered by `doc_id`, not by file, so every
   row must be read before any file can be split into kept/removed.
2. **The scan.** 8 busy worker processes on a 16-logical-CPU machine are 8/16 = 50 %
   in Task Manager. That is the expected picture for `--workers 8`, not a symptom of
   serialization.

### The earlier "33 MB/s at 8 workers" number

That benchmark (`scripts/quality_audit_benchmark.py`, 256 MiB) had 8 files of 32 MiB
with 32 MiB chunks: about 8 chunk tasks in total. At 8 workers each process did one
chunk, so the figure measured process spawn plus one chunk, not steady-state
throughput. It also explains why 16 workers measured slower than 8 (30.5 vs 32.9
MB/s): there was no work for processes 9-16. The real run has thousands of chunks.

## 2. Bottleneck breakdown (before the change, this container)

Per document (mean 4.6 k characters), `detectors.analyze` cost 1,170 µs:

| section | µs/doc | share |
|---|---:|---:|
| 5-gram + 10-gram + distinct-word sets (Python tuples) | 300 | 26 % |
| page-number / hyphen / soft-break regex scans | 146 | 12 % |
| zlib level-1 compression ratio | 118 | 10 % |
| character classes (bincount + per-character dict loop) | 93 | 8 % |
| runs (`maximal_runs` over every adjacent pair) | 85 | 7 % |
| duplicate lines (collapse + Counter) | 84 | 7 % |
| duplicate paragraphs (split + collapse) | 76 | 7 % |
| rest (words, lines, markup, boilerplate, URLs, classification) | ~270 | 23 % |

Outside `analyze`, `process_chunk` cost 128 µs/row; 44 µs of that was the strict
JSON loader's post-parse walk over every value (`_reject_nonfinite`).

Parent (4 workers, per-thread CPU): main thread (read, first SHA-256, framing, merge)
~4.8 ms/MB in steady state; queue feeder (pickling + pipe) 2.3 ms/MB; second-hash
threads 2.2 + 1.5 ms/MB with the GIL released. Pickling a 32 MiB task ran at
~1 GB/s; the single task pipe sustained ~230 MB/s. At the target 70 MB/s the parent
needs well under one core, so it was **not** the limit; the detector kernel was.

Overlay load: 22.6 µs/row in this container (54 µs/row under the profiler, a third of
it the same non-finite walk, plus per-row decoder construction and numpy scalar
writes).

## 3. Changes

### Accepted (all exactly equivalent)

| change | effect here | why exact |
|---|---|---|
| page numbers: the MULTILINE `^...$` pattern matched per `\n` line | 146 -> ~40 µs for the layout section | both alternatives are `^`/`$` anchored without `\n`: one whole line per match, at most one per line |
| soft breaks: adjacent-line scan | (same section) | a match is `[a-z,;]`, `\n`, `[a-z]`; leftmost non-overlapping scanning only skips a candidate when the previous match consumed a 1-character line |
| n-grams: first-seen word labels, 5-grams packed injectively into one int64 (base n; a dense 3-gram rank first when n**5 >= 2**63), 10-grams as the pair of 5-gram ranks; numpy sort counts | 300 -> ~210 µs; 4.7x on 3k+ word documents | no hashing, no collisions; < 48 words keep tuples |
| whitespace collapse: a stripped printable line without a double space is its own collapsed form; short lines/paragraphs are not collapsed | ~30 µs | every `str.split` whitespace except U+0020 is non-printable (tested over all code points); collapsing never lengthens |
| runs: a windowed-conjunction probe for any run >= 8, full `maximal_runs` only when one exists | 85 -> ~40 µs | identical to an empty result when there is no run |
| character classes: per-process 0x3000 x 18 `char_mask` bit matrix, one integer matrix product | 40 -> 22 µs | same `char_mask` values; the old path stays for code points >= 0x3000 |
| strict JSON (`strictjson.loads_strict_bytes`): reused decoder, `parse_float` rejects non-finite literals while parsing | -40 µs/row; overlay -40 % | non-finite floats can only come from a NaN/Infinity constant or an overflowing literal |
| overlay: `MembershipParser` writes rows into byte buffers laid out exactly as the bool / packed 72-byte `IDENTITY` arrays | 22.6 -> 13.9 µs/row | arrays and binding compared bit for bit with c517fe0 |
| scheduler: "in flight" = submitted and not finished (still <= 2 x workers); up to 4 x workers finished results may wait behind a slow head | removes head-of-line stalls; within noise at 4 workers here | results are still consumed in task order |
| chunk size 32 -> 8 MiB | +4-8 %, higher busy fraction, -0.3 GiB RSS at 4 workers; 4x less chunk data in flight at 16 workers | measurements never depend on chunking (tests) |
| `--workers 12` | allows the benchmark to find the SMT sweet spot | envelope, CLI and checks updated |

### Evaluated and rejected / deferred

| idea | result | decision |
|---|---|---|
| queue depth 3x / 4x workers | +3-9 % at 32 MiB chunks, within noise at 8 MiB with the new scheduler | keep 2x (envelope-compatible); the backlog gives the look-ahead |
| 16 / 64 MiB chunks | 16 MiB between; 64 MiB slowest, most RSS | 8 MiB |
| path + byte-range tasks (workers read their own range, per-chunk SHA-256 cross-checked against the parent's) | would remove ~2.3 ms/MB pickling and the single pipe, but adds a second parent hash pass; the parent is not the measured limit | **deferred; proposal below for review** |
| shared memory for chunk bytes | same benefit, more lifecycle code on Windows | rejected for now |
| more verify threads for the second hash | 2 threads hash far faster than the scan produces files | unchanged |
| processor affinity | no demonstrated reason | not used |

## 4. Equivalence

- **Detectors:** `tests/quality_reference_detectors.py` is a frozen copy of the
  c517fe0 detector module. `test_analyze_equals_the_frozen_reference_implementation`
  compares every value (type-strict, NaN-aware), flag and class on all fixture texts,
  4,000 random adversarial strings (CR/VT/NEL/U+2028/U+3000 whitespace, roman and
  page numbers, overlapping soft breaks, lone surrogates, astral characters),
  n-gram boundary sizes (47-49, 6,208/6,209 words, 100,005 words), 160 authored
  benchmark documents and 100k-character runs. A scratch fuzz of 31,850 texts
  (including two authored benchmark files) found 0 mismatches.
- **Artifacts:** c517fe0 (worktree) vs this change at 1, 2 and 4 workers on three
  authored corpora: the 256 MiB benchmark corpus, the standard fixture corpus and
  the synthetic C05 flow WITH the kept overlay. All 10 artifacts
  (`quality-audit.json`, `quality-by-component.json`, `quality-histograms.json`,
  `quality-intersections.json`, `quality-language.json`, `review-manifest.jsonl`,
  three candidate-policy YAMLs, `quality-summary.md`) are byte-identical after
  replacing the one embedded audit-binding digest. That digest must change: it binds
  the code identity and the chunk size. Detector counts, histograms, review
  selections and candidate impacts are identical. 90/90 comparisons IDENTICAL:
  [artifact-equivalence.txt](../evidence/QUALITY-AUDIT-PERFORMANCE/artifact-equivalence.txt).
- **Overlay:** kept bitmaps, identity arrays and the overlay binding are identical to
  c517fe0's loader on 200,000 authored membership rows.
- **Worker counts:** the result digest and all artifacts are identical for 1, 2, 4,
  8 and 12 workers (test) and in every benchmark run.

## 5. Throughput

Same authored 512 MiB corpus (variable files 8-104 MiB, the original benchmark text
mix), c517fe0 worktree vs this change, this 4-vCPU container
([throughput-old-vs-new-512mib.jsonl](../evidence/QUALITY-AUDIT-PERFORMANCE/throughput-old-vs-new-512mib.jsonl)):

| workers | old MB/s | new MB/s (whole scan) | new steady MB/s | speed-up | new busy | RSS old -> new |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 3.51 | 5.07 | 5.08 | 1.44x | 97 % | 0.34 -> 0.20 GiB |
| 2 | 7.05 | 10.63 | 10.84 | 1.51x | 96 % | 0.77 -> 0.53 GiB |
| 4 | 12.91 | 19.46 | 20.37 | 1.51x | 91 % | 1.18 -> 0.84 GiB |
| 8 (4 vCPUs: oversubscribed) | 11.91 | 17.83 | 18.97 | 1.50x | 89 % | 1.89 -> 1.44 GiB |

The `benchmark` command itself ran 1/2/4/8/12/16 workers in 207 s here (512 MiB):
steady 5.2 / 10.8 / 20.0 / 18.7 / 17.5 / 15.9 MB/s, every run with exactly N worker
processes and N concurrently executing chunks, artifacts identical across all six.
12 and 16 workers are oversubscribed on 4 vCPUs and say nothing about the 8C/16T machine. Steady scaling of the
new code is 2.13x at 2 workers and 4.0x at 4 workers (4/4 CPUs, parent included):
the pool scales linearly while there are free cores. Parent CPU at 4 workers was
0.23-0.31 cores. CPU utilization, parent CPU, aggregate child CPU, busy fraction,
measured concurrency and peak RSS per run are in the benchmark JSON
([benchmark-cloud-final.json](../evidence/QUALITY-AUDIT-PERFORMANCE/benchmark-cloud-final.json)).

Chunk / queue / scheduler matrix at 4 workers
([chunk-queue-scheduler-4workers.jsonl](../evidence/QUALITY-AUDIT-PERFORMANCE/chunk-queue-scheduler-4workers.jsonl)):
8 MiB 19.2-19.4 MB/s (busy 0.91-0.92, RSS 0.83-0.85 GiB); 16 MiB 18.1-18.6; 32 MiB
16.8-18.6 (busy 0.84-0.87, RSS 1.16 GiB); 64 MiB 16.4 (RSS 1.43 GiB); queue 3x/4x at
32 MiB 17.7/18.3; 8 MiB + 3x 19.7; old scheduler 18.0 (32 MiB) / 18.4 (8 MiB).

Earlier Windows measurements (old code, earlier reports): 6.6 MB/s at 1 worker; the
"20-33 MB/s at 4-8 workers" figures were start-up dominated (section 1).

## 6. Projection for 104,506,534,003 bytes (Ryzen 7 5700X3D, 8C/16T)

Inputs: the earlier measured Windows single-worker rate of the OLD code (6.6 MB/s on
the original benchmark mix) x the measured 1.44-1.51x speed-up gives about 9.5 MB/s
per worker. Steady scaling is linear up to the physical cores here; on the 5700X3D the
all-core clock is below the single-core boost, and SMT adds 15-30 % for this kind of
interpreter-bound work. Fixed costs: C05 membership parse at most 15.1 M rows at about
300k rows/s (earlier Windows 184k rows/s x 1.6) = 50 s; aggregation of 2,035 units
about 22 s (earlier Windows measurement); pool start-up and publication about 15 s.
Total fixed: about 1.5 minutes.

| case | configuration | scan rate | scan | total |
|---|---|---:|---:|---:|
| optimistic | 16 workers, SMT +30 % | 85 MB/s | 20.5 min | **~22 min** |
| likely | 16 (or 12) workers | 70 MB/s | 24.9 min | **~26.5 min** |
| conservative | SMT gives nothing (8 effective) | 55 MB/s | 31.7 min | **~33 min** |

**Can it finish in <= 30 minutes on the Ryzen 7 5700X3D?** Probably (likely case
~26.5 min), but this is a projection, not a measurement. The 5-minute benchmark
command decides it before the real run. **<= 20 minutes is not reachable** without a
further design change: it needs ~95 MB/s. The remaining limit is single-thread
interpreter time per document in the workers (the detector kernel), not I/O, IPC,
hashing or the parent.

**Recommended production configuration** (if the benchmark agrees):
- `--workers 16`, or the benchmark's `recommended_workers` (12 if 16 is not at least
  3 % faster).
- Chunk 8 MiB and queue `2 x workers` (fixed by this implementation).
- `--progress-interval-seconds 5 --progress-log C:/XLM-logs/quality-audit-v3.progress.log`.
- `--max-rss-gib 12`: expected peak about 3-4 GiB at 16 workers (1.44 GiB at 8
  workers here, plus a 1.1 GiB overlay).
- `--free-reserve-gib 8 --max-output-gib 4 --max-document-mib 64 --deadline-hours 12`.
- A NEW `--output` directory: the code identity changed, so any earlier attempt's
  directory refuses as a different binding.

## 7. Progress, log, status, benchmark

See the runbook section "Live progress, progress log and status". Summary:

- `Telemetry` counters plus one `Reporter` thread: a line every
  `--progress-interval-seconds` (default 5) for every phase (`prepare`, `overlay`,
  `resume-verify`, `scan`, `verify-drain`, `aggregate`, `write`, `publish`,
  `complete`). Rates use monotonic time; `now` is a ~15 s window and `ewma` a 30 s
  EWMA that starts at the first interval with progress. Active workers are measured
  from per-child CPU times (>= 0.5 core). The cost is one psutil tree sample per
  interval.
- `--progress-log PATH`: the same lines, appended and flushed; refused inside
  `--output`, inside the data root, or overlapping an audit input; the first line
  records the PID; numbers and phase names only.
- `status --manifest --output [--progress-log] [--watch --interval-seconds]`:
  read-only (stat only, never opens units), committed files/bytes/docs, percent,
  committed rate, ETA, COMPLETE/INCOMPLETE, binding state, and the live process
  tree (PID, elapsed, active children, cores, RSS). Tested to leave the output tree
  and corpus bit-identical, and to discover a real running audit subprocess.
- `benchmark --scratch DIR`: about 2-4 minutes on authored data; steady and
  whole-scan MB/s, busy fraction, CPU, parent cores, measured processes and
  concurrency, RSS per worker count; artifact identity across worker counts;
  membership parse rate; recommended workers and the projection.

## 8. Proposals for review (not implemented)

1. **Compiled detector kernel** (mypyc, Cython or a small Rust extension for
   `analyze`; same tests against the frozen oracle). Probably 2-3x per worker, the
   only route to <= 20 minutes. It needs a native build toolchain on Windows and a
   locked dependency change.
2. **Byte-range chunk transport.** Workers read their own byte range; the parent
   still hashes the whole file once, computes each range's SHA-256 and the worker
   refuses unless its bytes hash to it, so the measured bytes are exactly the hashed
   bytes; the second whole-file hash is unchanged. This removes pickling and the
   single task pipe. Adopt only if the Windows benchmark shows the parent above
   ~0.8 cores.
3. **Parallel overlay parsing.** Split `membership.jsonl` into line-aligned ranges
   parsed by the worker pool, while the parent hashes the stream once and checks
   per-range digests, row order across range boundaries and duplicate locations
   when merging. It would save about 1 minute at most.

## 9. Requirement ledger

| requirement | status |
|---|---|
| A: trace process model, prove concurrent execution | IMPLEMENTED, VERIFIED (activity facts; `test_workers_really_execute_chunks_concurrently`) |
| A: explain "one thread" | VERIFIED by code trace (overlay load in the parent, no output); Windows observation not reproduced here |
| B: live stderr progress for every phase, interval, EWMA/ETA, workers, inflight, RSS, output bytes | IMPLEMENTED, VERIFIED (tests, live runs) |
| C: `--progress-log`, outside output/data/inputs, flushed, content-free | IMPLEMENTED, VERIFIED |
| D: read-only `status` / `--watch`, process discovery | IMPLEMENTED, VERIFIED on Linux (incl. a live subprocess); Windows NOT RUN |
| E: benchmark 1/2/4/8/12/16 workers | 1/2/4/8 VERIFIED here; 12/16 BLOCKED (4 vCPUs) -> Windows benchmark command provided |
| F1: parent / IPC investigation | VERIFIED (per-thread CPU); byte-range transport deferred to review |
| F2: chunk size | VERIFIED; 8 MiB adopted |
| F3: queue depth | VERIFIED; 2x kept, backlog scheduler adopted |
| F4: first/second hash | VERIFIED: second hash concurrent (2 threads), not limiting; semantics unchanged |
| F5: detector hot path | IMPLEMENTED, VERIFIED (1.44-1.51x end to end) |
| F6: overlay O(total kept rows) | VERIFIED (dense per-file buffers, linear); 1.6x faster |
| F7: Windows process behaviour | NOT RUN (no Windows here); spawn cost is excluded by the steady-state metric |
| G: exact equivalence | VERIFIED (detector oracle, 90/90 artifacts, overlay arrays) |
| H: projection and decision | IMPLEMENTED (projection); <= 30 min NOT VERIFIED on target hardware |
| I: <= 5-minute Windows benchmark command | IMPLEMENTED; VERIFIED here (4 vCPUs) |
| J: focused tests, ruff, mypy --strict, diff-check | VERIFIED (see COMMANDS.md) |
| real audit, real C05, G:/X: access | OUT OF SCOPE (not run) |

## 10. Limitations

- No Windows measurement in this session; the container has 4 vCPUs, so 8/12/16
  workers could not be measured here. The projection is arithmetic on the earlier
  Windows single-worker number and the speed-up measured here; the benchmark
  command is the decision tool.
- Authored text is not the real detector-cost mix. Real long or markup-heavy
  documents may cost more per byte.
- `status` sees committed files only; chunk-level progress needs `--progress-log`.
  On Windows process discovery needs the audit's command line, which is readable
  for the same user's processes.
- The `report` and `materialize-review` commands still print no progress.
- The second-hash threads and the parent compete with 16 workers for 16 logical CPUs.

## 11. Next

1. Operator: run the `benchmark` command (runbook) on the Windows machine and return
   its JSON.
2. Review proposal 1 (compiled kernel) if <= 20 minutes is required, and proposal
   2 if the benchmark reports the parent above ~0.8 cores.
3. Then, with a NEW output directory, the audit command from the runbook with the
   recommended `--workers` and `--progress-log`.
