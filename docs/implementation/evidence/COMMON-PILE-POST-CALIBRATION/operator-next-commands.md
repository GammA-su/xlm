# Operator commands after offline Common Pile calibration

Prepared only. No network, admission, allocation/bounds record, policy freeze,
production plan or production run was executed by the agent. Inspect the report
and JSON inputs first. Preview records use `PREVIEW_ONLY`, not an operator identity.
Recording with your actual identity/rationale produces a different record digest.

Run from the existing worktree; keep network flags off:

```powershell
Set-Location F:\Project\xlm-common-pile
$env:HF_HUB_OFFLINE='1'
$env:HF_DATASETS_OFFLINE='1'
$env:UV_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:XLM_HOME='G:\XLM\xlm-home'
$env:XLM_DATA_ROOT='G:\XLM'
$env:XLM_SCRATCH_ROOT='C:\XLM-scratch'
$env:PYTHONUTF8='1'
$preview='docs/implementation/evidence/COMMON-PILE-POST-CALIBRATION'
```

## Shared measurement materialization

The agent's atomic replacement of the existing `calibration.json` was denied
by Windows (WinError 5). The original remained unchanged. The successful
`calibration.common-pile-post.json` preserves every prior source entry and adds
Common Pile; its estimate is `headroom_estimate.common-pile-post.json`.
The current driver still reads the original default filenames. In your operator
shell, run these existing CLIs to update those defaults; do not change ACLs or
silently use a versioned file as a fallback. Stop if either command fails.

The two explicit auxiliary entries are existing IFM view measurements. Their
existing combined quota measurement is used for the mixture estimate. They
remain disclosed separately and are neither removed nor counted a second time.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py record --source common_pile_prose --calibration G:\XLM\calib\calibration.json --measurement G:\XLM\calib\common_pile_prose\measurement.json --adopt
if ($LASTEXITCODE -ne 0) { throw 'Shared measurement not recorded; stop' }
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_inventory.py estimate --quotas recipes/mixtures/mix01_quotas_6b.yaml --calibration G:\XLM\calib\calibration.json --output G:\XLM\calib\headroom_estimate.json --auxiliary-source ifm_general --auxiliary-source ifm_planning
if ($LASTEXITCODE -ne 0) { throw 'Shared estimate not recorded; stop' }
```

## Read-only alternatives and bounds

These commands were executed offline, each with exit 0. B is a syntactically
valid preview but capacity-infeasible under the calibrated 15% safety margin.
C is the technical recommendation; no allocation is selected automatically.

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py component-split preview --source-key common_pile --data-root G:\XLM --input "$preview/strategy-a.input.json" --operator PREVIEW_ONLY --rationale 'Calibrated strategy A; no operator choice recorded'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py component-split preview --source-key common_pile --data-root G:\XLM --input "$preview/strategy-b.input.json" --operator PREVIEW_ONLY --rationale 'Calibrated strategy B; no operator choice recorded'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py component-split preview --source-key common_pile --data-root G:\XLM --input "$preview/strategy-c.input.json" --operator PREVIEW_ONLY --rationale 'Calibrated strategy C; no operator choice recorded'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py component-bounds preview --source-key common_pile --data-root G:\XLM --input "$preview/bounds.input.json" --operator PREVIEW_ONLY --rationale 'Calibrated operating ceilings with explicit 2x rate/row/line margins; not whole-shard measured maxima'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py evidence show --source-key common_pile --data-root G:\XLM --sample-dir G:\XLM\calib\common_pile_cal01 --sample-dir G:\XLM\calib\common_pile_cal02
```

## Operator decisions — do not paste as an automatic sequence

Only after choosing an allocation and accepting or revising the concrete
bounds, record those decisions. The input files are data-only. Bounds are
fail-closed operating ceilings, not measured full-shard maxima. Exceeding a
ceiling requires a new review; no unattended increase is authorized.

```powershell
$operator=Read-Host 'Your operator name'
$choice=Read-Host 'Reviewed allocation input: a, b, or c (B is currently infeasible)'
if ($choice -notin @('a','b','c')) { throw 'Invalid allocation choice' }
$allocationReason=Read-Host 'Your allocation rationale'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py component-split record --source-key common_pile --data-root G:\XLM --input "$preview/strategy-$choice.input.json" --operator "$operator" --rationale "$allocationReason"
if ($LASTEXITCODE -ne 0) { throw 'Allocation not recorded; stop' }
$boundsReason=Read-Host 'Your rationale for accepting these bounds'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py component-bounds record --source-key common_pile --data-root G:\XLM --input "$preview/bounds.input.json" --operator "$operator" --rationale "$boundsReason"
if ($LASTEXITCODE -ne 0) { throw 'Bounds not recorded; stop' }
```

The bridge is ready technically but remains unpublished. After inspecting its
preview, the operator can publish it locally and show the actual review basis:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py evidence publish --source-key common_pile --data-root G:\XLM --sample-dir G:\XLM\calib\common_pile_cal01 --sample-dir G:\XLM\calib\common_pile_cal02
if ($LASTEXITCODE -ne 0) { throw 'Bridge not published; stop' }
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py review show --source-key common_pile --data-root G:\XLM
if ($LASTEXITCODE -ne 0) { throw 'Review facts unavailable; stop' }
```

Now read the facts. Repository license is null; the exact six-component
allowlist and evidence matrix supply the component review basis. Underlying
document rights and benchmark contamination are not certified. C05 is still
required. To record your decision, explicitly supply each value:

```powershell
$licenseDecision=Read-Host 'License decision: approve_research_pretraining or reject'
$provenanceDecision=Read-Host 'Provenance decision: approved or rejected'
$reviewReason=Read-Host 'Your license/provenance review rationale'
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py review record --source-key common_pile --data-root G:\XLM --review-dir G:\XLM\reviews\common_pile_balanced_cal01 --operator "$operator" --license-decision "$licenseDecision" --provenance-decision "$provenanceDecision" --benchmark-risk suspect_with_mitigation --rationale "$reviewReason"
if ($LASTEXITCODE -ne 0) { throw 'Review not recorded; stop' }
```

Only if your decisions approve the intended use, personally run admission:

```powershell
uv run --offline --locked --no-sync --extra cpu --extra eval python scripts/mix01_source.py admit --source-key common_pile --data-root G:\XLM --review-dir G:\XLM\reviews\common_pile_balanced_cal01
```

**STOP after operator decisions/admission.** Return the outputs to Astra for
offline verification. No policy freeze, production plan, production authorization,
production run, C05, tokenizer, training, further network, or push is included.
