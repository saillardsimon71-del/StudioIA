[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [Parameter(Mandatory=$true)][string]$DataDir,
    [string]$EnvFile,
    [string]$Python = 'python',
    [string]$TaskName = 'StudioIA-V2-Social'
)
$ErrorActionPreference = 'Stop'
$resolvedData = (Resolve-Path -LiteralPath $DataDir).Path
$resolvedPython = (Get-Command $Python -ErrorAction Stop).Source
$runner = Join-Path $PSScriptRoot 'run-social.ps1'
if (-not $EnvFile) { $EnvFile = Join-Path $resolvedData 'social.env' }
$resolvedEnv = (Resolve-Path -LiteralPath $EnvFile).Path
foreach ($value in @($resolvedData, $resolvedPython, $runner, $resolvedEnv)) {
    if ($value.Contains('"')) { throw 'Un chemin contient un guillemet interdit.' }
}
$shell = Join-Path $PSHOME 'powershell.exe'
if (-not (Test-Path -LiteralPath $shell)) { $shell = (Get-Command powershell.exe).Source }
& $shell -NoProfile -NonInteractive -File $runner -DataDir $resolvedData -EnvFile $resolvedEnv -Python $resolvedPython -Doctor
if ($LASTEXITCODE -ne 0) { throw 'Pré-requis incomplets. Aucune tâche installée.' }
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -DataDir "{1}" -EnvFile "{2}" -Python "{3}"' -f $runner, $resolvedData, $resolvedEnv, $resolvedPython
$action = New-ScheduledTaskAction -Execute $shell -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 9) -MultipleInstances IgnoreNew -RunOnlyIfNetworkAvailable
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
if ($PSCmdlet.ShouldProcess($TaskName, 'Installer le passage API toutes les dix minutes')) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Studio IA V2 : file X, DM consentis et confirmation email.' -Force | Out-Null
    Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
}
