#Requires -Version 5.1
<#
.SYNOPSIS
  Bounded automatic operator runner for the FAST Essential-Web campaign.
.DESCRIPTION
  Dot-source scripts/operator_storage.ps1 first. Stages:

    Status       offline  authoritative state, next batch and its classification,
                          campaign targets, disk and a projected ETA; writes nothing
    PrepareAuto  offline  build the bounded auto-authorization envelope: campaign,
                          source, revision, selector, adapter and running-code
                          identity, and the exact per-batch authorization digest of
                          every batch it may start; print AUTO AUTHORIZATION DIGEST
    RunAuto      NETWORK  only through the unchanged per-batch executor: while the
                          campaign says CONTINUE, plan, authorize (only the bound
                          child digest), gate, check C04 admission, run and verify
                          the next batch

  RunAuto needs -Authorize <digest printed by PrepareAuto>. It stops at the
  first-pass targets, at the end of the envelope (at most 20 batches), and at
  every condition that needs a human: a recorded unit failure, a nonzero batch
  exit, identity or code drift, a gate refusal, an automation guard or Ctrl+C.
  A repeated RunAuto with the same digest resumes from the authoritative state.

  Exit codes of RunAuto: 0 first pass complete, 1 refused, 5 envelope
  exhausted (a new PrepareAuto is required), 6 human review required,
  7 automation guard, 130 interrupted.

  It never approves a recovery amendment, a changed record bound, a changed
  source identity or code contract, or a top-up; it never deletes data,
  tokenizes, runs C05, trains or pushes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Status', 'PrepareAuto', 'RunAuto')]
    [string]$Stage,
    [ValidateRange(0, 9999)][int]$StartBatch = -1,
    [ValidateRange(1, 20)][int]$MaxBatches = 20,
    [string]$Authorize = '',
    [string]$Operator = $env:USERNAME,
    # Operator automation guards, in addition to the campaign's own reserve. 0 keeps the default:
    # 128 GiB durable free, scratch cap + scratch reserve free.
    [ValidateRange(0, 100000)][int]$MinDurableFreeGiB = 0,
    [ValidateRange(0, 100000)][int]$MinScratchFreeGiB = 0,
    [switch]$Json
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
if (-not $env:XLM_SCRATCH_ROOT -or $env:XLM_SCRATCH_ROOT -ne $storage.scratch_root) {
    throw 'Unexpected or missing scratch root: dot-source scripts/operator_storage.ps1 again'
}

$env:OMP_NUM_THREADS = '1'; $env:MKL_NUM_THREADS = '1'; $env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'; $env:TOKENIZERS_PARALLELISM = 'false'

$U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')
$Tool = 'scripts/essential_web_campaign.py'

$script:RunnerExit = 0
function Invoke-Runner([string[]]$Arguments) {
    "COMMAND: uv $($U -join ' ') python $Tool $($Arguments -join ' ')" | Out-Host
    # Never capture this call: the batch executor inherits the terminal for its live dashboard.
    uv @U python $Tool @Arguments
    $script:RunnerExit = $LASTEXITCODE
}

switch ($Stage) {
    'Status' {
        $arguments = @('status')
        if ($Json) { $arguments += '--json' }
        Invoke-Runner $arguments
        $code = $script:RunnerExit
        if ($code -ne 0) { throw "status failed (exit $code)" }
    }
    'PrepareAuto' {
        $arguments = @('prepare-auto', '--max-batches', "$MaxBatches", '--operator', $Operator)
        if ($StartBatch -ge 0) { $arguments += @('--start-batch', "$StartBatch") }
        if ($MinDurableFreeGiB -gt 0) { $arguments += @('--min-durable-free-gib', "$MinDurableFreeGiB") }
        if ($MinScratchFreeGiB -gt 0) { $arguments += @('--min-scratch-free-gib', "$MinScratchFreeGiB") }
        if ($Json) { $arguments += '--json' }
        Invoke-Runner $arguments
        $code = $script:RunnerExit
        if ($code -eq 3) { 'First-pass targets are met. Nothing to automate.' | Out-Host; return }
        if ($code -ne 0) { throw "prepare-auto refused (exit $code)" }
    }
    'RunAuto' {
        if ($Authorize -eq '') { throw 'RunAuto needs -Authorize <digest printed by PrepareAuto>' }
        Invoke-Runner @('run-auto', '--authorize', $Authorize, '--operator', $Operator)
        $code = $script:RunnerExit
        switch ($code) {
            0 { 'ESSENTIAL-WEB FIRST PASS COMPLETE. C05 has NOT run; training is NOT permitted.' | Out-Host }
            5 { 'STOP: the envelope is exhausted while targets are insufficient. Run PrepareAuto again.' | Out-Host }
            6 { throw 'STOP: human review required. See the reasons above and plans/ew-fast/auto/campaign-runner.jsonl' }
            7 { throw 'STOP: an automation disk guard was reached. Nothing was deleted.' }
            130 { 'STOP: interrupted. Run the same RunAuto command again to resume.' | Out-Host }
            default { throw "run-auto stopped (exit $code)" }
        }
    }
}
