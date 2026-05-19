@echo off
title voice-commander

REM ---------------------------------------------------------------------------
REM Launcher wrapper.
REM
REM The bare `powershell -File start.ps1` form closed this console the instant
REM start.ps1 exited — clean OR crashed — so any launch failure flashed past
REM unseen ("no reports, no nothing"). We now capture the exit code and, on a
REM non-zero exit, hold the window open so the error stays readable.
REM
REM Exit-code key (mirrors start.ps1):
REM   0                 clean exit
REM   130 / -1073741510 stopped by user (Ctrl+C)
REM   2                 audio-device enumeration failed
REM   other             launch or daemon error
REM ---------------------------------------------------------------------------

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "VC_EXIT=%ERRORLEVEL%"

if not "%VC_EXIT%"=="0" (
    echo.
    echo [start.bat] Voice Commander exited with code %VC_EXIT%.
    echo [start.bat] Window held open so the error above stays readable.
    echo [start.bat] Logs: supervisor.log, voice-commander.log, outputs\cancel_guard.log
    echo.
    pause
)

exit /b %VC_EXIT%
