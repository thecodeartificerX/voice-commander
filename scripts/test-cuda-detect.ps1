#Requires -Version 5.1

<#
.SYNOPSIS
    Smoke test for Get-VoiceCudaPath and Add-VoiceCudaToPath helpers in start.ps1.

.DESCRIPTION
    Loads the two CUDA helper functions from start.ps1 via the PowerShell AST
    parser (no fragile regex), then:
      1. Calls Get-VoiceCudaPath and prints the result.
      2. Calls Add-VoiceCudaToPath and verifies PATH gained the expected entries.
      3. Runs idempotency check (second call must not duplicate PATH entries).
    Exits 0 on success, 1 on failure.

.EXAMPLE
    .\scripts\test-cuda-detect.ps1

.EXAMPLE
    .\scripts\test-cuda-detect.ps1 -Verbose
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Resolve project root (parent of the scripts\ folder) — PS 5.1 compatible
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot

$scriptPath = Join-Path $projectRoot 'start.ps1'

Write-Host ''
Write-Host '=== Voice Commander CUDA detect smoke test ===' -ForegroundColor Cyan
Write-Host ''

# ---------------------------------------------------------------------------
# Extract and define the two CUDA helper functions using the PS AST parser.
# This is far more robust than a brace-counting regex for deeply nested code.
# ---------------------------------------------------------------------------

$tokens  = $null
$errors  = $null
$ast     = [System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath, [ref]$tokens, [ref]$errors
)

if ($errors.Count -gt 0) {
    Write-Host "FAIL: start.ps1 has parse errors:" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}

$targetFunctions = @('Get-VoiceCudaPath', 'Add-VoiceCudaToPath')

$funcDefs = $ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -in $targetFunctions
}, $false)

if (@($funcDefs).Count -ne 2) {
    Write-Host ("FAIL: Expected 2 CUDA helper functions in start.ps1, found {0}." -f @($funcDefs).Count) -ForegroundColor Red
    Write-Host "Found: $($funcDefs | ForEach-Object { $_.Name })" -ForegroundColor Red
    exit 1
}

# Define each function in the current scope by executing its source text
foreach ($fn in $funcDefs) {
    $fnSource = $fn.Extent.Text
    Invoke-Expression $fnSource
    Write-Host ("  Loaded: {0}" -f $fn.Name) -ForegroundColor DarkGray
}
Write-Host ''

# ---------------------------------------------------------------------------
# Step 1: Get-VoiceCudaPath
# ---------------------------------------------------------------------------

Write-Host '--- Get-VoiceCudaPath ---' -ForegroundColor Cyan
$paths = Get-VoiceCudaPath -Verbose:($VerbosePreference -eq 'Continue')

Write-Host ("  CudaBin  : {0}" -f $(if ($null -ne $paths.CudaBin)  { $paths.CudaBin  } else { '(null)' }))
Write-Host ("  CudnnBin : {0}" -f $(if ($null -ne $paths.CudnnBin) { $paths.CudnnBin } else { '(null)' }))
Write-Host ''

if ($null -eq $paths.CudaBin) {
    Write-Host 'WARN: CudaBin is null — CUDA Toolkit may not be installed at the expected path.' -ForegroundColor Yellow
}
if ($null -eq $paths.CudnnBin) {
    Write-Host 'WARN: CudnnBin is null — cuDNN may not be installed at the expected path.' -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Step 2: Add-VoiceCudaToPath — verify entries appear in PATH
# ---------------------------------------------------------------------------

Write-Host '--- Add-VoiceCudaToPath ---' -ForegroundColor Cyan

Add-VoiceCudaToPath -Verbose:($VerbosePreference -eq 'Continue')

$failures = [System.Collections.Generic.List[string]]::new()

if ($null -ne $paths.CudaBin) {
    $normalised = $paths.CudaBin.TrimEnd('\')
    $entries    = $env:PATH -split ';' | ForEach-Object { $_.TrimEnd('\') }
    if ($entries -contains $normalised) {
        Write-Host ("  PASS: CudaBin '{0}' present in PATH." -f $normalised) -ForegroundColor Green
    }
    else {
        $msg = "CudaBin '$normalised' not found in PATH after Add-VoiceCudaToPath"
        $failures.Add($msg)
        Write-Host ("  FAIL: {0}" -f $msg) -ForegroundColor Red
    }
}
else {
    Write-Host '  SKIP: CudaBin check skipped (null).' -ForegroundColor DarkGray
}

if ($null -ne $paths.CudnnBin) {
    $normalised = $paths.CudnnBin.TrimEnd('\')
    $entries    = $env:PATH -split ';' | ForEach-Object { $_.TrimEnd('\') }
    if ($entries -contains $normalised) {
        Write-Host ("  PASS: CudnnBin '{0}' present in PATH." -f $normalised) -ForegroundColor Green
    }
    else {
        $msg = "CudnnBin '$normalised' not found in PATH after Add-VoiceCudaToPath"
        $failures.Add($msg)
        Write-Host ("  FAIL: {0}" -f $msg) -ForegroundColor Red
    }
}
else {
    Write-Host '  SKIP: CudnnBin check skipped (null).' -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Step 3: Idempotency — second call must not duplicate entries
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host '--- Idempotency check ---' -ForegroundColor Cyan
$pathBeforeSecond = $env:PATH
Add-VoiceCudaToPath -Verbose:($VerbosePreference -eq 'Continue')
$pathAfterSecond = $env:PATH

if ($pathBeforeSecond -eq $pathAfterSecond) {
    Write-Host '  PASS: Second call did not duplicate PATH entries.' -ForegroundColor Green
}
else {
    $msg = 'Idempotency FAIL: PATH changed on second Add-VoiceCudaToPath call'
    $failures.Add($msg)
    Write-Host ("  FAIL: {0}" -f $msg) -ForegroundColor Red
}

# ---------------------------------------------------------------------------
# Final result
# ---------------------------------------------------------------------------

Write-Host ''
if ($failures.Count -eq 0) {
    Write-Host '=== ALL CHECKS PASSED ===' -ForegroundColor Green
    exit 0
}
else {
    Write-Host '=== FAILURES ===' -ForegroundColor Red
    foreach ($f in $failures) {
        Write-Host ("  - {0}" -f $f) -ForegroundColor Red
    }
    exit 1
}
