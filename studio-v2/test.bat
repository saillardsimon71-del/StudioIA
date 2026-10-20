@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    call "%~dp0setup.bat"
    if %ERRORLEVEL% NEQ 0 exit /b 1
)

.venv\Scripts\python.exe -m pytest -v tests
