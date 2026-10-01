# FinePDFs b1 archive and b2 processing-capacity gate (2026-10-01)

**FINEPDFS B2 REQUIRES B3 FOR SCRATCH CAPACITY.** The authorized b1 scratch
archive is complete and verified. b2 remains a valid, complete local source,
but the remaining scratch capacity cannot cover the planner's processing
allowance. The continuation's **Task 4 STOP** applies before the generic
scheduler repair and real b2 retry. No b3 plan or authorization was minted.

This continues [the original investigation](FINEPDFS-SCRATCH-ACCOUNTING.md),
which remains historical evidence. The archive is now an implemented path;
the source-growth scheduler repair is not claimed as implemented.

## Archive and history contracts

Original: `C:\XLM-scratch\finepdfs\bench-b1`.

Archive: `C:\XLM-scratch-history\finepdfs\bench-b1`.

All five regular files, totaling **2,907,965,893 B**, retain their relative
paths, sizes, SHA-256 and last-write timestamps. The whole directory moved
by same-volume `os.rename` after resolved-path, plain-path, destination
nonexistence and device checks. There is no copy/delete fallback, junction or
symlink. Both b1 and b2 source paths still refer to the same NTFS file.
The archive still uses physical disk space; it is outside active scratch
accounting, not exempted from physical occupancy.

The new `source_archive.py` records a durable intent before moving, verifies
every file after moving, then writes a receipt exclusively. A repeat of the
same API call recovers a completed move whose receipt publication was
interrupted. It refuses different destinations/bounds, modified files,
unexpected entries, active-root destinations and ambiguous duplicate roots.
Enumeration is limited to 10,000 entries; this real operation's explicit
byte bound was 2,907,965,893 B per inventory pass.

Durable operator records:

- `G:\XLM\plans\finepdfs\benchmarks\b1\scratch-archive.intent.json`
- `G:\XLM\plans\finepdfs\benchmarks\b1\scratch-archive.json`
- `G:\XLM\plans\finepdfs\benchmarks\b2\b1-archive-capacity-check.json`

Archive receipt digest:
`e5ac48be7724749c953c405a6e84c437f868fa4cff26f4db977b32dcc05afb83`.
Post-check digest:
`7c2f15f26795969f33d2e7e22b3b1bfe859b68f504156dfe64a4229aecfc429a`.
Metadata-only copies are in
[`evidence/FINEPDFS-SCRATCH-ARCHIVE`](../evidence/FINEPDFS-SCRATCH-ARCHIVE).

Historical performance/adoption receipts bind source and benchmark identities,
not a promise that working bytes remain forever at the active scratch path.
However, `benchmark adopt --donor b1` previously resolved that path directly.
It now resolves the explicit archive receipt, checks its benchmark/source
identity and re-verifies the complete archived inventory before adoption.
Existing independent donor-state and linked-source SHA verification remains.
Adoption and archive use the source run lock; a benchmark with archive intent
or receipt refuses execution instead of recreating an active workspace.
That check is repeated under the run lock to cover archival during admission.

This preserves future donor reuse without rewriting the existing b2 adoption
receipt. All **12** existing b1/b2 JSON/JSONL operator files were compared by
SHA-256, size and mtime before and after: unchanged. No performance or events
file was added or modified during this continuation. Reversal remains a
separately coordinated metadata-and-directory operation; do not move files
back while leaving an active archive receipt pointing elsewhere.

## Capacity and the mandatory stop

Measured with the unchanged `ScratchBudget.occupied()` implementation:

| Quantity | Bytes |
| --- | ---: |
| Active usage before archival | 5,678,988,129 |
| Archived b1 logical footprint | 2,907,965,893 |
| Active usage after archival | 2,771,022,236 |
| Existing authorized scratch cap | 5,542,772,736 |
| Remaining capacity | **2,771,750,500** |
| Adopted input | 2,771,021,138 |
| Adopted state | 1,098 |
| Historical partial b1 documents | 136,943,760 |
| Historical b1 progress (lock has zero length) | 99 |
| b2 authorized canonical-text ceiling | 5,360,949,571 |
| b2 per-file durable/output ceiling | 16,264,671,878 |
| b2 per-file source ceiling | 5,542,772,736 |
| Planner's processing-output allowance | **10,721,899,142** |
| Deficit against that allowance alone | **7,950,148,642** |

The historical prefix gives **136,943,859 B** of observed working output.
It is not a measured whole-file b2 requirement and is not extrapolated.

`source_plan.plan_limits` computes:

```text
durable_per_file = max_file + ceil(canonical_per_file * CANONICAL_FILE_OVERHEAD)
CANONICAL_FILE_OVERHEAD = 2.0
processing allowance = 16,264,671,878 - 5,542,772,736 = 10,721,899,142
```

That is the existing plan-derived conservative output allowance to budget,
not a newly measured output size. Input/state plus this allowance alone need
**13,492,921,378 B** in the active root. Additional state/progress/atomic
metadata overlap must also be accounted for when defining a safe b3 cap.
No exact sufficient b3 cap is certified here.

There is also an enforcement gap: `PROCESS_LIMIT_KEYS` does not pass canonical
or durable ceilings to `adapt_source_file`; `StreamingJsonlWriter` has no disk
budget, and benchmark `on_done` reconciles the completed documents without
calling production `seal_unit`'s canonical/durable ceiling checks. The ledger
has a 536,870,912 B uncompressed bound, but its compressed artifact and
summary/progress storage still require disk accounting. The 2x planner factor
is an allowance, not a proof of every JSON/metadata expansion. There is no
explicit, enforced processing-growth reservation or complete scratch-output
upper bound on this benchmark path today.

Consequently, claiming that the 2.77 GB remaining capacity safely covers the
authorized processing envelope would be unsupported. This does **not** prove
the actual final file would exceed 2.77 GB; it proves the requested conservative
reservation cannot fit and current code cannot guarantee the smaller limit.
The Task 4 stop requires **b3 with larger scratch capacity**, plus the pending
generic growth accounting and bounded output enforcement, before execution.
Merely moving b1 or fixing the source reservation does not establish readiness.

## Scheduler, physical safety and authorization

The original scheduler defect remains: `essential_web_local.run_pipeline`
passes `limits.max_file_bytes` to `ScratchBudget.reserve` before
`_Download.run` can verify and reuse the complete input. The budget already
subtracts existing candidate bytes, but needlessly reserves possible source
growth. After archival an in-memory source-only reservation at actual file
size succeeds; it neither reserves processing space nor demonstrates b2 fit.

Required future behavior (not implemented in this continuation):

| Class | New source growth | Other required accounting |
| --- | --- | --- |
| `sealed_skip` | 0 | Existing leftovers stay occupied. |
| `local_complete_reuse` | 0 after complete identity/hash verification | Existing input and bounded processing/metadata growth count. |
| `local_processing_retry` | 0 for a verified durable source | Bound processing scratch and metadata. |
| `resumable_partial` | Validated total minus retained verified prefix | Count unverified tails until safely truncated; preserve drift/restart behavior. |
| `fresh_download` | Conservative full-file bound | Also bound processing and metadata. |

Physical free space after verification: **851,285,602,304 B**. The unchanged
`scratch_min_free_bytes` is **34,359,738,368 B**. Archival creates no second
corpus copy; all future new physical growth must still preserve that reserve.
No hard-link exemption or disk-free-check change was introduced.

**B2 AUTHORIZATION REMAINS VALID** for its existing unchanged operands. A new
b3 is required to authorize the larger capacity, not because archival itself
invalidated b2. Benchmark code/scheduler executable identity is not bound by
this authorization; adapter code identity is separately checked and untouched.

- b2 digest: `5a9080d424b18b39f10fd81325515ec8a870e9b81984f4e7a0f40bf4839bd298`.
- Plan hash: `25bcb8ae9121d1744d945d9a9a687f918a28b1ce54cb9a6945c0aaa277f5bffb`.
- Source SHA-256 rehashed before and after move:
  `4eeb58bc769a194f472100ed4acd1d960e35c669caecd96fd4ce6425fabca38d`.
- b2 state remains byte-identical; SHA-256:
  `75f7f0d2ebdb39eccd97b3f79b6da433dd9eadc8f57b4435d19afcbc59fc1992`.
- Post-move classification: complete local reuse **1**, other four classes **0**;
  required network bytes **0**.
- FinePDFs record/parser **33,554,432 B**; UltraX record **8,388,608 B**.
  Membership, source revision, adapter and every other limit are unchanged.

## Verification and commands

Windows 11 build 26200, Python 3.12.13, existing locked uv CPU/eval environment,
no-sync. No dependency or installation-policy changes. The real archive script
denied socket connect, DNS and bind through a Python audit hook. No source
probe, acquisition, corpus text display or network access occurred.

PowerShell environment for verification:

```powershell
$env:UV_OFFLINE='1'
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
$env:PYTHONUTF8='1'
```

Executed commands (all exits 0 unless specified):

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_source_archive.py tests/test_source_record_bound.py -n 0
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_source_archive.py tests/test_source_record_bound.py tests/test_source_run.py tests/test_mix01_source_cli.py -n 0
uv run --offline --locked --no-sync --extra cpu --extra eval python docs/implementation/evidence/FINEPDFS-SCRATCH-ARCHIVE/archive_b1.py
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_source_archive.py::test_run_rechecks_archival_after_admission -n 0
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_source_archive.py::test_archive_preserves_existing_adoption_and_future_donor_reuse tests/test_source_archive.py::test_archive_resumes_after_receipt_publication_failure tests/test_source_record_bound.py::test_a_9_06_mb_row_passes_the_finepdfs_bound_and_a_larger_row_fails_closed -n 0
uv run --offline --locked --no-sync --extra cpu --extra eval ruff check src/xlm/data/acquisition/source_archive.py src/xlm/data/acquisition/source_benchmark.py tests/test_source_archive.py tests/test_source_record_bound.py docs/implementation/evidence/FINEPDFS-SCRATCH-ARCHIVE/archive_b1.py
uv run --offline --locked --no-sync --extra cpu --extra eval ruff format --check src/xlm/data/acquisition/source_archive.py src/xlm/data/acquisition/source_benchmark.py tests/test_source_archive.py tests/test_source_record_bound.py docs/implementation/evidence/FINEPDFS-SCRATCH-ARCHIVE/archive_b1.py
uv run --offline --locked --no-sync --extra cpu --extra eval mypy --strict src/xlm/data/acquisition/source_archive.py src/xlm/data/acquisition/source_benchmark.py tests/test_source_archive.py tests/test_source_record_bound.py docs/implementation/evidence/FINEPDFS-SCRATCH-ARCHIVE/archive_b1.py
```

- Initial 23 passed; related selection 37 passed in 18.49 s; added archive/run
  race regression 1 passed; three import-cleanup regressions 3 passed. **38
  distinct focused cases**, including 12 archive cases; zero skipped.
  Authored fixtures only; production/benchmark tests use isolated loopback HTTP,
  not a dataset endpoint. Pytest reported a cache-directory permission warning;
  tests themselves passed. Full acceptance/CUDA were not run.
- Real archive and post-check: exit 0, **14.359 s**, five files preserved;
  source-only capacity diagnostic and history identity checks passed. No peak
  RSS or processing throughput measurement was taken. This script is a one-time
  evidence capture and deliberately refuses a changed initial inventory.
- Ruff check and format: all five files pass. Format was applied to new files
  during development. Strict mypy passed on the two product files first;
  expanding it to tests/evidence initially found five implicit-import errors
  (exit 1). Imports were corrected without weakening checks; the final five-file
  command passes. The only record-bound test edit imports `RecordLimitError`
  directly from its defining module. Runtime policy is unchanged.

| Requirement | Status | Scope |
| --- | --- | --- |
| Explicit archive metadata and donor resolution | IMPLEMENTED / VERIFIED | Synthetic recovery/refusal tests plus real archive |
| Preserve b1 history and b2 adopted bytes | VERIFIED | File hashes, sizes, mtimes, b2 state and source hash |
| Post-archive active occupancy and classification | VERIFIED | Metadata-only durable post-check |
| b2 conservative processing capacity | BLOCKED | Allowance exceeds capacity by 7,950,148,642 B before extra metadata |
| Generic source/processing reservation repair | NOT RUN | Task 4 STOP before Task 5 |
| New scheduler reservation tests | NOT RUN | No scheduler implementation claimed |
| Existing record policies and source-run reuse | VERIFIED | Related focused suites |
| b2 real retry and whole-file metrics | NOT RUN | Capacity gate failed; no success receipt |
| New b3 plan/authorization | NOT RUN | Requires capacity/enforcement repair and explicit review |
| Range, production, C05, tokenizer, training, external network, push | OUT OF SCOPE | None executed |

## Closeout

The local commit contains the archive implementation, donor integration,
focused tests, both investigation reports, copied archive evidence, additive
status entries and the relevant runbook update. The original 101 lines of
unrelated dirty status work and all unrelated untracked files are left unstaged.
No push. The old UltraX range command remains
`--max-input-bytes 134217728` in its unchanged historical report.

Next operator command: `git show --stat HEAD` to review the archive commit.
Next implementation prompt: implement generic verified-source growth and
processing/metadata reservations with write-time enforcement; derive a b3 cap
that covers that envelope, preserve all scientific/record policies, prepare
b3 and stop at its digest for operator authorization. Do not rerun b2 or run
the range benchmark. FinePDFs is not ready for the range benchmark.
