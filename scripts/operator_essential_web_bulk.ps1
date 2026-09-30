#Requires -Version 5.1
<#
.SYNOPSIS
  One-batch-at-a-time operator driver for the Essential-Web bulk campaign.
.DESCRIPTION
  Dot-source scripts/operator_storage.ps1 first. Stages:

    Show     offline   deterministic membership of the batch
    Layout   NETWORK   Parquet footers of the batch only (no record payloads)
    Plan     offline   slice plans, limits and the AUTHORIZATION DIGEST
    Prepare            Show, Layout, Plan
    Run      NETWORK   gate, authorize, then per slice: fetch, adapt x3, seal;
                       finally the cumulative yield and the stop decision
    Status   offline   batch state, cumulative yield and the stop decision

  Run needs -Authorize <digest> the first time; the digest is printed by Plan
  and binds this campaign, this batch and the exact slice plan hashes. A
  repeated Run resumes: completed fetches, adaptations and sealed slices are
  skipped, never redone or overwritten.

  Fail-closed: every nonzero native exit stops the script. Network is enabled
  only around the footer read and each fetch, and restored in finally blocks.
  Machine values travel through JSON files and exit codes, never stdout.
  The script never deletes data, admits a source, tokenizes, trains or pushes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateRange(0, 9999)][int]$Batch,
    [Parameter(Mandatory = $true)]
    [ValidateSet('Show', 'Layout', 'Plan', 'Prepare', 'Run', 'Status')]
    [string]$Stage,
    [string]$Authorize = '',
    [string]$Operator = $env:USERNAME,
    [ValidateRange(1, 16)][int]$Workers = 1,
    [string]$TopUpReason = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Repo
if (-not $env:XLM_DATA_ROOT) { throw 'Dot-source scripts/operator_storage.ps1 first' }
$storage = Get-Content -LiteralPath (Join-Path $Repo 'recipes/operator/storage.json') -Raw | ConvertFrom-Json
if ($env:XLM_DATA_ROOT -ne $storage.data_root) { throw 'Unexpected operator root' }
if ($env:XLM_HOME -ne (Join-Path $env:XLM_DATA_ROOT $storage.artifact_store_relative)) {
    throw 'Unexpected artifact store'
}

$U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')
$Tool = 'scripts/essential_web_bulk.py'
$Campaign = Get-Content -LiteralPath (Join-Path $Repo 'docs/implementation/evidence/ESSENTIAL-WEB-BULK-ACQUISITION/bulk-campaign.json') -Raw | ConvertFrom-Json
$BatchDir = Join-Path (Join-Path $env:XLM_DATA_ROOT $Campaign.roots.plans) ('b{0:d4}' -f $Batch)

function Invoke-Tool([string[]]$Arguments, [int[]]$Allowed = @(0)) {
    "COMMAND: uv $($U -join ' ') python $Tool $($Arguments -join ' ')" | Out-Host
    uv @U python $Tool @Arguments | Out-Host
    $code = $LASTEXITCODE
    if ($Allowed -notcontains $code) { throw "essential_web_bulk $($Arguments[0]) failed (exit $code)" }
    return $code
}

function Invoke-Online([scriptblock]$Action) {
    $oldHub = $env:HF_HUB_OFFLINE; $oldData = $env:HF_DATASETS_OFFLINE
    try {
        $env:HF_HUB_OFFLINE = '0'; $env:HF_DATASETS_OFFLINE = '0'
        & $Action
    }
    finally { $env:HF_HUB_OFFLINE = $oldHub; $env:HF_DATASETS_OFFLINE = $oldData }
}

function Invoke-Xlm([string[]]$Arguments) {
    "COMMAND: uv $($U -join ' ') xlm $($Arguments -join ' ')" | Out-Host
    uv @U xlm @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "xlm $($Arguments[0]) $($Arguments[1]) failed (exit $LASTEXITCODE)" }
}

function Invoke-Layout {
    Invoke-Online { Invoke-Tool @('layout', '--batch', "$Batch") | Out-Null }
}

function Invoke-Run {
    $gate = @('gate', '--batch', "$Batch")
    if ($TopUpReason -ne '') { $gate += @('--top-up-reason', $TopUpReason) }
    $code = Invoke-Tool $gate @(0, 3, 4)
    if ($code -eq 3) { 'First-pass targets are met. Nothing was fetched.' | Out-Host; return }
    if ($code -eq 4) { "Batch $Batch is already complete. Nothing was fetched." | Out-Host; return }
    if ($Authorize -ne '') {
        Invoke-Tool @('authorize', '--batch', "$Batch", '--digest', $Authorize, '--operator', $Operator) | Out-Null
    }
    Invoke-Tool @('state', '--batch', "$Batch") | Out-Null
    $state = Get-Content -LiteralPath (Join-Path $BatchDir 'state.json') -Raw | ConvertFrom-Json
    foreach ($slice in $state.slices) {
        if ($slice.sealed) { "slice $($slice.name): already sealed" | Out-Host; continue }
        if (-not $slice.authorized) {
            throw "slice $($slice.name) is not authorized: rerun with -Authorize <digest printed by Plan>"
        }
        Invoke-Tool ($gate + @('--slice', "$($slice.slice)")) | Out-Null
        if (-not $slice.fetched) {
            Invoke-Online {
                Invoke-Xlm @('data', 'fetch', '--plan', $slice.plan, '--output-dir', $slice.raw_dir, '--scratch-dir', $slice.scratch_dir)
            }
        }
        foreach ($view in $state.views) {
            if ($slice.adapted.$view) { continue }
            Invoke-Xlm @('data', 'adapt', '--plan', $slice.plan, '--adapter', $Campaign.adapt.adapter_id,
                '--adapter-config', $view, '--input', $slice.raw_file, '--output-dir', $slice.canonical.$view,
                '--on-reject', $Campaign.adapt.on_reject, '--max-input-bytes', "$($slice.max_input_bytes)")
        }
        Invoke-Tool @('seal-slice', '--batch', "$Batch", '--slice', "$($slice.slice)") | Out-Null
    }
    $code = Invoke-Tool @('account') @(0, 3)
    if ($code -eq 3) {
        'STOP: first-pass targets are met. Do not run another batch without a top-up reason.' | Out-Host
    }
    else {
        "CONTINUE: next batch is $($Batch + 1)." | Out-Host
    }
}

switch ($Stage) {
    'Show' { Invoke-Tool @('show', '--batch', "$Batch") | Out-Null }
    'Layout' { Invoke-Layout }
    'Plan' { Invoke-Tool @('plan', '--batch', "$Batch", '--workers', "$Workers") | Out-Null }
    'Prepare' {
        Invoke-Tool @('show', '--batch', "$Batch") | Out-Null
        Invoke-Layout
        Invoke-Tool @('plan', '--batch', "$Batch", '--workers', "$Workers") | Out-Null
    }
    'Run' { Invoke-Run }
    'Status' {
        if (Test-Path -LiteralPath (Join-Path $BatchDir 'batch.json')) {
            Invoke-Tool @('state', '--batch', "$Batch") | Out-Null
        }
        Invoke-Tool @('account') @(0, 3) | Out-Null
    }
}
