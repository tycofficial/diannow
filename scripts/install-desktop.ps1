$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$sourceExe = Join-Path $project 'outputs\exe\RemoteDesktop.exe'
$appDir = Join-Path $env:LOCALAPPDATA 'CodexDesktopViewer'
$installedExe = Join-Path $appDir 'RemoteDesktop.exe'
$desktopExe = Join-Path ([Environment]::GetFolderPath('Desktop')) '远程桌面.exe'
$configuration = Join-Path $appDir 'desktop-service\connection.dpapi'
if (-not (Test-Path -LiteralPath $sourceExe) -or -not (Test-Path -LiteralPath $configuration)) { throw 'EXE or existing protected connection configuration is missing.' }
$backupDir = Join-Path $appDir ('backups\'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$oldTask = Get-ScheduledTask -TaskName CodexDesktopViewer -ErrorAction SilentlyContinue
if ($oldTask) { Export-ScheduledTask -TaskName CodexDesktopViewer | Set-Content -LiteralPath (Join-Path $backupDir 'previous-task.xml') -Encoding Unicode }
if (Test-Path -LiteralPath $desktopExe) { Copy-Item -LiteralPath $desktopExe -Destination (Join-Path $backupDir 'previous-desktop.exe') }
if (Test-Path -LiteralPath $installedExe) { Copy-Item -LiteralPath $installedExe -Destination (Join-Path $backupDir 'previous-host.exe') }
$stateDir=Join-Path $appDir 'desktop-service'
New-Item -ItemType File -Path (Join-Path $stateDir 'stop.request') -Force | Out-Null
$deadline=(Get-Date).AddSeconds(20)
do {
    $owned=@(Get-Process -Name RemoteDesktop -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq $installedExe})
    if(-not $owned.Count){break}
    Start-Sleep -Milliseconds 250
}while((Get-Date) -lt $deadline)
if ($oldTask) { Stop-ScheduledTask -TaskName CodexDesktopViewer }
# A frozen bootloader can leave its worker alive when the scheduler stops only
# the outer process. Scope any remaining cleanup to this installed executable.
Get-Process -Name RemoteDesktop -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq $installedExe} | Stop-Process -Force
Start-Sleep -Seconds 1
Copy-Item -LiteralPath $sourceExe -Destination $installedExe -Force
Copy-Item -LiteralPath $sourceExe -Destination $desktopExe -Force
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$escapedExe = [Security.SecurityElement]::Escape($installedExe)
$escapedDir = [Security.SecurityElement]::Escape($appDir)
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
 <RegistrationInfo><Description>远程桌面后台：登录后启动、唤醒恢复、异常退出后重启。</Description></RegistrationInfo>
 <Triggers>
  <LogonTrigger><Enabled>true</Enabled><UserId>$identity</UserId><Delay>PT5S</Delay></LogonTrigger>
  <SessionStateChangeTrigger><Enabled>true</Enabled><StateChange>SessionUnlock</StateChange><UserId>$identity</UserId></SessionStateChangeTrigger>
  <EventTrigger><Enabled>true</Enabled><Subscription>&lt;QueryList&gt;&lt;Query Id="0" Path="System"&gt;&lt;Select Path="System"&gt;*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;</Subscription><Delay>PT5S</Delay></EventTrigger>
 </Triggers>
 <Principals><Principal id="Author"><UserId>$identity</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
 <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Enabled>true</Enabled><RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure></Settings>
 <Actions Context="Author"><Exec><Command>$escapedExe</Command><Arguments>--background</Arguments><WorkingDirectory>$escapedDir</WorkingDirectory></Exec></Actions>
</Task>
"@
Register-ScheduledTask -TaskName CodexDesktopViewer -Xml $xml -Force | Out-Null
Start-ScheduledTask -TaskName CodexDesktopViewer
[pscustomobject]@{DesktopExecutable=$desktopExe;InstalledExecutable=$installedExe;Backup=$backupDir;Task='CodexDesktopViewer'} | ConvertTo-Json -Compress
