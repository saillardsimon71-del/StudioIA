[CmdletBinding()]
param(
    [string]$ServiceRoot = 'C:\Users\saill\MetaAIServiceV3',
    [Parameter(Mandatory = $true)]
    [string]$BackupPath
)

$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $ServiceRoot -ErrorAction Stop).Path
$venvPython = Join-Path $resolvedRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Python du venv introuvable : $venvPython. Aucun fichier n'a été modifié."
}
$version = & $venvPython -c "import importlib.metadata; print(importlib.metadata.version('metaai-sdk'))"
if ($LASTEXITCODE -ne 0 -or $version.Trim() -ne '5.3.0') {
    throw "La restauration exige metaai-sdk==5.3.0. Aucun fichier n'a été modifié."
}
$packagePath = & $venvPython -c "import metaai_api, pathlib; print(pathlib.Path(metaai_api.__file__).resolve().parent)"
if ($LASTEXITCODE -ne 0) { throw 'Impossible de localiser le paquet metaai_api.' }
$packagePath = (Resolve-Path -LiteralPath $packagePath.Trim()).Path
$backupRoot = Join-Path $packagePath '.studioia-backup'
$resolvedBackup = (Resolve-Path -LiteralPath $BackupPath -ErrorAction Stop).Path
$backupRootPrefix = [IO.Path]::GetFullPath($backupRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $resolvedBackup.StartsWith($backupRootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "La sauvegarde doit se trouver sous $backupRoot. Aucun fichier n'a été modifié."
}

$modules = @('browser.py', 'playwright_backend.py', 'generation.py', 'main.py', 'api_server.py')
foreach ($module in $modules) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedBackup $module) -PathType Leaf)) {
        throw "Sauvegarde incomplète : $module absent. Aucun fichier n'a été modifié."
    }
}

$rollbackPath = Join-Path $backupRoot ('.before-restore-' + (Get-Date -Format 'yyyyMMdd-HHmmss-ffff'))
New-Item -ItemType Directory -Path $rollbackPath -Force | Out-Null
try {
    foreach ($module in $modules) {
        Copy-Item -LiteralPath (Join-Path $packagePath $module) -Destination $rollbackPath -ErrorAction Stop
    }
    foreach ($module in $modules) {
        Copy-Item -LiteralPath (Join-Path $resolvedBackup $module) -Destination (Join-Path $packagePath $module) -Force -ErrorAction Stop
    }
} catch {
    foreach ($module in $modules) {
        $savedCurrent = Join-Path $rollbackPath $module
        if (Test-Path -LiteralPath $savedCurrent -PathType Leaf) {
            Copy-Item -LiteralPath $savedCurrent -Destination (Join-Path $packagePath $module) -Force
        }
    }
    throw
}

Write-Host "Restauration terminée pour les cinq modules metaai_api. Copie de l'état remplacé : $rollbackPath"
Write-Host 'Aucun cookie, profil navigateur, projet Studio IA ou asset n’a été modifié. Redémarrez MetaAIServiceV3.'
