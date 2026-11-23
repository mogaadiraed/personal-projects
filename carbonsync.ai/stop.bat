@echo off
rem CarbonSync AI - stop everything the dashboard runs (Windows).
rem
rem   stop.bat               stops the dashboard and its background work (pipeline runs, forecasts,
rem                          model evaluation, assistant), any CarbonSync command started from a
rem                          terminal, unloads the local models (frees the GPU) and closes Ollama.
rem   stop.bat keep-ollama   same, but leaves Ollama open (the models are still unloaded).
rem
rem The work is done by scripts\stop_dashboard.ps1.

setlocal
cd /d "%~dp0"
title CarbonSync AI - stop
set "OPTS="
if /i "%~1"=="keep-ollama" set "OPTS=-KeepOllama"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_dashboard.ps1" %OPTS%
echo.
pause
endlocal
