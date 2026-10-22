[CmdletBinding()]
param(
    [string]$ServiceUrl = 'http://127.0.0.1:8000',
    [string]$ApiKey = $env:META_AI_API_KEY,
    [string]$OutDir = (Join-Path $env:TEMP ('StudioIA-MetaAI-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))),
    [int]$TimeoutSec = 240
)

# Test A — trois générations consécutives, sans clic humain :
# cube rouge, sphère verte, pyramide bleue. Vérifie la provenance, l'unicité des
# conversations, l'unicité des fichiers et la couleur dominante de chaque image.
# Aucun asset de production n'est créé : ce script ne parle qu'au service Meta AI.

$ErrorActionPreference = 'Stop'
$ServiceUrl = $ServiceUrl.TrimEnd('/')
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Net.Http

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$health = Invoke-RestMethod -Uri "$ServiceUrl/healthz" -TimeoutSec 20
Write-Host ("Service: version {0}, navigateur prêt={1}, cookies={2}" -f $health.version, $health.browser_ready, $health.cookies_configured)
if (-not $health.cookies_configured) { throw 'Le service ne voit pas les cookies Meta AI.' }

$cases = @(
    @{ Name = 'cube-rouge'; Channel = 'red'; Prompt = 'A single bright red cube, centered on a plain white background, studio product photo, soft shadow, no text, no other objects.' },
    @{ Name = 'sphere-verte'; Channel = 'green'; Prompt = 'A single bright green sphere, centered on a plain white background, studio product photo, soft shadow, no text, no other objects.' },
    @{ Name = 'pyramide-bleue'; Channel = 'blue'; Prompt = 'A single bright blue pyramid, centered on a plain white background, studio product photo, soft shadow, no text, no other objects.' }
)

function Get-DominantChannel {
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
            Width = $bitmap.Width
            Height = $bitmap.Height
            Sampled = $sampled
            RedShare = if ($sampled) { [Math]::Round($red / $sampled, 4) } else { 0 }
            GreenShare = if ($sampled) { [Math]::Round($green / $sampled, 4) } else { 0 }
            BlueShare = if ($sampled) { [Math]::Round($blue / $sampled, 4) } else { 0 }
        }
    } finally {
        $bitmap.Dispose()
    }
}

$results = @()
$conversations = New-Object System.Collections.Generic.HashSet[string]
$requestIds = New-Object System.Collections.Generic.HashSet[string]
$hashes = New-Object System.Collections.Generic.HashSet[string]
$failures = New-Object System.Collections.Generic.List[string]

foreach ($case in $cases) {
    $requestId = [guid]::NewGuid().ToString()
    $headers = @{ 'Content-Type' = 'application/json' }
    if ($ApiKey) { $headers['Authorization'] = "Bearer $ApiKey" }
    $body = @{ prompt = $case.Prompt; request_id = $requestId; new_conversation = $true } | ConvertTo-Json -Compress

    Write-Host ("Génération {0} (requête {1})..." -f $case.Name, $requestId)
    $started = Get-Date
    try {
        $response = Invoke-RestMethod -Uri "$ServiceUrl/image" -Method Post -Headers $headers -Body $body -TimeoutSec $TimeoutSec
    } catch {
        $detail = $_.ErrorDetails.Message
        if (-not $detail) { $detail = $_.Exception.Message }
        $failures.Add("$($case.Name): échec HTTP — $detail")
        Write-Host ("  ÉCHEC : {0}" -f $detail) -ForegroundColor Red
        break
    }
    $elapsed = [Math]::Round(((Get-Date) - $started).TotalSeconds, 1)

    if (-not $response.success) { $failures.Add("$($case.Name): success=false"); continue }
    if ($response.request_id -cne $requestId) { $failures.Add("$($case.Name): request_id retourné différent") }
    if ($response.prompt -cne $case.Prompt) { $failures.Add("$($case.Name): prompt retourné différent de la demande") }
    if ($response.attribution -ne 'fresh_assistant_response') { $failures.Add("$($case.Name): attribution non confirmée") }
    if ($response.prompt_echo_verified -ne $true) { $failures.Add("$($case.Name): message envoyé non confirmé") }
    if (-not $response.conversation_id) { $failures.Add("$($case.Name): conversation absente") }
    if (-not $conversations.Add([string]$response.conversation_id)) { $failures.Add("$($case.Name): conversation réutilisée") }
    [void]$requestIds.Add([string]$response.request_id)
    if (-not $response.image_urls -or @($response.image_urls).Count -lt 1) { $failures.Add("$($case.Name): aucune image") ; continue }

    $imageUrl = [string](@($response.image_urls)[0])
    $extension = [IO.Path]::GetExtension(($imageUrl.Split('?')[0]))
    if (-not $extension -or $extension.Length -gt 5) { $extension = '.png' }
    $file = Join-Path $OutDir ($case.Name + $extension)
    Invoke-WebRequest -Uri $imageUrl -OutFile $file -TimeoutSec 90 | Out-Null
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
    if (-not $hashes.Add($hash)) { $failures.Add("$($case.Name): image identique à une autre génération") }

    $colors = Get-DominantChannel -Path $file
    $share = switch ($case.Channel) { 'red' { $colors.RedShare } 'green' { $colors.GreenShare } default { $colors.BlueShare } }
    if ($colors.Width -lt 256 -or $colors.Height -lt 256) { $failures.Add("$($case.Name): image trop petite ($($colors.Width)x$($colors.Height))") }
    if ($share -lt 0.03) { $failures.Add("$($case.Name): couleur dominante $($case.Channel) insuffisante ($([Math]::Round($share * 100, 1)) %)") }

    $results += [pscustomobject]@{
        Name = $case.Name
        ExpectedColor = $case.Channel
        ColorShare = $share
        Width = $colors.Width
        Height = $colors.Height
        ElapsedSeconds = $elapsed
        RequestId = $response.request_id
        ConversationId = $response.conversation_id
        Sha256 = $hash
        ImageFile = $file
        RedShare = $colors.RedShare
        GreenShare = $colors.GreenShare
        BlueShare = $colors.BlueShare
    }
    Write-Host ("  OK : {0}x{1}, {2} % de {3}, {4}s, conversation {5}" -f $colors.Width, $colors.Height, [Math]::Round($share * 100, 1), $case.Channel, $elapsed, $response.conversation_id)
}

if ($results.Count -ne $cases.Count) { $failures.Add("Seulement $($results.Count)/$($cases.Count) générations ont abouti") }

$report = [pscustomobject]@{
    generatedAt = (Get-Date).ToString('o')
    serviceUrl = $ServiceUrl
    serviceVersion = $health.version
    generations = $results
    uniqueConversations = $conversations.Count
    uniqueRequests = $requestIds.Count
    uniqueImages = $hashes.Count
    failures = $failures
    ok = ($failures.Count -eq 0)
}
$reportPath = Join-Path $OutDir 'verification.json'
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding UTF8

Write-Host ''
$results | Format-Table Name, ExpectedColor, ColorShare, Width, Height, ElapsedSeconds, ConversationId -AutoSize
Write-Host "Preuves et images : $OutDir"
if ($failures.Count -gt 0) {
    Write-Host 'VALIDATION ÉCHOUÉE :' -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
Write-Host 'VALIDATION RÉUSSIE : trois conversations distinctes, trois images distinctes, couleurs conformes.' -ForegroundColor Green
Write-Host 'Vérifiez visuellement les trois fichiers (aucun avatar, aucune ancienne génération).'
exit 0
