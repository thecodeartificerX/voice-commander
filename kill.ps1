<#
.SYNOPSIS
    Terminate running voice-commander supervisor + daemon + voice-sprite processes.

.DESCRIPTION
    Three-stage kill:
      0. Read PID from outputs/.supervisor.lock. Verify its cmdline contains
         "voice-commander-supervisor". If match, taskkill /T /F first — tree
         kill takes daemon + sprite children with it.
      1. Read PID from outputs/.daemon.lock. Verify its cmdline contains
         "voice_commander" (guards against stale lock pointing at a recycled
         PID). If match, taskkill /T /F (catches direct daemon launches with
         no supervisor).
      2. Fallback sweep: match Win32_Process CommandLine against the project
         venv path so no random python.exe outside this repo is at risk.
         Catches supervisor + daemon + sprite when locks are missing/stale.

    Prints each PID + cmdline before killing. Removes both lock files at the
    end. Safe to run when nothing is alive — exit 0 either way.
#>

$ErrorActionPreference = 'Stop'
$ProjectRoot     = $PSScriptRoot
$DaemonLock      = Join-Path $ProjectRoot 'outputs\.daemon.lock'
$SupervisorLock  = Join-Path $ProjectRoot 'outputs\.supervisor.lock'
$VenvMarker      = 'voice-commander\.venv\Scripts\voice-'  # matches voice-commander*.exe + voice-sprite.exe

$killed = @()

function Invoke-SafeKill {
    param(
        [int]$ProcessId,
        [string]$Reason,
        [switch]$Tree
    )
    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction Stop
    } catch {
        Write-Host "  PID $ProcessId already gone ($Reason)" -ForegroundColor DarkGray
        return
    }
    Write-Host "  killing PID $ProcessId ($Reason) - $($proc.ProcessName)" -ForegroundColor Yellow
    if ($Tree) {
        & taskkill.exe /PID $ProcessId /T /F 2>&1 | Out-Null
    } else {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
    $script:killed += $ProcessId
}

function Invoke-LockKill {
    param(
        [string]$LockPath,
        [string]$Label,
        [string]$CmdlinePattern
    )
    if (-not (Test-Path $LockPath)) { return }
    $raw = Get-Content $LockPath -Raw -ErrorAction SilentlyContinue
    if ($raw) { $raw = $raw.Trim() }
    if (-not ($raw -and $raw -match '^(\d+):')) { return }
    $lockPid = [int]$Matches[1]
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$lockPid" -ErrorAction SilentlyContinue
    if ($proc -and $proc.CommandLine -match $CmdlinePattern) {
        Write-Host "$Label lock -> PID $lockPid" -ForegroundColor Cyan
        Invoke-SafeKill -ProcessId $lockPid -Reason "$Label.lock" -Tree
    } elseif ($proc) {
        Write-Host "Stale $Label lock: PID $lockPid is '$($proc.Name)', cmdline does not match. Skipping." -ForegroundColor DarkYellow
    } else {
        Write-Host "Stale $Label lock: PID $lockPid not running." -ForegroundColor DarkGray
    }
}

# --- Stage 0: supervisor lock (tree-kill cascades to daemon + sprite) -----
Invoke-LockKill -LockPath $SupervisorLock -Label 'supervisor' `
    -CmdlinePattern 'voice-commander-supervisor|voice_commander\.supervisor'

# --- Stage 1: daemon lock (covers direct launch with no supervisor) -------
Invoke-LockKill -LockPath $DaemonLock -Label 'daemon' `
    -CmdlinePattern 'voice_commander|voice-commander'

# --- Stage 2: Cmdline sweep (catches daemon if no lock + orphaned sprite) -
$hits = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -like "*$VenvMarker*" -and
        $_.ProcessId -notin $killed
    }

if ($hits) {
    Write-Host "Cmdline sweep -> $($hits.Count) process(es)" -ForegroundColor Cyan
    foreach ($m in $hits) {
        $short = if ($m.CommandLine.Length -gt 100) { $m.CommandLine.Substring(0,100) + '...' } else { $m.CommandLine }
        Write-Host "  match: PID $($m.ProcessId)  $short" -ForegroundColor DarkCyan
        Invoke-SafeKill -ProcessId $m.ProcessId -Reason 'cmdline-match'
    }
}

# --- Cleanup ---------------------------------------------------------------
foreach ($lock in @($SupervisorLock, $DaemonLock)) {
    if (Test-Path $lock) {
        Remove-Item $lock -Force -ErrorAction SilentlyContinue
        Write-Host "Removed stale $lock" -ForegroundColor DarkGray
    }
}

if ($killed.Count -eq 0) {
    Write-Host "Nothing to kill." -ForegroundColor Green
} else {
    Write-Host "Killed $($killed.Count) process(es)." -ForegroundColor Green
}

exit 0
