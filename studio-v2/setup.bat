@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   STUDIO IA V2 — Installation isolee (aucun impact sur V1)
echo ============================================================

if not exist ".venv\Scripts\python.exe" (
    where uv >nul 2>nul
    if %ERRORLEVEL%==0 (
        echo Creation de l'environnement Python isole avec uv...
        uv venv --python 3.12 .venv
    ) else (
        echo Creation de l'environnement Python isole avec python -m venv...
        python -m venv .venv
    )
    if %ERRORLEVEL% NEQ 0 (
        echo [ERREUR] Impossible de creer studio-v2\.venv. Verifiez Python 3.11/3.12.
        exit /b 1
    )
)

echo Installation des dependances Python dans studio-v2\.venv...
where uv >nul 2>nul
if %ERRORLEVEL%==0 (
    uv pip install --python .venv\Scripts\python.exe -r requirements.txt
) else (
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)
if %ERRORLEVEL% NEQ 0 (
    echo [ERREUR] Echec d'installation des dependances Python.
    exit /b 1
)

if not exist "config.json" (
    copy /Y "config.example.json" "config.json" >nul
    echo Configuration creee : studio-v2\config.json
)

if "%1"=="--rebuild-ui" (
    pushd web
    call npm.cmd install
    call npm.cmd run build
    popd
)

if not exist "web\ui_bundle\index.html" (
    where npm.cmd >nul 2>nul
    if %ERRORLEVEL%==0 (
        echo Compilation de l'interface SvelteKit...
        pushd web
        call npm.cmd install
        call npm.cmd run build
        popd
    )
)

echo.
echo [OK] Studio IA V2 est pret !
echo   1. Renseignez votre cle DeepSeek dans studio-v2\config.json (ou dans l'interface)
echo   2. Lancez : start.bat
exit /b 0
