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

# C7: quoting unit rules for ConvertTo-NativeArgument.
Check "quote-plain" ((ConvertTo-NativeArgument "plain") -eq "plain")
Check "quote-empty" ((ConvertTo-NativeArgument "") -eq '""')
Check "quote-space" ((ConvertTo-NativeArgument "has space") -eq '"has space"')
Check "quote-semicolon-preserved" ((ConvertTo-NativeArgument "a;b") -eq "a;b")
Check "quote-quote-escaped" ((ConvertTo-NativeArgument 'say "hi"') -eq '"say \"hi\""')
Check "quote-trailing-backslash" ((ConvertTo-NativeArgument 'trail\') -eq 'trail\')

# C8: end-to-end argv round-trip through the real wrapper chain
# (powershell -> Start-Process -> uv -> python), including a -c-shaped
# payload, spaced paths, backslashes and Unicode, each as ONE argv item.
$nasty = @(
    "plain",
    "has space",
    "semi;colon",
    'quote"inside',
    "back\slash",
    "trail\",
    "C:\Path With\Spaces\file.json",
    "unicode-日本語-🌊",
    "mix'ed`"all; together\",
    'from x import y; print("hi; ok")'
)
$echoArgs = $UvBase + @("python", "tests/files/echo_argv.py") + $nasty
$echoBase = Join-Path $tmp "echo"
$echoCap = Invoke-NativeCapture -FilePath "uv" -ArgumentList $echoArgs `
    -LogBase $echoBase -WorkingDirectory $WorkDir
Check "argv-roundtrip-exit" ($echoCap.ExitCode -eq 0) ("exit=" + $echoCap.ExitCode)
# NOTE: exactness is decided by digest, not by decoding the JSON line:
# PS 5.1 ConvertFrom-Json mangles \u escapes inconsistently AND wraps
# piped top-level arrays (both proven), so neither form is trustworthy here.
$echoLines = $echoCap.Stdout -split "`r?`n"
$countLine = ($echoLines | Where-Object { $_ -match '^COUNT:\d+\s*$' } | Select-Object -Last 1)
Check "argv-roundtrip-count" ($countLine -eq ("COUNT:" + $nasty.Count)) ("got '" + $countLine + "'")
$digestLine = ($echoLines | Where-Object { $_ -match '^SHA256:[0-9a-f]{64}\s*$' } | Select-Object -Last 1)
$utf8 = [System.Text.Encoding]::UTF8
$hasher = [System.Security.Cryptography.SHA256]::Create()
$expectedDigest = ([BitConverter]::ToString(
    $hasher.ComputeHash($utf8.GetBytes(($nasty -join "`0"))))).Replace("-", "").ToLower()
Check "argv-roundtrip-digest" ($digestLine -eq ("SHA256:" + $expectedDigest)) ("got '" + $digestLine + "'")

if ($state.Failed) { exit 1 }
"ALL NATIVE WRAPPER CASES PASSED" | Out-Host
exit 0
