$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$principal=[Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
    $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'"'
    Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -Verb RunAs -ArgumentList $arguments -WindowStyle Hidden -Wait
    exit
}
$sourceExe=Join-Path $project 'outputs\exe\RemoteDesktop.exe'
$sourceHost=Join-Path $project 'outputs\host\RemoteDesktop'
$appDir=Join-Path $env:LOCALAPPDATA 'CodexDesktopViewer'
$stateDir=Join-Path $appDir 'desktop-service'
$oldInstalledExe=Join-Path $appDir 'RemoteDesktop.exe'
$installDir=Join-Path $env:ProgramFiles 'DiannaoRemoteDesktop'
$installedExe=Join-Path $installDir 'RemoteDesktop.exe'
$configDir=Join-Path (Join-Path $env:ProgramData 'DiannaoRemoteDesktop') $env:USERNAME
$configuration=Join-Path $stateDir 'connection.dpapi'
$desktopExe=Join-Path ([Environment]::GetFolderPath('Desktop')) '远程桌面.exe'
$report=Join-Path $stateDir 'installation-result.json'
try{
 if(-not(Test-Path -LiteralPath $sourceExe) -or -not(Test-Path -LiteralPath (Join-Path $sourceHost 'RemoteDesktop.exe')) -or -not(Test-Path -LiteralPath $configuration)){throw 'Build or connection configuration missing.'}
 & (Join-Path $PSScriptRoot 'install-signed-drivers.ps1')
 $driverCode=0
 $backupDir=Join-Path $appDir ('backups\'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
 New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
 $oldTask=Get-ScheduledTask -TaskName CodexDesktopViewer -ErrorAction SilentlyContinue
 if($oldTask){Export-ScheduledTask -TaskName CodexDesktopViewer | Set-Content -LiteralPath (Join-Path $backupDir 'previous-task.xml') -Encoding Unicode}
 if(Test-Path -LiteralPath $desktopExe){Copy-Item -LiteralPath $desktopExe -Destination (Join-Path $backupDir 'previous-desktop.exe')}
 if(Test-Path -LiteralPath $oldInstalledExe){Copy-Item -LiteralPath $oldInstalledExe -Destination (Join-Path $backupDir 'previous-host.exe')}
 New-Item -ItemType File -Path (Join-Path $stateDir 'stop.request') -Force | Out-Null
 $deadline=(Get-Date).AddSeconds(20)
 do{
  $owned=@(Get-Process -Name RemoteDesktop -ErrorAction SilentlyContinue | Where-Object {$_.Path -in @($oldInstalledExe,$installedExe)})
  if(-not $owned.Count){break}
  Start-Sleep -Milliseconds 250
 }while((Get-Date) -lt $deadline)
 if($oldTask){Stop-ScheduledTask -TaskName CodexDesktopViewer}
 Get-Process -Name RemoteDesktop -ErrorAction SilentlyContinue | Where-Object {$_.Path -in @($oldInstalledExe,$installedExe)} | Stop-Process -Force
 New-Item -ItemType Directory -Path $installDir,$configDir -Force | Out-Null
 Copy-Item -Path (Join-Path $sourceHost '*') -Destination $installDir -Recurse -Force
 Copy-Item -LiteralPath $configuration -Destination (Join-Path $configDir 'connection.dpapi') -Force
 function Protect-Path([string]$target){
  $item=Get-Item -LiteralPath $target
  $acl=if($item.PSIsContainer){New-Object Security.AccessControl.DirectorySecurity}else{New-Object Security.AccessControl.FileSecurity}
  $acl.SetAccessRuleProtection($true,$false)
  $admin=New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
  $system=New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
  $users=New-Object Security.Principal.SecurityIdentifier('S-1-5-32-545')
  $acl.SetOwner($admin)
  foreach($entry in @(@($admin,'FullControl'),@($system,'FullControl'),@($users,'ReadAndExecute'))){
   $rule=New-Object Security.AccessControl.FileSystemAccessRule($entry[0],$entry[1],'Allow');$acl.AddAccessRule($rule)
  }
  Set-Acl -LiteralPath $target -AclObject $acl
 }
 foreach($root in @($installDir,(Split-Path $configDir -Parent))){
  Get-ChildItem -LiteralPath $root -Recurse -Force | ForEach-Object {if($_.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Unexpected reparse point in protected installation.'};Protect-Path $_.FullName}
  Protect-Path $root
 }
 Copy-Item -LiteralPath $sourceExe -Destination $desktopExe -Force
 $identity=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
 $escapedExe=[Security.SecurityElement]::Escape($installedExe)
 $escapedDir=[Security.SecurityElement]::Escape($installDir)
 $xml=@"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
 <RegistrationInfo><Description>独立远程桌面后台：管理员输入、登录自启、唤醒恢复、异常重启。</Description></RegistrationInfo>
 <Triggers>
  <LogonTrigger><Enabled>true</Enabled><UserId>$identity</UserId><Delay>PT5S</Delay></LogonTrigger>
  <SessionStateChangeTrigger><Enabled>true</Enabled><StateChange>SessionUnlock</StateChange><UserId>$identity</UserId></SessionStateChangeTrigger>
  <EventTrigger><Enabled>true</Enabled><Subscription>&lt;QueryList&gt;&lt;Query Id="0" Path="System"&gt;&lt;Select Path="System"&gt;*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;</Subscription><Delay>PT5S</Delay></EventTrigger>
 </Triggers>
 <Principals><Principal id="Author"><UserId>$identity</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
 <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Priority>4</Priority><Enabled>true</Enabled><RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure></Settings>
 <Actions Context="Author"><Exec><Command>$escapedExe</Command><Arguments>--background</Arguments><WorkingDirectory>$escapedDir</WorkingDirectory></Exec></Actions>
</Task>
"@
 Register-ScheduledTask -TaskName CodexDesktopViewer -Xml $xml -Force | Out-Null
 Start-ScheduledTask -TaskName CodexDesktopViewer
 @{ok=$true;desktop=$desktopExe;installed=$installedExe;backup=$backupDir;driverCode=$driverCode;runLevel='HighestAvailable';updated=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $report -Encoding utf8
}catch{
 @{ok=$false;error=$_.Exception.Message;updated=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $report -Encoding utf8
 throw
}
