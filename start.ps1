# start.ps1 -- Voice Commander launcher with audio-device TUI
# Phase 1 (hotkey + audio capture): presents a device-selection menu on first
# run (or when the user wants to switch), persists the choice to config.toml,
# then starts the daemon via `uv run voice-commander`.
#
# Compatible with Windows PowerShell 5.1+ and PowerShell 7+.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Set-Location $PSScriptRoot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Read-SingleKey {
    # Returns a single character string.  Falls back to Read-Host in
    # non-interactive or restricted hosts where RawUI is unavailable.
    if ([Environment]::UserInteractive -and $Host.UI.RawUI -ne $null) {
        try {
            $key = [Console]::ReadKey($true)
            return $key.KeyChar.ToString()
        } catch {
            return (Read-Host).Trim()
        }
    } else {
        return (Read-Host).Trim()
    }
}

function Get-CurrentDevice {
    # Returns the integer stored in config.toml [audio] device, or -1 on error.
    try {
        $val = uv run python -c "import tomllib; d=tomllib.load(open('config.toml','rb')); print(d['audio']['device'])" 2>$null
        return [int]$val.Trim()
    } catch {
        return -1
    }
}

function Get-InputDevices {
    # Returns a parsed array of device objects from list-input-devices.py,
    # or $null on error.
    try {
        $raw = uv run python scripts/list-input-devices.py 2>$null
        $parsed = $raw | ConvertFrom-Json
        # If the script returned {"error": "..."} the array check below handles it.
        if ($parsed -is [System.Array] -or $parsed -is [System.Collections.ArrayList]) {
            return $parsed
        }
        # Single object (error case or single device returned as object)
        if ($parsed.PSObject.Properties['error']) {
            Write-Host "Warning: could not list devices: $($parsed.error)"
            return $null
        }
        # Single device returned as object -- wrap it
        return @($parsed)
    } catch {
        Write-Host "Warning: could not query input devices: $_"
        return $null
    }
}

function Find-DeviceByIndex([int]$idx, $devices) {
    foreach ($d in $devices) {
        if ([int]$d.index -eq $idx) { return $d }
    }
    return $null
}

function Show-DeviceMenu($devices) {
    Write-Host ""
    Write-Host "Available input devices:"
    Write-Host "--------------------------------------------------"
    foreach ($d in $devices) {
        Write-Host ("  [{0}] {1}  ({2}, {3} ch)" -f $d.index, $d.name, $d.hostapi, $d.max_input_channels)
    }
    Write-Host "--------------------------------------------------"
}

# ---------------------------------------------------------------------------
# Non-interactive fast-path
# ---------------------------------------------------------------------------
$interactive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected

# ---------------------------------------------------------------------------
# Main TUI
# ---------------------------------------------------------------------------

$currentDevice = Get-CurrentDevice
$devices       = Get-InputDevices

Write-Host ""
Write-Host "== Voice Commander =="

# Determine if we have a valid saved device
$savedDevice = $null
if ($devices -ne $null -and $currentDevice -ge 0) {
    $savedDevice = Find-DeviceByIndex $currentDevice $devices
}

$pickedIndex = $null

if (-not $interactive) {
    # Non-interactive session: use saved device if valid, else abort.
    if ($savedDevice -ne $null) {
        Write-Host "Non-interactive session -- using saved device [$currentDevice]: $($savedDevice.name)"
        $pickedIndex = $currentDevice
    } else {
        Write-Host "Non-interactive session and no valid saved device -- set audio.device in config.toml and re-run."
        exit 1
    }
} elseif ($savedDevice -ne $null) {
    # Show the "Use last / Choose new" prompt
    Write-Host ("Last device: [{0}] {1}" -f $currentDevice, $savedDevice.name)
    Write-Host ""
    Write-Host "  [1] Use last"
    Write-Host "  [2] Choose new"

    $choice = $null
    while ($choice -notin @('1','2')) {
        Write-Host -NoNewline "> "
        $choice = Read-SingleKey
        Write-Host $choice  # echo the key
        if ($choice -notin @('1','2')) {
            Write-Host "Please press 1 or 2."
        }
    }

    if ($choice -eq '1') {
        $pickedIndex = $currentDevice
    }
    # else fall through to device picker below
}

# Device picker (runs when no valid saved device, or user pressed 2)
if ($pickedIndex -eq $null) {
    if ($devices -eq $null -or $devices.Count -eq 0) {
        Write-Host "No input devices found.  Check sounddevice / PortAudio installation."
        exit 1
    }

    Show-DeviceMenu $devices

    $validIndices = @($devices | ForEach-Object { [int]$_.index })

    $picked = $null
    while ($picked -eq $null) {
        $raw = (Read-Host "Enter device index").Trim()
        $asInt = $null
        if ([int]::TryParse($raw, [ref]$asInt) -and $validIndices -contains $asInt) {
            $picked = $asInt
        } else {
            Write-Host ("  Invalid -- enter one of: {0}" -f ($validIndices -join ', '))
        }
    }

    # Persist to config.toml
    uv run python scripts/set-audio-device.py $picked
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to save device choice to config.toml (exit $LASTEXITCODE)."
        exit $LASTEXITCODE
    }

    $pickedIndex = $picked
}

# Confirmation line
$chosenDevice = Find-DeviceByIndex $pickedIndex $devices
if ($chosenDevice -ne $null) {
    Write-Host ("Using device [{0}]: {1}." -f $pickedIndex, $chosenDevice.name)
} else {
    Write-Host "Using device [$pickedIndex]."
}

Write-Host ""
Write-Host "Starting Voice Commander (Phase 1: hotkey + audio)..."

# ---------------------------------------------------------------------------
# Launch daemon -- stdin/stdout NOT redirected (Ctrl+C must reach the process)
# ---------------------------------------------------------------------------
uv run voice-commander

$code = $LASTEXITCODE
Write-Host "Voice Commander exited (code $code)."
exit $code
