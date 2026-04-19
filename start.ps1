# start.ps1 — Voice Commander one-click launcher
# Updated each phase to run the current phase's entry command.
# Phase 1 (hotkey + audio capture): runs the daemon entrypoint via uv.

$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot

Write-Host "Starting Voice Commander (Phase 1: hotkey + audio)..."

uv run voice-commander

Write-Host "Voice Commander exited (code $LASTEXITCODE)."
exit $LASTEXITCODE
