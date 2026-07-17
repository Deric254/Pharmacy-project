@echo off
rem Started by run.bat in its own window.
cd /d "%~dp0"
npm run dev -- --host 0.0.0.0 --port 5173
