@echo off
setlocal enabledelayedexpansion
title Pharmacy ERP - Build Installer
cd /d "%~dp0"

echo ===============================================
echo  Pharmacy ERP - Build Installer
echo ===============================================
echo.
echo This builds a real Windows installer (.exe) from this source code,
echo entirely on this machine -- no GitHub, no internet required except
echo for downloading dependencies the first time.
echo.

rem --- Sanity check: same as Pharmacy-ERP.bat -- catches the most common
rem     way this fails (double-clicked from inside a still-zipped folder).
if not exist "%~dp0backend\app\main.py" (
    echo [ERROR] This doesn't look like a fully extracted copy of the project
    echo ^(backend\app\main.py is missing^).
    echo.
    echo If you double-clicked this straight out of a .zip file in Windows
    echo Explorer, extract it fully first ^(right-click the .zip, "Extract
    echo All..."^), then run this script from the extracted folder.
    echo.
    pause
    exit /b 1
)
if not exist "%~dp0electron\main.js" (
    echo [ERROR] electron\main.js is missing -- this doesn't look like a
    echo complete copy of the project.
    pause
    exit /b 1
)

rem --- Check prerequisites --------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo Install Python 3.12 or newer from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH" on the installer's first screen^),
    echo then re-run this script.
    pause
    exit /b 1
)
where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js was not found on PATH.
    echo Install Node.js 20 or newer ^(LTS^) from https://nodejs.org/
    echo then re-run this script.
    pause
    exit /b 1
)
echo [OK] Python and Node found.
echo.

rem --- Icon check, informational only -- never blocks the build ------
if exist "%~dp0electron\build\icon.ico" (
    echo [OK] Custom icon found -- this build will be branded with it.
    echo.
) else if exist "%~dp0electron\build\logo.png" (
    echo [OK] logo.png found -- this will be converted to the installer
    echo icon automatically in a moment.
    echo.
) else (
    echo [INFO] No logo found. This build will use Electron's generic
    echo default icon everywhere ^(installer, shortcuts, taskbar^).
    echo.
    echo To brand this installer for a specific business: put a square
    echo PNG image at electron\build\logo.png and run this script again
    echo -- it converts automatically, no other tool needed.
    echo.
    set /p CONTINUE_NO_ICON="Continue with the default icon? (Y/N): "
    if /i not "!CONTINUE_NO_ICON!"=="Y" (
        echo Cancelled. Add electron\build\logo.png and re-run when ready.
        pause
        exit /b 0
    )
    echo.
)

echo ===============================================
echo  Step 1/4: Building the frontend
echo ===============================================
cd frontend
call npm install
if errorlevel 1 (
    echo [ERROR] Frontend dependency install failed. See the output above.
    cd ..
    pause
    exit /b 1
)
call npm run build
if errorlevel 1 (
    echo [ERROR] Frontend build failed. See the output above.
    cd ..
    pause
    exit /b 1
)
cd ..
echo [OK] Frontend built.
echo.

echo ===============================================
echo  Step 2/4: Setting up the backend build environment
echo ===============================================
cd backend
if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the Python virtual environment.
        cd ..
        pause
        exit /b 1
    )
)
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Could not activate the virtual environment.
    cd ..
    pause
    exit /b 1
)
python -m pip install --upgrade pip >nul
pip install -r requirements.txt -r requirements-dev.txt
if errorlevel 1 (
    echo [ERROR] Backend dependency install failed. See the output above.
    cd ..
    pause
    exit /b 1
)
cd ..
echo [OK] Backend environment ready.
echo.

if exist "electron\build\logo.png" if not exist "electron\build\icon.ico" (
    echo ===============================================
    echo  Converting electron\build\logo.png to icon.ico
    echo ===============================================
    cd backend
    call .venv\Scripts\activate.bat
    python -m scripts.make_icon ..\electron\build\logo.png
    if errorlevel 1 (
        echo [ERROR] Icon conversion failed. See the output above.
        cd ..
        pause
        exit /b 1
    )
    cd ..
    echo.
)

echo ===============================================
echo  Step 3/4: Building the backend executable
echo ===============================================
call backend\.venv\Scripts\activate.bat
pyinstaller pyinstaller\pharmacy-erp.spec --distpath dist --noconfirm
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed. See the output above.
    pause
    exit /b 1
)
if not exist "dist\Pharmacy-ERP\Pharmacy-ERP.exe" (
    echo [ERROR] Build reported success but dist\Pharmacy-ERP\Pharmacy-ERP.exe is missing.
    pause
    exit /b 1
)
echo [OK] Backend executable built.
echo.

echo ===============================================
echo  Step 4/4: Building the Windows installer
echo ===============================================
cd electron
call npm install
if errorlevel 1 (
    echo [ERROR] Electron dependency install failed. See the output above.
    cd ..
    pause
    exit /b 1
)
call npm run build:win
if errorlevel 1 (
    echo [ERROR] Installer build failed. See the output above.
    cd ..
    pause
    exit /b 1
)
cd ..
echo [OK] Installer built.
echo.

rem --- Copy the final installer somewhere obvious, with a clear name --
if not exist "installer-output" mkdir installer-output
set FOUND_INSTALLER=0
for %%F in (electron\dist\*.exe) do (
    copy /Y "%%F" "installer-output\%%~nxF" >nul
    set FOUND_INSTALLER=1
    set INSTALLER_NAME=%%~nxF
)
if "!FOUND_INSTALLER!"=="0" (
    echo [ERROR] Installer build reported success but no .exe was found
    echo under electron\dist. Check the output above for what actually
    echo happened.
    pause
    exit /b 1
)

echo ===============================================
echo  Done.
echo.
echo  Installer ready at:
echo  installer-output\!INSTALLER_NAME!
echo ===============================================
pause
