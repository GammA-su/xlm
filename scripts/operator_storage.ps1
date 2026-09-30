#Requires -Version 5.1
# Dot-source this file in the authoritative checkout before operator commands.
[CmdletBinding()]
param([string]$Config = '')
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($Config)) {
    $Config = Join-Path $PSScriptRoot '../recipes/operator/storage.json'
}
$storage = Get-Content -LiteralPath $Config -Raw -Encoding utf8 | ConvertFrom-Json
$env:XLM_DATA_ROOT = [IO.Path]::GetFullPath($storage.data_root)
$env:XLM_HOME = Join-Path $env:XLM_DATA_ROOT $storage.artifact_store_relative
$env:HF_HOME = Join-Path $env:XLM_DATA_ROOT $storage.cache_relative
$env:HF_DATASETS_CACHE = Join-Path $env:HF_HOME 'datasets'
$env:TMP = Join-Path $env:XLM_DATA_ROOT $storage.temporary_relative
$env:TEMP = $env:TMP
# Optional fast scratch on another volume: bounded temporary files only, never durable data.
if ($storage.PSObject.Properties.Name -contains 'scratch_root') {
    $env:XLM_SCRATCH_ROOT = [IO.Path]::GetFullPath($storage.scratch_root)
}
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:PYTHONUTF8 = '1'
# This setup resolves paths only. It performs no downloads or data acquisition.
