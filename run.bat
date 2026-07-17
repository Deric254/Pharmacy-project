@echo off
setlocal
title Pharmacy ERP - Run
cd /d "%~dp0"

if not exist "backend\.venv" (
    echo [ERROR] Backend isn't installed yet. Run install.bat first.
    pause
    exit /b 1
)
if not exist "frontend\node_modules" (
    echo [ERROR] Frontend isn't installed yet. Run install.bat first.
    pause
    exit /b 1
)

rem --- Start Redis if nothing is already listening on 6379 -------------
netstat -an | findstr "6379" | findstr "LISTENING" >nul 2>nul
if errorlevel 1 (
    where redis-server >nul 2>nul
    if errorlevel 1 (
        echo [WARNING] Nothing is listening on port 6379 and redis-server
        echo isn't on PATH. If you installed Memurai, its service should
        echo already be running in the background -- check Windows Services
        echo if the app fails to start. Continuing anyway...
    ) else (
        echo Starting Redis...
        start "Pharmacy ERP - Redis" /min redis-server
        timeout /t 2 >nul
    )
) else (
    echo [OK] Something is already listening on port 6379.
)

echo Starting backend...
start "Pharmacy ERP - Backend" cmd /k "%~dp0backend\start-backend.bat"

echo Waiting for the backend to come up...
where curl >nul 2>nul
if errorlevel 1 (
    echo curl isn't available to health-check the backend -- just waiting 8s instead.
    timeout /t 8 >nul
) else (
    setlocal enabledelayedexpansion
    set BACKEND_READY=0
    for /l %%i in (1,1,20) do (
        if "!BACKEND_READY!"=="0" (
            curl -s -o nul http://localhost:8000/health
            if not errorlevel 1 (
                set BACKEND_READY=1
            ) else (
                timeout /t 1 >nul
            )
        )
    )
    if "!BACKEND_READY!"=="0" (
        echo [WARNING] Backend didn't respond to a health check after 20s.
        echo Check the "Pharmacy ERP - Backend" window for errors.
    )
    endlocal
)

echo Starting frontend...
start "Pharmacy ERP - Frontend" cmd /k "%~dp0frontend\start-frontend.bat"

timeout /t 3 >nul
start http://localhost:5173

echo.
echo ===============================================
echo  Pharmacy ERP is starting in two new windows.
echo  Frontend: http://localhost:5173
echo  Backend:  http://localhost:8000/health
echo  Close those two windows (or this one) to stop it.
echo ===============================================
pause
