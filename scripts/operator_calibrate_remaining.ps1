#Requires -Version 5.1
<#
.SYNOPSIS
  Bounded calibration driver for the REMAINING Mix-01 units (Track A follow-up).
.DESCRIPTION
  Runs the pilot-capped calibration chain for one unit at a time:
  probe -> sample-blocks -> plan -> fetch -> status -> verify -> adapt
  -> summary -> record. Every command is displayed before it runs; any
  non-zero exit stops the script immediately.

  Safety properties (fail-closed):
  - Network (HF_HUB_OFFLINE / HF_DATASETS_OFFLINE) is 1 except inside the
    three explicitly bounded live calls (probe, sample-blocks footer
    discovery, fetch), restored in finally blocks.
  - All uv invocations use --offline --locked --no-sync (run -Stage Env
    once first to sync; later stages refuse loudly if the env is absent).
  - One scratch/output root per unit under $DataRoot\calib\<unit>.
  - Calibration stays pilot-capped (plan defaults); the script has NO
    max-bytes/max-records/max-output-disk parameters and performs NO
    production-scale fetch.
  - The script NEVER runs: data admit, production authorization, tokenizer
    commands, prepare --authorize, experiment/train/resume, or any push.
  - Restart-safe adoption per stage (scripts/calibration_adopt.py, offline):
    compatible probe evidence / row ranges / plans / adapted outputs /
    verified publications are REUSED with an explicit message; absent
    outputs run normally; incompatible, corrupt or incomplete outputs FAIL
    CLOSED (never deleted, overwritten, or bypassed with a fresh identity).
    Fetch journals resume natively; sample-blocks/plan reruns are
    same-hash no-ops. Record uses --adopt: identical re-records are
    no-ops (this covers the convergent essential triple; keep -Files
    identical across the three essential runs), divergent ones fail.
  - Native execution contract (Invoke-NativeCapture): stdout/stderr go to
    FILES via Start-Process redirection, never the PowerShell stream, so
    harmless native stderr can never raise NativeCommandError under
    $ErrorActionPreference='Stop'. Failure is determined SOLELY by the
    native exit code (0 = success even with stderr; nonzero = fail-stop).
    $ErrorActionPreference itself is never weakened.
  - Dot-sourcing this file under '.' loads functions only (main guarded);
    see tests/files/calibrate_driver_native.ps1.

  The three essential slices share one raw fetch each (the slice stamp is
  adapt-time metadata over identical rows); the script runs independent
  per-unit chains so every chain mirrors its production per-view plan.
  IFM general/planning record under view-qualified keys; combine them into
  the quota key with the documented one-liner before estimate (see report).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "essential_science", "essential_practical", "essential_prose",
        "synth_en_explanations", "nemotron_wiki_rewrite", "simple_stories",
        "finepdfs_en", "finewiki_en", "ifm_general", "ifm_planning"
    )]
    [string]$Unit,
    [Parameter(Mandatory = $false)]
    [string]$Files = "",
    [Parameter(Mandatory = $false)]
    [string]$Repo = "G:\Project\xlm-data-ultrax",
    [Parameter(Mandatory = $false)]
    [string]$DataRoot = "X:\XLM",
    [Parameter(Mandatory = $false)]
    [ValidateSet("All", "Env", "Probe", "SampleBlocks", "Plan", "Fetch", "Status",
        "Verify", "Adapt", "Summary", "Record")]
    [string]$Stage = "All"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Units = @{
    essential_science   = @{ Source = "essential_web"; Repository = "EssentialAI/essential-web-v1.0"; View = "essential_science"; Revision = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"; Adapter = "essential_web"; AdapterConfig = "essential_science"; AdapterSpec = "essential_web:essential_science"; DefaultFiles = "data/v1/train/00001.parquet"; RecordAs = @("essential_science", "essential_practical", "essential_prose") }
    essential_practical = @{ Source = "essential_web"; Repository = "EssentialAI/essential-web-v1.0"; View = "essential_practical"; Revision = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"; Adapter = "essential_web"; AdapterConfig = "essential_practical"; AdapterSpec = "essential_web:essential_practical"; DefaultFiles = "data/v1/train/00001.parquet"; RecordAs = @("essential_science", "essential_practical", "essential_prose") }
    essential_prose     = @{ Source = "essential_web"; Repository = "EssentialAI/essential-web-v1.0"; View = "essential_prose"; Revision = "ce4eccc7e9604667b6d7f32cb6274b8b41f3113d"; Adapter = "essential_web"; AdapterConfig = "essential_prose"; AdapterSpec = "essential_web:essential_prose"; DefaultFiles = "data/v1/train/00001.parquet"; RecordAs = @("essential_science", "essential_practical", "essential_prose") }
    synth_en_explanations = @{ Source = "synth"; Repository = "PleIAs/SYNTH"; View = "default"; Revision = "0d6813a2966662c39f22f0b9af28a0c1c9f7a437"; Adapter = "synth_en"; AdapterConfig = ""; AdapterSpec = "synth_en"; DefaultFiles = "synth_001.parquet"; RecordAs = @("synth_en_explanations") }
    nemotron_wiki_rewrite = @{ Source = "nemotron_specialized"; Repository = "nvidia/Nemotron-Pretraining-Specialized-v1"; View = "Nemotron-Pretraining-Wiki-Rewrite"; Revision = "9ed3718b5f2ae29074c5e34e64115432b7c4320f"; Adapter = "wiki_rewrite"; AdapterConfig = ""; AdapterSpec = "wiki_rewrite"; DefaultFiles = "Nemotron-Pretraining-Wiki-Rewrite/part_000003.parquet"; RecordAs = @("nemotron_wiki_rewrite") }
    simple_stories      = @{ Source = "simple_stories"; Repository = "SimpleStories/SimpleStories"; View = "default"; Revision = "e63b8adc3b1a1bdc7cac5b500d150b71346b0628"; Adapter = "simple_stories"; AdapterConfig = ""; AdapterSpec = "simple_stories"; DefaultFiles = "data/train-00003-of-00007.parquet"; RecordAs = @("simple_stories") }
    finepdfs_en         = @{ Source = "finepdfs_edu"; Repository = "HuggingFaceFW/finepdfs-edu"; View = "eng_Latn"; Revision = "9cfabe2127faca99b3d5c4dc6d1fcb397399ebde"; Adapter = "finepdfs_en"; AdapterConfig = ""; AdapterSpec = "finepdfs_en"; DefaultFiles = "data/eng_Latn/train/000_00083.parquet"; RecordAs = @("finepdfs_en") }
    finewiki_en         = @{ Source = "finewiki"; Repository = "HuggingFaceFW/finewiki"; View = "en"; Revision = "8bd13e72e6a002407649b3e898535f42ceb1aeb9"; Adapter = "finewiki_en"; AdapterConfig = ""; AdapterSpec = "finewiki_en"; DefaultFiles = "data/enwiki/000_00013.parquet"; RecordAs = @("finewiki_en") }
    ifm_general         = @{ Source = "ifm_behaviors"; Repository = "IFM/Pretrain-Behaviors"; View = "general"; Revision = "3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5"; Adapter = "ifm_general"; AdapterConfig = ""; AdapterSpec = "ifm_general"; DefaultFiles = "general/general_full.chunk0-bdbff8a5c6-00315.parquet"; RecordAs = @("ifm_general") }
    ifm_planning        = @{ Source = "ifm_behaviors"; Repository = "IFM/Pretrain-Behaviors"; View = "planning"; Revision = "3345e13d7f3f6d0ecb5fdd67b37aed289f3191f5"; Adapter = "ifm_planning"; AdapterConfig = ""; AdapterSpec = "ifm_planning"; DefaultFiles = "planning/planning.chunk0-160f3594ed-00416.parquet"; RecordAs = @("ifm_planning") }
}

$U = $Units[$Unit]
$FileList = if ($Files -ne "") { $Files } else { $U.DefaultFiles }
if ([string]::IsNullOrWhiteSpace($FileList)) {
    throw "No remote file list for unit '$Unit': pass -Files from probe review (never invented)."
}
$UnitRoot = Join-Path $DataRoot ("calib\" + $Unit)
$Scratch = Join-Path $UnitRoot "scratch"
$Raw = Join-Path $UnitRoot "raw"
$Canonical = Join-Path $UnitRoot "canonical"
$PlanPath = Join-Path $UnitRoot "plan.json"
$RowsPath = Join-Path $UnitRoot "rows.json"
$ReportPath = Join-Path $UnitRoot "rows.evidence.json"
$LogDir = Join-Path $UnitRoot "logs"
$CalibJson = Join-Path $DataRoot "calib\calibration.json"

$UvBase = @("run", "--offline", "--locked", "--no-sync", "--extra", "cpu", "--extra", "eval")

function Write-Command([string[]]$Argv) {
    "COMMAND: uv " + ($Argv -join " ") | Out-Host
}

function Invoke-NativeCapture(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(Mandatory = $true)][string[]]$ArgumentList,
    [Parameter(Mandatory = $true)][string]$LogBase,
    [string]$WorkingDirectory = ""
) {
    # Windows-safe native execution. Stdout/stderr are redirected to FILES,
    # never the PowerShell output stream, so harmless native stderr can NEVER
    # surface as a terminating NativeCommandError under
    # $ErrorActionPreference='Stop'. Failure is determined SOLELY by the
    # native process exit code. Ordinary cmdlet error handling is untouched.
    $parent = Split-Path -Parent $LogBase
    if ($parent -ne "" -and !(Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $stdoutFile = "$LogBase.stdout.txt"
    $stderrFile = "$LogBase.stderr.txt"
    $startArgs = @{
        FilePath = $FilePath
        ArgumentList = $ArgumentList
        NoNewWindow = $true
        Wait = $true
        PassThru = $true
        RedirectStandardOutput = $stdoutFile
        RedirectStandardError = $stderrFile
    }
    if ($WorkingDirectory -ne "") { $startArgs["WorkingDirectory"] = $WorkingDirectory }
    $proc = Start-Process @startArgs
    $stdout = ""
    $stderr = ""
    if (Test-Path -LiteralPath $stdoutFile) {
        $stdout = Get-Content -LiteralPath $stdoutFile -Raw -Encoding utf8
        if ($null -eq $stdout) { $stdout = "" }
    }
    if (Test-Path -LiteralPath $stderrFile) {
        $stderr = Get-Content -LiteralPath $stderrFile -Raw -Encoding utf8
        if ($null -eq $stderr) { $stderr = "" }
    }
    return @{ ExitCode = $proc.ExitCode; Stdout = $stdout; Stderr = $stderr }
}

function Invoke-Step([string]$Name, [string[]]$CliArgs, [bool]$Live) {
    $ts = Get-Date -Format "yyyyMMdd-HHmmss"
    if (!(Test-Path -LiteralPath $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    }
    $logBase = Join-Path $LogDir ("{0}-{1}" -f $Name, $ts)
    $savedHub = $env:HF_HUB_OFFLINE
    $savedDs = $env:HF_DATASETS_OFFLINE
    if ($Live) { $env:HF_HUB_OFFLINE = "0"; $env:HF_DATASETS_OFFLINE = "0" }
    try {
        $full = $UvBase + @("xlm") + $CliArgs
        Write-Command $full
        $cap = Invoke-NativeCapture -FilePath "uv" -ArgumentList $full `
            -LogBase $logBase -WorkingDirectory $Repo
        $combined = ("=== STDOUT ===`n" + $cap.Stdout + "`n=== STDERR ===`n" `
            + $cap.Stderr + "`n=== EXIT: " + $cap.ExitCode + " ===`n")
        [System.IO.File]::WriteAllText("$logBase.log", $combined, [System.Text.UTF8Encoding]::new($false))
        if ($cap.Stdout -ne "") { $cap.Stdout | Out-Host }
        if ($cap.Stderr -ne "") { $cap.Stderr | Out-Host }
        if ($cap.ExitCode -ne 0) { throw "$Name failed with exit $($cap.ExitCode) (log: $logBase.log)" }
        return $cap.Stdout
    } finally {
        $env:HF_HUB_OFFLINE = $savedHub
        $env:HF_DATASETS_OFFLINE = $savedDs
    }
}

function Invoke-Adopt([string[]]$AdoptArgs) {
    # Runs calibration_adopt.py (offline decision helper) through the same
    # file-redirected native capture. Returns the helper exit code directly:
    # 0 = no usable output, run the stage; 2 = compatible output reused;
    # anything else = fail closed (the helper already printed the reason).
    $full = $UvBase + @("python", "scripts/calibration_adopt.py") + $AdoptArgs
    Write-Command $full
    $ts = Get-Date -Format "yyyyMMdd-HHmmssfff"
    if (!(Test-Path -LiteralPath $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    }
    $cap = Invoke-NativeCapture -FilePath "uv" -ArgumentList $full `
        -LogBase (Join-Path $LogDir "adopt-$ts") -WorkingDirectory $Repo
    if ($cap.Stdout -ne "") { $cap.Stdout | Out-Host }
    if ($cap.Stderr -ne "") { $cap.Stderr | Out-Host }
    return $cap.ExitCode
}

function Invoke-Python([string]$Code) {
    $full = $UvBase + @("python", "-c", $Code)
    Write-Command $full
    $ts = Get-Date -Format "yyyyMMdd-HHmmssfff"
    if (!(Test-Path -LiteralPath $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    }
    $cap = Invoke-NativeCapture -FilePath "uv" -ArgumentList $full `
        -LogBase (Join-Path $LogDir "python-$ts") -WorkingDirectory $Repo
    if ($cap.ExitCode -ne 0) {
        if ($cap.Stdout -ne "") { $cap.Stdout | Out-Host }
        if ($cap.Stderr -ne "") { $cap.Stderr | Out-Host }
        throw "python helper failed with exit $($cap.ExitCode)"
    }
    return $cap.Stdout
}

if ($MyInvocation.InvocationName -ne '.') {

Set-Location -LiteralPath $Repo

# ArtifactStore reads XLM_HOME; default it deterministically so the CLI and
# the adoption checks below resolve the identical store. An explicitly set
# operator value is always respected.
if ([string]::IsNullOrWhiteSpace($env:XLM_HOME)) {
    $env:XLM_HOME = Join-Path $DataRoot "xlm-home"
    "XLM_HOME defaulted to $env:XLM_HOME (was unset)" | Out-Host
}
$XlmHome = $env:XLM_HOME

switch ($Stage) {
    { $_ -in "All", "Env" } {
        $syncArgs = @("sync", "--offline", "--locked", "--extra", "cpu", "--extra", "eval")
        Write-Command $syncArgs
        if (!(Test-Path -LiteralPath $LogDir)) {
            New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
        }
        $ts = Get-Date -Format "yyyyMMdd-HHmmss"
        $syncBase = Join-Path $LogDir "env-$ts"
        $cap = Invoke-NativeCapture -FilePath "uv" -ArgumentList $syncArgs `
            -LogBase $syncBase -WorkingDirectory $Repo
        if ($cap.Stdout -ne "") { $cap.Stdout | Out-Host }
        if ($cap.Stderr -ne "") { $cap.Stderr | Out-Host }
        if ($cap.ExitCode -ne 0) { throw "Env sync failed with exit $($cap.ExitCode) (log: $syncBase.log)" }
        $combined = ("=== STDOUT ===`n" + $cap.Stdout + "`n=== STDERR ===`n" `
            + $cap.Stderr + "`n=== EXIT: " + $cap.ExitCode + " ===`n")
        [System.IO.File]::WriteAllText("$syncBase.log", $combined, [System.Text.UTF8Encoding]::new($false))
        "Env OK: $Repo" | Out-Host
    }
    { $_ -in "All", "Probe" } {
        $decision = Invoke-Adopt @("probe", "--store", $XlmHome, "--source", $U.Source,
            "--view", $U.View, "--revision", $U.Revision, "--repository", $U.Repository)
        if ($decision -eq 2) { "existing compatible probe evidence reused" | Out-Host }
        elseif ($decision -eq 0) {
            Invoke-Step "probe" @("data", "probe", "--catalog", "manifests/datasets.catalog.yaml",
                "--source", $U.Source, "--view", $U.View, "--live", "--budget-mib", "16",
                "--probe-id", "cal01", "--json") $true | Out-Null
        }
        else { throw "probe adoption refused; no evidence deleted, no fresh identity minted" }
    }
    { $_ -in "All", "SampleBlocks" } {
        $decision = Invoke-Adopt @("sample-blocks", "--rows", $RowsPath, "--report", $ReportPath,
            "--source", $U.Source, "--view", $U.View, "--revision", $U.Revision,
            "--seed", "20260918", "--files-csv", $FileList)
        if ($decision -eq 2) { "existing compatible row ranges reused" | Out-Host }
        elseif ($decision -eq 0) {
            Invoke-Step "sample-blocks" @("data", "sample-blocks", "--source", $U.Source,
                "--view", $U.View, "--revision", $U.Revision, "--files", $FileList,
                "--seed", "20260918", "--mode", "rowgroup", "--target-records", "1000",
                "--output", $RowsPath, "--report", $ReportPath) $true | Out-Null
        }
        else { throw "sample-blocks adoption refused; remove the outputs explicitly to redo them" }
    }
    { $_ -in "All", "Plan" } {
        $decision = Invoke-Adopt @("plan", "--plan", $PlanPath, "--rows", $RowsPath,
            "--source", $U.Source, "--view", $U.View, "--revision", $U.Revision,
            "--seed", "20260918", "--files-csv", $FileList, "--mode", "selected_records")
        if ($decision -eq 2) { "existing compatible plan reused" | Out-Host }
        elseif ($decision -eq 0) {
            Invoke-Step "plan" @("data", "plan", "--source", $U.Source, "--view", $U.View,
                "--catalog", "manifests/datasets.catalog.yaml", "--files", $FileList,
                "--mode", "selected_records", "--row-ranges", $RowsPath,
                "--adapter-spec", $U.AdapterSpec, "--seed", "20260918", "--attempt", "1",
                "--pilot-approved", "--output", $PlanPath) $false | Out-Null
        }
        else { throw "plan adoption refused; use a new reviewed plan path to redo it" }
        Invoke-Python ("from xlm.data.acquisition.plan import load_acquisition_plan; " +
            "p=load_acquisition_plan(r'" + $PlanPath + "'); " +
            "print(p.plan_id, p.plan_hash, p.revision)") | Out-Host
    }
    { $_ -in "All", "Fetch" } {
        Invoke-Step "fetch" @("data", "fetch", "--plan", $PlanPath,
            "--output-dir", $Raw, "--scratch-dir", $Scratch, "--pilot-approved") $true | Out-Null
    }
    { $_ -in "All", "Status" } {
        Invoke-Step "status" @("data", "status", "--plan", $PlanPath,
            "--scratch-dir", $Scratch) $false | Out-Null
    }
    { $_ -in "All", "Verify" } {
        $decision = Invoke-Adopt @("verify", "--store", $XlmHome, "--plan", $PlanPath,
            "--output-dir", $Raw)
        if ($decision -eq 2) {
            "existing verified publication reused; re-confirming without republishing" | Out-Host
            Invoke-Step "verify" @("data", "verify", "--plan", $PlanPath,
                "--output-dir", $Raw, "--scratch-dir", $Scratch, "--json",
                "--no-publish") $false | Out-Null
        }
        elseif ($decision -eq 0) {
            Invoke-Step "verify" @("data", "verify", "--plan", $PlanPath,
                "--output-dir", $Raw, "--scratch-dir", $Scratch, "--json") $false | Out-Null
        }
        else { throw "verify adoption refused; outputs do not match the published artifact" }
    }
    { $_ -in "All", "Adapt" } {
        $decision = Invoke-Adopt @("adapt", "--output-dir", $Canonical, "--plan", $PlanPath)
        if ($decision -eq 2) { "existing compatible adapted outputs reused" | Out-Host }
        elseif ($decision -eq 0) {
            $adaptArgs = @("data", "adapt", "--plan", $PlanPath, "--adapter", $U.Adapter,
                "--input", (Join-Path $Raw "selected_records.jsonl"),
                "--output-dir", $Canonical, "--on-reject", "record")
            if ($U.AdapterConfig -ne "") { $adaptArgs += @("--adapter-config", $U.AdapterConfig) }
            Invoke-Step "adapt" $adaptArgs $false | Out-Null
        }
        else { throw "adapt adoption refused; use a fresh output dir to redo it" }
    }
    { $_ -in "All", "Summary" } {
        Get-Content -LiteralPath (Join-Path $Canonical "adaptation_summary.json") -Raw -Encoding utf8 | Out-Host
    }
    { $_ -in "All", "Record" } {
        $statusText = Invoke-Step "status-json" @("data", "status", "--plan", $PlanPath,
            "--scratch-dir", $Scratch, "--json") $false
        $jsonOnly = $statusText.Substring($statusText.IndexOf("{"))
        $st = $jsonOnly | ConvertFrom-Json
        $summary = Get-Content -LiteralPath (Join-Path $Canonical "adaptation_summary.json") -Raw -Encoding utf8 | ConvertFrom-Json
        $canonOut = Invoke-Python ("import json; d=[json.loads(l) for l in open(r'" +
            (Join-Path $Canonical "documents.jsonl") + "',encoding='utf-8') if l.strip()]; " +
            "print(sum(x.get('utf8_byte_count',0) for x in d))")
        $canonBytes = ($canonOut -split "`n" | Where-Object { $_ -match '^\d+$' } | Select-Object -Last 1)
        if ([string]::IsNullOrWhiteSpace($canonBytes)) { throw "could not parse canonical byte count" }
        foreach ($key in $U.RecordAs) {
            $recordArgs = $UvBase + @("python", "scripts/mix01_inventory.py", "record",
                "--calibration", $CalibJson, "--source", $key,
                "--records-sampled", "$($summary.total_input_records)",
                "--accepted", "$($summary.accepted_records)",
                "--rejected", "$($summary.rejected_records)",
                "--transferred-bytes", "$($st.transferred_bytes)",
                "--canonical-bytes", "$canonBytes",
                "--adopt")
            Write-Command $recordArgs
            $ts = Get-Date -Format "yyyyMMdd-HHmmssfff"
            $recBase = Join-Path $LogDir ("record-{0}-{1}" -f $key, $ts)
            $cap = Invoke-NativeCapture -FilePath "uv" -ArgumentList $recordArgs `
                -LogBase $recBase -WorkingDirectory $Repo
            $combined = ("=== STDOUT ===`n" + $cap.Stdout + "`n=== STDERR ===`n" `
                + $cap.Stderr + "`n=== EXIT: " + $cap.ExitCode + " ===`n")
            [System.IO.File]::WriteAllText("$recBase.log", $combined, [System.Text.UTF8Encoding]::new($false))
            if ($cap.Stdout -ne "") { $cap.Stdout | Out-Host }
            if ($cap.Stderr -ne "") { $cap.Stderr | Out-Host }
            if ($cap.ExitCode -ne 0) { throw "record ($key) failed with exit $($cap.ExitCode) (log: $recBase.log)" }
        }
        "Record OK: $($U.RecordAs -join ', ') -> $CalibJson" | Out-Host
        if ($Unit -like "ifm_*") {
            "NOTE: ifm_general + ifm_planning are recorded separately; combine them into" | Out-Host
            "ifm_behaviors_general_planning with the report one-liner before estimate." | Out-Host
        }
    }
}

"Done: unit $Unit stage $Stage" | Out-Host

} # end main guard (dot-sourcing under '.' loads functions only, for tests)
