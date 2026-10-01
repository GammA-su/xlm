# FinePDFs p01 record-limit recovery (2026-10-01, offline)

Verdict: **FINEPDFS P01 RECOVERY READY FOR OPERATOR DIGEST REVIEW.**

Repair plan **p02** is prepared and is **not authorized**. Its digest is
`98209572a1939769dc3032562d2887bb8da4912b4936c1845f450a1bdb9799ad`. p01, its
authorization, its failed-run receipt, its two sealed units and every benchmark
record are unchanged.

This session used no network. It did not authorize or run p02, re-run
production, seal, run C05, fit a tokenizer, train or push. No corpus text was
printed or stored.

## 1. The failed unit

| Field | Value |
| --- | --- |
| Plan / unit | p01 `de220b01…3890`, unit `f00000` |
| Inventory rank | 0 of 100 (inventory `7fc50884…09cd`, seed 20260918) |
| File | `data/eng_Latn/train/000_00022.parquet` |
| Declared size | 3,106,919,244 B (inventory and `x-linked-size`) |
| Retained raw | `G:\XLM\acq-raw\finepdfs\source\data\eng_Latn\train\000_00022.parquet` + `.identity.json` |
| SHA-256 | `b33dba5fa5a3086d34c0decc76ca6e3f1f794850037eec019005557090d7c3c2`; re-hashed offline today; equals the sidecar and the repository `x-linked-etag`; `sha256_independently_verified: true` |
| Provider identity | `HuggingFaceFW/finepdfs-edu` @ `9cfabe21…bde`, xet `70a2977b…09d` |
| Failure | `RecordLimitError` at row 52,794: `encoded_bytes=35,618,267`, bound 33,554,432 (`source_local.py:413 _replay`). Failed receipt: `performance-00.json`, digest `a2fc78eb…0859` |

The verified download also remains in scratch as
`C:\XLM-scratch\finepdfs\p01\f00000.parquet.part`, with a complete state file
(3,106,919,244 B, same SHA-256). The partial staging from the failed attempt is
under `G:\XLM\canonical\finepdfs\.staging\p01\f00000\`. Neither of these is
authoritative output, and both were left untouched.

## 2. Whole-file scan (no text)

Script: [`scan_failed_source.py`](../evidence/FINEPDFS-P01-RECORD-LIMIT-RECOVERY/scan_failed_source.py).
Output: [`scan_f00000.json`](../evidence/FINEPDFS-P01-RECORD-LIMIT-RECOVERY/scan_f00000.json).
The script uses the certified 9-column projection and `located_record`. These
are the exact bytes the bound compares.

- The file has 234,423 rows in 235 row groups. Every row was scanned.
- **The maximum is 35,618,267 B at row 52,794 (row group 52).** That is the
  failing row. It is the true maximum and the only row above 32 MiB.

| Percentile of encoded row bytes | p50 | p90 | p99 | p99.9 | p99.99 | p99.999 |
| --- | --- | --- | --- | --- | --- | --- |
| Bytes | 6,765 | 48,295 | 311,956 | 1,302,690 | 4,070,341 | 17,183,849 |

| Rows above | 4 MiB | 8 MiB | 16 MiB | 24 MiB | 32 MiB | 40 MiB | 48 MiB | 64 MiB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Count | 23 | 6 | 3 | 2 | 1 | 0 | 0 | 0 |

All 23 rows above 4 MiB have `extractor = rolmOCR` and `is_truncated = true`.
The certified adapter **rejects every one of them**. A larger bound therefore
changes no accepted document. It only lets those rows reach the rejection
ledger instead of failing the whole unit. Row group 52 decodes to 99,378,263 B.

Four whole FinePDFs files have now been measured:

| File | Largest row | Basis |
| --- | --- | --- |
| 000_00037 | 16,257,336 B | Sealed receipt; selected-record line, which includes about 610 B of locator |
| 000_00093 | 22,828,005 B | Sealed receipt; selected-record line |
| 000_00083 | 24,828,818 B | b1 row scan |
| 000_00022 | 35,618,267 B | This row scan |

## 3. What the record and parser bounds protect

- **`max_record_bytes`** bounds one decoded row after projection and canonical
  JSON serialization (`located_record`).
  - It is checked per row in `selected_payloads` and in the worker's
    `adapt_row_group`. It is checked again as payload ≤ bound + 8192.
  - It is a per-record memory bound. Each worker briefly holds about 5–7 copies
    of one row: the Arrow batch slice, the Python value, the canonical line, the
    payload, the parsed record, and the document or rejection line. The payload
    then also sits in the group result until the coordinator hashes it.
- **`max_parser_bytes`** in this whole-file local path is only pyarrow's
  `thrift_string_size_limit` and `thrift_container_size_limit`. These cover
  footer and page-header metadata.
  - `layout_record` applies only the decompression-ratio refusal. Row-group
    `total_byte_size` is **not** bounded by the parser bound here: group 52 is
    about 100 MB.
  - Data pages are decompressed regardless of the Thrift limits. p01 decoded
    row 52,794 under the 32 MiB parser bound before the record bound fired.
    This scan decoded every row under the same bound.
- **Conclusion:** in whole-file mode the two bounds are independent.
  - The coupling `record ≤ parser` existed in two places: the planner's
    `record_bound` and the generic `AcquisitionLimits` validator.
  - In selected-record (range) mode the parser bound also caps the buffered
    group or range a record is decoded from. There the invariant stays, enforced
    by `AcquisitionPlan` for `SELECTED_RECORDS`.
  - Source-specific record bounds now have a hard ceiling,
    `MAX_SOURCE_RECORD_BYTES` = 2 × parser = 64 MiB.
  - **The parser bound stays 32 MiB.**

## 4. New bound: `finepdfs-record-v2` = 48 MiB (50,331,648 B)

- The v1 rule was a bound of at least 1.35× the largest observed row.
  1.35 × 35,618,267 B = 48,084,660 B = 45.9 MiB.
- 40 MiB gives only 1.18× headroom.
- **48 MiB gives 1.41×** and is the smallest of the listed candidates that
  satisfies the rule.
- For p02 alone, the whole file is scanned, so any bound ≥ 35,618,267 B is
  certain to succeed. The headroom serves later top-up files.
- These bounds are unchanged:
  - generic 8 MiB;
  - UltraX 8 MiB;
  - the Essential-Web code (its frozen file identities are not touched);
  - parser 32 MiB.

## 5. Measured resource impact (offline, 48 MiB, p01 limits otherwise)

Output: [`adapt_48mib_serial_parallel.json`](../evidence/FINEPDFS-P01-RECORD-LIMIT-RECOVERY/adapt_48mib_serial_parallel.json).
The production `adapt_source_file` was run on the retained file, into private
scratch only.

| Run | Wall | Peak tree RSS | Max worker RSS |
| --- | --- | --- | --- |
| serial | 100.97 s | – | – |
| W4 #1 | 33.0 s | 1,912,561,664 B | 1,095,000,064 B |
| W4 #2 | 35.4 s | 1,809,784,832 B | 1,120,374,784 B |

- **All three runs are byte-identical:**
  - documents `2a3e2dd4…004a`;
  - ledger file `3f4a2ce4…a93a`;
  - uncompressed ledger `75cc6345…bc81`;
  - selected records `8a917aa6…9d29`.
- The output has 152,614 documents, 81,809 rejections and 1,952,122,354
  canonical bytes, which is **488,030,588 estimated tokens**.

Against the plan's frozen contracts:

| Contract | Ceiling | Measured |
| --- | --- | --- |
| Sampled memory | 6 GiB | 1.91 GB |
| Max group result | – | 101.9 MB |
| Peak buffered results | – | 359 MB |
| Decoded bytes | 22.17 GB | 6.01 GB (largest batch 69.1 MB) |
| Canonical bytes | 3.57 GB | 1.95 GB |
| Output growth | 7.14 GB | documents 2.15 GB |
| Ledger | 512 MiB | 47.8 MB |
| Rows | 440,814 | 234,423 |
| File | 5.54 GB | 3.11 GB |

- A full 48 MiB row adds about 14 MiB × ~7 copies per worker over the observed
  maximum, which is well inside the sampled ceiling. The ceiling still fails
  closed with `ProcessingMemoryError`.
- p02 scratch cap is 12,684,227,976 B. Present occupancy is about 5.88 GB:
  bench-b2 plus the p01 duplicate `.part`. A retained unit makes no scratch
  reservation.
- The transfer meter conservatively charges the retained 3,106,919,244 B
  against the 4,156,555,264 B ceiling. The unit receipt records 0 B transferred.
- No resource limit other than `max_record_bytes` changes, so none needed
  re-binding.

## 6. Plan semantics audit (before this change)

1. **Can p01 complete unchanged? No.**
   - `max_record_bytes` is in p01's `plan.json`, so it is part of the digest.
   - It is also in its `AcquisitionLimits`, which feed the authorized plan hash.
   - The runner passes `record["limits"]` to workers.
   - A retry deterministically fails again at row 52,794. Changing the bound
     would mean rewriting p01.
2. **Could a new plan repair only f00000? No mechanism existed.**
3. **Does ordinary top-up start at cursor 3? Yes, so it would skip rank 0.**
   It is refused anyway: `build_plan` refuses an incompletely sealed
   predecessor, and the driver's `predecessor()` requires `TOP_UP`.
4. **Could the seal accept an incomplete plan? Latent defect.**
   - `first_pass_seal` required only `SUFFICIENT`.
   - `sufficiency` returned `SUFFICIENT` whenever the bytes sufficed, even with
     an unsealed unit. That would silently drop the failed unit. The seal's own
     docstring promised to refuse a partial pass.
   - It could not fire here: 920,419,745 estimated tokens < 990,000,000.
5. **What identity binds a repaired unit? Nothing defined one.**

## 7. Recovery chosen: B, a generic repair plan with explicit supersession

- **A** (raise the bound under p01) would rewrite an authorized plan. Rejected.
- **C** (skip rank 0 and top up) is content-dependent exclusion. A file would be
  dropped because it holds one oversized rolmOCR row that the adapter rejects
  anyway. It is also refused by the existing top-up rule. Rejected.
- **D** is folded into B. The repair record *is* the explicit supersession of
  the unsealed ranks.

The new mechanism is generic. Nothing in the code names FinePDFs, p01, f00000
or rank 0.

- **`source_plan.build_repair_plan` and `Repaired`.** The input is an
  authorized latest plan with failed-run receipts whose root failure names an
  unsealed unit. The output is the next sequence, covering exactly the unsealed
  ranks under today's limits.
  - At least one per-unit limit must change (`UNIT_LIMIT_KEYS`); otherwise
    "resume the plan instead".
  - It keeps the predecessor's `next_cursor`.
  - `acquired_before` = the predecessor's acquired bytes + its sealed bytes.
  - Retained verified bytes are bound as `expected_file_digests`. These enter
    the plan hash.
  - The `repair` block binds: the predecessor digest, accounting digest, sealed
    ranks, failed receipts, changed limits (from → to) and retained SHA-256.
- **`source_run`:**
  - `repairs_of` and `resolution`: a rank is resolved when it is sealed in its
    own plan or in that plan's repair. A rank sealed twice is refused.
  - `sufficiency`: an unresolved rank makes the pass `INCOMPLETE` even when the
    bytes suffice, and the record lists `unresolved_ranks`. `repairs` and
    `unresolved_ranks` appear only when present, so existing records keep their
    bytes.
  - `first_pass_seal` records `repair_of` and `repaired_ranks`. It refuses a
    file sealed in more than one unit, and a stale authorization.
  - `load_authorized` refuses to run a repaired plan.
  - `prepare_units` refuses a retained source whose SHA-256 differs from the
    plan's bound digest.
- **Driver:** `scripts/mix01_source.py plan-repair --plan N` (offline) gathers
  the evidence, re-hashes the retained sources and prints the digest with a
  STOP.

## 8. Identity boundary

**Unchanged and verified this session:**

- p01 `plan.json`, `authorization.json`, `acquisition.plan.json`,
  `events.jsonl` and `performance-00.json`. Their mtimes are 17:47–17:57 and
  [`history_sha256.txt`](../evidence/FINEPDFS-P01-RECORD-LIMIT-RECOVERY/history_sha256.txt)
  records their SHA-256.
- Sealed f00001 (receipt `7c0eb19c…314e`) and f00002 (receipt `c2b7602a…4273`).
  Content verify re-hashed 2/2.
- Transport policy `f3a52411…`, sizing `4709fb84…`, inventory `7fc50884…`,
  revision `9cfabe21…`, adapter `finepdfs_en`, b1/b2/b3 records.
- Today's planner with the v1 bound rebuilds p01 digest `de220b01…3890`
  exactly (`check_against_inputs` passes).
- UltraX: recomputed sufficiency equals both its file and its seal, and a
  re-seal is an identical no-op (`efdb99db…2f1f`).

**New identities:**

- Record policy `finepdfs-record-v2`.
- A fresh plan 1 under v2 would have digest `1f44a4f5…4da3`. This is not
  stored; it is shown only as proof that the new bound is a new identity.
- **Repair plan p02:**
  - digest `98209572a1939769dc3032562d2887bb8da4912b4936c1845f450a1bdb9799ad`;
  - plan hash `32962b1c…4706`;
  - selection hash `2078501c…04ee`;
  - the only changed limit is `max_record_bytes` 33,554,432 → 50,331,648;
  - expected transfer 0 B;
  - next cursor 3.
- `resume-check --plan 2` classifies f00000 as `local_processing_retry`, with
  worst-case network bytes 0.

**Sufficiency arithmetic (estimate):**

- Now: 3,681,678,984 canonical bytes, which is 920,419,746 estimated tokens
  (the per-unit floors sum to 920,419,745). `INCOMPLETE`, unresolved `{2: [0]}`.
- After a successful p02: 5,633,801,338 canonical bytes, which is 1,408,450,334
  estimated tokens, against the requirement of 3,960,000,000 canonical bytes.
  The expected status is `SUFFICIENT` with no top-up.
- Only the p02 receipt is authoritative.

## 9. Tests and checks (exit 0 unless stated)

Environment: Windows 11, Python 3.12.13, uv locked/offline. Thread variables
were set to 1 and `TOKENIZERS_PARALLELISM=false`. Basetemp was `C:\xt\…`.

- `uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_source_repair.py tests/test_source_record_bound.py -n 0 -p no:cacheprovider --basetemp=C:/xt/rp`: **29 passed**.
- Related fast selection, 23 modules (acquisition plan/bounds/fetcher/verifier,
  production ingest, window/row-group sampling, selected-record concurrency, HF
  range transport, source plan/run/record-bound/repair/rowgroups/growth/archive,
  mix01 CLI/admission, Essential-Web fast/recovery/bulk), with
  `-n 8 --dist=worksteal --max-worker-restart=0` and serial markers excluded:
  **424 passed**. The same files' serial-marked tests with `-n 0`: **3 passed**.
- `ruff check` and `ruff format --check` on the changed files: passed.
- `mypy --strict` on `plan.py`, `source_plan.py`, `source_run.py`,
  `mix01_source.py` and both test files: **no issues** (compiled mypy ran).
- `git diff --check`: clean.
- Real-store read-only regression,
  [`real_store_check.py`](../evidence/FINEPDFS-P01-RECORD-LIMIT-RECOVERY/real_store_check.py):
  all checks true.
- The new tests use authored loopback fixtures, not live data. They cover:
  - the old plan reproduces under its own bound;
  - a new bound is a new identity;
  - the generic and UltraX bounds are unchanged;
  - record > parser is accepted for whole files only;
  - a 33 MiB row passes v2 under a 32 MiB parser bound;
  - the repair plan's shape and determinism;
  - seven refusals for missing evidence, plus unchanged-limits and
    other-source/inventory refusals;
  - top-up refused over an incomplete plan;
  - the repaired plan cannot run;
  - an unauthorized repair cannot run;
  - an offline repair uses zero network and zero transfer;
  - row-group-parallel output equals serial;
  - p01 bytes are untouched;
  - the seal binds `repair_of` and `repaired_ranks`, is idempotent, and counts
    each file once;
  - top-up after a repair starts at cursor 3;
  - an incomplete-but-sufficient pass is refused;
  - a retained source with the wrong SHA-256 is refused.
- **Not run:** the full suite, network tests, CUDA tests, and the real p02 run.

## 10. Operator commands (future; STOP at review)

```powershell
$env:XLM_DATA_ROOT='G:\XLM'
$env:XLM_HOME='G:\XLM\xlm-home'
$env:XLM_SCRATCH_ROOT='C:\XLM-scratch'
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:PYTHONUTF8='1'
Get-Content -LiteralPath 'G:\XLM\plans\finepdfs\p02\plan.json'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py resume-check --source-key finepdfs --plan 2
# STOP: review PLAN DIGEST 98209572a1939769dc3032562d2887bb8da4912b4936c1845f450a1bdb9799ad
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py authorize --source-key finepdfs --plan 2 --digest 98209572a1939769dc3032562d2887bb8da4912b4936c1845f450a1bdb9799ad --operator '<operator-name>'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py run --source-key finepdfs --plan 2 --offline
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py verify --source-key finepdfs --plan 2
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py sufficiency --source-key finepdfs
# Only if SUFFICIENT:
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py seal --source-key finepdfs
```

`run --offline` refuses any unit that would need a download, so the retained
bytes are reused. `run --plan 1` is now refused.

## Open limitations

- A unit that fails for a reason no limit change can fix, such as a corrupt
  upstream file, has no abandonment record yet. Such a pass stays `INCOMPLETE`
  by design.
- The 1.35× headroom rule is an engineering margin over four files, not a
  statistical bound. A later top-up file with a larger row would fail closed
  again and be repaired the same way.
- `status` still prints p01's restart class `local_processing_retry` for
  information. `run --plan 1` refuses.
