# C05 compact parallel engine (`c05-facts-v2`) with live progress

Date: 2026-10-03. Worktree `F:\Project\xlm-c05-parallel`, branch
`perf/c05-parallel-progress`, starting commit
`0b39a806cf5f22a1ceeb037e42f854cfe4fa2763` (accepted `feat/c05-global-preparation`).
Offline; authored synthetic fixtures only. No access to `X:\C05-Protected`,
`X:\C05-Scratch` production state or the `G:\XLM` corpus; the operator checkout
`F:\Project\xlm-c05-global` and the historical p0001 work directory were not
touched. No real C05 run, no training, no network, no push.

## 1. Why the historical engine could not finish

The real protected p0001 attempt (operator observation) showed about 77 docs/s
after 54 minutes, i.e. about 54 hours for the scan of 15,097,174 documents, against a
reviewed `stage_seconds` of 24 hours, with `facts.sqlite` already 2.48 GiB (a linear
projection of about 149 GiB against `index_bytes = 128 GiB`). Root causes, measured
on authored data at the starting commit:

* `Resources.workers` was documented and implemented for benchmark preparation only;
  the scan was single-process by design.
* About 83% of per-document CPU was one function: the NumPy MinHash kernel allocated
  dozens of full `128 x shingles` uint64 temporaries per document (16.6 ms/doc on
  7.7 KB authored documents).
* The single SQLite writer alone (no CPU work at all) sustained only 640-1,030
  docs/s with an 8 MiB page cache and about 70 random B-tree inserts per document
  (band postings stored three times: table, PK autoindex and `band_doc`).
* `DiskGroups.group()` ran at 443-468 docs/s at 25k-100k documents (at least about
  9.5 hours for 15.1M, before the database outgrew RAM).

## 2. Scientific invariant: unchanged

Physical/execution change only. Unchanged: `c05-matcher-v4`, rendering, fallback,
match-view normalization, exact contamination semantics, MinHash algorithm/seed/
128 permutations/32 bands, `near_threshold`, oversized-bucket skip-and-count,
`max_bucket_size`, `max_candidates_per_document`, candidate ordering, known-lineage-v3,
parent lineage, survivor rule, family construction, split/quick/audit allocation,
membership schema, completion schema (same keys; `storage.bounds` names follow the
new storage components), benchmark index semantics and every policy identity.
The logical definitions **and exact bytes** of `facts_digest(file)` and
`group_digest()` are preserved (section 9).

## 3. Exact fast MinHash (`xlm.data.dedup.minhash`)

`signature_from_array` computes `min_h((a*h + b) mod P)`, `P = 2^61 - 1`, exactly:

* `g = h mod P` once per shingle (`(h & P) + (h >> 61) < P + 8`, one conditional
  subtract), so `(a*h + b) mod P = (a*g + b) mod P`;
* `a = a1*2^31 + a0`, `g = g1*2^31 + g0` (`a1, g1 < 2^30`; `a0, g0 < 2^31`),
  `2^61 = 1`, `2^62 = 2 (mod P)`: `a*g = 2*a1*g1 + mid*2^31 + a0*g0` with
  `mid = a1*g0 + a0*g1 < 2^62` and `mid*2^31 = 2*s1 + s0*2^31` (`s1 = mid >> 31`,
  `s0 = mid & (2^31-1)`);
* `x = 2*a1*g1 + 2*s1 + s0*2^31 + a0*g0 + b < 2^61 + 2^32 + 2^62 + 2^62 + 2^61 < 2^64`
  (no wrap), reduced by one fold to `y < P + 8`;
* the final conditional subtract is folded into the minimum: `min(min(y), min(y - P))`
  with wrapping `y - P`, which exceeds `2^63` exactly when `y < P`.

All work happens in three cache-resident `128 x 256` buffers with in-place ufuncs
(chunk 256 measured fastest of 64/128/256/512/1024/2048). The historical kernel stays
as `_signature_vectorized_v1` and `_signature_python` as oracles; `_signature_vectorized`
keeps its contract (Arrow/pure-Python fallbacks unchanged). The C05 shingle path
(`scanprep.shingle_hashes`) hashes byte slices of the UTF-8 match view instead of
re-joining tokens: tokens are separated by exactly one ASCII space and UTF-8 never
encodes another character with byte 0x20, so every historical shingle
`" ".join(tokens[i:i+5])` is a byte slice; an empty view is the single `""` shingle
(the historical `DiskGroups.add` behavior).

Exact comparison counts (all equal, zero mismatches): `tests/test_minhash_fast_kernel.py`
compares 21 uint64 boundary sets, 2,000 random sets (sizes 1-5,000, many containing
boundary values, oracle `_signature_python` up to 300 shingles and
`_signature_vectorized_v1` above), 7 chunk sizes, duplicate-heavy arrays and 600
documents (empty/short/long/unicode/adversarially repetitive) on shingles,
signatures and band keys; the development check compared a further 3,000 sets.
The compact-vs-reference harness additionally compares every stored signature.

| authored measurement | historical kernel | fast kernel | speedup |
|---|---:|---:|---:|
| 1,500 random shingles | 16.51 ms | 2.25 ms | 7.3x |
| 4,000 random shingles | 40.26 ms | 5.74 ms | 7.0x |
| production-sized documents (5.9 KB lines, 3,000 docs) | 4.58 ms/doc | 1.11 ms/doc | 4.1x |

## 4. Architecture

```
parent: reserve bytes_read (whole file) -> read line -> reserve attempted_records
        -> SHA-256 raw bytes -> batch (<= 256 rows, <= 4 MiB)
           | bounded task queue (credit: <= 2 x workers in flight)
   N spawned workers (role "scan"): parse/validate, normalize, matcher, shingles,
   MinHash, band keys, lineage keys, parents, digests, facts-digest fragment
           | bounded result queue -> reorder buffer (by global sequence)
parent: in-order integration -> per-file unit writer -> verify SHA/rows/bytes
        -> signed unit facts/<ordinal>.unit (assembled, fsync, rename)
group pool (role "group"): band keys, family orderings, digest rows, output lines
parent: dense ids, exact runs, band index, near replay, lineage, parents,
        union-find, survivors, families, splits, digest, seal, publication
```

### Scan workers and the parent

* **Workers compute** (`scanprep.Preparer`): strict canonical JSON + `CanonicalDocument`
  validation (type checks fail closed), source identity, UTF-8 byte count, match view
  and token ceiling, `CompactExactMatcher.match`, Gutenberg policy check, shingle
  hashes, exact MinHash, band keys, `lineage_keys_v3`, unique parents, normalized
  exact SHA-256, canonical content digest, document-id digest, lineage/parent key
  digests and the historical facts-digest fragment. They return compact bytes
  (fixed records, signatures, blobs + offsets); never text or tokens (unique tokens
  only when heuristic review is enabled).
* **The parent keeps**: reading, raw SHA-256, every reservation, ordering, per-row
  counter checks, the facts-digest hash (fragments in row order), the review queue
  (when enabled), unit writing, attestation signing, commit, all budget checks,
  state, grouping decisions, publication.
* **Errors**: a failing row is returned as an ordered, content-free error (C05Error
  text or exception type only); reader refusals (record ceiling, extra rows) are
  ordered markers too, so the reported refusal never depends on scheduling.
* **Windows spawn**: `multiprocessing` spawn context; each child re-verifies and
  memory-maps the compiled matcher read-only (shared page cache, no private copy),
  closes it at exit. `workers == 1` runs the same `Preparer` in-process.
* **Flow control**: credit-based; at most `capacity = 2 x workers` jobs in flight;
  queue sizes exceed capacity so neither side ever blocks on a full pipe.
* **Effective workers**: `min(Resources.workers, os.cpu_count())` (16 here); shown in
  progress; results never depend on it. No production `--workers` override exists.

### Batching

Batches are bounded by **both** rows and raw bytes: `BATCH_ROWS = 256`,
`BATCH_BYTES = 4 MiB` (whichever comes first); in flight at most `2 x workers`
batches, so raw input held in queues is at most `32 x 4 MiB = 128 MiB` at 16
workers (in practice ~1.5 MB per batch for 5.9 KB lines). Steady-state scan rate
(median rolling rate over the middle 60% of the scan, 16 workers, 100k documents,
4 MiB byte bound):

| rows | 64 | 128 | 256 | 512 | 1024 |
|---|---:|---:|---:|---:|---:|
| docs/s | 2,830 | 2,625 | 2,823 | 2,574 | 2,353 |

64 and 256 rows are equal within run-to-run noise; 256 keeps per-batch IPC/
dispatch overhead lower and is kept. Byte bounds 1/2/8 MiB: **NOT RUN** (the
1 MiB and 2 MiB runs were aborted by the operator's cancellation; 8 MiB never
started). At production document sizes the 4 MiB bound does not bind
(256 x 5.9 KB = 1.5 MB). Results are identical for every batch shape
(membership `62970ac517b2d1e9...` in all runs; plus
`test_batch_row_and_byte_bounds_never_change_results`).

### Per-file atomicity and resume

A plan file is reusable only after its exact raw SHA-256, row count and canonical
byte count match the frozen manifest and its unit is published: sections are
buffered in memory (spilling to `NNNNN.staging/` only above 64 MiB per unit), then
assembled behind the signed header into `NNNNN.unit.staging`, fsynced and renamed to
`NNNNN.unit`. The signed header binds plan, ordinal, path, SHA-256, documents,
canonical bytes, format `c05-facts-v2`, the logical facts digest and every
section's offset/size/SHA-256; zero padding is verified. On every run the parent
discards staging, refuses units outside the plan range, re-hashes the source of
every reused unit (parallel threads) and verifies the unit fully, without re-preparing
any document; the scan continues with missing files only. Grouping restarts from
scratch after any interruption (as the historical single grouping transaction did);
publication is still staging-then-rename.

### Spent-work accounting

Unchanged semantics and code path (`spend`): `bytes_read` for the whole frozen file
before its first read (and for each reused source before re-hashing),
`attempted_records` for each raw line as it is read, before any worker can see it,
and one `comparisons` reservation before every candidate comparison (and every
review comparison). Reservations are fsynced blocks in signed state; in-flight work
lost to an interruption stays spent; nothing resets them.

## 5. Compact fact format (`c05-facts-v2`)

One file per plan file: `MAGIC` + header length + canonical signed header + zero
padding + 64-byte-aligned sections: `records` (104 B/doc: canonical bytes,
exact SHA-256, content digest, id digest), `signatures` (1,024 B/doc), `ids` +
`ids_off`, `id_order` (rows in exact id byte order), `lineage_*` and `parents_*`
(blob, offsets, per-doc counts, 32-byte digests), `hits` (row + pattern identity,
matched documents only), and `review` (heuristic review only). Band postings are
**not** persisted: band keys are regenerated from the stored signatures in parallel
during grouping. No pickle anywhere.

Measured on the 100k authored run (`evidence/.../fact-bytes-100k.json`):
**1,311.9 B per document** (131,187,072 B for 100,000 documents in 13 units):
`signatures` 1,024.0, `records` 104.0, `lineage_blob` 85.5, `lineage_dig` 48.3,
`ids` 17.0 (authored ids are short), `lineage_off` 12.1, `ids_off` 8.0,
`id_order` 4.0, `lineage_cnt` 4.0, `parents_cnt` 4.0, parents/hits < 1 together;
header + alignment ~2.6 KB per unit. Sealed group arrays: 81.2 B per document.
The historical SQLite engine used 7.5 KB/doc in its 100k pilot and about 10 KB/doc
in production (2.48 GiB at about 250k documents).

Projection for 15,097,174 documents (NOT measured at that scale; production ids,
URLs and lineage keys may be longer than authored ones): units about 19.8 GB
(18.4 GiB), sealed group arrays about 1.2 GB, transient band index (postings of
buckets with >= 2 members only) and lineage sort spills bounded by the ledger.

## 6. Grouping (`xlm.data.exclusion.grouping`)

* **Dense ids**: k-way merge of each unit's id order; dense numbers follow exact
  UTF-8 byte order (= SQLite BINARY collation), so `ORDER BY id` is ascending dense
  and "lexicographically smaller root" is "smaller dense id". Duplicate ids refuse.
  `uint32` bound enforced (`<= 2^32 - 2` and `<= Resources.records`).
* **Union-find**: vectorized hooking of larger roots under the smallest connected
  root plus pointer jumping (`components`); invariant `parent[i] <= i`, so every root
  is its component's minimum regardless of union order (the historical semantics;
  no union-by-rank). Duplicate components: exact + near edges; family components:
  duplicate components + lineage + resolved parent edges.
* **Exact**: one sort of (exact SHA-256 words, dense); equal runs.
* **Band index**: per band, sort (12-byte key, dense) as (high 8 bytes, low 4 bytes
  packed with dense); buckets with 2..`max_bucket_size` members keep their sorted
  member lists, larger buckets keep only their size; postings are stored per band in
  document order. Bands per pass come from the RAM ceiling.
* **Near replay**: for each document in ascending id order, its postings in band
  order exactly as `DiskGroups.group`: an oversized bucket counts one event (against
  the ceiling) and is skipped; otherwise smaller members are added in ascending order,
  skipping repeats, until one more would exceed the cap (then the document is capped
  and no later band is visited); candidates are compared in ascending order after one
  reservation each; `agreements / 128 >= near_threshold`.
* **Lineage**: external sort of (key SHA-256, dense); every equal-digest run is then
  verified on the exact key bytes (a SHA-256 collision refuses rather than merging).
* **Parents**: resolved by id SHA-256 lookup then exact string comparison; unknown or
  empty parents are ignored (historical JOIN semantics).
* **Survivors, families, splits**: vectorized `ORDER BY duplicate, bytes DESC, source,
  id`; family surviving bytes (int64), hit propagation, `digest([seed, family])`
  (computed by group workers), greedy diagnostic/quick/audit allocation over hit-free
  families `ORDER BY ordering, id`.
* **Digest**: the historical `group_digest` rows (docs, families, stats) rendered by
  group workers in order and hashed by the parent; sealed with every group file hash.

## 7. Progress

`xlm.data.exclusion.progress.RunProgress`: monotonic clock (injectable), rate-limited
lines, a forced line on every stage transition, a 30-second rolling-rate window (one
sample per second at most), ETA only from the rolling rate after 10 seconds of samples
and only with an exact denominator (`--:--:--` otherwise), lifetime average shown
separately, estimator reset per stage, numeric-only fields and telemetry (type-checked;
no text/ids/paths/hashes/tokens/provenance can be printed), telemetry (RSS, peak RSS,
RAM ceiling, working-index bytes and ceiling, scratch, free space) sampled every 5 s.
`NullProgress` for `--no-progress`. CLI: `run`/`resume` `--progress-interval`,
`--progress-format text|jsonl`, `--no-progress`; stderr only; stdout unchanged.
Matcher compile/verify phases report through an additive callback in `compact.py`
(encode bytes, vocabulary remap, unique sort, materialize, anchor generation, anchor
sort, packed write, verify open, verify structure).

Real lines from the authored 100k / 16-worker run, rendered by
`progress.render` from its recorded JSONL stream (`progress-run100k-w16.jsonl`):

```text
[C05] MATCHER COMPILE: ENCODE | done | 33,486,817/33,486,817 bytes (100.00%) | 12,110,965 bytes/s avg | RSS 0.1/48.0 GiB (peak 0.1) | scratch 0.03 GiB | free 753.4 GiB | elapsed 00:00:02 (run 00:00:02) | ETA 00:00:00
[C05] SCAN | 50,570/100,000 docs (50.57%) | files 6/13 | 0.33/0.56 GiB input | committed 45,194 docs | 2,869 docs/s rolling | 2,743 docs/s avg | 16.2 MiB/s | workers 15/16 busy | tasks 15/32 results 0 | RSS 1.8/48.0 GiB (peak 1.8) | index 0.06/64.0 GiB | scratch 0.10 GiB | free 753.4 GiB | elapsed 00:00:18 (run 00:00:23) | ETA 00:00:17
[C05] GROUP: NEAR | done | 100,000/100,000 docs (100.00%) | candidates 11,526 | comparisons 18,115 | 96,993 docs/s avg | candidate_cap_documents 0 | oversized_bands 0 | RSS 1.4/48.0 GiB (peak 1.8) | index 0.12/64.0 GiB | scratch 0.18 GiB | free 753.3 GiB | elapsed 00:00:01 (run 00:00:47) | ETA 00:00:00
[C05] PUBLISH: MEMBERSHIP | done | 100,000/100,000 docs (100.00%) | duplicates 7,864 | excluded 159 | kept 91,977 | 145,560 docs/s avg | written 64.9 MiB | RSS 1.4/48.0 GiB (peak 1.8) | index 0.12/64.0 GiB | scratch 0.18 GiB | free 753.3 GiB | elapsed 00:00:00 (run 00:00:49) | ETA 00:00:00
```

Percentages: `SCAN` = processed (prepared and integrated in order) documents /
`sum(plan.files.documents)` (reused documents count as processed); `committed` =
documents inside published units; `files` = published units / plan files; input
bytes = raw bytes read / frozen `file_bytes`. Group stages use exact denominators
computed once at stage start (documents, `32 x documents` postings, lineage keys,
parent references, families, digest rows); matcher stages use index bytes or
compiled bytes. Rolling rate: samples at most once per second; the rate is
`(count_now - count_oldest) / (t_now - t_oldest)` over samples within the last 30 s
(the oldest retained sample is the latest one at least 30 s old); ETA =
`remaining / rolling rate`, shown only after 10 s of samples.

## 8. Equivalence evidence (reference SQLite engine vs compact engine)

`tests/test_c05_compact_equivalence.py` runs the retained reference engine and the
compact engine on the same authored corpora and requires equality of: membership
bytes and SHA-256, private decision bytes (every doc's duplicate/lineage roots,
decision, split, quick), completion scientific fields (counts, components,
allocations, dedup stats, review summary, digests), every per-file `facts_digest`,
the `group_digest`, and every stored signature, band key set, exact hash, hit,
lineage key set and parent set. Fixtures: three mixed corpora (exact/near duplicates
across files, hits, URL lineage, parents incl. missing/empty, unicode ids, empty
texts), cap/oversize grids (`max_bucket_size` 2/3/4/8/3/256 x cap 1/2/3/2/64/1),
bucket exactly at and one above the maximum, Jaccard exactly at / one 1/128 step
below / above the threshold, identical signatures with candidates on both sides of
the current id, survivor ties (bytes, source, id), split/quick/audit allocation,
lineage/parents/URL variants, Gutenberg upstream allocations, heuristic review
enabled (1 and 2 workers), empty/short/unicode/empty-file inputs, and workers
1/2/4 byte-identity. All 19 pass.

The planned larger reference-vs-compact comparison on the 20k authored benchmark
corpus is **NOT RUN** (cancelled). Equivalence evidence is the 19 fixture-level
tests above plus byte-identical membership/group digests across 1/2/4/8/16 workers
on the 100k corpus (compact engine only).

## 9. Benchmarks (authored synthetic; informational, not CI gates)

Corpus generator: `scripts/c05_compact_benchmark.py generate` (60k-word Zipf
vocabulary, punctuation/case, mean 5.4 KB of text per document = the production mean
81.86 GB / 15.1M, 3% exact and 5% near duplicates, 50% URL metadata with 2% shared
hosts, 1% parents, 0.5% embedded benchmark patterns, skewed file sizes, ~7,400
documents per file). Machine: AMD Ryzen 7 5700X3D (8 cores / 16 threads), 72 GB RAM,
Windows 11, scratch on C: (NVMe). Every timing below comes from the engine's own JSONL
progress stream.

### Worker scaling (100,000 documents, 598 MB input)

| workers | scan docs/s | scan MiB/s | scan s | group s | publish s | wall s | peak tree RSS |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 364 | 2.1 | 274.7 | 10.6 | 2.2 | 291.1 | 0.30 GiB |
| 2 | 557 | 3.2 | 179.5 | 7.2 | 1.7 | 192.7 | 0.49 GiB |
| 4 | 1,028 | 5.9 | 97.3 | 4.8 | 1.1 | 107.6 | 0.65 GiB |
| 8 | 1,808 | 10.3 | 55.3 | 4.1 | 0.7 | 65.1 | 0.99 GiB |
| 16 | 2,569 | 14.7 | 38.9 | 4.6 | 0.8 | 49.5 | 1.82 GiB |

Membership SHA-256 `62970ac517b2d1e9...` and group digest `ee9e1bdc81c50b59...` are
identical for all five worker counts. 16 workers is 7.1x the in-process scan and 1.42x
8 workers (SMT), so production uses all 16.

### Per-document CPU (single process, 3,000 production-sized documents)

| step | ms/doc |
|---|---:|
| parse + validate | 0.058 |
| normalize (match view) | 0.373 |
| compact matcher | 0.320 |
| shingle hashing | 0.592 |
| MinHash (fast; historical kernel 4.584) | 1.109 |
| band keys | 0.077 |
| lineage keys | 0.030 |
| digests | 0.101 |
| total (fast) | 2.660 |

With the historical kernel the same work is 6.13 ms/doc; the historical engine
additionally serialized everything with SQLite inserts in one process.

### Not run (cancelled by the operator on 2026-10-03)

* 500k and 1M document scale runs (grouping/publication scaling shape);
* SQLite reference engine throughput on the 20k corpus;
* RAM-ceiling memory plans 24/32/40/48/56 GiB (production-scale band passes
  8/12/16/19/23 and lineage run sizes);
* production-sized compiled matcher (~7.6M authored patterns): worker RSS and the
  RSS-vs-USS (shared mmap) difference.

### Projection for the real run (PROJECTION, not a measurement)

* Scan: 15,097,174 documents / 2,823 docs/s (steady, 16 workers, authored docs with
  the production mean size) = about 5,350 s, **about 1.5 h**. The production matcher
  (about 666 MB compiled vs 33 MB authored) may make the matcher step (0.32 of 2.66
  ms/doc) slower: not measured.
* Grouping + publication: 5.4 s at 100k; linear-to-n log n extrapolation is roughly
  10-20 minutes at 15.1M (not measured above 100k).
* Matcher compile (if not reused): the operator observed about 198 s.
* Total about 1.75-2 h. Peak process-tree RSS measured 1.82 GiB at 100k (authored
  33 MB matcher); the production matcher's shared pages are counted once per worker by
  the RSS gate (working set), up to about 16 x 0.65 GB = 10.6 GB of double counting
  if every worker touches every page (unmeasured; the gate stays conservative).

### Recommended p0002 resources (operator decision)

* `workers: 16` (16 is 1.42x 8 workers; results never depend on it).
* `ram_bytes: 51539607552` (48 GiB) on the 72 GB machine: covers the unmeasured
  shared-mapping double counting (about 10.6 GB worst case) plus parent grouping
  arrays (the 48 GiB plan uses 19 bands per pass) with about 24 GB left for Windows
  and file cache. Going higher buys nothing measured (the scan is CPU-bound).
* `index_bytes: 68719476736` (64 GiB): about 3x the projected 19.8 GB of units plus
  group arrays and transients; hard, ledger-enforced. The historical 128 GiB value
  is also admissible but reserves more scratch.
* Other fields: unchanged from the reviewed p0001 decision is acceptable
  (`records >= 15,097,174`, `attempted_records`, `bytes_read`, `stage_seconds`
  86,400, `overall_seconds`); the plan command refuses (fail closed) if the derived
  worst case does not fit `scratch_bytes`.

## 10. Tests and static checks

All runs offline with locked dependencies (`uv run --offline --locked --extra cpu
--extra eval`), thread variables pinned to 1, `TOKENIZERS_PARALLELISM=false`.

| check | result | log |
|---|---|---|
| `pytest tests/test_c05_*.py tests/test_minhash_fast_kernel.py tests/test_minhash_arrow_kernel.py -m "not serial" -n 16 --dist=worksteal --max-worker-restart=0` | 347 passed, exit 0 | `c05-nonserial-n16.log` |
| `pytest tests/test_c05_*.py -m serial -n 0` | 1 passed, 329 deselected, exit 0 | `c05-serial-n0.log` |
| operator-path and determinism selection (`-v`) | 25 passed | `operator-path-verbose.log` |
| `pytest tests/test_dedup_throughput.py -n 0` (exclusive) | 18 passed | (development run) |
| `pytest tests/test_opus_review_equivalence.py -n 0` | 27 passed | (development run) |
| `ruff format --check src tests scripts` | 747 files formatted, exit 0 | `static-ruff.log` |
| `ruff check src tests scripts` | all checks passed, exit 0 | `static-ruff.log` |
| `mypy --strict src/xlm/data/exclusion src/xlm/data/dedup/minhash.py` | no issues in 37 files, exit 0 | `static-mypy-diffcheck.log` |
| `git diff --check` | exit 0 | `static-mypy-diffcheck.log` |

New test files: `test_c05_compact_equivalence.py` (19), `test_c05_parallel_scan.py`
(29: out-of-order/delayed first batch, batch bounds, malformed row with 1/2
workers, SHA/row/byte drift, duplicate ids within/across files, hard worker kill,
Ctrl+C with no orphan processes, RAM ceiling while workers run, disk and stage
deadline, resume after `row`/`file_committed`/`grouped`/`before_publication`
without re-preparing committed files, matcher mappings released by every process,
bounded pool capacity and typed child errors, spilled unit sections, memory-plan
independence, external sort exactness and run ceiling, working-index ceiling,
parent hit propagation), `test_c05_progress.py` (14), `test_minhash_fast_kernel.py`
(16). Retargeted: SQLite journal/hot-journal/page-cap/table-tampering tests now
run against the reference engine, with compact counterparts (staging leftovers,
working-index ceiling, unit/seal/group/state tampering incl. padding).
Not run here: the full non-C05 repository suite.

## 11. Files changed

New: `src/xlm/data/exclusion/{scanprep,scanpool,factstore,grouping,extsort,publish,
progress,reference,reference_capacity}.py`, `scripts/c05_compact_benchmark.py`,
`tests/{c05_compact_support,test_c05_compact_equivalence,test_c05_parallel_scan,
test_c05_progress,test_minhash_fast_kernel}.py`, this report and its evidence.
Changed: `src/xlm/data/exclusion/{runner,capacity,artifacts,compact,control,review,
policy}.py` (policy: comments only), `src/xlm/data/dedup/minhash.py`,
`tests/test_c05_{engine,capacity,acceptance_audit,detached_volume}.py`,
`scripts/c05_synthetic_flow.py` (authored scratch ceiling),
`docs/runbooks/c05-global-preparation.md`, `docs/implementation/STATUS.md`.

## 12. Requirement ledger

| requirement | status |
|---|---|
| Exact fast MinHash, oracle-tested | IMPLEMENTED, VERIFIED |
| Compact per-file fact units, atomic publication, resume without re-preparation | IMPLEMENTED, VERIFIED |
| 1..16 worker spawn scan, bounded queues, deterministic ordering | IMPLEMENTED, VERIFIED |
| Byte-identical outputs vs SQLite reference (fixtures) and across worker counts | VERIFIED (fixtures; 100k authored for worker counts) |
| `facts_digest` / `group_digest` byte preservation | VERIFIED |
| Compact grouping (dense ids, exact, band index, near replay, lineage, parents, union-find, survivors, families, splits) | IMPLEMENTED, VERIFIED |
| Fail-closed: crash, Ctrl+C, RAM/disk/deadlines, drift, duplicates, tampering | VERIFIED |
| Spent-work accounting unchanged (reserve before work, never reset) | VERIFIED |
| Live progress (stages, rolling ETA, telemetry, stderr only, CLI flags) | IMPLEMENTED, VERIFIED |
| Storage contract (hard working-index ledger, derived worst case) | IMPLEMENTED, VERIFIED |
| Worker scaling 1/2/4/8/16 (100k authored) | VERIFIED (measured) |
| 500k / 1M scale, RAM-ceiling sweep, production-sized matcher RSS | NOT RUN (cancelled) |
| Real production C05 run | NOT RUN (operator) |

## 13. Operator migration (p0001 -> p0002)

The historical p0001 cannot be resumed with this code: its code identity differs and
its work directory contains `facts.sqlite`, which compact admission refuses. Leave it
untouched. The operator (never the agent) then:

1. Pushes the accepted commit (optional for the run; required for provenance).
2. Rebuilds the protected benchmark preparation receipt from this checkout into a
   fresh destination (same material spec and frozen matcher-v4 policy); the
   receipt binds the new `code_commit`/`code_identity`/`dependency_sha256`.
3. Verifies the new receipt.
4. Records a new reviewed resource decision (recommended values above).
5. Creates the next plan (sequence 2, `p0002.json`) from this checkout; reviews its
   digest; signs its authorization.
6. Runs `resume-check` (read-only; expects "no signed state" before the first run),
   then `run` with progress.

Any later commit on the run's checkout changes `code_commit`; create the receipt and
the plan only after the final commit, and run from that same HEAD.
