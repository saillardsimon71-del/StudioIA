@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if "%~1"=="" (
    echo Usage : phase3_run_authorized.bat "C:\chemin\vers\fruit_chatgpt.png" [plafond_eur]
    exit /b 2
)

set MAX_EUR=%~2
if "%MAX_EUR%"=="" set MAX_EUR=0.50

.venv\Scripts\python.exe scripts\phase3_fruit_take.py --image "%~1" --max-eur %MAX_EUR% --authorize-gpu
