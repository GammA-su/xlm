# Independent v4 narrow review commands

2026-09-29, `F:\Project\xlm-data-ultrax`, PowerShell. Starting HEAD
`791d3b866db7276180c2e8a93d13d1b775256baf`, branch `data/mix01-ultrax-6b`.
All commands offline; no live root or corpus text. Authored synthetic fixtures
only. Review tests live here, not in the implementation or existing test files.

Starting commands (each exit 0):

```powershell
git status --short
git rev-parse HEAD
git log --oneline -10
```

Starting status:

```text
 M docs/implementation/STATUS.md
 M docs/implementation/reports/ESSENTIAL-WEB-SELECTOR-RECON.md
?? docs/implementation/evidence/ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW/
?? docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-REVALIDATION.md
?? docs/implementation/reports/ESSENTIAL-WEB-SELECTOR-POLICY-REVIEW.md
?? docs/implementation/reports/ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW-AUDIT.md
?? docs/implementation/reports/ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW.md
```

Focused test settings (single process; no xdist controller):

```powershell
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
```

The final parser-only deadline probe does not invoke numerical libraries; it was
run without repeating these process environment assignments. For commands below,
stdout/stderr were captured with `2>&1 | Tee-Object -FilePath <artifact>` and the
PowerShell wrapper ended with `exit $LASTEXITCODE`. Artifact paths are relative
to this directory. No wrapper changes the tool exit status.

1. Existing v4 tests — exit **0**, **157 passed**, 15.40 s; `v4-focused.txt`,
   `v4-focused.xml`:

```powershell
uv run --offline --locked python -m pytest -n 0 -q -p no:cacheprovider tests/test_evidence_v4_plan.py tests/test_evidence_v4_transport.py tests/test_evidence_v4_engine.py tests/test_evidence_v4_e2e.py --junitxml=docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW/v4-focused.xml
```

2. Scientific lineage/regressions — exit **0**, **63 passed**, 1.35 s;
   `science-regressions.txt`. v3 tests validate historical identities/arithmetic,
   not additional execution obligations:

```powershell
uv run --offline --locked python -m pytest -n 0 -q -p no:cacheprovider tests/test_evidence_v3.py::test_v3_freeze_and_protocol_verify tests/test_evidence_v3.py::test_cap_map_digest_reproduces_from_freeze tests/test_evidence_v3.py::test_v2_parent_graph_preserved_and_closed tests/test_evidence_v3.py::test_adopted_observations_carry_no_budget_history tests/test_evidence_v3.py::test_scientific_identity_unchanged tests/test_evidence_v3.py::test_epoch_not_started_and_frozen_root_absent tests/test_evidence_v3.py::test_canonical_url_matches_frozen_first_network_boundary tests/test_evidence_v3.py::test_m_dry_schedules_exact tests/test_evidence_v3.py::test_t_dry_schedules_exact tests/test_evidence_v2_core.py tests/test_evidence_v2_text.py
```

3. Independent probes, at the initial two-test revision — exit **1**, **2 failed**,
   1.31 s; `review-probes.txt`, `review-probes.xml`. Tests:
   `test_completed_chunk_bytes_survive_live_parser_interruption` and
   `test_eof_after_attempt_deadline_cannot_succeed`:

```powershell
uv run --offline --locked python -m pytest -n 0 -q -s -p no:cacheprovider docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW/test_review_probes.py --junitxml=docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW/review-probes.xml
```

4. Added actual-parser close-framed EOF diagnostic — exit **1**, **1 failed**,
   1.18 s; `live-eof-probe.txt`:

```powershell
uv run --offline --locked python -m pytest -n 0 -q -s -p no:cacheprovider docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW/test_review_probes.py::test_live_close_framed_response_late_eof_cannot_succeed
```

5. Added actual buffered-parser absolute-deadline probe — exit **1**, **1 failed**,
   0.23 s; `live-deadline-probe.txt`. The two EOF probes are diagnostic; this
   segmented buffered-parser test establishes the production blocking-step issue:

```powershell
uv run --offline --locked python -m pytest -n 0 -q -s -p no:cacheprovider docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW/test_review_probes.py::test_live_chunked_read_obeys_absolute_attempt_deadline
```

6. Independent stdlib digests, 29 bindings, original locator manifest, AST/runtime
   dependency inventory and root/free-space — exit **0**;
   `independent-verification.json`, `independent-verification.txt`:

```powershell
uv run --offline --locked python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW/verify_review.py
```

7. Original implementation/CLI/tests lint — exit **0**, all checks passed;
   `ruff-check.txt`:

```powershell
uv run --offline --locked ruff check src/xlm/data/evidence_v4 scripts/evidence_v4.py tests/test_evidence_v4_plan.py tests/test_evidence_v4_transport.py tests/test_evidence_v4_engine.py tests/test_evidence_v4_e2e.py tests/evidence_v4_support.py
```

8. Format check — exit **0**, 13 files formatted; `ruff-format.txt`:

```powershell
uv run --offline --locked ruff format --check src/xlm/data/evidence_v4 scripts/evidence_v4.py tests/test_evidence_v4_plan.py tests/test_evidence_v4_transport.py tests/test_evidence_v4_engine.py tests/test_evidence_v4_e2e.py tests/evidence_v4_support.py
```

9. Strict mypy — exit **0**, 13 files; `mypy.txt` (unused-section note only):

```powershell
uv run --offline --locked mypy src/xlm/data/evidence_v4 scripts/evidence_v4.py tests/test_evidence_v4_plan.py tests/test_evidence_v4_transport.py tests/test_evidence_v4_engine.py tests/test_evidence_v4_e2e.py tests/evidence_v4_support.py
```

10. Production verifier — exit **0**, exact bindings; `cli-verify.json`:

```powershell
uv run --offline --locked python scripts/evidence_v4.py verify
```

11. Read-only status — exit **0**, NOT_STARTED/root absent; `cli-status.json`:

```powershell
uv run --offline --locked python scripts/evidence_v4.py phase-p-status
```

12. Wrong digest — exit **1**, pre-state refusal; `cli-wrong-digest.txt`:

```powershell
uv run --offline --locked python scripts/evidence_v4.py phase-p --confirm-plan-digest WRONG
```

No correct-digest live command ran. CLI shape, abbreviations and substituted
recording transport were exercised by existing offline tests. No actual network
transport was allowed to connect. The live-wrapper probes use memory-backed
stdlib parsers and blocked socket/DNS entry points.

Four independent assertions failed; 220 existing tests passed. Zero skips.
The new probe file grew by adding two tests; no failing assertion was changed.
No full-suite pass is claimed. CUDA/live tests, unrelated acquisition tests and
full acceptance are NOT RUN. Peak memory for the full pytest selection was NOT
MEASURED; the identity script alone measured 34,816,000-byte peak working set.

Initial incidental read-only `rg` searches using a Windows literal wildcard and
a nonexistent guessed v3 filename reported errors; corrected reads found the
actual file. These are discovery errors, not omitted or failed test groups.

Next action: request B01/B02 repair using the report's exact prompt, then focused
offline recertification. No live Phase P until the narrow review passes.

13. Closeout — exit **0**, `closeout.json`: prepended the review notice to
    STATUS.md, verified every pre-existing status byte remains an exact suffix,
    rechecked all v4 source hashes, no tracked source/test/dependency diff,
    unchanged HEAD and absent real root:

```powershell
uv run --offline --locked python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.0-REVIEW/closeout_review.py
```

14. `git diff --check` reported CRLF whitespace in the preserved dirty user
    documentation (and the new status notice using the same CRLF representation).
    The first combined readout's final shell exit belonged to `git status`; an
    isolated capture confirmed diff-check exit **2**, recorded in `diff-check.json`.
    No whitespace-clean claim is made; user documentation was not normalized.
    `git status --short` exits **0**; review files remain untracked/uncommitted.
