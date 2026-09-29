# Independent Phase-P authorization review 3: command record

2026-09-29, Windows, `F:\Project\xlm-data-ultrax`. No network or real execution.
Commands below are the actual verification selections, not a full repository gate.
No dependency install/sync occurred. All Python invocations used uv offline/locked/no-sync.

Starting read-only commands, exit 0:

```powershell
git status --short
git rev-parse HEAD
git log --oneline -12
git show --stat 3dd5ebc
git show --stat 12d8515
```

HEAD `12d85158206378c0d6833d95df9b9d8fa5789761`; branch `data/mix01-ultrax-6b`.
Initial status (preserved):

```text
 M docs/implementation/STATUS.md
 M docs/implementation/reports/ESSENTIAL-WEB-SELECTOR-RECON.md
?? docs/implementation/evidence/ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW/
?? docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V3.0-REVALIDATION.md
?? docs/implementation/reports/ESSENTIAL-WEB-SELECTOR-POLICY-REVIEW.md
?? docs/implementation/reports/ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW-AUDIT.md
?? docs/implementation/reports/ESSENTIAL-WEB-SELECTOR-SWEEP-REVIEW.md
```

Test settings:

```powershell
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
$env:TOKENIZERS_PARALLELISM='false'
```

The stock run additionally set `HF_HUB_OFFLINE=1` and `HF_DATASETS_OFFLINE=1`.
Independent probes deny socket connect/create_connection/getaddrinfo; the
positive crash subprocess installs the same denial. Stock tests use the
repository's offline fixtures and exact offline FakeTransport. No xdist
controller/workers (`-n 0`); stock and independent selections briefly overlapped
as separate single-process runs; all mutable roots were private temporary roots.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest tests/test_evidence_v3.py tests/test_evidence_v3_authorization.py tests/test_evidence_v3_journal.py tests/test_evidence_v3_execution.py tests/test_evidence_v3_containment.py tests/test_evidence_v3_e2e.py tests/test_evidence_v3_children.py -n 0 -p no:cacheprovider -q -rs --junitxml=docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-AUTHORIZATION-REVIEW-3/stock.xml
```

Exit **0**, **224 passed, 1 skipped**, 296.33 s. Output `stock.txt`.
Skip: Windows symlink privilege; actual NTFS junction tests passed. The selection
includes two child regenerations (relative and absolute output paths), concurrent
genesis, real process death/restart, and eight integrated adversarial variants.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-AUTHORIZATION-REVIEW-3/test_independent.py -n 0 -p no:cacheprovider -q -s --tb=short --junitxml=docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-AUTHORIZATION-REVIEW-3/independent.xml
```

Exit **1**, **8 failed**, zero errors/skips, 42.97 s console / 42.846 s JUnit.
Output `independent.txt`. These are eight deliberately stated safety assertions
that the unchanged implementation violates. They are not xfails or repaired tests.
No retry. Readiness is actually recomputed under the disabled-arms-guard probe.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-AUTHORIZATION-REVIEW-3/audit_identity.py
```

Initial exit **1**, output `identity-initial.txt`: the independent checker assumed
uniform LF/CRLF for all historical metadata; `relocated_revalidation.json` has a
mixed-newline committed blob. Corrected only the new review script's checkout
equivalence check. Exact Git blob comparisons and normative hashes were retained.
Second exit **0**, output `identity.txt`, artifact `identity.json`, measured script
wall 5.04146 s. Config/code SHA-256s are computed from committed bytes, never from
normalized metadata. The corrected script was run once; no product fix occurred.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python -m pytest docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-AUTHORIZATION-REVIEW-3/test_positive.py -n 0 -p no:cacheprovider -q -s --tb=short --junitxml=docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V3.0-AUTHORIZATION-REVIEW-3/positive.xml
```

Exit **0**, **2 passed**, zero skips, 6.17 s console / 5.773 s JUnit. Output
`positive.txt`; exact recovery artifact `exact-recovery.json`. Child process exits
**137** (intentional crash) and **0** (successful resume). Disk control checks
both ordinary and replacement reservations at the requested 8+8 >10-byte peak.

Outputs were redirected with PowerShell `*>` to the named text files; each run
saved `$LASTEXITCODE`, printed the tail, and exited with that code. PowerShell
may format native stderr as a `NativeCommandError` in the initial checker log.

Source reads used `Get-Content`, `git show`, and `rg`; Python inspected only JSON
metadata, AST/code, Git blobs, and authored synthetic data. Two exploratory `rg`
calls with shell-style wildcard paths produced Windows error 123; corrected
searches used `rg ... tests -g 'test_evidence_v3*.py'` or explicit paths.

NOT RUN: complete offline repository acceptance, unrelated acquisition tests
(they start a loopback HTTP server, excluded by NO NETWORK), live source/TLS/DNS,
CUDA, old-commit exploit runner, external original manifest parsing, performance
qualification. Original scientific identities are audited from committed frozen
descriptors and unchanged scientific code/artifacts; external bytes are not newly
certified. No G: writes, real genesis, operator approval, Phase D, commit or push.

Measured audit-process peak working set: **41,783,296 bytes**; endpoint RSS
**37,101,568 bytes**. This is the identity checker, not the entire test tree.
The extra recovery fixture's physical root occupancy before sealed no-op:
**51,880 bytes** (logical file sizes), with separately conserved journal/head
allowances. Aggregate test-tree peak RSS, disk peak and CPU time were NOT MEASURED.
Synthetic time advances are model inputs, never real elapsed/GPU measurements.

Next action: remediate the report's blocking findings in a separately authorized
implementation task, then rerun these exact failing safety assertions and directly
related regressions before another independent authorization review.
