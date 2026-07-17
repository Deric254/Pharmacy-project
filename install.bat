@echo off
setlocal enabledelayedexpansion
title Pharmacy ERP - Install
cd /d "%~dp0"

echo ===============================================
echo  Pharmacy ERP - first-time install
echo ===============================================
echo.

rem --- Check Python ---------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Install Python 3.12 or newer from https://www.python.org/downloads/
    echo IMPORTANT: on the installer's first screen, tick "Add python.exe to PATH".
    echo Then re-run this script.
    pause
    exit /b 1
)
echo [OK] Python found.

rem --- Check Node -------------------------------------------------------
where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js was not found on PATH.
    echo Install Node.js 20 or newer (LTS) from https://nodejs.org/
    echo Then re-run this script.
    pause
    exit /b 1
)
echo [OK] Node found.

rem --- Check Redis --------------------------------------------------------
where redis-server >nul 2>nul
if errorlevel 1 (
    echo [ERROR] redis-server was not found on PATH.
    echo Windows has no official Redis build. The simplest fix:
    echo   1. Install Memurai Developer ^(free, Redis-compatible Windows service^)
    echo      from https://www.memurai.com/get-memurai
    echo   2. Its installer registers a background service automatically,
    echo      so redis-server does not need to be on PATH after that --
    echo      just re-run this script once it's installed.
    echo   OR install Docker Desktop and use run-docker.bat instead, which
    echo   does not need Redis on Windows at all.
    pause
    exit /b 1
)
echo [OK] Redis found.
echo.

rem --- Backend: virtual environment + dependencies -----------------------
echo Setting up backend...
cd backend
if not exist ".venv" (
    python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt -r requirements-dev.txt
if errorlevel 1 (
    echo [ERROR] Backend dependency install failed. See the output above.
    pause
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
        echo CORS_ORIGINS=["http://localhost:5173"]
    ) > .env
    echo [OK] backend\.env created. Keep this file private -- it has real secrets in it.
) else (
    echo [OK] backend\.env already exists, leaving it alone.
)

rem --- Load .env into this session so alembic/create_first_user can see it.
rem     Simple key=value loader -- this script never writes comments or
rem     blank lines into .env, so it doesn't need to handle them.
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" set "%%A=%%B"
)

echo.
echo Running database migrations...
alembic upgrade head
if errorlevel 1 (
    echo [ERROR] Migrations failed. See the output above.
    pause
    exit /b 1
)

echo.
echo ===============================================
echo  Create the first user (owner account)
echo ===============================================
set /p ADMIN_NAME="Full name (e.g. Jane Doe): "
set /p ADMIN_USER="Username (e.g. jane): "
python -m scripts.create_first_user --full-name "%ADMIN_NAME%" --username "%ADMIN_USER%" --role ChemistOwner
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
    pause
    exit /b 1
)
cd ..

echo.
echo ===============================================
echo  Install complete. Run run.bat to start it.
echo ===============================================
pause
