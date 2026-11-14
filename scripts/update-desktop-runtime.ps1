param([Parameter(Mandatory=$true)][string]$SourceHost,[string]$BackupRoot='D:\DIANNOW\Backups')
$ErrorActionPreference='Stop'
$principal=[Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
 throw 'Runtime update requires an administrator terminal. No UAC, backend operation or permission changes were started.'
}
Import-Module (Join-Path $PSScriptRoot 'runtime-update.psm1') -Force
$installDir=Join-Path $env:ProgramFiles 'DiannaoRemoteDesktop'
$installedExe=Join-Path $installDir 'RemoteDesktop.exe'
$stateDir=Join-Path $env:LOCALAPPDATA 'CodexDesktopViewer\desktop-service'
$taskName='CodexDesktopViewer'
if(-not(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)){throw 'Existing scheduled task is required; update does not register tasks.'}

function Get-OwnedRuntimeProcesses {
 return @(Get-Process -Name RemoteDesktop -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq $installedExe})
}
function Wait-RuntimeStopped {
 $deadline=(Get-Date).AddSeconds(40)
 do{
  if(-not (Get-OwnedRuntimeProcesses).Count -and (Get-ScheduledTask -TaskName $taskName).State -ne 'Running'){return $true}
  Start-Sleep -Milliseconds 250
 }while((Get-Date) -lt $deadline)
 return $false
}
function Test-RuntimeHealth([datetime]$Since) {
 try{
  $status=Get-Content -LiteralPath (Join-Path $stateDir 'host-status.json') -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
  $epoch=[datetime]'1970-01-01T00:00:00Z'
  if(-not $status.running -or [double]$status.updated -lt ($Since.ToUniversalTime()-$epoch.ToUniversalTime()).TotalSeconds){return $false}
  if((Get-Date).ToUniversalTime().Subtract($epoch.ToUniversalTime().AddSeconds([double]$status.updated)).TotalSeconds -gt 5){return $false}
  $hostProcess=Get-Process -Id $status.hostPid -ErrorAction Stop
  $engineProcess=Get-Process -Id $status.enginePid -ErrorAction Stop
  return $hostProcess.Path -eq $installedExe -and $engineProcess.Path -eq $installedExe -and (Get-ScheduledTask -TaskName $taskName).State -eq 'Running'
 }catch{return $false}
}
function Start-AndCheckRuntime {
 # Wait for a draining task; never force-kill a production process or assume
 # Start-ScheduledTask restarts a still-running task.
 $attempt=Get-Date;$deadline=$attempt.AddSeconds(45);$launched=$false
 do{
  if((Get-ScheduledTask -TaskName $taskName).State -ne 'Running' -and -not $launched){Start-ScheduledTask -TaskName $taskName;$launched=$true}
  if(Test-RuntimeHealth $attempt){return $true}
  Start-Sleep -Milliseconds 250
 }while((Get-Date) -lt $deadline)
 return $false
}
function Test-FrozenFileRuntime([string]$Report) {
 $probe=Start-Process -FilePath $installedExe -ArgumentList @('--file-self-test',('"'+$Report+'"')) -WindowStyle Hidden -PassThru
 try{
  if(-not $probe.WaitForExit(30000)){
   # Only the owned, device-free smoke process is eligible for termination.
   if(-not $probe.HasExited){$probe.Kill();[void]$probe.WaitForExit(5000)}
   return $false
  }
  if($probe.ExitCode -ne 0 -or -not(Test-Path -LiteralPath $Report)){return $false}
  $health=Get-Content -LiteralPath $Report -Raw -Encoding UTF8 | ConvertFrom-Json
  return $health.ok -and $health.frozen -and $health.fileProtocol -eq 1
 }finally{$probe.Dispose()}
}
$mutex=[Threading.Mutex]::new($false,'Global\DiannowRuntimeUpdate');$claimed=$false
try{
 try{$claimed=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$claimed=$true;throw 'Previous updater ended unexpectedly; inspect its retained journal before retrying.'}
 if(-not $claimed){throw 'Another runtime update is already active.'}
 $hooks=@{
  IsRunning={return (Get-OwnedRuntimeProcesses).Count -gt 0}
  Stop={return Wait-RuntimeStopped}
  StartAndCheck={return Start-AndCheckRuntime}
  Smoke={param($report) return Test-FrozenFileRuntime $report}
 }
 $result=Invoke-RuntimeUpdate -SourceRoot $SourceHost -InstallRoot $installDir -StateDirectory $stateDir -BackupRoot $BackupRoot -Hooks $hooks
 $result | ConvertTo-Json -Depth 6
 if(-not $result.ok){throw ('Runtime update failed at '+$result.phase+'. '+$result.error+' Rollback: '+$result.rollbackError+' Backup: '+$result.backup)}
}finally{if($claimed){$mutex.ReleaseMutex()};$mutex.Dispose()}
