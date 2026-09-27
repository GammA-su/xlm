#Requires -Version 5.1
<#
.SYNOPSIS
  Offline regression harness for the calibration driver's native wrapper.
.DESCRIPTION
  Dot-sources operator_calibrate_remaining.ps1 for FUNCTIONS ONLY (its main
  body is guarded and never executes under '.') with a dummy unit, then
  asserts the exit-code contract with stub native commands. No network, no
  acquisition, no uv sync: cmd.exe cases are pure local processes; the two
  Invoke-Step cases run the already-synced repo env with --offline
  --no-sync flags. Exits 0 when every case passes, 1 otherwise.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DriverPath,
    [Parameter(Mandatory = $true)][string]$WorkDir
)

$ErrorActionPreference = "Stop"

. "$DriverPath" -Unit simple_stories

$state = @{ Failed = $false }

function Check([string]$Name, [bool]$Condition, [string]$Detail = "") {
    if ($Condition) {
        ("PASS: " + $Name) | Out-Host
    } else {
        ("FAIL: " + $Name + " " + $Detail) | Out-Host
        $state.Failed = $true
    }
}

# C0: sourcing the driver must not weaken error handling.
Check "error-action-stays-stop" ($ErrorActionPreference -eq "Stop")

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("nativecap-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$global:LogDir = Join-Path $tmp "logs"
Set-Location -LiteralPath $WorkDir

function Invoke-CmdCase([string]$Base, [string]$Chain) {
    return Invoke-NativeCapture -FilePath "cmd.exe" `
        -ArgumentList @("/c", $Chain) -LogBase (Join-Path $tmp $Base) `
        -WorkingDirectory $env:SystemRoot
}

# C1: exit 0 + stderr text MUST succeed (the canary bug: this used to throw
# NativeCommandError under $ErrorActionPreference='Stop').
$r1 = Invoke-CmdCase "c1" "echo err-only 1>&2 & exit 0"
Check "exit0-stderr-succeeds" ($r1.ExitCode -eq 0) ("exit=" + $r1.ExitCode)
Check "exit0-stderr-captured" ($r1.Stderr -match "err-only")
Check "exit0-stderr-logged" (Test-Path -LiteralPath (Join-Path $tmp "c1.stderr.txt"))

# C2: exit 0 + stdout + stderr MUST succeed with both streams preserved.
$r2 = Invoke-CmdCase "c2" "echo out-text & echo err-text 1>&2 & exit 0"
Check "exit0-both-succeeds" ($r2.ExitCode -eq 0)
Check "exit0-stdout-preserved" ($r2.Stdout -match "out-text")
Check "exit0-stderr-preserved" ($r2.Stderr -match "err-text")

# C3: nonzero exit + stderr MUST be reported as failure.
$r3 = Invoke-CmdCase "c3" "echo boom 1>&2 & exit 3"
Check "nonzero-stderr-reported" ($r3.ExitCode -eq 3) ("exit=" + $r3.ExitCode)

# C4: nonzero exit + stdout MUST be reported as failure.
$r4 = Invoke-CmdCase "c4" "echo out-here & exit 4"
Check "nonzero-stdout-reported" ($r4.ExitCode -eq 4) ("exit=" + $r4.ExitCode)

# C5: end-to-end success through Invoke-Step (real offline xlm --help).
try {
    $helpOut = Invoke-Step "help-probe" @("--help") $false
    Check "step-success-returns" (-not [string]::IsNullOrWhiteSpace($helpOut))
} catch {
    Check "step-success-returns" $false ("threw: " + $_)
}

# C6: end-to-end fail-stop through Invoke-Step (real nonzero exit).
$threw = $false
try {
    Invoke-Step "bad-cmd" @("data", "__no_such_command_xyz__") $false | Out-Null
} catch {
    $threw = ("$_" -match "failed with exit")
}
Check "step-nonzero-failstops" $threw

if ($state.Failed) { exit 1 }
"ALL NATIVE WRAPPER CASES PASSED" | Out-Host
exit 0
