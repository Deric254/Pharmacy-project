@echo off
setlocal enabledelayedexpansion
title Pharmacy ERP - Backend
cd /d "%~dp0"

rem Normally started by Pharmacy-ERP.bat in its own window, but hardened
rem to be safe if double-clicked directly too -- every failure path below
rem prints a clear reason and pauses, so this window can never vanish
rem without the message being readable.

if not exist ".venv" (
    echo [ERROR] backend\.venv does not exist yet.
    echo Run Pharmacy-ERP.bat from the project root first -- it sets
    echo everything up, including this.
    pause
    exit /b 1
)
if not exist ".env" (
    echo [ERROR] backend\.env does not exist yet.
    echo Run Pharmacy-ERP.bat from the project root first -- it sets
    echo everything up, including this.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Could not activate the virtual environment.
    echo Try deleting backend\.venv and re-running Pharmacy-ERP.bat.
    pause
    exit /b 1
)

for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" set "%%A=%%B"
)

rem --- Check for an already-healthy instance before trying to bind ---
rem A leftover backend from an earlier run/test session sitting on this
rem port is the single most common reason this crashes -- confirmed by
rem an actual bug report showing exactly this raw, unreadable error.
rem If something here already answers correctly, there's nothing to
rem start; just say so and stop, instead of failing to bind and
rem printing a confusing crash.
for /f %%H in ('powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\windows\check-backend-health.ps1"') do set ALREADY_HEALTHY=%%H
if "!ALREADY_HEALTHY!"=="yes" (
    echo [OK] Pharmacy ERP is already running at http://localhost:8000
    echo Nothing to start -- this window can be closed.
    pause
    exit /b 0
)

echo Starting the backend on http://localhost:8000 ...
echo ^(Ctrl+C to stop, or just close this window^)
echo.
uvicorn app.main:app --host 0.0.0.0 --port 8000
if errorlevel 1 (
    echo.
    echo [ERROR] The backend exited with an error -- see the output above.
    pause
    exit /b 1
)

echo.
echo Backend stopped.
pause
