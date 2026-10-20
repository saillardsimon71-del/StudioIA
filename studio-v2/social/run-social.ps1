param(
    [Parameter(Mandatory=$true)][string]$DataDir,
    [string]$EnvFile,
    [string]$Python = 'python',
    [switch]$Doctor,
    [switch]$DryRun,
    [switch]$ConfigureKit
)
$ErrorActionPreference = 'Stop'
$resolvedData = (Resolve-Path -LiteralPath $DataDir).Path
if (-not (Test-Path -LiteralPath (Join-Path $resolvedData 'v2.db') -PathType Leaf)) {
    throw 'Le dossier doit contenir la base V2 existante.'
}
if (-not $EnvFile) { $EnvFile = Join-Path $resolvedData 'social.env' }
$allowed = @('X_API_KEY', 'X_API_SECRET', 'X_ACCESS_TOKEN', 'X_ACCESS_TOKEN_SECRET',
    'KIT_API_KEY', 'KIT_FORM_ID', 'KIT_SEQUENCE_ID', 'KIT_SENDER_EMAIL',
    'STUDIO_LANDING_URL', 'STUDIO_CHECKOUT_URL', 'STUDIO_PRIVACY_URL')
if (Test-Path -LiteralPath $EnvFile -PathType Leaf) {
    foreach ($line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
        if ($line -match '^\s*(#|$)') { continue }
        $parts = $line.Split(@('='), 2)
        $key = $parts[0].Trim()
        if ($parts.Count -ne 2 -or $key -notin $allowed) { throw 'Entrée social.env invalide.' }
        [Environment]::SetEnvironmentVariable($key, $parts[1].Trim(), 'Process')
    }
}
$env:STUDIO_V2_DATA_DIR = $resolvedData
if ($ConfigureKit) {
    & $Python (Join-Path $PSScriptRoot 'funnel.py') --data-dir $resolvedData --configure-kit
} elseif ($Doctor) {
    & $Python (Join-Path $PSScriptRoot 'x_scheduler.py') --data-dir $resolvedData --doctor
} elseif ($DryRun) {
    & $Python (Join-Path $PSScriptRoot 'x_scheduler.py') --data-dir $resolvedData
} else {
    & $Python (Join-Path $PSScriptRoot 'x_scheduler.py') --data-dir $resolvedData --publish --backend api
}
exit $LASTEXITCODE
