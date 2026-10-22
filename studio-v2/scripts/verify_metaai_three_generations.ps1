[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [Parameter(Mandatory = $true)]
    [string]$RedCubeShotId,
    [Parameter(Mandatory = $true)]
    [string]$GreenSphereShotId,
    [Parameter(Mandatory = $true)]
    [string]$BluePyramidShotId,
    [string]$StudioUrl = 'http://127.0.0.1:8270',
    [string]$OutputDirectory = (Join-Path $env:TEMP 'StudioIA-MetaAI-3-generations')
)

$ErrorActionPreference = 'Stop'
$StudioUrl = $StudioUrl.TrimEnd('/')
$cases = @(
    @{ ShotId = $RedCubeShotId; Name = 'red-cube'; Prompt = 'A single red cube, centered on a plain white background, studio product image, no text, no other objects.' },
    @{ ShotId = $GreenSphereShotId; Name = 'green-sphere'; Prompt = 'A single green sphere, centered on a plain white background, studio product image, no text, no other objects.' },
    @{ ShotId = $BluePyramidShotId; Name = 'blue-pyramid'; Prompt = 'A single blue pyramid, centered on a plain white background, studio product image, no text, no other objects.' }
)

$project = Invoke-RestMethod -Uri "$StudioUrl/api/projects/$([uri]::EscapeDataString($ProjectId))" -TimeoutSec 30
if ($project.id -ne $ProjectId) { throw 'Le serveur a renvoyé un autre projet.' }
$shotIds = @($cases | ForEach-Object { $_.ShotId })
$uniqueShotIds = @($shotIds | Select-Object -Unique)
if ($uniqueShotIds.Count -ne 3) { throw 'Les trois identifiants de plans doivent être différents.' }
foreach ($case in $cases) {
    $shot = @($project.shots | Where-Object { $_.id -eq $case.ShotId }) | Select-Object -First 1
    if (-not $shot) { throw "Plan introuvable dans le projet $ProjectId : $($case.ShotId)" }
    if ($shot.image_ref) { throw "Le plan $($case.ShotId) possède déjà une image. Utilisez un projet de test vide ; rien n'a été écrasé." }
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$conversationIds = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
$results = @()

# Les trois POST sont émis à la suite, sans pause ni intervention entre les générations.
foreach ($case in $cases) {
    $body = @{
        prompt = $case.Prompt
        kind = 'frame'
        name = "TEST — $($case.Name)"
        description = 'Asset temporaire créé par verify_metaai_three_generations.ps1.'
    } | ConvertTo-Json -Compress
    $asset = Invoke-RestMethod -Uri "$StudioUrl/api/assets/generate" -Method Post `
        -ContentType 'application/json' -Body $body -TimeoutSec 240

    if (-not $asset.metaai_generation) { throw "Le serveur Studio IA n'a pas renvoyé les preuves de corrélation pour $($case.Name)." }
    if ($asset.metaai_generation.prompt -cne $case.Prompt) { throw "Le prompt retourné ne correspond pas à $($case.Name)." }
    if (-not $asset.metaai_generation.request_id -or -not $asset.metaai_generation.conversation_id) {
        throw "Identifiant de requête ou de conversation absent pour $($case.Name)."
    }
    if (-not $conversationIds.Add([string]$asset.metaai_generation.conversation_id)) {
        throw "Conversation Meta AI réutilisée : $($asset.metaai_generation.conversation_id)."
    }
    if ($asset.metaai_generation.attribution -ne 'fresh_assistant_response') {
        throw "La source de l'image de $($case.Name) n'est pas confirmée comme réponse courante."
    }

    $assignBody = @{ asset_id = $asset.id } | ConvertTo-Json -Compress
    $project = Invoke-RestMethod -Uri "$StudioUrl/api/projects/$([uri]::EscapeDataString($ProjectId))/shots/$([uri]::EscapeDataString($case.ShotId))/assign-asset" `
        -Method Post -ContentType 'application/json' -Body $assignBody -TimeoutSec 30
    $assignedShot = @($project.shots | Where-Object { $_.id -eq $case.ShotId }) | Select-Object -First 1
    if ($project.id -ne $ProjectId -or -not $assignedShot -or $assignedShot.image_ref -ne $asset.file_path) {
        throw "L'image $($case.Name) n'est pas associée au plan $($case.ShotId) du projet $ProjectId."
    }
    foreach ($otherCase in $cases | Where-Object { $_.ShotId -ne $case.ShotId }) {
        $otherShot = @($project.shots | Where-Object { $_.id -eq $otherCase.ShotId }) | Select-Object -First 1
        if ($otherShot -and $otherShot.image_ref -eq $asset.file_path) {
            throw "L'image $($case.Name) a été associée au mauvais plan $($otherCase.ShotId)."
        }
    }

    $encodedPath = [uri]::EscapeDataString([string]$asset.file_path)
    $imageUri = "$StudioUrl/api/media?path=$encodedPath"
    $extension = [IO.Path]::GetExtension([string]$asset.file_path)
    $imagePath = Join-Path $OutputDirectory ($case.Name + $extension)
    Invoke-WebRequest -Uri $imageUri -OutFile $imagePath -TimeoutSec 60 | Out-Null
    if (-not (Test-Path -LiteralPath $imagePath -PathType Leaf) -or (Get-Item $imagePath).Length -lt 12) {
        throw "Le fichier téléchargé pour $($case.Name) est vide ou incomplet."
    }
    $results += [PSCustomObject]@{
        Shape = $case.Name
        Prompt = $asset.metaai_generation.prompt
        RequestId = $asset.metaai_generation.request_id
        ConversationId = $asset.metaai_generation.conversation_id
        ProjectId = $ProjectId
        ShotId = $case.ShotId
        AssetId = $asset.id
        AssetPath = $asset.file_path
        ImageFile = $imagePath
    }
}

$finalProject = Invoke-RestMethod -Uri "$StudioUrl/api/projects/$([uri]::EscapeDataString($ProjectId))" -TimeoutSec 30
foreach ($result in $results) {
    $shot = @($finalProject.shots | Where-Object { $_.id -eq $result.ShotId }) | Select-Object -First 1
    if (-not $shot -or $shot.image_ref -ne $result.AssetPath) {
        throw "Association finale absente ou incorrecte pour $($result.ShotId)."
    }
}
$results | Format-Table Shape, ProjectId, ShotId, RequestId, ConversationId, ImageFile -AutoSize
Write-Host "Images de test : $OutputDirectory"
Write-Host 'Les identifiants sont distincts et les associations projet/plan ont été vérifiées.'
Write-Host 'Ouvrez les trois fichiers : vérifiez visuellement cube rouge, sphère verte, pyramide bleue, et l’absence d’avatar ou d’ancien visuel.'
Write-Host 'Les assets créés sont explicitement nommés TEST —. Aucun asset préexistant n’a été remplacé ou supprimé.'
