param([Parameter(Mandatory=$true)][string]$SourceHost,[string]$BackupRoot='D:\DIANNOW\Backups')
$ErrorActionPreference='Stop'
$principal=[Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
 throw 'Runtime update requires an administrator terminal. No UAC window or configuration changes were started.'
}
$installDir=Join-Path $env:ProgramFiles 'DiannaoRemoteDesktop'
$installedExe=Join-Path $installDir 'RemoteDesktop.exe'
$stateDir=Join-Path $env:LOCALAPPDATA 'CodexDesktopViewer\desktop-service'
$source=(Resolve-Path -LiteralPath $SourceHost).Path
if(-not(Test-Path -LiteralPath $installedExe) -or -not(Test-Path -LiteralPath (Join-Path $source 'RemoteDesktop.exe'))){throw 'Existing installation or built runtime is missing.'}
if(-not(Get-ScheduledTask -TaskName CodexDesktopViewer -ErrorAction SilentlyContinue)){throw 'Existing scheduled task is required; runtime update does not register tasks.'}
foreach($root in @($source,$installDir)){
 foreach($item in @((Get-Item -LiteralPath $root)) + @(Get-ChildItem -LiteralPath $root -Recurse -Force)){
  if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Unexpected reparse point in runtime path.'}
 }
}
$directoryAcl=Get-Acl -LiteralPath $installDir
$fileAcl=Get-Acl -LiteralPath $installedExe
$backup=Join-Path $BackupRoot ('runtime-'+(Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $backup -ErrorAction Stop | Out-Null
Copy-Item -LiteralPath $installDir -Destination (Join-Path $backup 'previous-host') -Recurse
# Preserve DPAPI files, scheduled-task definition, drivers, firewall and proxies.
New-Item -ItemType File -Path (Join-Path $stateDir 'stop.request') -Force | Out-Null
$deadline=(Get-Date).AddSeconds(25)
do{
 $owned=@(Get-Process -Name RemoteDesktop -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq $installedExe})
 if(-not $owned.Count){break}
 Start-Sleep -Milliseconds 250
}while((Get-Date) -lt $deadline)
if($owned.Count){throw 'Existing backend did not stop gracefully. Files were not replaced; backup is retained.'}
try{
 Copy-Item -Path (Join-Path $source '*') -Destination $installDir -Recurse -Force
 # New runtime files receive exactly the installation's existing access policy.
 foreach($item in Get-ChildItem -LiteralPath $installDir -Recurse -Force){Set-Acl -LiteralPath $item.FullName -AclObject $(if($item.PSIsContainer){$directoryAcl}else{$fileAcl})}
 $expected=(Get-FileHash -LiteralPath (Join-Path $source 'RemoteDesktop.exe') -Algorithm SHA256).Hash
 $actual=(Get-FileHash -LiteralPath $installedExe -Algorithm SHA256).Hash
 if($actual -ne $expected){throw 'Installed EXE hash does not match reviewed build.'}
 $checked=0
 foreach($file in Get-ChildItem -LiteralPath $source -File -Recurse){
  $relative=$file.FullName.Substring($source.Length).TrimStart([IO.Path]::DirectorySeparatorChar)
  $target=Join-Path $installDir $relative
  if((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash){throw ('Runtime file hash mismatch: '+$relative)}
  $checked++
 }
 $selfTest=Join-Path $backup 'file-runtime-self-test.json'
 $probe=Start-Process -FilePath $installedExe -ArgumentList @('--file-self-test',('"'+$selfTest+'"')) -WindowStyle Hidden -Wait -PassThru
 if($probe.ExitCode -ne 0 -or -not(Test-Path -LiteralPath $selfTest)){throw 'Frozen file runtime self-test failed.'}
 $health=Get-Content -LiteralPath $selfTest -Raw | ConvertFrom-Json
 if(-not $health.ok -or -not $health.frozen -or $health.fileProtocol -ne 1){throw 'Installed file runtime version is incompatible.'}
 Start-ScheduledTask -TaskName CodexDesktopViewer
 @{ok=$true;installed=$installedExe;sha256=$actual;verifiedFiles=$checked;backup=$backup;runtimeOnly=$true;networkChanged=$false;credentialsChanged=$false} | ConvertTo-Json
}catch{
 Write-Error ('Runtime update failed; retained backup: '+$backup+'; '+$_.Exception.Message)
 throw
}
