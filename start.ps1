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
    [switch]$ListDevices
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -LiteralPath $PSScriptRoot

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
    Write-VoiceHeader '  Phase 1: Hotkey + Audio Capture'
    Write-Host '  ---------------------------------------------------------------' -ForegroundColor Cyan
    Write-Host ''
}

# ---------------------------------------------------------------------------
# Device data helpers
# ---------------------------------------------------------------------------

function Get-VoiceConfigDevice {
    <#
    .SYNOPSIS
        Returns the integer device index from config.toml [audio] section, or $null.
    #>
    Write-Verbose 'Reading audio.device from config.toml'
    try {
        $raw = uv run python -c "import tomllib; d=tomllib.load(open('config.toml','rb')); print(d['audio']['device'])" 2>$null
        $val = [int]$raw.Trim()
        Write-Verbose "config.toml audio.device = $val"
        return $val
    }
    catch [System.Management.Automation.RuntimeException] {
        Write-Verbose "config.toml read failed (RuntimeException): $_"
        return $null
    }
    catch {
        Write-Verbose "config.toml read failed: $_"
        return $null
    }
}

function Get-VoiceInputDevice {
    <#
    .SYNOPSIS
        Returns all PortAudio input devices as typed PSCustomObject array.

    .DESCRIPTION
        Calls list-input-devices.py and returns [PSCustomObject[]] with PSTypeName
        'VoiceCommander.AudioDevice' and properties: Index, Name, HostApi,
        MaxInputChannels. Returns $null on error.
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
            [PSCustomObject]@{
                PSTypeName        = 'VoiceCommander.AudioDevice'
                Index             = [int]$d.index
                Name              = [string]$d.name
                HostApi           = [string]$d.hostapi
                MaxInputChannels  = [int]$d.max_input_channels
            }
        }

        Write-Verbose "Device list contained $(@($typed).Count) entries"
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
        The row whose Index matches -CurrentIndex is highlighted in Green;
        all other rows are White with DarkGray secondary columns.

    .PARAMETER DeviceList
        Array of PSCustomObject with PSTypeName 'VoiceCommander.AudioDevice'.

    .PARAMETER CurrentIndex
        Optional device index to highlight green (the currently saved device).
    #>
    param(
        [Parameter(Mandatory)]
        [PSCustomObject[]]$DeviceList,

        [int]$CurrentIndex = -1
    )

    $header = '{0,4}  {1,-40} {2,-20} {3,4}' -f 'Idx', 'Name', 'HostAPI', 'Ch'
    $divider = '{0,4}  {1,-40} {2,-20} {3,4}' -f '----', '----------------------------------------', '--------------------', '----'

    Write-VoiceHeader $header
    Write-VoiceHeader $divider

    foreach ($d in $DeviceList) {
        $row = '{0,4}  {1,-40} {2,-20} {3,4}' -f $d.Index, $d.Name, $d.HostApi, $d.MaxInputChannels
        if ($d.Index -eq $CurrentIndex) {
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

    .OUTPUTS
        [bool] $true on success, $false on failure.
    #>
    param(
        [Parameter(Mandatory)]
        [int]$Index
    )

    Write-Verbose "Persisting audio.device = $Index to config.toml"
    try {
        uv run python scripts/set-audio-device.py $Index
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

function Start-VoiceDaemon {
    <#
    .SYNOPSIS
        Launch `uv run voice-commander` and return its exit code.

    .DESCRIPTION
        Stdin/stdout are NOT redirected so Ctrl+C reaches the process directly.
        Returns the process exit code as [int].
    #>
    Write-Verbose 'Launching daemon: uv run voice-commander'
    uv run voice-commander
    return $LASTEXITCODE
}

# ---------------------------------------------------------------------------
# Pre-flight: enumerate devices (needed by most code paths)
# ---------------------------------------------------------------------------

$IsInteractive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected

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
    $SavedForList = Get-VoiceConfigDevice
    $CurrentIdx = if ($null -ne $SavedForList) { $SavedForList } else { -1 }
    Show-VoiceBanner
    Write-VoiceHeader 'Available input devices:'
    Show-VoiceDeviceTable -DeviceList $DevicesForList -CurrentIndex $CurrentIdx
    Write-VoiceSecondary '  (Green row = currently saved device in config.toml)'
    Write-Host ''
    exit 0
}

# ---------------------------------------------------------------------------
# -Device N path  (persist + launch, no TUI)
# ---------------------------------------------------------------------------

if ($PSCmdlet.ParameterSetName -eq 'DirectDevice') {
    Write-Verbose "-Device $Device specified; skipping TUI"
    $SaveResult = Save-VoiceDeviceChoice -Index $Device
    if (-not $SaveResult) {
        Write-Error "Failed to persist device $Device to config.toml."
        exit 1
    }
    $DevicesForDirect = Get-VoiceInputDevice
    $ChosenDirect = if ($null -ne $DevicesForDirect) {
        $DevicesForDirect | Where-Object { $_.Index -eq $Device } | Select-Object -First 1
    }
    else { $null }

    if ($null -ne $ChosenDirect) {
        Write-VoiceSuccess ("Using device [{0}]: {1}." -f $Device, $ChosenDirect.Name)
    }
    else {
        Write-VoiceSuccess "Using device [$Device]."
    }
    Write-Host ''
    Write-VoicePrompt 'Starting Voice Commander (Phase 1: hotkey + audio)...'
    $ExitCode = Start-VoiceDaemon
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
    Write-Verbose '-NoMenu specified; reading saved device from config.toml'
    $SavedNoMenu = Get-VoiceConfigDevice
    if ($null -eq $SavedNoMenu -or $SavedNoMenu -lt 0) {
        Write-Error 'No valid audio.device found in config.toml. Run start.ps1 interactively to choose a device, or use -Device <index>.'
        exit 1
    }
    Write-Verbose "Non-interactive: using saved device [$SavedNoMenu]"
    Write-VoiceSuccess "Using saved device [$SavedNoMenu]."
    Write-Host ''
    Write-VoicePrompt 'Starting Voice Commander (Phase 1: hotkey + audio)...'
    $ExitCode = Start-VoiceDaemon
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
    Write-Verbose 'Non-interactive session detected; falling back to saved device'
    $SavedAuto = Get-VoiceConfigDevice
    if ($null -eq $SavedAuto -or $SavedAuto -lt 0) {
        Write-Error 'Non-interactive session and no valid audio.device in config.toml. Set audio.device in config.toml or run with -Device <index>.'
        exit 1
    }
    Write-VoiceSuccess "Non-interactive session -- using saved device [$SavedAuto]."
    Write-Host ''
    Write-VoicePrompt 'Starting Voice Commander (Phase 1: hotkey + audio)...'
    $ExitCode = Start-VoiceDaemon
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

# Load saved device
$CurrentDevice = Get-VoiceConfigDevice
$SavedDevice = $null
if ($null -ne $CurrentDevice -and $CurrentDevice -ge 0) {
    $SavedDevice = $Devices | Where-Object { $_.Index -eq $CurrentDevice } | Select-Object -First 1
}

$PickedIndex = $null

# ------ "Use last / Choose new" view (when a valid saved device exists) ------
if ($null -ne $SavedDevice) {
    Write-VoiceHeader '== Voice Commander =='
    Write-Host ''
    Write-VoiceHeader 'Last device:'
    Write-VoiceSuccess ("  [{0}] {1}" -f $SavedDevice.Index, $SavedDevice.Name)
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
        $PickedIndex = $CurrentDevice
    }
    # '2' falls through to device picker below
}

# ------ Device picker (no saved device, or user pressed 2) ------
if ($null -eq $PickedIndex) {
    :DevicePicker while ($true) {
        Write-Host ''
        Write-VoiceHeader 'Available input devices:'
        $highlight = if ($null -ne $CurrentDevice) { $CurrentDevice } else { -1 }
        Show-VoiceDeviceTable -DeviceList $Devices -CurrentIndex $highlight
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
                Write-VoiceSuccess ("  [{0}] {1}" -f $SavedDevice.Index, $SavedDevice.Name)
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
                    $PickedIndex = $CurrentDevice
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
        $SaveOk = Save-VoiceDeviceChoice -Index $Chosen
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
Write-VoicePrompt 'Starting Voice Commander (Phase 1: hotkey + audio)...'

$DaemonExitCode = Start-VoiceDaemon

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
