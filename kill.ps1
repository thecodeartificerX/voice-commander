<#
.SYNOPSIS
    Terminate running voice-commander daemon + voice-sprite processes.

.DESCRIPTION
    Two-stage kill:
      1. Read PID from outputs/.daemon.lock. Verify its cmdline contains
         "voice_commander" (guards against stale lock pointing at a recycled
         PID). If match, taskkill /T /F (tree kill catches sprite child).
      2. Fallback sweep: match Win32_Process CommandLine against the project
         venv path so no random python.exe outside this repo is at risk.

    Prints each PID + cmdline before killing. Removes stale lock at the end.
    Safe to run when nothing is alive — exit 0 either way.
#>

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$LockFile    = Join-Path $ProjectRoot 'outputs\.daemon.lock'
$VenvMarker  = 'voice-commander\.venv\Scripts\voice-'  # matches voice-commander.exe + voice-sprite.exe

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

# --- Stage 1: PID from lock file ------------------------------------------
if (Test-Path $LockFile) {
    $raw = Get-Content $LockFile -Raw -ErrorAction SilentlyContinue
    if ($raw) { $raw = $raw.Trim() }
    if ($raw -and $raw -match '^(\d+):') {
        $lockPid = [int]$Matches[1]
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$lockPid" -ErrorAction SilentlyContinue
        if ($proc -and $proc.CommandLine -match 'voice_commander|voice-commander') {
            Write-Host "Daemon lock -> PID $lockPid" -ForegroundColor Cyan
            Invoke-SafeKill -ProcessId $lockPid -Reason 'daemon.lock' -Tree
        } elseif ($proc) {
            Write-Host "Stale lock: PID $lockPid is '$($proc.Name)', cmdline does not match voice-commander. Skipping." -ForegroundColor DarkYellow
        } else {
            Write-Host "Stale lock: PID $lockPid not running." -ForegroundColor DarkGray
        }
    }
}

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
if (Test-Path $LockFile) {
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
    Write-Host "Removed stale $LockFile" -ForegroundColor DarkGray
}

if ($killed.Count -eq 0) {
    Write-Host "Nothing to kill." -ForegroundColor Green
} else {
    Write-Host "Killed $($killed.Count) process(es)." -ForegroundColor Green
}

exit 0
