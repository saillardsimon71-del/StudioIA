[CmdletBinding()]
param(
    [string]$ServiceRoot = 'C:\Users\saill\MetaAIServiceV3'
)

$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $ServiceRoot -ErrorAction Stop).Path
$venvPython = Join-Path $resolvedRoot '.venv\Scripts\python.exe'
$patcher = Join-Path $PSScriptRoot 'patch_metaai_sdk_5_3.py'

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Python du venv introuvable : $venvPython. Aucun fichier n'a été modifié."
}
if (-not (Test-Path -LiteralPath $patcher -PathType Leaf)) {
    throw "Patcher introuvable : $patcher. Placez ce script avec patch_metaai_sdk_5_3.py."
}

# V3 exige une installation V2 complète et crée une sauvegarde horodatée
# des cinq modules, mais ne modifie que playwright_backend.py (new_chat et
# la garde d'identifiant). Aucun cookie/.env, projet ou asset n'est ouvert.
& $venvPython $patcher --python $venvPython
if ($LASTEXITCODE -ne 0) {
    throw "Échec du patch Meta AI (code $LASTEXITCODE). Le patcher restaure les fichiers ciblés en cas d'échec."
}

Write-Host 'Patch Meta AI appliqué ou déjà présent. Sauvegardes : .venv\Lib\site-packages\metaai_api\.studioia-backup'
Write-Host 'Redémarrez MetaAIServiceV3. Ne copiez pas et ne partagez pas les cookies.'
