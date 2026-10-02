# Common Pile p01 operator commands (prepared, not executed)

Plan: `G:\XLM\plans\common_pile\p01\plan.json`.
PLAN DIGEST: **`c38eb2be01a28579ffa77a319c0713e5fef2df0f4da9ac3ed3b3089ab04edaa7`**.
Review the [production report](../../reports/COMMON-PILE-PRODUCTION-PLAN.md)
and the frozen plan before personally authorizing. Acquisition is production
scope; admission alone does not authorize this plan.

## Environment and explicit authorization

Paste the preamble, then personally run authorization if the complete plan is acceptable.

```powershell
Set-Location F:\Project\xlm-common-pile
$env:XLM_HOME = 'G:\XLM\xlm-home'
$env:XLM_DATA_ROOT = 'G:\XLM'
$env:XLM_SCRATCH_ROOT = 'C:\XLM-scratch'
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:UV_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$cp = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval', 'python', 'scripts/mix01_source.py')

& uv @cp authorize --source-key common_pile --plan 1 --digest c38eb2be01a28579ffa77a319c0713e5fef2df0f4da9ac3ed3b3089ab04edaa7 --operator GammA
if ($LASTEXITCODE -ne 0) { throw 'Authorization refused; stop.' }
```

## Real acquisition (operator only)

Only after successful authorization. uv remains offline for dependency resolution.
The Hugging Face flags are temporarily enabled for the pinned source transfer;
TRANSFORMERS_OFFLINE remains 1. No probe, discovery or other source is requested.

```powershell
try {
    $env:HF_HUB_OFFLINE = '0'
    $env:HF_DATASETS_OFFLINE = '0'
    & uv @cp run --source-key common_pile --plan 1 --download-workers 1 --process-workers 1
    if ($LASTEXITCODE -ne 0) { throw 'Acquisition stopped; preserve receipts and inspect the failure.' }
} finally {
    $env:HF_HUB_OFFLINE = '1'
    $env:HF_DATASETS_OFFLINE = '1'
}
```

## Offline post-run inspection

Run individually, stopping on a nonzero exit. Full content verification is required.

```powershell
& uv @cp verify --source-key common_pile --plan 1
if ($LASTEXITCODE -ne 0) { throw 'Verification failed; stop.' }
& uv @cp resume-check --source-key common_pile --plan 1
if ($LASTEXITCODE -ne 0) { throw 'Resume inspection failed; stop.' }
& uv @cp sufficiency --source-key common_pile
if ($LASTEXITCODE -ne 0) { throw 'Sufficiency inspection failed; stop.' }
```

Read the reported status; an exit of zero does not by itself mean SUFFICIENT.

* INCOMPLETE: inspect status/resume-check and the failed performance receipt.
  Verified partial transfer can resume under unchanged validators and remaining
  budgets. Complete verified compressed files can retry local processing from
  byte zero. Use the same run command only after reviewing the cause and spent
  budget; `run ... --offline` is appropriate only when resume-check shows every
  remaining input locally reusable. Never retry a deterministic bound failure.
* TOP_UP with every current unit sealed: `& uv @cp plan --source-key common_pile`.
  This selects only deficient components after their saved cursors. STOP at
  its new digest, review and authorize that new sequence separately. News/OER/PDR
  already exhaust eligible files here: an actual deficit in these components
  has no in-inventory top-up. No repetitions, substitutions or use of reserved ranks.
* A failed authorized plan needing changed per-unit limits or renewed admission:
  first obtain the corresponding reviewed evidence/decision and a supported
  explicit artifact amendment. Existing bounds are write-once: do not overwrite
  them or invent a new recording command. Then
  `& uv @cp plan-repair --source-key common_pile --plan 1` prepares unsealed ranks
  under the new inputs and preserves cursors/verified retained sources. STOP at
  the repair digest; separately review/authorize/run its returned sequence.
  Unchanged limits/admission refuse repair. A spent deadline is not reset by a retry.
* SUFFICIENT after full verify: `& uv @cp seal --source-key common_pile`.
  The seal attests raw first-pass availability, not C05 or training readiness.

No repair, top-up, seal, authorization, or production run was executed by this agent.
The commands above are conditional operator instructions, not a script to run wholesale.
Return receipts and failure/sufficiency state for offline review. C05, exact-token
selection, tokenizer fitting and training remain later separately instructed stages.
