#Requires -Version 5.1
<#
.SYNOPSIS
  Offline argv capture for the calibration driver's SampleBlocks/Plan stages.
.DESCRIPTION
  Dot-sources operator_calibrate_remaining.ps1 (functions only) for one
  unit, replaces the native runners (Invoke-Adopt / Invoke-Step / Invoke-Uv)
  with recorders that execute NOTHING, runs Invoke-Stage SampleBlocks and
  Plan, and prints one "ARGV <name> <json array>" line per recorded call.
  Invoke-Adopt reports "run" (0) so both mutating steps are recorded.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DriverPath,
    [Parameter(Mandatory = $true)][string]$Unit,
    [Parameter(Mandatory = $true)][string]$DataRoot
)

$ErrorActionPreference = "Stop"

. "$DriverPath" -Unit $Unit -Repo (Get-Location).Path -DataRoot $DataRoot

$env:XLM_HOME = Join-Path $DataRoot "xlm-home"

function Write-Argv([string]$Name, [string[]]$Values) {
    "ARGV " + $Name + " " + (ConvertTo-Json -InputObject @($Values) -Compress) | Out-Host
}
function Invoke-Adopt([string[]]$AdoptArgs) { Write-Argv "adopt" $AdoptArgs; return 0 }
function Invoke-Step([string]$Name, [string[]]$CliArgs, [bool]$Live) { Write-Argv $Name $CliArgs }
function Invoke-Uv([string]$Name, [string[]]$UvArgs, [bool]$Live) { Write-Argv $Name $UvArgs }

try {
    Invoke-Stage "SampleBlocks"
    Invoke-Stage "Plan"
} catch {
    ("ARGV FAILED: " + $_) | Out-Host
    exit 1
}
"ARGV OK" | Out-Host
exit 0
