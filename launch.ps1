param(
    [ValidateRange(1024, 65535)][int]$Port = 8247,
    [switch]$Rebuild,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
foreach ($binary in @('ffmpeg', 'ffprobe')) {
    if (-not (Get-Command $binary -ErrorAction SilentlyContinue)) {
        throw "$binary est requis. Installez FFmpeg et ajoutez son dossier bin au PATH."
    }
}
if (-not (Test-Path -LiteralPath $pythonPath)) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv --python 3.12 .venv
    } else {
        if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
            throw 'Installez Python 3.12 ou uv, puis relancez launch.cmd.'
        }
        python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Création de l environnement Python impossible.' }
}
$dependencyHash = & $pythonPath -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('requirements-lock.txt').read_bytes()+pathlib.Path('calliope-backend/pyproject.toml').read_bytes()).hexdigest())"
$marker = Join-Path $PSScriptRoot '.venv\studio-dependencies.txt'
if (-not (Test-Path -LiteralPath $marker) -or
    (Get-Content -LiteralPath $marker -Raw).Trim() -ne $dependencyHash) {
    Write-Host 'Installation des dépendances du studio...'
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv pip install --python $pythonPath -r requirements-lock.txt
        if ($LASTEXITCODE -ne 0) { throw 'Installation des dépendances impossible.' }
        uv pip install --python $pythonPath --no-deps -e .\calliope-backend
    } else {
        & $pythonPath -m pip install -r requirements-lock.txt
        if ($LASTEXITCODE -ne 0) { throw 'Installation des dépendances impossible.' }
        & $pythonPath -m pip install --no-deps -e .\calliope-backend
    }
    if ($LASTEXITCODE -ne 0) { throw 'Installation du studio impossible.' }
    Set-Content -LiteralPath $marker -Value $dependencyHash -Encoding ascii
}
if ($Rebuild -or -not (Test-Path -LiteralPath 'calliope-web\build\index.html')) {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw 'Installez Node.js avec npm, puis relancez launch.cmd.'
    }
    Push-Location -LiteralPath 'calliope-web'
    try {
        npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Installation de l interface impossible.' }
        npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Compilation de l interface impossible.' }
    } finally { Pop-Location }
}
$studioUrl = "http://127.0.0.1:$Port/studio"
try {
    $existing = Invoke-RestMethod "http://127.0.0.1:$Port/api/studio/config" -TimeoutSec 2
} catch { $existing = $null }
if ($existing -and $existing.modal_app) {
    if (-not $existing.pipeline_revision -or $existing.restart_required) {
        throw 'Le studio ouvert utilise une ancienne version. Attendez la fin des productions, fermez le studio puis relancez launch.cmd.'
    }
    Write-Host "Studio déjà ouvert: $studioUrl"
    if (-not $NoBrowser) { Start-Process $studioUrl }
    exit 0
}
Write-Host "Studio: $studioUrl"
Write-Host 'Gardez cette fenêtre ouverte. Ctrl+C arrête le studio; les projets restent enregistrés.'
if (-not $NoBrowser) { Start-Process $studioUrl }
& $pythonPath run_studio.py --port $Port
exit $LASTEXITCODE
