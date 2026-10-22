[CmdletBinding()]
param(
    [string]$StudioUrl = 'http://127.0.0.1:8270',
    [string]$OutDir = (Join-Path $env:TEMP ('StudioIA-MetaAI-Studio-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))),
    [int]$TimeoutSec = 900
)

# Test A (côté Studio IA) — un projet de test, trois plans sans image, un seul
# appel de lot : le Studio doit générer les trois visuels, les associer aux bons
# plans et n'enregistrer que des assets dont la provenance Meta AI est vérifiée.
# Aucun projet existant n'est modifié.

$ErrorActionPreference = 'Stop'
$StudioUrl = $StudioUrl.TrimEnd('/')
Add-Type -AssemblyName System.Drawing
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$failures = New-Object System.Collections.Generic.List[string]

$health = Invoke-RestMethod -Uri "$StudioUrl/api/health" -TimeoutSec 20
Write-Host ("Studio IA {0} — service Meta AI : {1}" -f $health.version, $health.metaai.url)

$cases = @(
    @{ Id = 'shot_1'; Channel = 'red'; State = 'A single bright red cube centered on a plain white background, studio product photo, no other objects.' },
    @{ Id = 'shot_2'; Channel = 'green'; State = 'A single bright green sphere centered on a plain white background, studio product photo, no other objects.' },
    @{ Id = 'shot_3'; Channel = 'blue'; State = 'A single bright blue pyramid centered on a plain white background, studio product photo, no other objects.' }
)
$shots = @()
foreach ($case in $cases) {
    $shots += @{
        id = $case.Id
        duration_s = 4
        action = 'The object stays still under studio light.'
        initial_state = $case.State
        final_state = $case.State
        image_prompt = $case.State + ' No text, no captions.'
        dialogue = @()
        continue_from_previous = $false
        camera_setup = 'static centered product framing'
        time_transition = 'continuous'
        decor_id = $null
    }
}

$project = Invoke-RestMethod -Uri "$StudioUrl/api/projects" -Method Post -ContentType 'application/json' `
    -Body (@{ title = 'TEST — Meta AI 3 générations'; idea = 'Test technique de provenance des images.'; target_duration_s = 12 } | ConvertTo-Json) -TimeoutSec 30
$projectPayload = $project | ConvertTo-Json -Depth 12 | ConvertFrom-Json
$projectPayload.shots = $shots
$saved = Invoke-RestMethod -Uri ("$StudioUrl/api/projects/" + [uri]::EscapeDataString($project.id)) -Method Put `
    -ContentType 'application/json' -Body ($projectPayload | ConvertTo-Json -Depth 12) -TimeoutSec 30

Write-Host ("Projet de test : {0} ({1} plans sans image)" -f $project.id, $saved.shots.Count)
$report = Invoke-RestMethod -Uri ("$StudioUrl/api/projects/" + [uri]::EscapeDataString($project.id) + "/generate-images") `
    -Method Post -ContentType 'application/json' -Body (@{ kinds = @('frame') } | ConvertTo-Json) -TimeoutSec $TimeoutSec

Write-Host ("Lot : {0} généré(s), {1} échec(s), arrêt={2}" -f $report.generated, $report.failed, $report.stopped)
foreach ($item in $report.results) {
    if ($item.status -ne 'generated') { $failures.Add("$($item.key) : $($item.status) $($item.error)") }
}
if ($report.generated -ne 3 -or $report.failed -ne 0) { $failures.Add('Le lot n''a pas produit exactement trois images sans échec.') }

$final = Invoke-RestMethod -Uri ("$StudioUrl/api/projects/" + [uri]::EscapeDataString($project.id)) -TimeoutSec 30
$assets = Invoke-RestMethod -Uri "$StudioUrl/api/assets" -TimeoutSec 30
$projectAssets = @($assets | Where-Object { $_.kind -eq 'frame' -and $_.source -eq 'metaai' })

function Get-ColorShares {
    param([string]$Path)
    $bitmap = New-Object System.Drawing.Bitmap($Path)
    try {
        $step = [Math]::Max(1, [int]($bitmap.Width / 96))
        $red = 0; $green = 0; $blue = 0; $sampled = 0
        for ($y = 0; $y -lt $bitmap.Height; $y += $step) {
            for ($x = 0; $x -lt $bitmap.Width; $x += $step) {
                $pixel = $bitmap.GetPixel($x, $y)
                $sampled++
                $max = [Math]::Max($pixel.R, [Math]::Max($pixel.G, $pixel.B))
                $min = [Math]::Min($pixel.R, [Math]::Min($pixel.G, $pixel.B))
                if (($max - $min) -lt 45) { continue }
                if ($pixel.R -eq $max -and $pixel.R -gt ($pixel.G + 25) -and $pixel.R -gt ($pixel.B + 25)) { $red++ }
                elseif ($pixel.G -eq $max -and $pixel.G -gt ($pixel.R + 25) -and $pixel.G -gt ($pixel.B + 25)) { $green++ }
                elseif ($pixel.B -eq $max -and $pixel.B -gt ($pixel.R + 25) -and $pixel.B -gt ($pixel.G + 25)) { $blue++ }
            }
        }
        return [pscustomobject]@{
            Width = $bitmap.Width; Height = $bitmap.Height
            RedShare = [Math]::Round($red / [Math]::Max(1, $sampled), 4)
            GreenShare = [Math]::Round($green / [Math]::Max(1, $sampled), 4)
            BlueShare = [Math]::Round($blue / [Math]::Max(1, $sampled), 4)
        }
    } finally { $bitmap.Dispose() }
}

$rows = @()
$hashes = New-Object System.Collections.Generic.HashSet[string]
$conversations = New-Object System.Collections.Generic.HashSet[string]
foreach ($case in $cases) {
    $shot = @($final.shots | Where-Object { $_.id -eq $case.Id }) | Select-Object -First 1
    if (-not $shot -or -not $shot.image_ref) { $failures.Add("$($case.Id) : aucune image associée"); continue }
    if (-not (Test-Path -LiteralPath $shot.image_ref)) { $failures.Add("$($case.Id) : fichier image absent"); continue }
    $asset = $projectAssets | Where-Object { $_.file_path -eq $shot.image_ref } | Select-Object -First 1
    if (-not $asset) { $failures.Add("$($case.Id) : l'image associée n'est pas un asset Meta AI"); continue }
    if (-not $asset.verified -or $asset.provenance.attribution -ne 'fresh_assistant_response') {
        $failures.Add("$($case.Id) : provenance non vérifiée")
    }
    if (-not $conversations.Add([string]$asset.provenance.conversation_id)) { $failures.Add("$($case.Id) : conversation réutilisée") }
    $hash = (Get-FileHash -LiteralPath $shot.image_ref -Algorithm SHA256).Hash
    if (-not $hashes.Add($hash)) { $failures.Add("$($case.Id) : image identique à une autre") }
    $shares = Get-ColorShares -Path $shot.image_ref
    $share = switch ($case.Channel) { 'red' { $shares.RedShare } 'green' { $shares.GreenShare } default { $shares.BlueShare } }
    if ($share -lt 0.03) { $failures.Add("$($case.Id) : couleur $($case.Channel) insuffisante ($([Math]::Round($share * 100, 1)) %)") }
    $copy = Join-Path $OutDir ($case.Id + [IO.Path]::GetExtension($shot.image_ref))
    Copy-Item -LiteralPath $shot.image_ref -Destination $copy
    $rows += [pscustomobject]@{
        Shot = $case.Id; ExpectedColor = $case.Channel; ColorShare = $share
        Width = $shares.Width; Height = $shares.Height
        ConversationId = $asset.provenance.conversation_id
        RequestId = $asset.provenance.request_id; ImageFile = $copy
    }
}

$reportObject = [pscustomobject]@{
    generatedAt = (Get-Date).ToString('o')
    studioUrl = $StudioUrl
    projectId = $project.id
    shots = $rows
    uniqueConversations = $conversations.Count
    uniqueImages = $hashes.Count
    failures = $failures
    ok = ($failures.Count -eq 0)
}
$reportObject | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $OutDir 'verification-studio.json') -Encoding UTF8
$rows | Format-Table Shot, ExpectedColor, ColorShare, Width, Height, ConversationId -AutoSize
Write-Host "Preuves : $OutDir"
Write-Host "Projet de test conservé : $($project.id) (supprimez-le depuis l'interface une fois vérifié)."

if ($failures.Count -gt 0) {
    Write-Host 'VALIDATION ÉCHOUÉE :' -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
Write-Host 'VALIDATION RÉUSSIE : trois images associées aux bons plans avec provenance vérifiée.' -ForegroundColor Green
exit 0
