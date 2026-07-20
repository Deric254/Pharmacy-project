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
