#Requires -Version 5.1
<#
.SYNOPSIS
  Thin staged operator driver for the first bounded real-data acquisition pilot.
.DESCRIPTION
  Every stage invokes the real XLM CLI (python -m xlm.cli.main) with explicit
  arguments and checks native exit codes. Live stages (Discover, Fetch) run
  only when explicitly selected. Session state persists in <Home>/pilot-state.json
  so a new terminal resumes with -Stage Resume and no shell variables.
  The operator runs every live operation personally; nothing here approves,
  admits, or trains anything automatically.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Env", "Catalog", "Select", "Discover", "Admit",
        "Files", "Plan", "Confirm", "Fetch", "Status", "Verify", "Preview",
        "Summary", "Resume")]
    [string]$Stage,
    [string]$Repo = "D:\Project\xlm-final-integration",
    [string]$HomeDir = "D:\Project\xlm-operator-pilot"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PyExe = Join-Path $Repo ".venv-final\Scripts\python.exe"
$StatePath = Join-Path $HomeDir "pilot-state.json"
$LogDir = Join-Path $HomeDir "logs"

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $dir = Split-Path -Parent $Path
    if ($dir -ne "" -and !(Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

function Read-State {
    if (!(Test-Path -LiteralPath $StatePath)) { return @{} }
    $read = Get-Content -LiteralPath $StatePath -Raw -Encoding utf8 | ConvertFrom-Json
    $table = @{}
    foreach ($property in $read.PSObject.Properties) { $table[$property.Name] = $property.Value }
    return $table
}

function Write-State([hashtable]$State) {
    Write-Utf8NoBom $StatePath ($State | ConvertTo-Json -Depth 6)
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
}

function Invoke-XlmStep([string]$StepName, [string[]]$CliArgs, [hashtable]$ExtraEnv) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $log = Join-Path $LogDir ("{0}-{1}.log" -f $StepName, $stamp)
    if (!(Test-Path -LiteralPath $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    }
    $stdoutFile = "$log.stdout.txt"
    $stderrFile = "$log.stderr.txt"
    $saved = @{}
    foreach ($key in $ExtraEnv.Keys) {
        if (Test-Path -Path "env:$key") { $saved[$key] = (Get-Item -Path "env:$key").Value }
        Set-Item -Path "env:$key" -Value $ExtraEnv[$key]
    }
    try {
        $proc = Start-Process -FilePath $PyExe `
            -ArgumentList (@("-m", "xlm.cli.main") + $CliArgs) `
            -WorkingDirectory $Repo -RedirectStandardOutput $stdoutFile `
            -RedirectStandardError $stderrFile -Wait -PassThru -NoNewWindow
    } finally {
        foreach ($key in $ExtraEnv.Keys) {
            if ($saved.ContainsKey($key)) { Set-Item -Path "env:$key" -Value $saved[$key] }
            else { Remove-Item -Path "env:$key" -ErrorAction SilentlyContinue }
        }
    }
    $stdoutText = ""
    if (Test-Path -LiteralPath $stdoutFile) {
        $stdoutText = Get-Content -LiteralPath $stdoutFile -Raw -Encoding utf8
        if ($null -eq $stdoutText) { $stdoutText = "" }
    }
    $stderrText = ""
    if (Test-Path -LiteralPath $stderrFile) {
        $stderrText = Get-Content -LiteralPath $stderrFile -Raw -Encoding utf8
        if ($null -eq $stderrText) { $stderrText = "" }
    }
    Write-Utf8NoBom $log ("=== STDOUT ===`n" + $stdoutText + "`n=== STDERR ===`n" + $stderrText)
    $stdoutText | Out-Host
    if ($proc.ExitCode -ne 0) {
        throw "$StepName failed with exit $($proc.ExitCode). Full output preserved at: $log"
    }
    Remove-Item -LiteralPath $stdoutFile, $stderrFile -ErrorAction SilentlyContinue
    return @{ Text = $stdoutText; Log = $log }
}

Set-Location -LiteralPath $Repo
Assert-True (Test-Path -LiteralPath $PyExe) "venv python not found: $PyExe"
if (!(Test-Path -LiteralPath $HomeDir)) {
    New-Item -ItemType Directory -Path $HomeDir -Force | Out-Null
}

$VenvEnv = @{
    "UV_PROJECT_ENVIRONMENT" = (Join-Path $Repo ".venv-final")
    "UV_OFFLINE" = "1"
    "HF_HUB_OFFLINE" = "1"
    "HF_DATASETS_OFFLINE" = "1"
    "OMP_NUM_THREADS" = "1"
    "MKL_NUM_THREADS" = "1"
}
$HomeEnv = @{ "XLM_HOME" = $HomeDir }

switch ($Stage) {
    "Env" {
        $head = (& git rev-parse HEAD 2>&1); Assert-True ($LASTEXITCODE -eq 0) "git rev-parse failed"
        $branch = (& git branch --show-current 2>&1); Assert-True ($LASTEXITCODE -eq 0) "git branch failed"
        "repo=$Repo branch=$branch head=$head" | Out-Host
        $syncEnv = @{} + $VenvEnv
        foreach ($key in $syncEnv.Keys) { Set-Item -Path "env:$key" -Value $syncEnv[$key] }
        try {
            & uv sync --offline --locked --extra cpu --extra eval
            Assert-True ($LASTEXITCODE -eq 0) "uv sync failed"
        } finally {
            foreach ($key in $syncEnv.Keys) { Remove-Item -Path "env:$key" -ErrorAction SilentlyContinue }
        }
        & $PyExe -c "import sys,xlm; print(sys.version.split()[0]); print(xlm.__file__)"
        Assert-True ($LASTEXITCODE -eq 0) "interpreter/module check failed"
        $state = Read-State
        $state["repo"] = $Repo
        $state["home"] = $HomeDir
        $state["head"] = "$head".Trim()
        Write-State $state
        "Env OK. State: $StatePath" | Out-Host
    }
    "Catalog" {
        $env = @{} + $VenvEnv + $HomeEnv
        $result = Invoke-XlmStep "catalog" @("data", "sources", "--json") $env
        $catalog = $result.Text | ConvertFrom-Json
        Assert-True ($catalog.sources.Count -ge 1) "catalog lists no sources"
        Write-Utf8NoBom (Join-Path $HomeDir "catalog.json") ($catalog | ConvertTo-Json -Depth 8)
        "candidate sources:" | Out-Host
        foreach ($source in $catalog.sources) {
            "{0,4} {1,-24} {2}" -f $source.candidate_number, $source.source_id, $source.provider | Out-Host
        }
        $state = Read-State
        $state["catalog"] = Join-Path $HomeDir "catalog.json"
        Write-State $state
    }
    "Select" {
        $state = Read-State
        Assert-True ($state.ContainsKey("catalog")) "run -Stage Catalog first"
        $catalog = Get-Content -LiteralPath $state["catalog"] -Raw -Encoding utf8 | ConvertFrom-Json
        $ids = @($catalog.sources | ForEach-Object { $_.source_id })
        $sourceId = (Read-Host "source_id (one of: $($ids -join ', '))").Trim()
        Assert-True ($ids -contains $sourceId) "unknown source_id: $sourceId"
        $viewId = (Read-Host "view [default]").Trim()
        if ($viewId -eq "") { $viewId = "default" }
        $state["source_id"] = $sourceId
        $state["view_id"] = $viewId
        Write-State $state
        "selected ${sourceId}:${viewId}" | Out-Host
    }
    "Discover" {
        $state = Read-State
        Assert-True ($state.ContainsKey("source_id")) "run -Stage Select first"
        $liveEnv = @{} + $VenvEnv + $HomeEnv
        $liveEnv["HF_HUB_OFFLINE"] = "0"
        $liveEnv["HF_DATASETS_OFFLINE"] = "0"
        try {
            $result = Invoke-XlmStep "probe" @(
                "data", "probe", "--source", $state["source_id"],
                "--view", $state["view_id"], "--live", "--budget-mib", "16",
                "--probe-id", "pilot01", "--json"
            ) $liveEnv
        } finally {
            $liveEnv["HF_HUB_OFFLINE"] = "1"
            $liveEnv["HF_DATASETS_OFFLINE"] = "1"
        }
        $probePath = Join-Path $HomeDir "probe.json"
        Write-Utf8NoBom $probePath $result.Text
        $probe = $result.Text | ConvertFrom-Json
        $revision = $probe.immutable_revision
        Assert-True (![string]::IsNullOrWhiteSpace("$revision")) (
            "probe did not resolve an immutable revision. Available keys: " +
            (($probe.PSObject.Properties | ForEach-Object { $_.Name }) -join ", ") +
            ". Stop: pick another source or resolve the listed requirements."
        )
        Assert-True ("$revision" -notin @("latest", "master", "main", "head", "")) (
            "probe returned mutable revision '$revision'; refusing."
        )
        "revision=$revision fingerprint=$($probe.probe_fingerprint)" | Out-Host
        "observed_files=$($probe.observed_files_count) license=$($probe.declared_license)" | Out-Host
        "unresolved=$($probe.unresolved_requirements -join '; ') reason=$($probe.reason)" | Out-Host
        $state["probe"] = $probePath
        $state["revision"] = "$revision"
        $state["fingerprint"] = "$($probe.probe_fingerprint)"
        Write-State $state
    }
    "Admit" {
        $state = Read-State
        Assert-True ($state.ContainsKey("revision")) "run -Stage Discover first"
        "Pilot execution does NOT require production admission. Record a decision only if you are performing admission review." | Out-Host
        $record = (Read-Host "record an admission decision now? (yes/no) [no]").Trim().ToLower()
        if ($record -notin @("yes", "y")) { "skipped by operator" | Out-Host; return }
        $notes = (Read-Host "review notes (license/provenance/benchmark-risk basis)").Trim()
        Assert-True ($notes -ne "") "notes are required"
        $risk = (Read-Host "benchmark risk (clean/suspect/disabled_pending_audit)").Trim()
        Assert-True ($risk -in @("clean", "suspect", "disabled_pending_audit")) "invalid risk value"
        $license = (Read-Host "license review (approved/pending/rejected) [pending]").Trim()
        if ($license -eq "") { $license = "pending" }
        Assert-True ($license -in @("approved", "pending", "rejected")) "invalid license value"
        $decision = (Read-Host "decision (approve/reject) [reject]").Trim()
        if ($decision -eq "") { $decision = "reject" }
        Assert-True ($decision -in @("approve", "reject")) "invalid decision"
        $adapter = (Read-Host "tested adapter identifier (e.g. JsonlAdapter)").Trim()
        Assert-True ($adapter -ne "") "adapter identifier is required"
        $env = @{} + $VenvEnv + $HomeEnv
        Invoke-XlmStep "admit" @(
            "data", "admit", "--source", $state["source_id"], "--adapter", $adapter,
            "--notes", $notes, "--view", $state["view_id"], "--decision", $decision,
            "--license-review", $license, "--benchmark-risk", $risk
        ) $env | Out-Null
        $state["admission_decision"] = $decision
        Write-State $state
    }
    "Files" {
        $state = Read-State
        Assert-True ($state.ContainsKey("revision")) "run -Stage Discover first"
        $probe = Get-Content -LiteralPath $state["probe"] -Raw -Encoding utf8 | ConvertFrom-Json
        "probe observed_files_count=$($probe.observed_files_count)" | Out-Host
        $filename = (Read-Host "exact corpus filename from your source review").Trim()
        Assert-True ($filename -ne "" -and $filename -notmatch '[*?]') "invalid filename"
        $startText = (Read-Host "range start (integer, >= 0) [0]").Trim()
        if ($startText -eq "") { $startText = "0" }
        $stopText = (Read-Host "range stop (integer, > start) [3]").Trim()
        if ($stopText -eq "") { $stopText = "3" }
        $start = 0; $stop = 0
        Assert-True ([int]::TryParse($startText, [ref]$start) -and [int]::TryParse($stopText, [ref]$stop)) "range must be integers"
        Assert-True ($start -ge 0 -and $stop -gt $start) "need 0 <= start < stop"
        $rowsJson = "{`"$filename`": [$start, $stop]}"
        $rowsPath = Join-Path $HomeDir "rows.json"
        Write-Utf8NoBom $rowsPath ($rowsJson + "`n")
        $limits = [ordered]@{
            max_transferred_bytes = 16777216
            max_decompressed_bytes = 33554432
            max_records = 100
            max_temp_disk_bytes = 67108864
            max_output_disk_bytes = 67108864
            max_requests = 20
            max_retries = 1
            max_workers = 1
            per_request_timeout_seconds = 10.0
            overall_deadline_seconds = 600.0
            max_decompression_ratio = 15.0
            max_record_bytes = 1048576
            max_parser_bytes = 33554432
            max_scanned_records = 1000
        }
        $limitsPath = Join-Path $HomeDir "limits.json"
        Write-Utf8NoBom $limitsPath (($limits | ConvertTo-Json -Depth 3) + "`n")
        "wrote $rowsPath : $rowsJson" | Out-Host
        "wrote $limitsPath (16 MiB transfer, 100 records)" | Out-Host
        $state["filename"] = $filename
        $state["row_start"] = $start
        $state["row_stop"] = $stop
        $state["rows"] = $rowsPath
        $state["limits"] = $limitsPath
        Write-State $state
    }
    "Plan" {
        $state = Read-State
        foreach ($key in @("source_id", "view_id", "filename", "rows", "limits")) {
            Assert-True ($state.ContainsKey($key)) "run -Stage Files first (missing $key)"
        }
        $planPath = Join-Path $HomeDir "pilot-plan.json"
        Assert-True (!(Test-Path -LiteralPath $planPath)) (
            "refusing to overwrite existing immutable plan $planPath; use -Stage Resume"
        )
        $env = @{} + $VenvEnv + $HomeEnv
        Invoke-XlmStep "plan" @(
            "data", "plan", "--source", $state["source_id"], "--files", $state["filename"],
            "--mode", "selected_records", "--row-ranges", $state["rows"],
            "--limits", $state["limits"], "--output", $planPath
        ) $env | Out-Null
        $plan = Get-Content -LiteralPath $planPath -Raw -Encoding utf8 | ConvertFrom-Json
        Assert-True ($plan.revision -eq $state["revision"]) (
            "plan revision $($plan.revision) differs from probed $($state['revision']); refusing"
        )
        $state["plan"] = $planPath
        $state["plan_id"] = "$($plan.plan_id)"
        $state["plan_hash"] = "$($plan.plan_hash)"
        $state["raw_dir"] = Join-Path $HomeDir ("acquisition\" + $state["plan_id"] + "\raw")
        $state["scratch_dir"] = Join-Path $HomeDir ("acquisition\" + $state["plan_id"] + "\scratch")
        Write-State $state
    }
    "Confirm" {
        $state = Read-State
        Assert-True ($state.ContainsKey("plan")) "run -Stage Plan first"
        $plan = Get-Content -LiteralPath $state["plan"] -Raw -Encoding utf8 | ConvertFrom-Json
        "plan_id      : $($plan.plan_id)" | Out-Host
        "source       : $($plan.source_id):$($plan.view_id) revision $($plan.revision)" | Out-Host
        "files        : $($plan.selected_files -join ', ')" | Out-Host
        "row_ranges   : $(($plan.row_ranges | ConvertTo-Json -Compress))" | Out-Host
        "transfer<=   : $($plan.limits.max_transferred_bytes) records<= $($plan.limits.max_records)" | Out-Host
        "output<=     : $($plan.limits.max_output_disk_bytes) requests<= $($plan.limits.max_requests)" | Out-Host
        "plan_hash    : $($plan.plan_hash)" | Out-Host
        "pilot scope  : $($plan.is_pilot)" | Out-Host
        $answer = (Read-Host "approve acquisition of exactly this plan and limits? (type YES)").Trim()
        Assert-True ($answer -ceq "YES") "not approved; stopping before acquisition"
        $state["confirmed"] = $true
        Write-State $state
    }
    "Fetch" {
        $state = Read-State
        Assert-True ($state["confirmed"] -eq $true) "run -Stage Confirm first"
        $liveEnv = @{} + $VenvEnv + $HomeEnv
        $liveEnv["HF_HUB_OFFLINE"] = "0"
        $liveEnv["HF_DATASETS_OFFLINE"] = "0"
        try {
            Invoke-XlmStep "fetch" @(
                "data", "fetch", "--plan", $state["plan"],
                "--output-dir", $state["raw_dir"], "--scratch-dir", $state["scratch_dir"],
                "--pilot-approved"
            ) $liveEnv | Out-Null
        } finally {
            $liveEnv["HF_HUB_OFFLINE"] = "1"
            $liveEnv["HF_DATASETS_OFFLINE"] = "1"
        }
    }
    "Status" {
        $state = Read-State
        Assert-True ($state.ContainsKey("plan")) "no plan in state; run -Stage Plan first"
        $env = @{} + $VenvEnv + $HomeEnv
        $result = Invoke-XlmStep "status" @(
            "data", "status", "--plan", $state["plan"],
            "--scratch-dir", $state["scratch_dir"], "--json"
        ) $env
        $status = $result.Text | ConvertFrom-Json
        "status={0} records={1} transferred={2} requests={3}" -f $status.status,
            $status.records_acquired, $status.transferred_bytes, $status.requests_made | Out-Host
    }
    "Verify" {
        $state = Read-State
        Assert-True ($state.ContainsKey("plan")) "no plan in state; run -Stage Plan first"
        $env = @{} + $VenvEnv + $HomeEnv
        $result = Invoke-XlmStep "verify" @(
            "data", "verify", "--plan", $state["plan"],
            "--output-dir", $state["raw_dir"], "--scratch-dir", $state["scratch_dir"], "--json"
        ) $env
        $receipt = $result.Text | ConvertFrom-Json
        $receiptPath = Join-Path $HomeDir "receipt.json"
        Write-Utf8NoBom $receiptPath ($receipt | ConvertTo-Json -Depth 8)
        "receipt={0} eligibility={1} plan_hash={2}" -f $receipt.receipt_id,
            $receipt.eligibility, $receipt.plan_hash | Out-Host
        foreach ($file in $receipt.files) {
            "file={0} bytes={1} records={2} sha256={3}" -f $file.relative_path,
                $file.size_bytes, $file.record_count, $file.locally_computed_sha256 | Out-Host
        }
        $state["receipt"] = $receiptPath
        $state["receipt_id"] = "$($receipt.receipt_id)"
        Write-State $state
    }
    "Preview" {
        $state = Read-State
        Assert-True ($state.ContainsKey("receipt")) "run -Stage Verify first"
        $selected = Join-Path $state["raw_dir"] "selected_records.jsonl"
        Assert-True (Test-Path -LiteralPath $selected) "selected artifact missing: $selected"
        & $PyExe -c "import json,sys; rows=[json.loads(l) for l in open(r'$selected',encoding='utf-8')][:3]; [print(json.dumps({'id': r.get('id'), 'loc': r.get('_xlm_acquisition')}, ensure_ascii=False)) for r in rows]"
        Assert-True ($LASTEXITCODE -eq 0) "preview failed"
    }
    "Summary" {
        $state = Read-State
        foreach ($key in @("source_id", "view_id", "revision", "filename", "plan_id", "receipt_id")) {
            Assert-True ($state.ContainsKey($key)) "incomplete pilot state (missing $key)"
        }
        $receipt = Get-Content -LiteralPath $state["receipt"] -Raw -Encoding utf8 | ConvertFrom-Json
        "source    : $($state['source_id']):$($state['view_id']) revision $($state['revision'])" | Out-Host
        "file/range: $($state['filename']) [$($state['row_start']), $($state['row_stop']))" | Out-Host
        "plan      : $($state['plan_id']) hash $($state['plan_hash'])" | Out-Host
        "receipt   : $($state['receipt_id']) eligibility $($receipt.eligibility)" | Out-Host
        "records   : acquired; see receipt files[].record_count" | Out-Host
        "raw       : $($state['raw_dir'])" | Out-Host
        "status    : pilot-only; no training, admission, or certification performed" | Out-Host
    }
    "Resume" {
        $state = Read-State
        Assert-True ($state.ContainsKey("plan")) "no saved plan; start from -Stage Plan in any terminal"
        Assert-True ($state["confirmed"] -eq $true) "plan was never confirmed; rerun -Stage Confirm"
        "resuming plan $($state['plan_id']) with identical roots, journal, allowance, deadline" | Out-Host
        $liveEnv = @{} + $VenvEnv + $HomeEnv
        $liveEnv["HF_HUB_OFFLINE"] = "0"
        $liveEnv["HF_DATASETS_OFFLINE"] = "0"
        try {
            Invoke-XlmStep "fetch-resume" @(
                "data", "fetch", "--plan", $state["plan"],
                "--output-dir", $state["raw_dir"], "--scratch-dir", $state["scratch_dir"],
                "--pilot-approved"
            ) $liveEnv | Out-Null
        } finally {
            $liveEnv["HF_HUB_OFFLINE"] = "1"
            $liveEnv["HF_DATASETS_OFFLINE"] = "1"
        }
        $env = @{} + $VenvEnv + $HomeEnv
        Invoke-XlmStep "status-resume" @(
            "data", "status", "--plan", $state["plan"],
            "--scratch-dir", $state["scratch_dir"], "--json"
        ) $env | Out-Null
    }
}