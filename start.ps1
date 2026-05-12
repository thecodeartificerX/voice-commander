#Requires -Version 5.1

<#
.SYNOPSIS
    Voice Commander launcher with audio-device TUI.

.DESCRIPTION
    Presents an interactive audio-device selection menu on first run or when the
    user wants to switch devices. Persists the chosen PortAudio device index to
    config.toml, then starts the Voice Commander daemon via `uv run voice-commander`.

    Three non-interactive bypass modes are supported:
      -NoMenu     Use whatever audio.device is saved in config.toml; exit 1 if unset.
      -Device N   Persist device N and launch without showing the TUI.
      -ListDevices Print the device table and exit 0 without launching the daemon.

    Compatible with Windows PowerShell 5.1+ and PowerShell 7+.

.PARAMETER NoMenu
    Skip the TUI entirely. Uses the audio.device value currently in config.toml.
    Exits with code 1 if no valid device is saved.

.PARAMETER Device
    Device index (0-999) to persist and use without showing the TUI.
    Mutually exclusive with -NoMenu and -ListDevices.

.PARAMETER ListDevices
    Print the available input device table and exit 0. Does not launch the daemon.
    Mutually exclusive with -NoMenu and -Device.

.EXAMPLE
    .\start.ps1

    Show the full interactive TUI. If a device was previously saved you will be
    offered "Use last / Choose new"; otherwise you go straight to the device picker.

.EXAMPLE
    .\start.ps1 -NoMenu

    Non-interactive fast path: skip the menu, use the saved device, launch daemon.
    Useful in scripts, scheduled tasks, or CI environments.

.EXAMPLE
    .\start.ps1 -Device 2

    Persist device index 2 to config.toml and launch the daemon immediately.

.EXAMPLE
    .\start.ps1 -ListDevices

    Print the device table (aligned columns, current device highlighted green)
    and exit 0. Does not start the daemon.
#>

[CmdletBinding(DefaultParameterSetName = 'Interactive')]
param(
    [Parameter(ParameterSetName = 'NoMenu')]
    [switch]$NoMenu,

    [Parameter(ParameterSetName = 'DirectDevice')]
    [ValidateRange(0, 999)]
    [int]$Device = -1,

    [Parameter(ParameterSetName = 'ListDevices')]
    [switch]$ListDevices,

    [Parameter(ParameterSetName = 'Interactive')]
    [Parameter(ParameterSetName = 'NoMenu')]
    [Parameter(ParameterSetName = 'DirectDevice')]
    [switch]$NoUI,

    [Parameter(ParameterSetName = 'Interactive')]
    [Parameter(ParameterSetName = 'NoMenu')]
    [Parameter(ParameterSetName = 'DirectDevice')]
    [ValidateRange(1024, 65535)]
    [int]$UIPort = 0,

    [Parameter(ParameterSetName = 'Interactive')]
    [Parameter(ParameterSetName = 'NoMenu')]
    [Parameter(ParameterSetName = 'DirectDevice')]
    [switch]$NoOpenBrowser,

    [Parameter(ParameterSetName = 'Interactive')]
    [Parameter(ParameterSetName = 'NoMenu')]
    [Parameter(ParameterSetName = 'DirectDevice')]
    [switch]$NoSprite,

    [Parameter(ParameterSetName = 'Interactive')]
    [Parameter(ParameterSetName = 'NoMenu')]
    [Parameter(ParameterSetName = 'DirectDevice')]
    [Parameter(ParameterSetName = 'ListDevices')]
    [switch]$NoNuke
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -LiteralPath $PSScriptRoot

# ---------------------------------------------------------------------------
# Stale process nuke  (runs before everything else; bypass with -NoNuke)
# ---------------------------------------------------------------------------

function Invoke-VoiceStaleProcessNuke {
    <#
    .SYNOPSIS
        Kill leftover voice-commander and voice-sprite processes from a prior run.

    .DESCRIPTION
        Idempotent prelude that cleans up three categories of stale state:

          a. Lock-file owner — if outputs/.daemon.lock exists, reads the PID
             (stored as "PID:GUID"), kills the process tree if alive, then
             removes the lockfile.

          b. Port holder — resolves the web-UI port (UIPort param > env var
             VOICE_COMMANDER_WEB_PORT > default 8765) and kills any process
             listening on that TCP port.

          c. Orphan python/uv processes — kills any python.exe, pythonw.exe,
             or uv.exe whose CommandLine contains "voice_commander" or
             "voice_sprite", being careful not to kill the current shell.

        Emits one Write-VoiceSecondary line when anything was killed; silent
        on a clean system.
    #>

    $killedAnything = $false
    $currentPid = $PID  # PowerShell's own PID

    # --- a. Lock-file owner ---
    $lockPath = Join-Path $PSScriptRoot 'outputs/.daemon.lock'
    if (Test-Path -LiteralPath $lockPath) {
        try {
            $lockContent = (Get-Content -LiteralPath $lockPath -Raw -ErrorAction SilentlyContinue).Trim()
            if ($lockContent) {
                $lockPid = [int]($lockContent -split ':')[0]
                if ($lockPid -gt 0 -and $lockPid -ne $currentPid) {
                    try {
                        $testProc = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
                        if ($null -ne $testProc -and -not $testProc.HasExited) {
                            Write-Verbose "Killing lock-file owner PID $lockPid"
                            & taskkill.exe /PID $lockPid /T /F 2>&1 | Out-Null
                            $killedAnything = $true
                        }
                    }
                    catch {
                        Write-Verbose "Lock-file PID check failed: $_"
                    }
                }
            }
        }
        catch {
            Write-Verbose "Lock-file read failed (corrupt?): $_"
        }
        try {
            Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
            Write-Verbose "Removed stale lock file: $lockPath"
        }
        catch {
            Write-Verbose "Could not remove lock file: $_"
        }
    }

    # --- b. Port holder ---
    $resolvedPort = if ($UIPort -gt 0) {
        $UIPort
    } elseif ($env:VOICE_COMMANDER_WEB_PORT) {
        [int]$env:VOICE_COMMANDER_WEB_PORT
    } else {
        8765
    }

    try {
        $portConns = Get-NetTCPConnection -LocalPort $resolvedPort -State Listen -ErrorAction SilentlyContinue
        foreach ($conn in $portConns) {
            $portPid = $conn.OwningProcess
            if ($portPid -gt 0 -and $portPid -ne $currentPid) {
                Write-Verbose "Killing process $portPid holding port $resolvedPort"
                try {
                    & taskkill.exe /PID $portPid /T /F 2>&1 | Out-Null
                    $killedAnything = $true
                }
                catch {
                    Write-Verbose "taskkill for port holder PID $portPid failed: $_"
                }
            }
        }
    }
    catch {
        Write-Verbose "Get-NetTCPConnection failed: $_"
    }

    # --- c. Orphan python/uv processes ---
    $targetNames = @('python.exe', 'pythonw.exe', 'uv.exe')
    $targetKeywords = @('voice_commander', 'voice_sprite')

    foreach ($procName in $targetNames) {
        try {
            $candidates = Get-CimInstance Win32_Process -Filter "Name='$procName'" -ErrorAction SilentlyContinue
            foreach ($proc in $candidates) {
                if ($proc.ProcessId -eq $currentPid) { continue }
                $cmdLine = $proc.CommandLine
                if (-not $cmdLine) { continue }
                $isTarget = $false
                foreach ($kw in $targetKeywords) {
                    if ($cmdLine -like "*$kw*") { $isTarget = $true; break }
                }
                if ($isTarget) {
                    Write-Verbose "Killing orphan $procName PID $($proc.ProcessId): $cmdLine"
                    try {
                        & taskkill.exe /PID $proc.ProcessId /T /F 2>&1 | Out-Null
                        $killedAnything = $true
                    }
                    catch {
                        Write-Verbose "taskkill for orphan PID $($proc.ProcessId) failed: $_"
                    }
                }
            }
        }
        catch {
            Write-Verbose "CIM query for $procName failed: $_"
        }
    }

    if ($killedAnything) {
        Write-VoiceSecondary 'Cleaned up stale processes/lockfile.'
    }
}

if (-not $NoNuke) {
    Invoke-VoiceStaleProcessNuke
}

# ---------------------------------------------------------------------------
# Ctrl+C tree-kill guard
# ---------------------------------------------------------------------------
#
# PowerShell's default Ctrl+C handler can abort the script before the
# Python supervisor gets a clean shutdown signal — particularly when
# `uv run` is the foreground child and swallows the CTRL_C_EVENT.
# Without this guard, the daemon + sprite stay alive in the background.
#
# We hook the .NET-level [Console]::CancelKeyPress event, which fires
# *before* PowerShell aborts. The handler tree-kills the supervisor PID
# via taskkill /T /F so the daemon, sprite, and any uv intermediary all
# die together. Setting `Cancel = $true` also tells .NET to swallow the
# original Ctrl+C so the script's try/finally still runs.
#
# IMPORTANT: the handler MUST be implemented in pure .NET (Add-Type) and
# not as a PowerShell ScriptBlock. Console.CancelKeyPress fires on a
# threadpool thread that has no PowerShell Runspace; invoking a script
# block from there throws PSInvalidOperationException ("There is no
# Runspace available") and crashes the host before taskkill ever runs.
$script:SupervisorProc = $null

if (-not ('VoiceCommander.CancelGuard' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;

namespace VoiceCommander {
    public static class CancelGuard {
        public static int SupervisorPid;

        public static void Install() {
            Console.CancelKeyPress += OnCancelKeyPress;
        }

        private static void OnCancelKeyPress(object sender, ConsoleCancelEventArgs args) {
            int pid = SupervisorPid;
            if (pid <= 0) {
                return; // No supervisor running — let PS abort the TUI normally.
            }
            try {
                Process target = null;
                try {
                    target = Process.GetProcessById(pid);
                } catch (ArgumentException) {
                    return; // Already gone.
                }
                if (target.HasExited) {
                    return;
                }
                Console.Error.WriteLine();
                Console.Error.WriteLine("Ctrl+C received - tearing down voice-commander tree...");
                Process killer = new Process();
                killer.StartInfo.FileName = "taskkill.exe";
                killer.StartInfo.Arguments = "/PID " + pid + " /T /F";
                killer.StartInfo.UseShellExecute = false;
                killer.StartInfo.CreateNoWindow = true;
                killer.StartInfo.RedirectStandardOutput = true;
                killer.StartInfo.RedirectStandardError = true;
                killer.Start();
                killer.WaitForExit(3000);
                // Suppress the default abort so the PS try/finally runs and
                // the script reports the supervisor exit code cleanly.
                args.Cancel = true;
            } catch {
                // Best-effort: never let this handler throw.
            }
        }
    }
}
'@
}
[VoiceCommander.CancelGuard]::Install()

# Phase banner shown in Show-VoiceBanner. Extracted so phase bumps touch one place.
$script:PhaseString = '  Hardened: WASAPI-only + deterministic routing (ADRs 0081-0082)'

# ---------------------------------------------------------------------------
# Builder UI build helper
# ---------------------------------------------------------------------------

function Initialize-BuilderUI {
    <#
    .SYNOPSIS
        Build the web/builder-ui SPA only when sources are newer than the last build.
    .DESCRIPTION
        Compares the newest mtime under web/builder-ui/src/ (and index.html /
        vite.config.ts) against the newest mtime in the built output directory.
        Skips the build entirely when the output is up-to-date, so normal
        daemon restarts pay zero SPA build cost.

        Forces a full build when:
          - node_modules is missing (install + build)
          - built output directory is missing
          - any source file is newer than the newest built file

        Failures are non-fatal — the daemon will serve a friendly stub page.
    #>
    $uiDir     = Join-Path $PSScriptRoot 'web/builder-ui'
    $outDir    = Join-Path $PSScriptRoot 'src/voice_commander/web/static/builder'

    if (-not (Test-Path -LiteralPath $uiDir)) {
        Write-Verbose "web/builder-ui directory missing — skipping SPA build"
        return
    }

    $nodeModules = Join-Path $uiDir 'node_modules'
    $needsInstall = -not (Test-Path -LiteralPath $nodeModules)

    # Determine whether sources are newer than the built output.
    $needsBuild = $needsInstall -or (-not (Test-Path -LiteralPath $outDir))

    if (-not $needsBuild) {
        # Newest mtime across source files that affect the output.
        $srcDirs = @(
            (Join-Path $uiDir 'src'),
            (Join-Path $uiDir 'public')
        )
        $srcRoots = @(
            (Join-Path $uiDir 'index.html'),
            (Join-Path $uiDir 'vite.config.ts'),
            (Join-Path $uiDir 'tsconfig.json'),
            (Join-Path $uiDir 'package.json')
        )

        $newestSrc = $null

        foreach ($root in $srcRoots) {
            if (Test-Path -LiteralPath $root) {
                $t = (Get-Item -LiteralPath $root).LastWriteTimeUtc
                if ($null -eq $newestSrc -or $t -gt $newestSrc) { $newestSrc = $t }
            }
        }
        foreach ($dir in $srcDirs) {
            if (Test-Path -LiteralPath $dir) {
                $items = Get-ChildItem -LiteralPath $dir -Recurse -File -ErrorAction SilentlyContinue
                foreach ($f in $items) {
                    if ($null -eq $newestSrc -or $f.LastWriteTimeUtc -gt $newestSrc) {
                        $newestSrc = $f.LastWriteTimeUtc
                    }
                }
            }
        }

        $newestOut = $null
        $outItems = Get-ChildItem -LiteralPath $outDir -Recurse -File -ErrorAction SilentlyContinue
        foreach ($f in $outItems) {
            if ($null -eq $newestOut -or $f.LastWriteTimeUtc -gt $newestOut) {
                $newestOut = $f.LastWriteTimeUtc
            }
        }

        if ($null -ne $newestSrc -and $null -ne $newestOut -and $newestSrc -le $newestOut) {
            Write-Verbose "Builder UI up-to-date (src $newestSrc <= out $newestOut) — skipping build"
            return
        }

        $needsBuild = $true
    }

    Write-Host ''
    if ($needsInstall) {
        Write-VoicePrompt 'Builder UI dependencies missing. Running pnpm install + build...'
    }
    else {
        Write-VoicePrompt 'Builder UI sources changed — rebuilding SPA...'
    }

    try {
        Push-Location $uiDir
        if ($needsInstall) {
            & pnpm install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) { throw "pnpm install failed (code $LASTEXITCODE)" }
        }
        & pnpm build
        if ($LASTEXITCODE -ne 0) { throw "pnpm build failed (code $LASTEXITCODE)" }
        Write-VoiceSuccess 'Builder UI built successfully.'
        Write-Host ''
    }
    catch {
        Write-VoiceFailure "Builder UI build failed: $_"
        Write-VoiceSecondary '  Continuing — daemon will serve a friendly stub instead.'
        Write-Host ''
    }
    finally {
        Pop-Location
    }
}

# ---------------------------------------------------------------------------
# Color helper functions
# ---------------------------------------------------------------------------

function Write-VoiceHeader {
    <#
    .SYNOPSIS
        Write a section header line in Cyan.
    #>
    param([string]$Text)
    Write-Host $Text -ForegroundColor Cyan
}

function Write-VoicePrompt {
    <#
    .SYNOPSIS
        Write a prompt or hint line in Yellow, optionally without newline.
    #>
    param(
        [string]$Text,
        [switch]$NoNewline
    )
    $writeParams = @{
        Object          = $Text
        ForegroundColor = 'Yellow'
        NoNewline       = $NoNewline.IsPresent
    }
    Write-Host @writeParams
}

function Write-VoiceSuccess {
    <#
    .SYNOPSIS
        Write a success/confirmation line in Green.
    #>
    param([string]$Text)
    Write-Host $Text -ForegroundColor Green
}

function Write-VoiceFailure {
    <#
    .SYNOPSIS
        Write an error/failure line in Red.
    #>
    param([string]$Text)
    Write-Host $Text -ForegroundColor Red
}

function Write-VoiceSecondary {
    <#
    .SYNOPSIS
        Write secondary / supplementary text in DarkGray.
    #>
    param([string]$Text)
    Write-Host $Text -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

function Show-VoiceBanner {
    <#
    .SYNOPSIS
        Display the ASCII Voice Commander banner in Cyan.
    #>
    Write-Host ''
    Write-VoiceHeader '  __   ____  ____  ___  ____     ____  __  _  _  _  _   __   __ _  ____  ____  ____'
    Write-VoiceHeader ' \ \ / /  \/  _ \/ __)(  __)(  / ___)/  \( \/ )( \/ ) / _\ (  ( \(    \(  __)(  _ \'
    Write-VoiceHeader '  \ V /| () | | | \__ \ ) _)  )( (__( () |)  / | \/ |/    \/    / ) D ( ) _)  )   /'
    Write-VoiceHeader '   \_/ \____/\___/(____/(____)(__)\___)\__/(__/  \_)(_/\_/\_/\_)__)(____/(____)(__\_)'
    Write-VoiceHeader ''
    Write-VoiceHeader $script:PhaseString
    Write-Host '  ---------------------------------------------------------------' -ForegroundColor Cyan
    Write-Host ''
}

# ---------------------------------------------------------------------------
# Device data helpers
# ---------------------------------------------------------------------------

function Get-VoiceConfigDeviceName {
    <#
    .SYNOPSIS
        Returns the audio.device_name string from config.toml, or $null if unset/empty.
    .DESCRIPTION
        Reads the authoritative device identity (ADR 0081). The legacy
        audio.device int is no longer used by the launcher.
    #>
    Write-Verbose 'Reading audio.device_name via Config.load'
    try {
        $raw = uv run python -c "from pathlib import Path; from voice_commander.config import Config; print(Config.load(Path('config.toml')).audio.device_name)" 2>$null
        $val = $raw.Trim()
        Write-Verbose "merged audio.device_name = '$val'"
        if ($val -eq '') { return $null }
        return $val
    }
    catch [System.Management.Automation.RuntimeException] {
        Write-Verbose "config read failed (RuntimeException): $_"
        return $null
    }
    catch {
        Write-Verbose "config read failed: $_"
        return $null
    }
}

function Get-VoiceInputDevice {
    <#
    .SYNOPSIS
        Returns Windows WASAPI input devices as typed PSCustomObject array.

    .DESCRIPTION
        Calls list-input-devices.py and returns [PSCustomObject[]] with PSTypeName
        'VoiceCommander.AudioDevice' and properties: Index, Name, HostApi,
        MaxInputChannels, DefaultSampleRate. The list is filtered to Windows
        WASAPI entries only (ADR 0081: WASAPI is the mandatory host API;
        DirectSound and MME are excluded). Original PortAudio indices are
        preserved. Returns $null on error.
    #>
    Write-Verbose 'Querying PortAudio input devices via list-input-devices.py'
    try {
        $raw = uv run python scripts/list-input-devices.py 2>$null
        $parsed = $raw | ConvertFrom-Json

        # Error payload from the Python script
        if ($parsed -isnot [System.Array] -and $parsed -isnot [System.Collections.ArrayList]) {
            if ($null -ne $parsed -and $null -ne $parsed.PSObject.Properties['error']) {
                Write-Verbose "list-input-devices.py returned error: $($parsed.error)"
                return $null
            }
            # Single device returned as object -- wrap in array
            $parsed = @($parsed)
        }

        $typed = foreach ($d in $parsed) {
            if ($d.hostapi -ne 'Windows WASAPI') { continue }
            [PSCustomObject]@{
                PSTypeName        = 'VoiceCommander.AudioDevice'
                Index             = [int]$d.index
                Name              = [string]$d.name
                HostApi           = [string]$d.hostapi
                MaxInputChannels  = [int]$d.max_input_channels
                DefaultSampleRate = [int]$d.default_samplerate
            }
        }

        Write-Verbose "Device list contained $(@($typed).Count) WASAPI entries"
        return @($typed)
    }
    catch [System.Management.Automation.RuntimeException] {
        Write-Verbose "Device query failed (RuntimeException): $_"
        return $null
    }
    catch {
        Write-Verbose "Device query failed: $_"
        return $null
    }
}

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

function Show-VoiceDeviceTable {
    <#
    .SYNOPSIS
        Print the audio device table with aligned columns.

    .DESCRIPTION
        Renders a formatted table of VoiceCommander.AudioDevice objects.
        The row whose Name matches -CurrentName (case-insensitive trim) is
        highlighted in Green; all other rows are White.

    .PARAMETER DeviceList
        Array of PSCustomObject with PSTypeName 'VoiceCommander.AudioDevice'.

    .PARAMETER CurrentName
        Optional device name to highlight green (the currently saved device_name).
    #>
    param(
        [Parameter(Mandatory)]
        [PSCustomObject[]]$DeviceList,

        [string]$CurrentName = ''
    )

    $header = '{0,4}  {1,-40} {2,-20} {3,6} {4,4}' -f 'Idx', 'Name', 'HostAPI', 'SR', 'Ch'
    $divider = '{0,4}  {1,-40} {2,-20} {3,6} {4,4}' -f '----', '----------------------------------------', '--------------------', '------', '----'

    Write-VoiceHeader $header
    Write-VoiceHeader $divider

    $normalizedCurrent = $CurrentName.Trim().ToLowerInvariant()

    foreach ($d in $DeviceList) {
        $row = '{0,4}  {1,-40} {2,-20} {3,6} {4,4}' -f $d.Index, $d.Name, $d.HostApi, $d.DefaultSampleRate, $d.MaxInputChannels
        if ($d.Name.Trim().ToLowerInvariant() -eq $normalizedCurrent -and $normalizedCurrent -ne '') {
            Write-Host $row -ForegroundColor Green
        }
        else {
            Write-Host $row -ForegroundColor White
        }
    }
    Write-Host ''
}

# ---------------------------------------------------------------------------
# Interactive menu helpers
# ---------------------------------------------------------------------------

function Read-VoiceMenuChoice {
    <#
    .SYNOPSIS
        Read a single keypress from the valid key set; re-prompt on bad input.

    .PARAMETER ValidKeys
        Array of single-character strings (case-insensitive) that are accepted.

    .PARAMETER Prompt
        The prompt string shown before each read. Defaults to '> '.
    #>
    param(
        [Parameter(Mandatory)]
        [string[]]$ValidKeys,

        [string]$Prompt = '> '
    )

    $upperKeys = $ValidKeys | ForEach-Object { $_.ToUpperInvariant() }

    while ($true) {
        Write-VoicePrompt -Text $Prompt -NoNewline

        $keyChar = $null
        if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
            try {
                $key = [Console]::ReadKey($true)
                $keyChar = $key.KeyChar.ToString()
            }
            catch {
                $keyChar = (Read-Host).Trim()
            }
        }
        else {
            $keyChar = (Read-Host).Trim()
        }

        # Echo what was pressed
        Write-Host $keyChar

        if ($upperKeys -contains $keyChar.ToUpperInvariant()) {
            return $keyChar.ToUpperInvariant()
        }

        Write-VoiceFailure "  Invalid key. Please press one of: $($ValidKeys -join ', ')"
    }
}

function Read-VoiceDeviceIndex {
    <#
    .SYNOPSIS
        Prompt the user to type a device index; return the chosen Index or $null.

    .DESCRIPTION
        Validates the typed number against the actual Index values in DeviceList.
        Returns $null when the user presses B (back) or Q (quit).
        Pressing Q exits the script with code 0.
        Pressing B returns $null so the caller can navigate back.

    .PARAMETER DeviceList
        Array of PSCustomObject with PSTypeName 'VoiceCommander.AudioDevice'.
    #>
    param(
        [Parameter(Mandatory)]
        [PSCustomObject[]]$DeviceList
    )

    $validIndices = @($DeviceList | ForEach-Object { $_.Index })

    while ($true) {
        Write-VoicePrompt -Text '> ' -NoNewline
        $raw = (Read-Host).Trim()

        if ($raw.ToUpperInvariant() -eq 'Q') {
            Write-Host ''
            Write-VoiceSecondary 'Goodbye.'
            exit 0
        }

        if ($raw.ToUpperInvariant() -eq 'B') {
            return $null
        }

        $asInt = 0
        if ([int]::TryParse($raw, [ref]$asInt) -and $validIndices -contains $asInt) {
            return $asInt
        }

        Write-VoiceFailure ("  Invalid -- enter one of: {0}" -f ($validIndices -join ', '))
        Write-VoiceSecondary '  (or B to go back, Q to quit)'
    }
}

# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------

function Save-VoiceDeviceChoice {
    <#
    .SYNOPSIS
        Persist a device index to config.toml via set-audio-device.py.

    .PARAMETER Index
        The PortAudio device index to save.

    .PARAMETER Name
        Optional human-readable device name to pass to set-audio-device.py.

    .OUTPUTS
        [bool] $true on success, $false on failure.
    #>
    param(
        [Parameter(Mandatory)]
        [int]$Index,
        [string]$Name = ''
    )

    Write-Verbose "Persisting audio.device = $Index (name='$Name') to config.toml"
    try {
        $cmdArgs = @('run', 'python', 'scripts/set-audio-device.py', $Index)
        if ($Name) { $cmdArgs += $Name }
        & uv @cmdArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Verbose "set-audio-device.py exited with code $LASTEXITCODE"
            return $false
        }
        return $true
    }
    catch [System.Management.Automation.RuntimeException] {
        Write-Verbose "Save-VoiceDeviceChoice failed (RuntimeException): $_"
        return $false
    }
    catch {
        Write-Verbose "Save-VoiceDeviceChoice failed: $_"
        return $false
    }
}

function Start-VoiceSupervisor {
    <#
    .SYNOPSIS
        Launch `uv run voice-commander-supervisor` and return its exit code.

    .DESCRIPTION
        The supervisor owns the daemon and sprite lifecycle. We spawn it via
        Start-Process -PassThru -NoNewWindow so we can capture its PID and
        guarantee tree-kill on script exit — Ctrl+C, exception, or PowerShell
        window close.

        Three layers of cleanup, ordered most-graceful → most-forceful:

          1. The supervisor's own Win32 Job Object (KILL_ON_JOB_CLOSE) reaps
             the daemon + sprite when it exits normally.
          2. A [Console]::CancelKeyPress handler runs `taskkill /T /F` on the
             supervisor PID *before* PowerShell aborts the script — the most
             reliable hook on Windows when `uv` swallows the CTRL_C_EVENT.
          3. The try/finally below force-kills the tree on any exit path the
             CancelKeyPress handler missed.
    #>
    $supArgs = @('run', 'voice-commander-supervisor')
    if ($NoSprite) { $supArgs += '--no-sprite' }
    Write-Verbose "Launching supervisor: uv $($supArgs -join ' ')"

    # --- Memory-allocator tuning ------------------------------------------------
    # Defer to the system allocator so Python does not fight with mimalloc when
    # it is preloaded.
    $env:PYTHONMALLOC = 'malloc'

    # Cap incidental BLAS / OpenMP thread pools spawned by numpy / torch helpers.
    $env:OMP_NUM_THREADS = '2'
    $env:MKL_NUM_THREADS = '2'

    # Optional mimalloc preload: extend PATH so the Windows loader finds the DLLs
    # before Python initialises its allocator.
    $mimalloc     = Join-Path $PSScriptRoot 'bin\mimalloc.dll'
    $miRedirect   = Join-Path $PSScriptRoot 'bin\mimalloc-redirect.dll'
    if ((Test-Path -LiteralPath $mimalloc) -and (Test-Path -LiteralPath $miRedirect)) {
        if ($env:PATH -notlike "*$PSScriptRoot\bin*") {
            $env:PATH = "$PSScriptRoot\bin;$env:PATH"
        }
        Write-Host '[mimalloc] preload enabled'
    } else {
        Write-Host '[mimalloc] DLLs not found in bin/ — skipping (set PYTHONMALLOC=malloc only)'
    }
    # ---------------------------------------------------------------------------

    $proc = Start-Process -FilePath 'uv' -ArgumentList $supArgs `
        -NoNewWindow -PassThru
    $script:SupervisorProc = $proc
    [VoiceCommander.CancelGuard]::SupervisorPid = $proc.Id

    try {
        $proc.WaitForExit()
        return $proc.ExitCode
    }
    finally {
        Stop-VoiceSupervisorTree -Process $proc
        $script:SupervisorProc = $null
        [VoiceCommander.CancelGuard]::SupervisorPid = 0
    }
}

function Stop-VoiceSupervisorTree {
    <#
    .SYNOPSIS
        Force-kill the supervisor process tree if still alive.

    .DESCRIPTION
        Idempotent. Called from both the try/finally cleanup path and the
        [Console]::CancelKeyPress handler. `taskkill /T /F` walks the
        process tree so the daemon + sprite + any uv intermediary all die
        together — covers the case where the Job Object did not fire
        (pywin32 unavailable, supervisor crashed before assigning children).
    #>
    param(
        [Parameter(Mandatory)]
        [System.Diagnostics.Process]$Process
    )

    if ($null -eq $Process) { return }
    try {
        if ($Process.HasExited) { return }
    }
    catch {
        return
    }

    Write-Host ''
    Write-VoicePrompt 'Shutting down voice-commander process tree...'
    try {
        & taskkill.exe /PID $Process.Id /T /F 2>&1 | Out-Null
    }
    catch {
        Write-Verbose "taskkill failed: $_"
    }

    try {
        $Process.WaitForExit(3000) | Out-Null
    }
    catch {
        # Process already gone or handle invalid — both fine.
    }
}

function Start-VoiceWithUI {
    <#
    .SYNOPSIS
        Set web UI env vars, optionally open browser, then launch the supervisor.

    .DESCRIPTION
        Applies VOICE_COMMANDER_WEB_DISABLED and VOICE_COMMANDER_WEB_PORT from
        the -NoUI and -UIPort parameters before calling Start-VoiceSupervisor.
        When the web UI is enabled and -NoOpenBrowser is not set, a background
        job opens the browser 1.5 s after this function is called.
        Sprite spawn/teardown is now handled by the Python supervisor.
    #>

    if ($NoUI) {
        $env:VOICE_COMMANDER_WEB_DISABLED = '1'
    }
    if ($UIPort -gt 0) {
        $env:VOICE_COMMANDER_WEB_PORT = $UIPort.ToString()
    }

    if (-not $NoUI -and -not $NoOpenBrowser) {
        $webPort = if ($UIPort -gt 0) { $UIPort } else { 8765 }
        $webUrl  = "http://127.0.0.1:$webPort"
        Write-Verbose "Scheduling browser open: $webUrl (after 1500 ms)"
        Start-Job -ScriptBlock {
            Start-Sleep -Milliseconds 1500
            Start-Process $using:webUrl
        } | Out-Null
    }

    return Start-VoiceSupervisor
}

# ---------------------------------------------------------------------------
# Pre-flight: enumerate devices (needed by most code paths)
# ---------------------------------------------------------------------------

$IsInteractive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected

# Build the Builder SPA on first run (skip when only listing devices).
if (-not $ListDevices) {
    Initialize-BuilderUI
}

# ---------------------------------------------------------------------------
# -ListDevices path
# ---------------------------------------------------------------------------

if ($ListDevices) {
    $DevicesForList = Get-VoiceInputDevice
    if ($null -eq $DevicesForList -or $DevicesForList.Count -eq 0) {
        Write-VoiceFailure 'Could not enumerate audio devices.'
        Write-VoiceSecondary '  Hint: run `uv sync` then try again. Check that PortAudio is installed.'
        exit 2
    }
    $SavedNameForList = Get-VoiceConfigDeviceName
    Show-VoiceBanner
    Write-VoiceHeader 'Available input devices:'
    Show-VoiceDeviceTable -DeviceList $DevicesForList -CurrentName $SavedNameForList
    Write-VoiceSecondary '  (Green row = currently saved device_name in config.toml)'
    Write-Host ''
    exit 0
}

# ---------------------------------------------------------------------------
# -Device N path  (persist + launch, no TUI)
# ---------------------------------------------------------------------------

if ($PSCmdlet.ParameterSetName -eq 'DirectDevice') {
    Write-Verbose "-Device $Device specified; skipping TUI"
    $DevicesForDirect = Get-VoiceInputDevice
    $ChosenDirect = if ($null -ne $DevicesForDirect) {
        $DevicesForDirect | Where-Object { $_.Index -eq $Device } | Select-Object -First 1
    } else { $null }
    $DirectName = if ($null -ne $ChosenDirect) { $ChosenDirect.Name } else { '' }
    $SaveResult = Save-VoiceDeviceChoice -Index $Device -Name $DirectName
    if (-not $SaveResult) {
        Write-Error "Failed to persist device $Device to config.toml."
        exit 1
    }

    if ($null -ne $ChosenDirect) {
        Write-VoiceSuccess ("Using device [{0}]: {1}." -f $Device, $ChosenDirect.Name)
    }
    else {
        Write-VoiceSuccess "Using device [$Device]."
    }
    Write-Host ''
    Write-VoicePrompt 'Starting Voice Commander...'
    $ExitCode = Start-VoiceWithUI
    if ($ExitCode -eq 0) {
        Write-VoiceSuccess "Voice Commander exited cleanly (code 0)."
    }
    elseif ($ExitCode -eq 130 -or $ExitCode -eq -1073741510) {
        Write-VoicePrompt "Voice Commander stopped by user (code $ExitCode)."
    }
    else {
        Write-VoiceFailure "Voice Commander exited with error (code $ExitCode)."
    }
    exit $ExitCode
}

# ---------------------------------------------------------------------------
# -NoMenu path  (use saved device, no TUI)
# ---------------------------------------------------------------------------

if ($NoMenu) {
    Write-Verbose '-NoMenu specified; reading saved device_name from config.toml'
    $SavedNoMenu = Get-VoiceConfigDeviceName
    if ($null -eq $SavedNoMenu -or $SavedNoMenu -eq '') {
        Write-Error 'No valid audio.device_name found in config.toml. Run start.ps1 interactively to choose a device, or use -Device <index>.'
        exit 1
    }
    Write-Verbose "Non-interactive: using saved device '$SavedNoMenu'"
    Write-VoiceSuccess "Using saved device '$SavedNoMenu'."
    Write-Host ''
    Write-VoicePrompt 'Starting Voice Commander...'
    $ExitCode = Start-VoiceWithUI
    if ($ExitCode -eq 0) {
        Write-VoiceSuccess "Voice Commander exited cleanly (code 0)."
    }
    elseif ($ExitCode -eq 130 -or $ExitCode -eq -1073741510) {
        Write-VoicePrompt "Voice Commander stopped by user (code $ExitCode)."
    }
    else {
        Write-VoiceFailure "Voice Commander exited with error (code $ExitCode)."
    }
    exit $ExitCode
}

# ---------------------------------------------------------------------------
# Non-interactive auto-detect (no param set, non-interactive session)
# ---------------------------------------------------------------------------

if (-not $IsInteractive) {
    Write-Verbose 'Non-interactive session detected; falling back to saved device_name'
    $SavedAuto = Get-VoiceConfigDeviceName
    if ($null -eq $SavedAuto -or $SavedAuto -eq '') {
        Write-Error 'Non-interactive session and no valid audio.device_name in config.toml. Set audio.device_name in config.toml or run with -Device <index>.'
        exit 1
    }
    Write-VoiceSuccess "Non-interactive session -- using saved device '$SavedAuto'."
    Write-Host ''
    Write-VoicePrompt 'Starting Voice Commander...'
    $ExitCode = Start-VoiceWithUI
    exit $ExitCode
}

# ---------------------------------------------------------------------------
# Full interactive TUI
# ---------------------------------------------------------------------------

Show-VoiceBanner

# Load devices
$Devices = Get-VoiceInputDevice
if ($null -eq $Devices -or $Devices.Count -eq 0) {
    Write-VoiceFailure 'Could not enumerate audio devices.'
    Write-VoiceSecondary '  Hint: run `uv sync` then try again. Check that PortAudio is installed.'
    exit 2
}

Write-Verbose "Loaded $($Devices.Count) input devices"

# Load saved device_name (authoritative per ADR 0081)
$CurrentDeviceName = Get-VoiceConfigDeviceName
$SavedDevice = $null
if ($null -ne $CurrentDeviceName -and $CurrentDeviceName -ne '') {
    $SavedDevice = $Devices | Where-Object {
        $_.Name.Trim().ToLowerInvariant() -eq $CurrentDeviceName.Trim().ToLowerInvariant()
    } | Select-Object -First 1
}

$PickedIndex = $null

# ------ "Use last / Choose new" view (when a valid saved device exists) ------
if ($null -ne $SavedDevice) {
    Write-VoiceHeader '== Voice Commander =='
    Write-Host ''
    Write-VoiceHeader 'Last device:'
    Write-VoiceSuccess ("  {0}" -f $SavedDevice.Name)
    Write-VoiceSecondary ("       ({0}, {1} ch)" -f $SavedDevice.HostApi, $SavedDevice.MaxInputChannels)
    Write-Host ''
    Write-VoicePrompt '  [1] Use last'
    Write-VoicePrompt '  [2] Choose new'
    Write-VoicePrompt '  [Q] Quit'
    Write-Host ''

    $TopChoice = Read-VoiceMenuChoice -ValidKeys @('1', '2', 'Q') -Prompt '> '

    if ($TopChoice -eq 'Q') {
        Write-Host ''
        Write-VoiceSecondary 'Goodbye.'
        exit 0
    }

    if ($TopChoice -eq '1') {
        $PickedIndex = $SavedDevice.Index
    }
    # '2' falls through to device picker below
}

# ------ Device picker (no saved device, or user pressed 2) ------
if ($null -eq $PickedIndex) {
    :DevicePicker while ($true) {
        Write-Host ''
        Write-VoiceHeader 'Available input devices:'
        Show-VoiceDeviceTable -DeviceList $Devices -CurrentName $CurrentDeviceName
        Write-VoicePrompt '  Type the device index to select it.'
        Write-VoiceSecondary "  [B] Back  [Q] Quit"
        Write-Host ''

        $Chosen = Read-VoiceDeviceIndex -DeviceList $Devices

        if ($null -eq $Chosen) {
            # B was pressed
            if ($null -ne $SavedDevice) {
                # Navigate back to "Use last / Choose new" view
                Write-Host ''
                Write-VoiceHeader '== Voice Commander =='
                Write-Host ''
                Write-VoiceHeader 'Last device:'
                Write-VoiceSuccess ("  {0}" -f $SavedDevice.Name)
                Write-VoiceSecondary ("       ({0}, {1} ch)" -f $SavedDevice.HostApi, $SavedDevice.MaxInputChannels)
                Write-Host ''
                Write-VoicePrompt '  [1] Use last'
                Write-VoicePrompt '  [2] Choose new'
                Write-VoicePrompt '  [Q] Quit'
                Write-Host ''

                $BackChoice = Read-VoiceMenuChoice -ValidKeys @('1', '2', 'Q') -Prompt '> '

                if ($BackChoice -eq 'Q') {
                    Write-Host ''
                    Write-VoiceSecondary 'Goodbye.'
                    exit 0
                }

                if ($BackChoice -eq '1') {
                    $PickedIndex = $SavedDevice.Index
                    break DevicePicker
                }
                # '2' loops back to device picker
                continue DevicePicker
            }
            else {
                # No saved device -- B is treated as invalid (no back target)
                Write-VoiceFailure '  No previous device to go back to.'
                continue DevicePicker
            }
        }

        # Valid index chosen -- persist it
        $ChosenDevice = $Devices | Where-Object { $_.Index -eq $Chosen } | Select-Object -First 1
        $DeviceName   = if ($null -ne $ChosenDevice) { $ChosenDevice.Name } else { '' }
        $SaveOk = Save-VoiceDeviceChoice -Index $Chosen -Name $DeviceName
        if (-not $SaveOk) {
            Write-VoiceFailure "  Failed to save device $Chosen to config.toml."
            Write-VoiceSecondary '  Check that config.toml exists and is writable, then try again.'
            # Re-prompt rather than aborting
            continue DevicePicker
        }

        $PickedIndex = $Chosen
        break DevicePicker
    }
}

# ---------------------------------------------------------------------------
# Confirmation + launch
# ---------------------------------------------------------------------------

$FinalDevice = $Devices | Where-Object { $_.Index -eq $PickedIndex } | Select-Object -First 1
Write-Host ''
if ($null -ne $FinalDevice) {
    Write-VoiceSuccess ("Using device [{0}]: {1}." -f $PickedIndex, $FinalDevice.Name)
}
else {
    Write-VoiceSuccess "Using device [$PickedIndex]."
}

Write-Host ''
Write-VoicePrompt 'Starting Voice Commander...'

$DaemonExitCode = Start-VoiceWithUI

Write-Host ''
if ($DaemonExitCode -eq 0) {
    Write-VoiceSuccess "Voice Commander exited cleanly (code 0)."
}
elseif ($DaemonExitCode -eq 130 -or $DaemonExitCode -eq -1073741510) {
    Write-VoicePrompt "Voice Commander stopped by user (code $DaemonExitCode)."
}
else {
    Write-VoiceFailure "Voice Commander exited with error (code $DaemonExitCode)."
}

exit $DaemonExitCode
