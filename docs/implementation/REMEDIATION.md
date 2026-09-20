# P23 remediation ledger

Overall production acceptance: **BLOCKED**. Historical audit completion is not
platform acceptance. The user approved D01 and then Stage 2 (D06) implementation
and bounded offline verification with authored fixtures and tiny CPU models.
D03 core is implemented and offline verified. D02 and D04/D05 implementation
are now approved in separate branches: Astra owns D02 and later integration;
Opus owns D04/D05. Other defect stages still require separate approval.

| Defect | Implementation status | Verification / next gate |
|---|---|---|
| D01 | IMPLEMENTED | VERIFIED bounded offline: 99 focused + 92 caller tests; lint/format/types pass. [Code/test/result map and before/after evidence](reports/P23-D01.md). Legacy checksum-only; declared provenance only. |
| D06 | IMPLEMENTED | VERIFIED bounded Windows CPU offline: 53 core + 27 caller + 83 related regression + 99 D01 tests; format/lint/types pass. [Code/test/result map and preserved evidence](reports/P23-D06.md). External platform/operator validation NOT RUN. |
| D03 core | IMPLEMENTED | VERIFIED bounded Windows CPU: 406 test executions / 400 distinct cases, zero skips; format/lint/types pass. [Code/test/result map](reports/P23-D03.md) and [operator pilot commands](D03_BASELINE_PILOT.md). Original 2 failures / 1 positive control preserved. |
| D03 cross-tokenizer exposure comparisons | OPEN / DEFERRED | Matched-document/byte comparison execution deferred to tokenizer research by the user; not VERIFIED. |
| D02 | IN PROGRESS — SECURED PAUSE | User requested stop at usage limit. [Resume handoff](D02_RESUME_HANDOFF.md): latest quality passes; short check 11 passed / 1 child-output accounting failure; full regressions and integration pending. Before evidence preserved. |
| Full-size public campaign planning | BLOCKED | Public production CLI does not supply measured profile lookup; production evidence and plan-bound authority remain required. The D03 handoff is C13 bounded pilot only. |
| D04/D05 | IN PROGRESS (Opus) | Approved in separate `fix/d04-d05` checkout; not yet imported or verified by Astra. [Baseline and integration gate](D02_D0405_INTEGRATION.md). |
| D08 | NOT STARTED | Separate approval required. |
| D07 | NOT STARTED | Separate approval required. |
| Final complete offline rerun | NOT RUN | Scheduled after all approved stages. |

External acquisition, official evaluation, full-size profiling/training and
protected deployment: **NOT RUN — OPERATOR ACTION REQUIRED**. No such operations
are authorized by the remediation work.

Current action: finish D02 verification and operator acquisition handoff; Opus
implements the separately approved D04/D05 stage. Once both commits are ready,
Astra reviews a three-way merge in a clean integration worktree and runs both
regression sets plus the combined authored workflow. No other implementation
stage is approved. Overall platform acceptance remains BLOCKED.
