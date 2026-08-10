@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Lazybones Python environment was not found.
    echo Please redeploy the application environment first.
    pause
    exit /b 1
)

start "Lazybones" ".venv\Scripts\pythonw.exe" "src\main.py"
