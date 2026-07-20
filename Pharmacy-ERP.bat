@echo off
setlocal enabledelayedexpansion
title Pharmacy ERP
cd /d "%~dp0"

echo ===============================================
echo  Pharmacy ERP
echo ===============================================
echo.

rem --- Sanity check: were we actually extracted, or double-clicked from
rem     inside a still-zipped folder? Windows Explorer will happily open
rem     a .bat straight out of a zip preview and run it from a temp
rem     folder that doesn't have the rest of the project next to it --
rem     that fails in confusing ways, so catch it up front with a clear
rem     message instead of a wall of unrelated errors below.
if not exist "%~dp0backend\app\main.py" (
    echo [ERROR] This doesn't look like a fully extracted copy of the project
    echo ^(backend\app\main.py is missing^).
    echo.
    echo If you double-clicked this straight out of a .zip file in Windows
    echo Explorer, that's the problem: right-click the .zip, choose
    echo "Extract All...", then run Pharmacy-ERP.bat from the extracted
    echo folder instead.
    echo.
    pause
    exit /b 1
)
if not exist "%~dp0frontend\package.json" (
    echo [ERROR] This doesn't look like a fully extracted copy of the project
    echo ^(frontend\package.json is missing^). Extract the .zip fully first.
    echo.
    pause
    exit /b 1
)

rem --- Is setup already done? If so, skip straight to launching. -----------
set NEEDS_INSTALL=0
if not exist "backend\.venv" set NEEDS_INSTALL=1
if not exist "backend\.env" set NEEDS_INSTALL=1
if not exist "frontend\node_modules" set NEEDS_INSTALL=1

if "%NEEDS_INSTALL%"=="1" (
    call :INSTALL
    if errorlevel 1 (
        echo.
        echo [ERROR] Setup did not finish. See the messages above for why.
        pause
        exit /b 1
    )
)

call :LAUNCH
exit /b 0

:INSTALL
echo First-time setup detected. This only needs to happen once.
echo.

rem --- Check Python ---------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Install Python 3.12 or newer from https://www.python.org/downloads/
    echo IMPORTANT: on the installer's first screen, tick "Add python.exe to PATH".
    echo Then re-run this script.
    exit /b 1
)
echo [OK] Python found.

rem --- Check Node -------------------------------------------------------
where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js was not found on PATH.
    echo Install Node.js 20 or newer ^(LTS^) from https://nodejs.org/
    echo Then re-run this script.
    exit /b 1
)
echo [OK] Node found.

rem --- Check Redis --------------------------------------------------------
where redis-server >nul 2>nul
if errorlevel 1 (
    where memurai >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Neither redis-server nor Memurai was found on PATH.
        echo Windows has no official Redis build. The simplest fix:
        echo   1. Install Memurai Developer ^(free, Redis-compatible Windows
        echo      service^) from https://www.memurai.com/get-memurai
        echo   2. Its installer registers a background service automatically,
        echo      so nothing needs to be on PATH after that -- just re-run
        echo      this script once it's installed.
        echo   OR install Docker Desktop and use run-docker.bat instead,
        echo   which does not need Redis on Windows at all.
        exit /b 1
    )
)
echo [OK] Redis/Memurai found.
echo.

rem --- Backend: virtual environment + dependencies -----------------------
echo Setting up backend...
cd backend
if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the Python virtual environment.
        cd ..
        exit /b 1
    )
)
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Could not activate the virtual environment.
    cd ..
    exit /b 1
)
python -m pip install --upgrade pip >nul
pip install -r requirements.txt -r requirements-dev.txt
if errorlevel 1 (
    echo [ERROR] Backend dependency install failed. See the output above.
    cd ..
    exit /b 1
)

rem --- Generate a real .env if one doesn't exist yet ----------------------
if not exist ".env" (
    echo Creating backend\.env with a freshly generated encryption key...
    for /f "delims=" %%K in ('python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"') do set ENCKEY=%%K
    for /f "delims=" %%J in ('python -c "import secrets;print(secrets.token_hex(32))"') do set JWTKEY=%%J
    (
        echo ENVIRONMENT=development
        echo DATABASE_URL=sqlite+aiosqlite:///./dev.db
        echo REDIS_URL=redis://localhost:6379/0
        echo JWT_SECRET_KEY=!JWTKEY!
        echo ENCRYPTION_KEY=!ENCKEY!
        echo CORS_ORIGINS=["http://localhost:5173","http://localhost:8080"]
    ) > .env
    echo [OK] backend\.env created. Keep this file private -- it has real secrets in it.
) else (
    echo [OK] backend\.env already exists, leaving it alone.
)

for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" set "%%A=%%B"
)

echo.
echo Running database migrations...
alembic upgrade head
if errorlevel 1 (
    echo [ERROR] Migrations failed. See the output above.
    cd ..
    exit /b 1
)

echo.
echo ===============================================
echo  Create the first user ^(owner account^)
echo ===============================================
set /p ADMIN_NAME="Full name (e.g. Jane Doe): "
set /p ADMIN_USER="Username (e.g. jane): "
python -m scripts.create_first_user --full-name "!ADMIN_NAME!" --username "!ADMIN_USER!" --role ChemistOwner
if errorlevel 1 (
    echo [WARNING] Could not create the first user -- maybe one already exists.
    echo If this is a fresh install and you want a different account, delete
    echo backend\dev.db and re-run this script.
)

cd ..

rem --- Frontend: install dependencies --------------------------------------
echo.
echo Setting up frontend...
cd frontend
call npm install
if errorlevel 1 (
    echo [ERROR] Frontend dependency install failed. See the output above.
    cd ..
    exit /b 1
)
cd ..

echo.
echo [OK] Setup complete.
echo.
exit /b 0

:LAUNCH
echo Starting Pharmacy ERP...
echo.

where redis-server >nul 2>nul
if not errorlevel 1 (
    netstat -an | findstr "6379" | findstr "LISTENING" >nul 2>nul
    if errorlevel 1 (
        echo Starting Redis...
        start "Pharmacy ERP - Redis" /min redis-server
        timeout /t 2 /nobreak >nul
    ) else (
        echo [OK] Redis is already running.
    )
) else (
    echo [INFO] redis-server not on PATH -- assuming Memurai is running as a
    echo Windows service in the background. If the backend window below
    echo shows connection errors, that assumption was wrong.
)

echo Starting backend ^(new window^)...
start "Pharmacy ERP - Backend" cmd /k "%~dp0backend\start-backend.bat"

echo Waiting for the backend to come up...
set BACKEND_READY=0
for /l %%i in (1,1,30) do (
    curl -s -o nul -w "%%{http_code}" http://localhost:8000/health > "%TEMP%\pharmacy_health.txt" 2>nul
    set /p HEALTH_CODE=<"%TEMP%\pharmacy_health.txt"
    if "!HEALTH_CODE!"=="200" (
        set BACKEND_READY=1
        goto :backend_ready
    )
    timeout /t 1 /nobreak >nul
)
:backend_ready
if "!BACKEND_READY!"=="0" (
    echo [WARNING] Backend did not answer /health after 30s. Check the
    echo "Pharmacy ERP - Backend" window for errors. Continuing anyway --
    echo the frontend may just show connection errors until it's up.
) else (
    echo [OK] Backend is up.
)

echo Starting frontend ^(new window^)...
start "Pharmacy ERP - Frontend" cmd /k "%~dp0frontend\start-frontend.bat"

echo Waiting for the frontend to come up...
timeout /t 5 /nobreak >nul

echo Opening the app in your browser...
start http://localhost:5173

echo.
echo ===============================================
echo  Running. Two windows opened: Backend and Frontend.
echo  Close both of those (or Ctrl+C in each) to stop everything.
echo  This window can be closed.
echo ===============================================
pause
exit /b 0
