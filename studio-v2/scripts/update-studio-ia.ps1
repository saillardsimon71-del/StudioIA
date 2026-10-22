[CmdletBinding(DefaultParameterSetName = 'Update')]
param(
    [string]$RepoRoot = '',
    [string]$SourceRef = 'origin/fix/director-first-pass',
    [switch]$SkipDependencies,
    [switch]$RebuildUi,
    [Parameter(ParameterSetName = 'Rollback')]
    [switch]$Rollback,
    [Parameter(ParameterSetName = 'Rollback')]
    [string]$BackupPath,
    [Parameter(ParameterSetName = 'Rollback')]
    [switch]$RestoreDatabase,
    [switch]$Yes
)

# Mise à jour locale CONSERVATRICE et RÉVERSIBLE de Studio IA V2.
#
# - Studio IA (code, base v2.db, médias) et le service Meta AI sont deux
#   installations distinctes : ce script ne touche JAMAIS studio-v2\metaai-service.
# - Aucun commit local n'est réécrit, aucun force-push, aucun fichier de données
#   n'est supprimé.
# - Une sauvegarde de l'état courant (commit, configuration, base SQLite) est
#   créée avant toute modification ; -Rollback la restaure.

$ErrorActionPreference = 'Stop'
if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
Write-Host '============================================================'
Write-Host '  Studio IA V2 — mise à jour locale'
Write-Host '============================================================'

if (-not (Test-Path (Join-Path $RepoRoot '.git'))) {
    throw "Dépôt Git introuvable : $RepoRoot. Indiquez -RepoRoot <chemin du dépôt StudioIA>."
}
$StudioRoot = Join-Path $RepoRoot 'studio-v2'
if (-not (Test-Path (Join-Path $StudioRoot 'run.py'))) {
    throw "studio-v2 introuvable dans $RepoRoot."
}

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $output = & git -C $RepoRoot @Arguments
    if ($LASTEXITCODE -ne 0) { throw "git $($Arguments -join ' ') a échoué." }
    return $output
}

function Backup-State {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $dir = Join-Path $RepoRoot "backups\studio-v2-$stamp"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    ((Invoke-Git rev-parse HEAD) | Select-Object -First 1).Trim() | Set-Content -LiteralPath (Join-Path $dir 'commit.txt') -Encoding ASCII
    ((Invoke-Git branch '--show-current') | Select-Object -First 1).Trim() | Set-Content -LiteralPath (Join-Path $dir 'branch.txt') -Encoding ASCII
    Copy-Item -LiteralPath (Join-Path $StudioRoot 'config.json') -Destination $dir -ErrorAction SilentlyContinue
    $dataDir = Join-Path $StudioRoot 'data'
    if (Test-Path $dataDir) {
        $dbDir = Join-Path $dir 'data'
        New-Item -ItemType Directory -Force -Path $dbDir | Out-Null
        foreach ($name in @('v2.db', 'v2.db-wal', 'v2.db-shm')) {
            $file = Join-Path $dataDir $name
            if (Test-Path $file) { Copy-Item -LiteralPath $file -Destination $dbDir }
        }
    }
    Write-Host "Sauvegarde locale : $dir"
    return $dir
}

function Assert-ServerStopped {
    $listening = $false
    if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
        $listening = [bool](Get-NetTCPConnection -LocalPort 8270 -State Listen -ErrorAction SilentlyContinue)
    } else {
        try {
            $client = New-Object System.Net.Sockets.TcpClient
            $client.Connect('127.0.0.1', 8270)
            $listening = $client.Connected
            $client.Close()
        } catch { $listening = $false }
    }
    if ($listening) {
        throw "Studio IA écoute encore sur 8270. Arrêtez-le (Ctrl+C dans sa fenêtre) avant la mise à jour."
    }
}

function Install-Dependencies {
    $python = Join-Path $StudioRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) {
        Write-Host 'Environnement absent : exécution de setup.bat...'
        Push-Location $StudioRoot
        try { & cmd /c setup.bat } finally { Pop-Location }
        return
    }
    Write-Host 'Mise à jour des dépendances Python (studio-v2\.venv)...'
    & $python -m pip install --upgrade pip | Out-Null
    & $python -m pip install -r (Join-Path $StudioRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Installation des dépendances studio-v2 échouée.' }
}

function Build-Ui {
    param([switch]$Force)
    $web = Join-Path $StudioRoot 'web'
    if (-not (Test-Path (Join-Path $web 'package.json'))) { return }
    $bundle = Join-Path $web 'ui_bundle\index.html'
    if (-not $Force -and (Test-Path $bundle)) { return }
    Write-Host 'Compilation de l''interface SvelteKit...'
    Push-Location $web
    try {
        & npm.cmd ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { & npm.cmd install --no-audit --no-fund }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Compilation de l''interface échouée.' }
    } finally { Pop-Location }
}

Assert-ServerStopped

if ($Rollback) {
    if (-not $BackupPath) {
        $latest = Get-ChildItem -Path (Join-Path $RepoRoot 'backups') -Directory -Filter 'studio-v2-*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending | Select-Object -First 1
        if (-not $latest) { throw 'Aucune sauvegarde trouvée. Indiquez -BackupPath.' }
        $BackupPath = $latest.FullName
    }
    if (-not (Test-Path (Join-Path $BackupPath 'commit.txt'))) { throw "Sauvegarde invalide : $BackupPath" }
    $commit = (Get-Content -LiteralPath (Join-Path $BackupPath 'commit.txt') -Raw).Trim()
    Write-Host "Retour au commit $commit (sauvegarde $BackupPath)."
    if (-not $Yes) {
        $answer = Read-Host 'Confirmer le retour arrière ? (OUI/non)'
        if ($answer -ne 'OUI') { Write-Host 'Annulé. Rien n''a été modifié.'; exit 1 }
    }
    $dirtyRollback = ((Invoke-Git status '--porcelain') -join "`n").Trim()
    if ($dirtyRollback) {
        throw "Des modifications locales existent ; le retour arrière les écraserait.`n$dirtyRollback"
    }
    # Nouvelle sauvegarde avant d'écraser : le retour arrière reste lui-même réversible.
    [void](Backup-State)
    Invoke-Git reset '--hard' $commit | Out-Null
    if (-not $SkipDependencies) { Install-Dependencies }
    Build-Ui -Force:$RebuildUi
    if ($RestoreDatabase) {
        $dbSource = Join-Path $BackupPath 'data'
        $dbTarget = Join-Path $StudioRoot 'data'
        if (Test-Path $dbSource) {
            New-Item -ItemType Directory -Force -Path $dbTarget | Out-Null
            Copy-Item -Path (Join-Path $dbSource '*') -Destination $dbTarget -Force
            Write-Host 'Base SQLite restaurée depuis la sauvegarde.'
        } else {
            Write-Host 'Aucune base sauvegardée dans ce dossier : base actuelle conservée.'
        }
    }
    Write-Host '[OK] Retour arrière terminé. Relancez start.bat.'
    exit 0
}

# ── Mise à jour ────────────────────────────────────────────────────────────
$dirty = (Invoke-Git status '--porcelain') -join "`n"
if ($dirty.Trim()) {
    throw "Des modifications locales existent. Commitez-les ou archivez-les avant la mise à jour :`n$dirty"
}

$backup = Backup-State
Invoke-Git fetch '--all' '--prune' | Out-Null
$target = ((Invoke-Git rev-parse $SourceRef) | Select-Object -First 1).Trim()
$current = ((Invoke-Git rev-parse HEAD) | Select-Object -First 1).Trim()
Write-Host "Commit actuel : $current"
Write-Host "Commit cible  : $target ($SourceRef)"
if ($current -eq $target) {
    Write-Host 'Déjà à jour. Aucune modification appliquée.'
} else {
    # Fusion en avance rapide uniquement : aucun historique réécrit.
    Invoke-Git merge '--ff-only' $target | Out-Null
    Write-Host 'Code mis à jour (fast-forward).'
}
if (-not $SkipDependencies) { Install-Dependencies }
Build-Ui -Force:$RebuildUi

Write-Host ''
Write-Host 'Service Meta AI : installation séparée, NON modifiée par ce script.'
Write-Host '  Mise à jour : cd studio-v2\metaai-service ; .\install.ps1'
Write-Host "  Retour arrière de Studio IA : .\scripts\update-studio-ia.ps1 -Rollback -BackupPath `"$backup`""
Write-Host '[OK] Studio IA V2 est à jour. Relancez start.bat.'
exit 0
