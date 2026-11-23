@echo off
rem CarbonSync AI - start the dashboard (Windows).
rem
rem   start.bat             starts the dashboard, opens the browser when it is ready, and keeps this
rem                         window open: the activity of the dashboard, the pipeline and the assistant
rem                         is printed here.
rem   start.bat nobrowser   same, without opening the browser.
rem
rem Stop it: Ctrl+C in this window, close the window, or run stop.bat (which also stops the local models).

setlocal
cd /d "%~dp0"
title CarbonSync AI - dashboard
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
set "PORT=8501"
set "URL=http://localhost:%PORT%"

if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")

"%PY%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo Python or Streamlit was not found. Install them first: pip install -r requirements.txt
    goto :end
)

netstat -ano -p tcp | findstr /r /c:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
    echo CarbonSync AI is already running on %URL% - run stop.bat to stop it.
    if /i not "%~1"=="nobrowser" start "" "%URL%"
    goto :end
)

echo ==========================================================================
echo   CarbonSync AI - dashboard
echo   Address : %URL%
echo   Stop    : Ctrl+C here, close this window, or run stop.bat
echo   The activity of the dashboard, the pipeline and the assistant shows below.
echo ==========================================================================
echo.

rem Open the browser once the server answers (waits up to 2 minutes, in the background).
if /i not "%~1"=="nobrowser" start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference = 'SilentlyContinue'; for ($i = 0; $i -lt 120; $i++) { try { $null = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 'http://127.0.0.1:%PORT%/_stcore/health'; Start-Process '%URL%'; break } catch { Start-Sleep -Seconds 1 } }"

rem The full path of streamlit_app.py lets stop.bat find this dashboard among other Python processes.
"%PY%" -m streamlit run "%~dp0streamlit_app.py" --server.port %PORT% --server.headless true

echo.
echo CarbonSync AI has stopped.

:end
echo.
pause
endlocal
