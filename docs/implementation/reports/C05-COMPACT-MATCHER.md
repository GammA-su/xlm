# C05 compact exact matcher (physical backend redesign)

Date: 2026-10-03. Branch `feat/c05-global-preparation`; starting HEAD
`b0f239cbd2c70e2d1acb0c2a6f8e2a3b26ccb31a`. Offline, authored synthetic fixtures
only. No access to `X:\C05-Protected` or protected material. No real C05 run, no
network, no training, no push.

## Why the historical automaton cannot run the real index

`StreamingMatcher` allocates one Python `dict` plus three list slots per logical
trie node. In the authored benchmark below it used about 380 B of RSS per node
above baseline (771 MB for 2,022,021 nodes). The operator's exact count for the
frozen v4 index is 71,974,329 logical nodes. At that rate the automaton would need
about 27 GB, which is above the proposed 24 GiB `ram_bytes`, and would take minutes
of single-threaded construction before the first corpus row. Raising
`automaton_nodes` alone cannot fix memory that grows with Python objects per node.

## Scientific invariant: unchanged

The redesign is physical only. `c05-matcher-v4`, task rendering, fallback
rendering, match-view normalization, the 4/16/3 fallback floor, frozen signatures,
`protected-pattern-jsonl-v2` index semantics, the receipt and every policy identity
are untouched. The index is still read and hashed as before. Provenance never
enters the compiled artifact.

## Architecture (`src/xlm/data/exclusion/compact.py`)

`CompactExactMatcher`, backend `c05-compact-exact-v1`:

* **Token ids.** One streaming pass assigns provisional ids, then remaps them to
  vocabulary order. The vocabulary is sorted by code point, which equals UTF-8 byte
  order, so ids are deterministic for an identical index and independent of record
  order. Ids `1..V` are `uint32`; overflow refuses explicitly. Id `0` is reserved
  for every corpus token outside the vocabulary, and the open-time check
  `min(token id) >= 1` proves no pattern contains it.
* **Unique patterns.** Duplicate provenance is ignored for matching. Records are
  sorted lexicographically by id with `np.lexsort` on the first 16 id columns plus
  length; rows still tied beyond 16 tokens are ordered by their exact tails.
  Duplicates are adjacent and dropped. Patterns are stored as flattened `uint32`
  ids plus `uint64` offsets.
* **Anchors.** Each pattern gets one anchor:
  * Patterns with at least `q = 5` tokens use their **rarest internal 5-gram**,
    by occurrence count of its fingerprint over all unique patterns; ties go to the
    smallest offset.
  * Shorter patterns are anchored on themselves.
  * Anchors are bucketed by (anchor length, 64-bit fingerprint) in packed sorted
    arrays (`anchor_keys`, `anchor_starts`, `anchor_patterns`, `anchor_offsets`).
    Lookup is vectorized `np.searchsorted`; there is no Python dict of tuples.
* **Fingerprint.** Each token id is mixed with a splitmix64 finalizer, then
  combined into a polynomial hash modulo 2^64 with an odd multiplier. Document
  windows of length 1..q are computed with vectorized Horner steps. Any candidate
  `(start, length)` gets an O(1) full-sequence fingerprint from prefix sums with
  inverse powers; the multiplier is odd, hence invertible modulo 2^64.
* **Matching.** For one document:
  1. Map tokens to ids.
  2. Find anchor hits per anchor length, skipping windows that contain an unknown
     token.
  3. Expand bucket entries in bounded batches of 65,536 candidates.
  4. Drop candidates that are out of range, contain an unknown token, or have the
     wrong full fingerprint.
  5. Verify survivors in `(end, -length)` order by exact id comparison
     (`np.array_equal`).
* **Return value.** `match()` returns `None` or the canonical digest of the
  pattern's token tuple. That is the same value `StreamingMatcher.match` returns.

### Exactness

The observable historical result is: the longest pattern among those ending at the
smallest end position. The Aho-Corasick state is the longest trie suffix, and its
output link is the longest proper terminal suffix.

* **No false negatives.** Suppose pattern `P` (anchor length `a`, offset `o`)
  occurs at document start `s`. Its anchor tokens are `doc[s+o : s+o+a]`. The
  fingerprint is a pure function of the id window, so it equals the stored key.
  The lookup at anchor position `t = s + o` therefore returns the entry `(P, o)`.
  The candidate start `t - o` equals `s`, and the full fingerprint of a real
  occurrence also matches.
* **No false positives.** A candidate is accepted only after exact id-by-id
  equality.
* **Same pattern returned.** Any match ending at `e` has its anchor at `t <= e`.
  Once a verified best `(end, -length)` exists, anchors at `t > end` cannot
  improve on it, so the scan stops. Survivors are verified in `(end, -length)`
  order. The result is therefore the historical pattern.

Fingerprint collisions affect running time only, never the result.

### Hash-collision handling

`hash_mask` is an authored test seam. It is recorded in the manifest, and the
production defaults refuse a masked artifact (tested). With mask `0`, every anchor
and full fingerprint collides. Tests show that non-matching sequences are still
rejected, matching ones are accepted with the historical identity, and randomized
documents agree with `StreamingMatcher`. With mask `0x3`, buckets of several
hundred entries force multi-batch candidate expansion, and results stay exact.

### Candidate-bucket bound

`MAX_ANCHOR_BUCKET = 4096` is a hard backend constant, part of the backend
version. Compilation refuses (`CeilingExceeded("anchor_bucket")`, with content-free
statistics attached) and never publishes an artifact whose largest bucket exceeds
it. Buckets are never skipped. Per document position, at most `q` buckets of at
most 4096 entries are expanded, in bounded batches; the stage deadline still
applies. A two-letter-alphabet fixture with all 1,024 length-10 sequences and
`q = 4` exceeds a bucket ceiling of 32 and is refused; with the default ceiling it
compiles and stays exact. The audit reports `p50/p95/p99/p999/max` bucket sizes,
the number of buckets at the maximum, and the mean.

## Packed representation and compile artifact contract

The compiled artifact has nine raw little-endian files plus `manifest.json`:
`vocab_bytes`, `vocab_offsets`, `pattern_tokens`, `pattern_offsets`,
`pattern_hashes`, `anchor_keys`, `anchor_starts`, `anchor_patterns` and
`anchor_offsets`.

**Opening.** Opening maps each file read-only (`mmap.ACCESS_READ`). It hashes the
mapped bytes against the manifest, then builds zero-copy NumPy views. Only the
vocabulary (one Python string and dict entry per distinct benchmark token) and an
id-mix table live on the Python heap. `close()` drops the views and unmaps the
files; closing is tested by renaming the directory afterwards, which Windows
refuses while a mapping is open.

**Manifest.** It is canonical JSON with a self-digest and contains no tokens,
signatures, provenance, paths or timings. It binds:

* the source index SHA-256 and byte count;
* the backend, fingerprint function and mask;
* the anchor policy, `q` and bucket ceiling;
* counts: records, unique patterns, vocabulary tokens and bytes, flattened tokens,
  minimum and maximum pattern length, logical nodes;
* bucket statistics and per-file `{dtype, count, bytes, sha256}`;
* the compiled byte total.

**Verified reuse.** Reuse requires all of the following:

* the exact file set (nothing extra, nothing missing);
* the canonical manifest form and its self-digest;
* every binding equal to the requested values;
* every file's size and SHA-256 matching;
* structural invariants: monotone offsets, id range, anchor entries forming a
  permutation, anchor length and offset ranges, sorted keys, sorted vocabulary;
* the counts re-derived from the verified arrays: logical nodes recounted by LCP
  over the stored sorted patterns, which also requires strict order; minimum and
  maximum lengths; bucket statistics.

Any mismatch refuses with no silent rebuild and no deletion.

**Location.** The compiled matcher lives at
`<scratch_root>/<plan-digest>/matcher/`. For a detached-volume plan that is the
protected `X:\C05-Scratch\<plan-digest>\matcher\`. `runner.matcher_directory`
enforces:

* always inside the plan's scratch;
* protected plans: no overlap with the repository checkout, data root or C05
  output;
* detached plans: the protected filesystem device and no overlap with the
  protected root.

The artifact is never exported with the membership result. Authored fixtures use
ordinary temporary directories.

**Determinism.** Compiling the same index into two directories gives byte-identical
arrays and manifests. A shuffled index with extra duplicate records gives
byte-identical arrays.

## Resource-ceiling semantics: option A

`automaton_nodes` keeps its meaning as the exact **logical** trie size that
`StreamingMatcher` would allocate: the root plus every distinct non-empty prefix of
the unique patterns. The count is derived without a trie, as
`1 + sum(len) - sum(lcp with predecessor)` over the lexicographically sorted unique
patterns. Randomized tests assert it equals `StreamingMatcher.nodes` exactly. The
refusal threshold matches the historical one: `nodes <= automaton_nodes` passes and
`nodes - 1` refuses, for both backends. Reuse re-checks the re-derived count.

* `benchmark_patterns` is unchanged: it counts emitted index records, duplicates
  included.
* `ram_bytes` governs physical memory: compile and verification call the runner's
  sampled process-tree RSS check.
* `scratch_bytes` now includes a derived `compiled_matcher` bound in
  `capacity.storage_bounds`. Each token occurrence costs at least 3 index bytes plus
  its UTF-8 length; it is stored as 4 id bytes, plus vocabulary bytes and an 8-byte
  offset at its first occurrence, so at most 13/4 of the index bytes. Each unique
  pattern adds 40 bytes, plus a 64 KiB manifest and per-file allocation slack. The
  bound uses `benchmark_patterns` capped by `benchmark_bytes // 36`.
* `benchmark_bytes` is unchanged; compiled storage is accounted separately.
* `Resources` keeps its exact field set, so historical signed decisions still
  validate and plan digests are unchanged. The real ceiling for `automaton_nodes`
  (about 80,000,000) is an operator decision and is not hard-coded anywhere.

With the proposed `Resources()` defaults and a 512 B journal header, the worst-case
aggregate becomes 370,303,419,928 B (344.9 GiB; previously 363,223,060,992 B). That
is still inside the proposed 352 GiB scratch ceiling. With `benchmark_patterns` =
10,000,000 it is 370,623,419,928 B.

**Backward compatibility.** A historical plan whose `scratch_bytes` had less
headroom than the new derived bound now fails admission. That is fail-closed, with
no automatic widening. Two authored fixtures were restated rather than widened
silently:

* `test_hard_database_page_cap…`: scratch raised from 64 to 128 MiB; the page-cap
  assertion is unchanged.
* `scripts/c05_authored_pilot.py`: `benchmark_bytes` set to 64 MiB, because its
  index is a few KiB.

## Runner, resume and interruption

`run` order is unchanged up to the index hash check. Then `prepare()` either reuses
a fully verified published matcher or compiles one. Compilation:

1. writes into `matcher.staging/`;
2. writes the arrays with fsync, then `manifest.json` last;
3. passes the checkpoint seam `matcher_staged`;
4. renames atomically to `matcher/` and re-opens with full verification.

No corpus row is read before this succeeds (tested: corrupted file → refusal with
zero scan checkpoints). Compiling also re-hashes the index while reading it and
refuses if it changed during compilation. An interrupted staging directory is never
trusted: the next run deletes only its known file names and rebuilds; an unknown
entry there refuses. A published directory without its manifest refuses. The
matcher is closed in `finally`.

Storage admission (`capacity`) accounts the `matcher`/`matcher.staging`
directories and refuses unknown entries inside them.

`resume-check` reports, read-only:

* `compiled_matcher`: `verified`, or `absent; compiled before scanning`;
* `incomplete_matcher_staging`.

A corrupted artifact refuses. Separately, `resume-check` previously failed with an
opaque SQLite error when a crash happened before `facts.sqlite` existed. It now
reports zero verified files during the `scan` stage, and refuses in later stages.

Review is unchanged: `ReviewQueue.compile` still reads the protected index
independently and remains heuristic-only. The review-enabled authored regression
passes.

## Content-free operator audit

`operator benchmark-matcher-audit-local --index --resources --scratch
[--backend compact|streaming] [--mode protected|authored] [--self-check N]`.

* **Location check (protected mode).** The index must sit under a marked protected
  root. The scratch must be on that root's device and must not overlap the root or
  the repository checkout.
* **Compilation.** It compiles into an empty `<scratch>/matcher` under RAM, scratch
  and stage-deadline monitoring. On refusal it removes the partial staging copy.
* **Report-only ceilings.** `benchmark_patterns`, `automaton_nodes`,
  `benchmark_bytes` and the bucket fit are reported, not enforced, so the operator
  can size a decision.
* **Output.** Counts, bucket quantiles, compiled bytes and their derived bound, the
  worst-case aggregate (measured SQLite geometry), peak compile RSS, open RSS delta,
  seconds, and a bounded self-check: sampled patterns embedded between unknown
  tokens must be detected, and an unknown-only document must stay clean.
* **Exit codes.** 0 when everything fits, 2 when a ceiling would not fit, 1 on a
  refusal (refused output carries only the ceiling name and aggregates).
* **Never printed.** Tokens, signatures and provenance.
* `--backend streaming` builds the historical automaton for authored comparison.

## Evidence (authored synthetic only)

| requirement | status |
| --- | --- |
| 1 historical `StreamingMatcher` tests unchanged | VERIFIED (only an additive `nodes` property) |
| 2–8 equivalence: start/middle/end, overlap, prefix, repeated, unknown, normalization, short, long > q, shared prefixes, ties > 16 columns, fallback-v4 patterns, 12 randomized seeds | VERIFIED (identity equality and node-count equality) |
| 5 duplicate provenance → one physical pattern, identical arrays | VERIFIED |
| 9 forced hash collision exact verification | VERIFIED |
| 10 pathological bucket fails closed / large buckets bounded and exact | VERIFIED |
| 11 deterministic compile bytes, order-independent arrays | VERIFIED |
| 12–14 corrupted, wrong-digest and partial compiles refuse reuse | VERIFIED |
| 15–16 protected location guard; authored temp path | VERIFIED (detached-volume fixture via injected inspector) |
| 17–18 RAM and scratch ceilings during compilation | VERIFIED (sampled monitor, tiny ceilings) |
| 19 logical-node contract matches historical threshold | VERIFIED |
| 20 `benchmark_patterns` meaning | VERIFIED |
| 21–22 review disabled / enabled regressions | VERIFIED (existing tests) |
| 23 run / resume / resume-check matcher checks | VERIFIED |
| 24–26 detached-volume, matcher-v4, full C05 suite incl. serial | VERIFIED |
| real 1.75 GiB protected index compile and capacity | NOT RUN (operator, protected volume) |
| real corpus throughput | NOT RUN |

Synthetic metrics. Process baseline peak was about 157 MB in each phase; synthetic
Zipf words; timings are informational, single machine, not CI criteria.

* **200,000 index records** ([JSON](../evidence/C05-COMPACT-MATCHER/synthetic-benchmark-200k.json)):
  * Historical: build 8.79 s, peak 928 MB, 2,066,181 tokens/s.
  * Compact: compile and verify 3.05 s, peak 413 MB, 3,348,520 tokens/s.
  * Compiled 18,127,281 B: 90.6 B per unique pattern, 7.9 B per flattened token.
  * Logical nodes 2,022,021 for both; identical hit digests.
* **1,000,000 records**, through the real operator CLI in authored mode
  ([JSON](../evidence/C05-COMPACT-MATCHER/synthetic-operator-audit-1m.json)):
  * 9,937,364 logical nodes; compile 19.0 s; peak RSS 492 MB.
  * Compiled 86,951,104 B (87.0 B per pattern, 7.56 B per token).
  * Verified open 0.42 s, open RSS delta 91 MB; self-check 1,000/1,000 detected.
  * Exit 2, correctly reporting that the default `automaton_nodes` 8,000,000 does
    not fit.
* **Structural test:** 10× the patterns (and logical nodes) adds no tracked Python
  containers, and the arrays are non-owning mapping views.

**Open limitations.**

* Compilation is single-process, with an estimated real time of about 3 minutes.
  This extrapolates the 1M run linearly; it was not measured on real data.
* The peak at real scale is dominated by the per-window fingerprint arrays: about
  71M windows, an estimated 2–4 GB. The audit measures the real value.
* RSS is sampled every 50 ms and can miss a short peak.
* The synthetic vocabulary is Zipf-distributed. Real anchor buckets are unknown
  until the operator audit.

## Commands and results (exit 0 unless noted)

Environment: Windows 11, Python 3.12.13, numpy 2.5.3; `uv run --offline --locked
--extra cpu --extra eval`. Thread variables were set to 1 and
`TOKENIZERS_PARALLELISM=false`.

* `python -m pytest tests/test_c05_*.py -m "not serial" -n 16 --dist=worksteal --max-worker-restart=0`:
  257 passed ([log](../evidence/C05-COMPACT-MATCHER/c05-nonserial-n16.log)).
* `python -m pytest tests/test_c05_*.py -m serial -n 0`: 1 passed, 257 deselected
  ([log](../evidence/C05-COMPACT-MATCHER/c05-serial-n0.log)).
* New `tests/test_c05_compact_matcher.py`: 47 tests, included in the above.
* `ruff format --check src tests <changed scripts>`, `ruff check …`,
  `mypy --strict src/xlm/data/exclusion tests/test_c05_compact_matcher.py tests/test_c05_capacity.py tests/test_c05_matcher_v4.py scripts/c05_compact_matcher_benchmark.py`,
  `git diff --check`: clean ([log](../evidence/C05-COMPACT-MATCHER/static-checks.log)).
* `python scripts/c05_compact_matcher_benchmark.py --patterns 200000 --documents 2000`.
* `python -m xlm.data.exclusion.operator benchmark-matcher-audit-local --index <tmp>/index.jsonl --resources <tmp>/resources.json --scratch <tmp>/scratch --mode authored --self-check 1000`:
  exit 2, as intended.

Tests run: the C05 files only. The full repository suite was not run; this is not
the release gate.

## Operator next steps (not run by the agent)

1. With `X:` attached and `X:\C05-Scratch\matcher-audit` absent or empty:

   ```powershell
   $op = 'uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.exclusion.operator'
   Invoke-Expression "$op benchmark-matcher-audit-local --index X:/C05-Protected/prepared/index.jsonl --resources <resources-value.json> --scratch X:/C05-Scratch/matcher-audit --self-check 1000 > G:/XLM/c05/benchmark-matcher-audit.json"
   ```

   Expect exit 2 while `automaton_nodes` is 8,000,000; the logical count should
   reproduce 71,974,329. Exit 1 means a refusal; read `ceiling`.
2. Review `logical_trie_nodes`, `peak_compile_rss_bytes`, `compiled_matcher_bytes`,
   the anchor quantiles and `max_bucket` (must be ≤ 4096), and the self-check.
3. Sign a new resource decision: `automaton_nodes` from the audited count with a
   margin (for example 80,000,000) and `benchmark_patterns` (for example
   10,000,000). Keep `ram_bytes` above the measured peak plus the scan's needs.
   Confirm `scratch_bytes` still admits the worst case.
4. Delete `X:\C05-Scratch\matcher-audit` (a private copy of the signatures), then
   create the plan and authorization as before.
