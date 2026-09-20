# P23 remediation ledger

Overall production acceptance: **BLOCKED**. Historical audit completion is not
platform acceptance. The user approved D01 and then Stage 2 (D06) implementation
and bounded offline verification with authored fixtures and tiny CPU models.
D03 core implementation is now approved for a bounded baseline-pilot handoff.
Subsequent stages still require separate approval.

| Defect | Implementation status | Verification / next gate |
|---|---|---|
| D01 | IMPLEMENTED | VERIFIED bounded offline: 99 focused + 92 caller tests; lint/format/types pass. [Code/test/result map and before/after evidence](reports/P23-D01.md). Legacy checksum-only; declared provenance only. |
| D06 | IMPLEMENTED | VERIFIED bounded Windows CPU offline: 53 core + 27 caller + 83 related regression + 99 D01 tests; format/lint/types pass. [Code/test/result map and preserved evidence](reports/P23-D06.md). External platform/operator validation NOT RUN. |
| D03 core | IMPLEMENTED | VERIFIED bounded Windows CPU: 406 test executions / 400 distinct cases, zero skips; format/lint/types pass. [Code/test/result map](reports/P23-D03.md) and [operator pilot commands](D03_BASELINE_PILOT.md). Original 2 failures / 1 positive control preserved. |
| D03 cross-tokenizer exposure comparisons | OPEN / DEFERRED | Matched-document/byte comparison execution deferred to tokenizer research by the user; not VERIFIED. |
| D02 | IN PROGRESS | Implementation and bounded offline/loopback verification approved. [Preserved plan and reproduction](evidence/P23-D02/PLAN.md): 4 failures / 1 positive control plus 2 explicit refusal failures. Live/operator validation remains NOT RUN. |
| Full-size public campaign planning | BLOCKED | Public production CLI does not supply measured profile lookup; production evidence and plan-bound authority remain required. The D03 handoff is C13 bounded pilot only. |
| D04/D05 | NOT STARTED | Separate approval required. |
| D08 | NOT STARTED | Separate approval required. |
| D07 | NOT STARTED | Separate approval required. |
| Final complete offline rerun | NOT RUN | Scheduled after all approved stages. |

External acquisition, official evaluation, full-size profiling/training and
protected deployment: **NOT RUN — OPERATOR ACTION REQUIRED**. No such operations
are authorized by the remediation work.

Current action: implement and verify approved D02, then provide the acquisition
handoff and D04/D05 proposal. D03 core and its bounded pilot handoff are complete. No later
implementation stage is approved. Overall platform acceptance remains BLOCKED.
