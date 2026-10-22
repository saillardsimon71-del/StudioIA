[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$HostAddress = '127.0.0.1',
    [switch]$Headed,
    [switch]$Diagnose
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    throw 'Environnement absent. Lancez d''abord .\install.ps1.'
}

# .env local (jamais versionné) : une ligne CLÉ=valeur par variable.
$envFile = Join-Path $root '.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $name, $value = $line -split '=', 2
        Set-Item -Path ("Env:" + $name.Trim()) -Value $value.Trim().Trim('"').Trim("'")
    }
}

if (-not $env:META_AI_DATR -or -not $env:META_AI_ECTO_1_SESS) {
    Write-Host 'ERREUR : META_AI_DATR et META_AI_ECTO_1_SESS sont absents.' -ForegroundColor Red
    Write-Host 'Créez studio-v2\metaai-service\.env (ignoré par Git) :'
    Write-Host '  META_AI_DATR=...'
    Write-Host '  META_AI_ECTO_1_SESS=...'
    Write-Host '  META_AI_API_KEY=...   (facultatif mais recommandé)'
    exit 2
}

$listening = $false
if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
    $listening = [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
} else {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect($HostAddress, $Port)
        $listening = $client.Connected
        $client.Close()
    } catch { $listening = $false }
}
if ($listening) {
    Write-Host "ERREUR : le port $Port est déjà utilisé. Arrêtez l'ancien service (Ctrl+C) puis relancez." -ForegroundColor Red
    exit 3
}

$env:META_AI_PORT = $Port
$env:META_AI_HOST = $HostAddress
if ($Headed) { $env:META_AI_HEADED = '1' }

if ($Diagnose) {
    & $venvPython -m studioia_metaai diagnose --headed
    exit $LASTEXITCODE
}

Write-Host '============================================================'
Write-Host "  Service Meta AI — http://${HostAddress}:${Port}"
Write-Host '  Une image = une conversation neuve = une provenance vérifiée'
Write-Host '  Ctrl+C arrête le service (cookies et bibliothèque conservés).'
Write-Host '============================================================'
& $venvPython -m studioia_metaai serve
exit $LASTEXITCODE
