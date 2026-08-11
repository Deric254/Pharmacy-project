@echo off
setlocal enabledelayedexpansion
title Pharmacy ERP - Frontend
cd /d "%~dp0"

rem Normally started by Pharmacy-ERP.bat in its own window, but hardened
rem to be safe if double-clicked directly too.

if not exist "node_modules" (
    echo [ERROR] frontend\node_modules does not exist yet.
    echo Run Pharmacy-ERP.bat from the project root first -- it sets
    echo everything up, including this.
    pause
    exit /b 1
)

echo Starting the frontend on http://localhost:5173 ...
echo ^(Ctrl+C to stop, or just close this window^)
echo.
call npm run dev -- --host 0.0.0.0 --port 5173
if errorlevel 1 (
    echo.
    echo [ERROR] The frontend exited with an error -- see the output above.
    pause
    exit /b 1
)

echo.
echo Frontend stopped.
pause
