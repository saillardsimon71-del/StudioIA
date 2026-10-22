[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$SkipBrowser
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host '============================================================'
Write-Host '  Service Meta AI — installation isolée'
Write-Host '============================================================'

if ($Force -and (Test-Path (Join-Path $root '.venv'))) {
    Write-Host 'Suppression de l''environnement local (aucun cookie ni projet touché)...'
    Remove-Item -LiteralPath (Join-Path $root '.venv') -Recurse -Force
}

if (-not (Test-Path (Join-Path $root '.venv\Scripts\python.exe'))) {
    $created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in @('-3.12', '-3.11')) {
            & py $version -m venv .venv
            if ((Test-Path (Join-Path $root '.venv\Scripts\python.exe'))) { $created = $true; break }
        }
    }
    if (-not $created) {
        & python -m venv .venv
        $created = Test-Path (Join-Path $root '.venv\Scripts\python.exe')
    }
    if (-not $created) {
        throw 'Impossible de créer .venv. Installez Python 3.11 ou 3.12 (python.org) puis relancez.'
    }
}

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installation des dépendances Python échouée.' }

if (-not $SkipBrowser) {
    Write-Host 'Téléchargement de Chromium pour Playwright (une seule fois)...'
    & $venvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Installation de Chromium échouée.' }
}

New-Item -ItemType Directory -Force -Path (Join-Path $root 'data') | Out-Null

Write-Host ''
Write-Host 'Vérification hors ligne de la logique de provenance...'
& $venvPython -m studioia_metaai selfcheck
if ($LASTEXITCODE -ne 0) { throw 'Les tests hors ligne du service Meta AI ont échoué.' }

Write-Host ''
Write-Host '[OK] Service installé.'
Write-Host '  1. Créez .env (ignoré par Git) avec META_AI_DATR, META_AI_ECTO_1_SESS et META_AI_API_KEY.'
Write-Host '  2. Lancez .\start.ps1 puis .\verify-three-generations.ps1.'
Write-Host '  3. Dans Studio IA : Paramètres → URL du service metaai-api = http://127.0.0.1:8000'
