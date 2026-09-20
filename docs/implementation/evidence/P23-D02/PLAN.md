# D02 focused plan — awaiting separate approval

No D02 implementation is authorized or included. This is the next proposed stage
after the approved D03 core handoff. Overall platform acceptance stays BLOCKED.
Only authored, bounded offline fixtures are proposed here; the operator retains
real acquisition and real-corpus preparation.

## Preserved adversarial reproduction

`test_d02_before.py` exercises the existing fetcher, progress journal and storage
manager with an in-memory response (44 authored JSONL bytes). No socket is opened,
no real URL is requested, and no corpus/license/admission claim is made. The URL
is merely an allowlisted string passing through the existing host validator.

Exact command, from `D:\Project\xlm`, with the existing locked CPU/eval environment:

```
uv run --offline --locked --extra cpu --extra eval python data/audit/p23-remediation/stage04/run_before.py before data/audit/p23-remediation/stage04/test_d02_before.py -o faulthandler_timeout=0
```

Result: **exit 1; 4 failed, 1 positive control passed**. Pytest took 1.92 seconds;
the bounded command took 2.594 seconds, with sampled peak process-tree RSS
111,058,944 bytes. Source hashes before/after match. Exact argv/results and all
assertions are retained in `before-results.json`, `before-0.log`, `before.xml` and
`before-tmp/*/observed.json`.

| Assertion | Actual before behavior |
|---|---|
| Restart retains consumed transfer allowance | Journal retains 44 consumed bytes; new capacity manager starts at zero. |
| Same-length corrupt cache is not reused | 44 replacement `X` bytes accepted as a completed cache hit despite the independent expected SHA-256. |
| Record limit bounds retained corpus | Limit 1 retains 2 records, reports 0 records, and marks completion. |
| Concurrent reservations consume shared remaining capacity | Two 3,000-byte reservations both succeed under a 4,096-byte limit. |
| Valid authored transfer remains supported | Original payload and transferred-byte count match. |

These are logic reproductions, not evidence of live HTTP compatibility. Redirect,
retry, decompression and process-output weaknesses are additionally visible in
the inspected implementation; their full adversarial tests belong to the repair.

`test_d02_refusals_before.py` additionally states the proposed explicit refusal
contract for the cache and whole-file record cases: named integrity/record errors,
no overwrite of an existing completed original, and no over-cap file publication.
The earlier observation assertions remain untouched; they do not require a future
repair to overwrite corrupted originals or truncate whole files. The refusal
reproduction uses the same offline fixture and its own `refusals-before` evidence
directory, with two intended missing-exception failures on the current code.

## Implementation scope

1. **One durable accounting authority.** Extend the existing progress journal,
   capacity manager and file locks. Bind consumption and outstanding reservations
   to the immutable acquisition/prepare plan. Atomically reserve before reads and
   writes, reconcile actual consumption, preserve conservative reservations after
   crashes, and prevent restart/retry/concurrency from replenishing allowance.
   Reconcile owned caches, partials, scratch, extracted and published output.
   Do not infer unknown historical consumption as zero or rewrite originals.

2. **Measured transport and cache integrity.** Route discovery metadata, redirects,
   retries, range requests and payload reads through the existing transport and
   shared budget. Revalidate every redirect host, timeout and request allowance.
   Define the counted layer precisely: bytes read from HTTP response bodies and
   explicitly measured metadata, with TLS/TCP/wire overhead unclaimed. Remove
   fabricated fixed-byte overhead estimates. Stream independent/cache digests;
   length or ETag alone never establishes content integrity. Preserve locators,
   pinned revision and authoritative hashes. Corrupt/incomplete files cannot
   become successful cache hits or completed publications.

3. **Real record/selected-row gates.** Count parsed records before retention and
   processing. Whole-file mode must preserve original identity and explicitly
   refuse a file that cannot satisfy its declared record/storage limits; it must
   not silently publish a truncated file under the original identity. Implement
   selected-record artifacts through the existing readers, transport and artifact
   store, with explicit JSONL record and supported Parquet row-group/range routes,
   bounded reads/decompression and original file/row locators. Bind selection and
   output identity separately from full-file identity. Unsupported formats/views
   refuse explicitly; there is no whole-shard fallback for selected-row requests.

4. **Aggregate preparation limits.** Extend existing prepare planning/state and
   subprocess execution with shared cumulative time, output, scratch, final disk,
   record and decompression limits. Spool stdout/stderr into bounded owned files;
   drain both streams and terminate only the owned process tree on cap breach.
   Check-only stages and retries also consume budgets. Add bounded parser/buffer
   work where supported. Report sampled RSS as observation, never as proof of a
   hard OS memory ceiling; unavailable hard memory enforcement must be explicit.
   Existing commands, approvals and production guards remain in place.

5. **Atomic publication and recovery.** Use D01 staging, checksums, locks and ledger
   with the original immutable request. A failed/partial acquisition or stage must
   not publish completion. Cleanup is limited to this attempt's private staging.
   Resume restores both work progress and spent budgets; any changed source,
   selection or budget is a new reviewed plan, not an in-place allowance reset.

## Acceptance before marking implementation verified

- Promote all four adversarial cases and the explicit cache/record refusal
  assertions without xfail or weakened rejection requirements; preserve the
  observation tests, positive control and original logs/XML/hashes.
- Bounded loopback-only HTTP fixtures: redirects, metadata, retry bodies, changed
  ETags, invalid ranges and ignored ranges count against the same durable budget.
  No public network hosts or real acquisition will be contacted.
- Kill/restart near transfer/request/storage/deadline limits in fresh processes;
  prove no fresh allowance. Concurrent workers/processes must not over-reserve.
- Same-length cache corruption, digest mismatch, incomplete original and corrupt
  journal must fail closed with no completed artifact.
- Exceed records, decompression ratio/bytes, scratch/final/aggregate prepare caps;
  observe bounded reads/retention and no false completion. Oversized child output
  must be spooled/stopped under a measured cap, including stderr and descendants.
- Execute selected rows/row groups from authored files; verify original locators,
  selected-artifact identities and deterministic fresh-process continuation.
- Exercise actual registered public plan/fetch/verify/prepare commands with
  isolated authored fixtures. Run acquisition, transport, prepare, D01 and relevant
  D06/D03 regressions plus format/lint/type checks with uv offline and locked pins.
- Record source/import/environment, exact commands/exits, resource observations,
  before/after evidence and a defect/code/test/result map. Linux, real source
  compatibility and operator execution remain separately NOT RUN unless tested.

No trainer, evaluator, queue or parallel artifact/accounting service will be
created. Frozen research/scoring contracts are not amended by this plan. The
complete offline platform rerun remains after all separately approved stages.

**Requested approval:** implement D02 under this plan, using authored offline and
bounded loopback fixtures only. D03 approval does not authorize this repair.
