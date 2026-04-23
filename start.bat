@echo off
title voice-commander
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
