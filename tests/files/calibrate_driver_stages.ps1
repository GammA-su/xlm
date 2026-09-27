#Requires -Version 5.1
<#
.SYNOPSIS
  Offline stage harness for the calibration driver (authored fixtures only).
.DESCRIPTION
  Dot-sources operator_calibrate_remaining.ps1 (functions only) with a
  TEMPORARY -DataRoot, so every derived path (unit root, logs, plan,
  calibration.json, measurement) lives under that root, then runs the named
  stages through the driver's own Invoke-Stage in the given order.
  Offline guard: Invoke-Step is wrapped so any stage that would go LIVE
  (probe / sample-blocks / fetch) fails the harness instead of touching the
  network; a correct run over the authored fixture must adopt those stages.
  Prints "STAGES OK" and exits 0, or "STAGE FAILED: <reason>" and exits 1.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DriverPath,
    [Parameter(Mandatory = $true)][string]$Repo,
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$Stages
)

$ErrorActionPreference = "Stop"

. "$DriverPath" -Unit simple_stories -Repo $Repo -DataRoot $DataRoot

$env:XLM_HOME = Join-Path $DataRoot "xlm-home"
$env:HF_HUB_OFFLINE = "1"
$env:HF_DATASETS_OFFLINE = "1"
Set-Location -LiteralPath $Repo

function Invoke-Step([string]$Name, [string[]]$CliArgs, [bool]$Live) {
    if ($Live) { throw "OFFLINE HARNESS: live step '$Name' refused (it should have been adopted)" }
    Invoke-Uv $Name ($UvBase + @("xlm") + $CliArgs) $Live
}

try {
    foreach ($name in $Stages.Split(",")) {
        ("STAGE: " + $name) | Out-Host
        Invoke-Stage $name
    }
} catch {
    ("STAGE FAILED: " + $_) | Out-Host
    exit 1
}
"STAGES OK" | Out-Host
exit 0
