#Requires -Version 5.1
<#
.SYNOPSIS
  One-batch-at-a-time operator driver for the FAST Essential-Web campaign.
.DESCRIPTION
  Dot-source scripts/operator_storage.ps1 first. Stages:

    Show       offline   deterministic membership of the batch
    Prepare    offline   Show, then bind membership, revision and byte/disk
                         ceilings and print the AUTHORIZATION DIGEST.
                         No footer, layout or any other network read.
    Run        NETWORK   gate, authorize, then per file: one sequential stream
                         to fast scratch, verify, retain the source Parquet on
                         the durable volume, adapt the three views locally and
                         seal a receipt; finally the cumulative yield and the
                         stop decision
    Status     offline   unit states, cumulative yield and the stop decision
    ResumeCheck offline  verify sealed hashes; dry-plan only remaining units
    Resume     offline   same executor as Run, but refuses all download work;
                         -RecoveryAuthorize binds the reviewed amendment
    Benchmark  NETWORK   small transport benchmark on a frozen prefix of batch 0
                         plus a real-byte parity check; retains nothing and is
                         never campaign progress

  Run needs -Authorize <digest> the first time; the digest is printed by
  Prepare. A repeated Run resumes: sealed units are skipped, a verified partial
  download continues from its last checkpoint, a retained source file is not
  transferred again. Benchmark needs -Authorize <frozen benchmark plan digest>.

  Fail-closed: every unexpected native exit stops the script. The script never
  deletes a retained source file, a canonical unit or a receipt, and it never
  admits a source, tokenizes, trains or pushes.
#>
[CmdletBinding()]
param(
    [ValidateRange(0, 9999)][int]$Batch = 0,
    [Parameter(Mandatory = $true)]
    [ValidateSet('Show', 'Prepare', 'Run', 'Resume', 'ResumeCheck', 'Status', 'Benchmark')]
    [string]$Stage,
    [string]$Authorize = '',
    [string]$RecoveryAuthorize = '',
    [string]$Operator = $env:USERNAME,
    # 0 keeps the campaign default.
    [ValidateRange(0, 16)][int]$Workers = 0,
    [ValidateRange(0, 16)][int]$ProcessWorkers = 0,
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
if (-not $env:XLM_SCRATCH_ROOT -or $env:XLM_SCRATCH_ROOT -ne $storage.scratch_root) {
    throw 'Unexpected or missing scratch root: dot-source scripts/operator_storage.ps1 again'
}

# One thread per process: parallelism comes from file workers, not from libraries.
$env:OMP_NUM_THREADS = '1'; $env:MKL_NUM_THREADS = '1'; $env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'; $env:TOKENIZERS_PARALLELISM = 'false'

$U = @('run', '--offline', '--locked', '--no-sync', '--extra', 'cpu', '--extra', 'eval')
$Tool = 'scripts/essential_web_fast.py'

function Invoke-Tool([string[]]$Arguments, [int[]]$Allowed = @(0)) {
    "COMMAND: uv $($U -join ' ') python $Tool $($Arguments -join ' ')" | Out-Host
    uv @U python $Tool @Arguments | Out-Host
    $code = $LASTEXITCODE
    if ($Allowed -notcontains $code) { throw "essential_web_fast $($Arguments[0]) failed (exit $code)" }
    return $code
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
    if ($RecoveryAuthorize -ne '') {
        Invoke-Tool @('authorize-recovery', '--digest', $RecoveryAuthorize, '--operator', $Operator) | Out-Null
    }
    $run = @('run', '--batch', "$Batch")
    if ($Stage -eq 'Resume') { $run += '--offline' }
    if ($Workers -gt 0) { $run += @('--workers', "$Workers") }
    if ($ProcessWorkers -gt 0) { $run += @('--process-workers', "$ProcessWorkers") }
    if ($TopUpReason -ne '') { $run += @('--top-up-reason', $TopUpReason) }
    # Inherit the terminal for the live dashboard; don't pipe native output to Out-Host.
    uv @U python $Tool @run
    $code = $LASTEXITCODE
    if (@(0, 3, 4) -notcontains $code) { throw "essential_web_fast run failed (exit $code)" }
    if ($code -eq 3) {
        'STOP: first-pass targets are met. Do not run another batch without a top-up reason.' | Out-Host
    }
    else {
        "CONTINUE: next batch is $($Batch + 1)." | Out-Host
    }
}

switch ($Stage) {
    'Show' { Invoke-Tool @('show', '--batch', "$Batch") | Out-Null }
    'Prepare' {
        Invoke-Tool @('show', '--batch', "$Batch") | Out-Null
        Invoke-Tool @('plan', '--batch', "$Batch") | Out-Null
    }
    'Run' { Invoke-Run }
    'Resume' { Invoke-Run }
    'ResumeCheck' { Invoke-Tool @('resume-check', '--batch', "$Batch") | Out-Null }
    'Status' { Invoke-Tool @('status', '--batch', "$Batch") @(0, 3) | Out-Null }
    'Benchmark' {
        if ($Authorize -eq '') { throw 'Benchmark needs -Authorize <frozen benchmark plan digest>' }
        Invoke-Tool @('benchmark', '--authorize', $Authorize) | Out-Null
    }
}
