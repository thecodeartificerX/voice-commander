@echo off
title voice-commander-kill
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kill.ps1" %*
