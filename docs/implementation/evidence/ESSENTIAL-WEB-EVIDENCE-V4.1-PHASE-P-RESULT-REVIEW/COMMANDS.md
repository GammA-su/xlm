# Post-live Phase-P review commands and artifacts

2026-09-29; CWD `F:\Project\xlm-data-ultrax`. Windows PowerShell; Windows 11
build 26200 AMD64; CPython 3.12.13; uv 0.12.19; existing locked CPU/eval graph.
No network, Phase-P rerun, Phase D, corpus text inspection or G: mutation.

Initial `git status --short`, `git rev-parse HEAD`, `git log --oneline -6`:
exit 0; HEAD `91be1a1c5909a942561c2e37445cf543d9731372`.
STATUS.md already had an uncommitted host-review notice. Untracked host-review
artifacts, selector reviews and v3 revalidation were present and preserved.

Read-only inspection used `Get-Content` for the frozen v4.0/v4.1 protocols,
STATUS and implementation; `Get-ChildItem` for G: root names/sizes; `rg` for
relevant definitions. One `rg` search for range/coalescing text in child Python
files found no matches and returned 1; this was navigation, not a test failure.
Bounded Python stdin inspections read only JSON structure and SQLite with
`mode=ro&immutable=1` (exit 0), never corpus text or footer statistics.

## Reproducible substantive command

```powershell
uv run --offline --locked --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-P-RESULT-REVIEW/review_result.py
```

| Invocation | Exit | Log/result |
|---|---:|---|
| Initial | 1 | `review-run.log`: review assertion expected NULL error for REDIRECT, while production records empty string |
| Corrected exact NULL/empty-string check | 0 | `review-run-corrected.log`: all substantive checks pass |
| Final, added schema/allocated-size checks | 0 | `review-run-final.log`: all checks pass; final `review-result.json` |

Each invocation used `2>&1 | Tee-Object -FilePath <log>` followed by
`exit $LASTEXITCODE`. Logs retain PowerShell UTF-16 encoding. The diagnostic
read-only SQL grouped `outcome, error IS NULL, length(error), count(*)`:
40 REDIRECT rows have zero-length strings; 40 SUCCESS rows have NULL. The fixed
assertion requires exactly these representations. This was a review-harness
assumption correction, not an implementation failure or suppressed check.

The final invocation independently verifies canonical hashes, the SQLite run,
all 40 operations, all 80 attempts, every artifact hash/size, structural metadata,
locator mapping, caps and exact dry ranges. Network helpers raise if invoked.
SQLite uses URI `mode=ro&immutable=1`; root sidecars are refused before opening.
Metadata parsing never accesses statistics, key/value metadata or row contents.
All script writes are atomic and restricted to this F: review directory.

The script records bounded actual measurements: root ≤1000 paths and ≤256 MiB
before reading; streamed hashing in 64 KiB blocks; each JSON/footer input ≤4 MiB;
86 actual evidence files / 3,241,704 bytes; 3,399,872 allocated file bytes from
attribute-only Windows FILE_STANDARD_INFO handles. Final script measured
0.291761 seconds inside main and 58,224,640-byte peak process working set.
Review measurements do not establish production performance or historical peaks.

## Code lineage and final checks

```powershell
git diff --exit-code 9c420c014afabb25156d09aeff9c0ed719a3db2c HEAD -- src/xlm/data/evidence_v4 scripts/evidence_v41.py
git diff --exit-code HEAD -- src scripts tests pyproject.toml uv.lock .python-version
```

Both exit 0; no production/test/dependency changes. Final package validation,
G: before/after hash/stat comparison and staged-file scope are recorded in
`final-checks.json`. No test suite was run: this is actual result verification,
not implementation recertification. The host review's 337 tests are not claimed
as newly run here. No mocks are represented as live results.

Initial `git diff --cached --check` returned 1 for CRLF trailing whitespace in
the two newly authored `final-checks.json` and `status-preservation.json` files.
Only those F: review files were converted to LF; G: JSON/SQLite remained untouched.
The corrected staged whitespace check exits 0. Staging is restricted to this
evidence directory and the new report; actual staged paths are recorded in
`final-checks.json`. Commit command:

```powershell
git commit -m "docs: verify live v4.1 Phase-P results and freeze dry ranges"
```

## Package

- `review_result.py`: independent read-only review and deterministic dry planner.
- `root-before.json`, `root-after.json`: names, sizes, mtimes, modes and all file
  SHA-256 values. No copies of payloads or redirect bodies.
- `review-result.json`: actual run, reconciled totals, parent hashes, layout
  summaries, measured resources and explicit evidence limitations.
- `phase_d_dry_plan.json`: 124,373 bytes; self-digest
  `ee82125d1b0d2b6d0a6c420144910db954659d5b757283d39123ddc3d0080356`.
  Contains exact ranges, source identities and locator/row-group relationships,
  not corpus text. DRY_NOT_AUTHORIZED.
- Three logs above: initial review-script assumption failure and corrections.
- `final-checks.json`: final scope/integrity checks and their results.

The dry plan's 191,655,699 bytes are exact successful range payloads; unknown
redirect/error/retry bytes are not assigned a fabricated zero. One-redirect
counts and worst-case formulas are expressly scenarios, not authorization.
The 64 MiB Phase-P arm ceiling cannot cover T Phase D; a separately frozen
Phase-D protocol must resolve explicit operational/resource limits.

Only this new evidence directory and its result-review report are committed.
STATUS is updated locally without staging the pre-existing dirty notice.
All earlier user/untracked files are preserved. No push.

Next action: prepare/freeze the separate Phase-D protocol and authorization
package bound to the dry-plan digest and exact COMPLETE parents. Do not acquire
any Phase-D bytes or inspect document text without separate authorization.
