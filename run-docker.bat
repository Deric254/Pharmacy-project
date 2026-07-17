@echo off
setlocal enabledelayedexpansion
title Pharmacy ERP - Run (Docker)
cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Docker was not found on PATH.
    echo Install Docker Desktop from https://www.docker.com/products/docker-desktop/
    echo Make sure it's running ^(check the whale icon in your system tray^) before
    echo re-running this script.
    pause
    exit /b 1
)

if not exist "backend\.env" (
    echo backend\.env doesn't exist yet -- creating one from the template.
    copy backend\.env.example backend\.env >nul
    echo.
    echo [IMPORTANT] backend\.env was created with PLACEHOLDER secrets.
    echo Open backend\.env in Notepad now and replace JWT_SECRET_KEY and
    echo ENCRYPTION_KEY with real generated values before using this for
    echo anything beyond trying the system out. Generate them with:
    echo   python -c "import secrets;print(secrets.token_hex(32))"
    echo   python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
    echo.
    pause
)

echo Starting containers ^(this can take a few minutes the first time,
echo it's downloading MySQL, Redis, and building the app images^)...
docker compose up -d --build
if errorlevel 1 (
    echo [ERROR] docker compose up failed. See the output above.
    pause
    exit /b 1
)

echo.
echo Waiting for the backend to finish starting and running migrations...
timeout /t 15 >nul

echo.
echo ===============================================
echo  If this is a fresh install, create the first user now:
echo ===============================================
set /p CREATE_USER="Create the first user now? (y/n): "
if /i "%CREATE_USER%"=="y" (
    set /p ADMIN_NAME="Full name (e.g. Jane Doe): "
    set /p ADMIN_USER="Username (e.g. jane): "
    docker compose exec backend python -m scripts.create_first_user --full-name "!ADMIN_NAME!" --username "!ADMIN_USER!" --role ChemistOwner
)

start http://localhost:8080

echo.
echo ===============================================
echo  Pharmacy ERP is running.
echo  App:     http://localhost:8080
echo  Backend: http://localhost:8000/health
echo  Stop it with: docker compose down
echo  View logs with: docker compose logs -f
echo ===============================================
pause
