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

# Le patcher vérifie metaai-sdk==5.3.0, crée une sauvegarde horodatée,
# puis ne modifie que cinq modules Python de metaai_api. Aucun cookie/.env,
# profil navigateur, projet Studio IA ou fichier image n'est ouvert.
& $venvPython $patcher --python $venvPython
if ($LASTEXITCODE -ne 0) {
    throw "Échec du patch Meta AI (code $LASTEXITCODE). Le patcher restaure les fichiers ciblés en cas d'échec."
}

Write-Host 'Patch Meta AI appliqué ou déjà présent. Sauvegardes : .venv\Lib\site-packages\metaai_api\.studioia-backup'
Write-Host 'Redémarrez MetaAIServiceV3. Ne copiez pas et ne partagez pas les cookies.'
