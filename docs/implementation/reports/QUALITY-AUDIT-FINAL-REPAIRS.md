# Global quality audit Phase A: final repairs for recheck blockers I04, I08, I10, I11

Date: 2026-10-04. Base `1883093`; one focused commit on top.

**Verdict: the four remaining blockers are repaired. Ready for independent re-audit.
The real audit was NOT run.** All evidence is authored or offline. There was:

- no network access;
- no access to the real `G:` corpus or to `X:`;
- no real C05, tokenizer fit or training;
- no review text materialized;
- no push.

The I11 probes re-sign the AUTHORED public membership of the synthetic C05 fixture
with its authored key.

These historical files are unchanged:

- `docs/implementation/reports/QUALITY-AUDIT-INDEPENDENT-96F38F3.md`
- `docs/implementation/evidence/QUALITY-AUDIT-INDEPENDENT-96F38F3/`
- `docs/implementation/evidence/QUALITY-AUDIT-RECHECK-1883093/`

**Disclosure:** the recheck directory `QUALITY-AUDIT-RECHECK-1883093/` is present but
EMPTY in this worktree, so Astra's recheck probes could not be run. They were rebuilt
from the reported failures as `tests/test_quality_final_repairs.py` (83 tests).

## Repairs

- **I04, one authoritative final gate.** All work runs under the supervisor, and the
  receipt is only staged in memory. Then:
  1. The monitor and every worker are shut down.
  2. `Guard.final` reconciles every recorded failure (before or during shutdown,
     during aggregation, during staging).
  3. It then checks with FRESH measurements: deadline margin, whole process-tree RSS,
     free space on every watched volume, and the output byte total including the
     receipt.
  4. Only then is the receipt written, as the last write.
  5. If any failure is recorded afterwards (for example injected during the write),
     the receipt is removed and the command refuses.

  The old `gate` method was removed. `report` and `materialize-review` return
  success only after `Guard.final`. The CLI prints no success JSON on refusal.
- **I08, code and example context.** Markup structure is judged only outside
  code/example regions: fenced blocks, Markdown indented code blocks and inline code
  spans. Tags inside them are counted as `code_example_tags` with the
  `markup_code_example` flag (an interpretation, never an action). Full HTML now needs
  document structure, not a mention: an HTML doctype, a closed `<html>` element, or
  closed `<head>` and `<body>`. Prose mentioning `<html>` is `markup_light`. Raw tag
  counts outside examples are unchanged. The policy version is now v3.
- **I10, strict typed envelope.** `quality/envelope.py:OperationalEnvelope`
  (pydantic) is strict:
  - exact field set and exact types (no bool-as-int, ints refused for float fields,
    no NaN/Infinity);
  - workers ∈ {1,2,4,8,16}, and the queue must be ≥ workers and exactly the
    implementation bound (2 × workers, or 1 inline);
  - ranges for RSS (0, 16 GiB], deadline (0, 7 days], document ceiling, output,
    reserve, chunk, verify threads, pending commits, supervisor interval and
    review-per-stratum;
  - the publication margin must be below the deadline.

  Receipt validation REBUILDS this model for the effective envelope and for every
  producer envelope; the real implementation also validates its own envelope before
  running.
- **I11, full digests.** Kept-row identity stores the COMPLETE 32-byte SHA-256 of the
  `doc_id` and the COMPLETE 32-byte C05 content digest (`IDENTITY` u1[32] fields).
  The worker compares all 32 bytes: the raw-row SHA-256 first, then the full
  `canonical.digest` of the row. No prefix comparison remains anywhere. Review and
  materialization already used full 64-hex SHA-256 strings.

## Results

| Selection | Result |
|---|---|
| Astra's original 49 probes (file unchanged, `-n 0`) | 49 passed, exit 0 |
| New repair-edge probes (`tests/test_quality_final_repairs.py`) | 83 passed (inside the focused run) |
| Focused: detectors + audit + hardening + final repairs (`-n 8`, not serial) | 225 passed, exit 0 |
| Serial selection (`-n 0 -m serial_exclusive`) | 1 passed, exit 0 |
| `ruff format --check` / `ruff check` / `mypy --strict` (20 files) | exit 0 / 0 / 0 |
| `git diff --check` | exit 0 |

The focused run covers:

- worker determinism 1/2/4/8;
- receipt and envelope tests;
- overlay identity (5 digest-mismatch classes);
- supervisor finalization: deadline and RSS at finalization, disk failure during
  shutdown, monitor failure after the last result, failure during receipt
  publication, CLI refusal, and the report gate;
- I08 fixtures: inline `<html>`/`<body>`, an indented snippet, fenced HTML/XML, a real
  page, and prose.

One regression found and fixed during the run: Astra's `free_reserve_bytes=10**18`
probe first hit the new reserve bound. The bound is now 2^62, because a huge reserve
is valid and correctly refuses as "free space".

## Performance (authored 256 MiB benchmark; one result digest for all worker counts)

| Workers | MB/s | Docs/s | Peak tree RSS |
|---:|---:|---:|---:|
| 1 | 6.3 | 1,109 | 0.16 GiB |
| 2 | 12.5 | 2,187 | 0.41 GiB |
| 4 | 22.2 | 3,893 | 0.73 GiB |
| 8 | 33.2 | 5,820 | 1.31 GiB |

There is no slowdown versus `1883093` (30.1 MB/s at 8 workers; the difference is
run-to-run variation). Full identity arrays raise the overlay's parent memory
estimate from about 0.6 to about 1.1 GiB for 15.1 M rows.

**Real-corpus PROJECTION** for 104.5 GB:

- 0.87 h optimistic;
- 1.2–1.9 h likely (15–25 MB/s);
- 4.6 h conservative;
- plus about 3 min for overlay staging and under 1 min for aggregation.

The planning allowance is 1.5–2.5 h; the 12 h deadline covers the conservative case.

## Operator commands (only after the independent re-audit passes)

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.quality audit --manifest <"manifest" path inside G:/XLM/c05/p0002.proof.json> --output G:/XLM/quality/audit-v3 --c05-proof G:/XLM/c05/p0002.proof.json --workers 8 --max-rss-gib 12 --free-reserve-gib 8 --max-output-gib 4 --max-document-mib 64 --deadline-hours 12
uv run --offline --locked --no-sync --extra cpu --extra eval python -m xlm.data.quality report --manifest <same manifest> --output G:/XLM/quality/audit-v3 --c05-proof G:/XLM/c05/p0002.proof.json --workers 4 --max-rss-gib 8 --deadline-hours 6
```

Prerequisites: `X:` detached, and the trust keys of the proof set. Use a NEW output
directory, because the detector policy is now v3.

Leftover authored scratch for the operator to remove: `F:/qfin-*`.

Next: an independent re-audit of this commit, including Astra's original 49 probes
and the recheck probes (please supply them, since the recheck directory is empty).
