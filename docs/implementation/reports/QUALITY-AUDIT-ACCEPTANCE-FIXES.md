# Global quality audit Phase A: acceptance fixes (I04, historical RSS probe, I10)

Date: 2026-10-04. Base `aa7b580`; one focused commit on top. I08 and I11, accepted by
Astra's recheck, are unchanged.

**The real audit was NOT run.** All evidence is authored or offline. There was:

- no real `G:` corpus and no `X:`;
- no real C05, tokenizer or training;
- no review text materialized;
- no push.

The only network use was installing the locked Python packages into a scratch
environment. Exact commands are in
[evidence/QUALITY-AUDIT-ACCEPTANCE-FIXES/COMMANDS.md](../evidence/QUALITY-AUDIT-ACCEPTANCE-FIXES/COMMANDS.md).

These historical files are unchanged:

- `reports/QUALITY-AUDIT-INDEPENDENT-96F38F3.md`
- `evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/` (the probe file has the same SHA-256)

`evidence/QUALITY-AUDIT-RECHECK-1883093/` and
`evidence/QUALITY-AUDIT-FINAL-RECHECK-AA7B580/` **do not exist** in this checkout or
in any remote ref. Astra's final targeted edge probes could not be run, so they were
rebuilt from the reported failures as `tests/test_quality_acceptance_fixes.py`.

## 1. I04: two-phase completion protocol (`runner._publish_receipt`, `outputs.OutputTree`)

The sequence is:

1. All scan and aggregation work finishes.
2. The receipt is built entirely in memory from measured facts.
3. `OutputTree.stage` writes it ONLY to the owned `receipt-staging/` directory. It is
   flushed and fsynced, and the staging directory is fsynced. The monitor is still
   running throughout.
4. The supervisor is stopped and joined.
5. The staged bytes are checked against the in-memory receipt.
6. `Guard.final` reconciles every failure ever recorded, then measures again: the
   absolute deadline (with the 0.5 s margin), process-tree RSS, free space on every
   watched volume, output bytes (staged receipt included) and the supervisor failure
   state.
7. If and only if all of these pass, ONE atomic `os.replace` moves the receipt into
   place and the output directory is fsynced. Nothing else happens between the
   pre-publication gate and this rename.
8. The post-publication gate runs immediately and measures the same things again.
   If it fails, the receipt is removed, the directory is fsynced and the command
   refuses.

On any failure the staged copy is discarded. `run_audit` returns, and the CLI prints
success JSON, only after step 8.

**Staging location.** The receipt is staged in a subdirectory rather than as a `.tmp`
sibling. The frozen probe `test_interruptions_are_incomplete[receipt]` intercepts
`canonical.write_atomic` for a path named `quality-audit-receipt.json`; staging
through that function, in a subdirectory, keeps that probe meaningful. The rename
crosses directories on the same volume.

**Windows.** Directory fsync is not available through Python on Windows and is a
documented no-op there; NTFS journaling covers the rename.

**Fault injection.** 49 cases in `test_i04_late_failure_never_publishes`.

- Seven points:
  - receipt serialization;
  - the staging write;
  - the staged file fsync;
  - the staging directory fsync;
  - monitor shutdown;
  - immediately before the rename;
  - immediately after the rename.
- Seven modes:
  - a recorded RSS, disk or deadline failure;
  - every later fresh measurement violating its RSS, disk, deadline or output limit.

Every case gives CLI exit 1, refused JSON, no success fields, no receipt, an empty
staging directory, and a `report` that says incomplete.

Further tests cover:

- protocol order (staging with the monitor alive, then the gate, then the rename with
  the monitor joined, then the post gate);
- the withdrawal directory fsync;
- a partial staging write being discarded, with resume succeeding;
- foreign files in the staging directory refusing.

**Mutation check.** With the post-publication gate removed, all 8 probes that inject a
failure after the rename (the after-rename cases plus the withdrawal fsync test) fail.

## 2. Historical `test_child_rss_included`: pool-failure normalization (`scan.OrderedPool`)

`Supervisor.fail` records its reason before it terminates the workers. `OrderedPool`
now catches executor breakage in both `result()` and `submit()`: `BrokenExecutor`
(including `BrokenProcessPool`), `CancelledError`, `EOFError` and `ConnectionError`.
It then:

- re-raises the ALREADY RECORDED supervisor reason (RSS ceiling, deadline, disk,
  cancellation); or
- if no reason was recorded, raises a controlled `WorkerPoolError` ("a worker process
  terminated abnormally").

`WorkerPoolError` is a `QualityError`, so the CLI refuses cleanly. It is also a
`BrokenProcessPool`, so the frozen `test_worker_crash_refuses` still holds.

| Run | Result |
|---|---|
| Baseline aa7b580, frozen 49 probes | 48 passed, 1 failed (`test_child_rss_included`, BrokenProcessPool) |
| Fix, frozen 49 probes | **49 passed** |
| Fix, `test_child_rss_included` alone | 10/10 repeats passed |

New tests check that:

- the recorded reason wins for RSS, disk and deadline, both at pool level and through
  the CLI audit;
- a crash with no reason gives a controlled refusal.

## 3. I10: semantic receipt verification (`receipt.check_execution_envelope`, `runner._verify_envelope_facts`)

The receipt schema is now 3, and `execution` has an exact typed schema. Units are now
`unit_v3` and carry two new fields:

- `max_line_bytes`: the largest JSONL row, including its newline;
- `producer_facts`: elapsed seconds, peak process-tree RSS and minimum free bytes,
  measured at commit time.

The supervisor now records the minimum observed free bytes per watched volume.

**Static receipt checks.** The envelope is checked against the implementation and the
binding:

- `chunk_bytes`, verify threads, pending commits, supervisor interval, publication
  margin and review per stratum must equal the implementation constants;
- `max_document_bytes` must equal the binding's line ceiling;
- these rules apply to every producer envelope as well.

The envelope is also checked against the measured execution facts:

- workers equal the envelope's workers, and `1 <= peak tasks in flight <= queue_tasks`;
- `0 < peak tree RSS <= max_rss_bytes`, with at least one supervisor sample;
- `wall_seconds <= deadline_seconds`, and `scan_seconds <= wall_seconds`;
- per volume, the reserve equals the envelope's and the minimum observed free space is
  at least the reserve;
- the largest observed row is within `max_document_bytes`;
- output before the receipt plus the receipt's own size is within `max_output_bytes`;
- scanned plus resumed files equal the manifest's files;
- a fresh run's producer envelopes are exactly `[envelope]`, and a resumed run must
  contain its own envelope.

**Re-derived by `report` and `materialize-review`.** Nothing in this group is trusted
from the receipt:

- **Output bytes:** binding, units, artifacts and receipt sizes; these must equal the
  claim and the whole tree, and fit the ceiling.
- **Largest row:** re-derived from the units; it must equal the claim and fit the
  ceiling.
- **Unit facts:** every unit's facts must fit its own producer envelope. The largest
  text (`utf8_bytes` max) must not exceed the largest row, and the largest row must not
  exceed the unit's total row bytes. On a fresh run, each unit's facts must also be
  consistent with the run's peak RSS, elapsed time and minimum free space.
- **Review manifest:** it is re-read after its SHA, size and record count check. Every
  row's schema and path are checked. The total row count must match, and the rows per
  `(component, detector, coarse_bin)` stratum must be within `review_per_stratum`.

**Adversarial tests.** Each attack edits a valid receipt and re-digests it correctly,
leaving the artifacts unchanged.

- 37 attacks in total; every one is refused by `report`, by the CLI `report`, and by
  `materialize-review`:
  - 16 envelope fields changed alone, including 1-byte output, RSS and document
    ceilings, one review row per stratum, a deadline below the elapsed time, a reserve
    above the observed minimum, other worker settings, and changed constants;
  - 16 execution facts changed alone;
  - 5 envelope-and-facts pairs changed consistently.
- With the constant check bypassed and receipt plus units forged consistently, the
  review-strata re-derivation alone still refuses a one-row-per-stratum claim.
- 7 unit-fact forgeries refuse.

**Limitation (recorded).** The receipt and units are self-digested, not signed.
Consistency checks catch every single-field contradiction and the tested combined
ones. A forger who rewrites the receipt AND every unit consistently is outside what
self-consistency can detect.

## 4. Preserved behavior

The focused run (`-n 4`, worksteal) covers:

- I08 inline, fenced and indented HTML examples;
- I11 full 32-byte content SHA (5 mismatch classes plus signed overlay identity);
- worker determinism 1/2/4/8;
- strict JSON;
- source and resume re-hashing;
- output and junction safety (the 2 PowerShell-junction tests ran under the symlink
  shim);
- privacy canaries;
- review materialization verification.

The scan semantics are unchanged. Compared with aa7b580 on the same authored corpus,
every artifact is identical except for the code-bound binding digest; the
`review-manifest.jsonl` and `quality-summary.md` files are byte-identical.

## Results

| Selection | Result |
|---|---|
| Astra's original 49 probes (unchanged; Linux shim) | **49 passed**, exit 0 |
| Astra's FINAL-RECHECK-AA7B580 edge probes | **NOT RUN: directory absent**; rebuilt as 108 tests in `tests/test_quality_acceptance_fixes.py`, all pass |
| Focused quality regression (5 modules) | 331 passed, 0 failed (+2 junction tests under shim, +1 serial) |
| `test_c06_fast_hardening` (shared supervisor) | 73 passed, 2 failed: pre-existing on Linux, identical at aa7b580 (resource-tracker child) |
| `ruff format --check` / `ruff check` / `mypy --strict` (22 files) | 0 / 0 / 0 |
| `git diff --check` | 0 |
| Timing (32 MiB authored, 3 alternating rounds, median MB/s) | w1: 4.57 → 4.76; w4: 11.47 → 12.35 (noise; one `max` per chunk added) |

## Next

An independent re-audit of this commit, natively on Windows:

```powershell
uv run --offline --locked --extra cpu --extra eval python -m pytest docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/test_independent.py -n 0
uv run --offline --locked --extra cpu --extra eval python -m pytest tests/test_quality_acceptance_fixes.py tests/test_quality_final_repairs.py tests/test_quality_hardening.py tests/test_quality_audit.py tests/test_quality_detectors.py -n 8 --dist=worksteal --max-worker-restart=0 -m "not serial_exclusive"
```

Also run Astra's FINAL-RECHECK-AA7B580 probes (please supply the directory).

The operator commands in `reports/QUALITY-AUDIT-FINAL-REPAIRS.md` are unchanged, but
need a NEW output directory: both the unit and receipt schemas changed. Run them only
after the re-audit passes.
