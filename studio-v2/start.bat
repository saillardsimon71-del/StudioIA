@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Environnement studio-v2\.venv absent — execution automatique de setup.bat...
    call "%~dp0setup.bat"
    if %ERRORLEVEL% NEQ 0 exit /b 1
)

set STUDIO_V2_PORT=8270
echo ============================================================
echo   STUDIO IA V2 — http://127.0.0.1:%STUDIO_V2_PORT%
echo   Base isolee  : studio-v2\data\v2.db (aucun acces a V1)
echo   Ctrl+C pour arreter proprement le serveur.
echo ============================================================

.venv\Scripts\python.exe run.py --host 127.0.0.1 --port %STUDIO_V2_PORT%
