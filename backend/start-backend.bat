@echo off
rem Started by run.bat in its own window. Not meant to be run directly
rem (though it's harmless to -- it just needs backend\.venv and .env
rem to already exist, which install.bat creates).
cd /d "%~dp0"
call .venv\Scripts\activate.bat

for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if not "%%A"=="" set "%%A=%%B"
)

uvicorn app.main:app --host 0.0.0.0 --port 8000
