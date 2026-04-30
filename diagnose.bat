@echo off
title voice-commander diagnose
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose.ps1" %*
echo.
pause
