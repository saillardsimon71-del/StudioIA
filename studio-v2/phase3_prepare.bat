@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    call "%~dp0setup.bat"
    if %ERRORLEVEL% NEQ 0 exit /b 1
)

if "%~1"=="" (
    .venv\Scripts\python.exe scripts\phase3_fruit_take.py --prepare
) else (
    .venv\Scripts\python.exe scripts\phase3_fruit_take.py --prepare --image "%~1"
)
