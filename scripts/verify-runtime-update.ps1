# Real temp-directory data/SDDL checks; backend, smoke and fault paths are doubles.
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'runtime-update.psm1') -Force

function Assert-Check([bool]$Condition,[string]$Message){if(-not $Condition){throw $Message}}
function Write-Partial([string]$Target){
 $stream=[IO.FileStream]::new($Target,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$stream.SetLength(0);$stream.WriteByte(42);$stream.Flush($true)}finally{$stream.Dispose()}
}
function New-Fixture([string]$Root,[string]$Mode,[bool]$Running) {
 $ctx=@{Root=$Root;Mode=$Mode;Running=$Running;Stops=0;Starts=0;Installs=0;Rollbacks=0;Smokes=0;Drift=$false}
 $ctx.Assert=${function:Assert-Check};$ctx.Partial=${function:Write-Partial}
 $ctx.WriteBytes=(Get-Command Write-RuntimeBytes).ScriptBlock;$ctx.SecurityReader=(Get-Command Get-RuntimeSecurity).ScriptBlock
 foreach($key in @('source','installed','state','backups')){$ctx[$key]=Join-Path $Root $key;[void][IO.Directory]::CreateDirectory($ctx[$key])}
 foreach($base in @($ctx.source,$ctx.installed)){[void][IO.Directory]::CreateDirectory((Join-Path $base '_internal'))}
 foreach($relative in @('RemoteDesktop.exe','_internal\core.dll','_internal\same.dat')){
  [IO.File]::WriteAllText((Join-Path $ctx.installed $relative),'OLD '+$relative)
  [IO.File]::WriteAllText((Join-Path $ctx.source $relative),$(if($relative -eq '_internal\same.dat'){'OLD '+$relative}else{'NEW '+$relative}))
 }
 [IO.File]::WriteAllText((Join-Path $ctx.installed 'keep-user.txt'),'USER DATA')
 [IO.File]::WriteAllText((Join-Path $ctx.state 'connection.dpapi'),'SYNTHETIC CONFIG ONLY')
 $ctx.Security=@{};$ctx.Hashes=@{}
 foreach($item in @((Get-Item -LiteralPath $ctx.installed)) + @(Get-ChildItem -LiteralPath $ctx.installed -Recurse -Force)){
  $ctx.Security[$item.FullName]=Get-RuntimeSecurity $item.FullName
  if(-not $item.PSIsContainer){$ctx.Hashes[$item.FullName]=(Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash}
 }
 $ctx.Hooks=@{
  IsRunning={return $ctx.Running}.GetNewClosure()
  Stop={
   $ctx.Stops++
   & $ctx.Assert (Test-Path -LiteralPath (Join-Path $ctx.state 'stop.request')) 'Stop must own a marker first'
   [IO.File]::WriteAllText((Join-Path $ctx.state 'engine-stop.request'),'')
   if($ctx.Mode -eq 'stop_timeout'){return $false}
   if($ctx.Mode -eq 'rollback_stop_failure' -and $ctx.Stops -gt 1){return $false}
   $ctx.Running=$false
   if($ctx.Mode -eq 'stop_throw'){throw 'Synthetic stop exception after shutdown'}
   if($ctx.Mode -eq 'permission_drift'){$ctx.Drift=$true}
   if($ctx.Mode -eq 'source_changed'){[IO.File]::WriteAllText((Join-Path $ctx.source 'RemoteDesktop.exe'),'CHANGED SOURCE')}
   return $true
  }.GetNewClosure()
  StartAndCheck={
   $ctx.Starts++
   foreach($name in @('stop.request','engine-stop.request','restart.request')){& $ctx.Assert (-not(Test-Path -LiteralPath (Join-Path $ctx.state $name))) 'Stale request would block restart'}
   $ctx.Running=$true
   if($ctx.Mode -in @('new_start_failure','rollback_stop_failure') -and $ctx.Starts -eq 1){return $false}
   if($ctx.Mode -eq 'old_start_failure'){return $false}
   if($ctx.Mode -notin @('success','stopped_success','distinct_permissions')){
    foreach($path in $ctx.Hashes.Keys){& $ctx.Assert ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -eq $ctx.Hashes[$path]) 'Old startup must follow verified restoration'}
   }
   return $true
  }.GetNewClosure()
  Smoke={param($report)
   $ctx.Smokes++
   if($ctx.Mode -in @('smoke_fail','old_start_failure')){return $false}
   if($ctx.Mode -eq 'smoke_throw'){throw 'Synthetic frozen smoke exception'}
   if($ctx.Mode -eq 'backup_tampered'){
    $part=Get-ChildItem -LiteralPath $ctx.backups -Filter '*.bin' -File -Recurse | Select-Object -First 1
    [IO.File]::WriteAllText($part.FullName,'TAMPERED BACKUP');return $false
   }
   if($ctx.Mode -eq 'foreign_stop'){
    [IO.File]::WriteAllText((Join-Path $ctx.state 'stop.request'),'EXTERNAL STOP');return $false
   }
   return $true
  }.GetNewClosure()
  Write={param($source,$target,$phase)
   & $ctx.Assert ($target.StartsWith($ctx.installed+'\',[StringComparison]::OrdinalIgnoreCase)) 'Write escaped test install root'
   if($phase -eq 'install'){
    $ctx.Installs++
    if($ctx.Mode -in @('copy_partial','rollback_write_failure','stopped_copy_failure') -and $ctx.Installs -eq 1){& $ctx.Partial $target;throw 'Synthetic partial copy failure'}
    if($ctx.Mode -eq 'copy_second' -and $ctx.Installs -eq 2){throw 'Synthetic second copy failure'}
    & $ctx.WriteBytes $source $target
    if($ctx.Mode -eq 'hash_mismatch'){& $ctx.Partial $target}
   }else{
    $ctx.Rollbacks++
    if($ctx.Mode -eq 'rollback_write_failure'){throw 'Synthetic rollback IO failure'}
    & $ctx.WriteBytes $source $target
   }
  }.GetNewClosure()
 }
 if($Mode -eq 'backup_bad'){$ctx.Hooks.Backup={param($source,$target) [IO.File]::WriteAllText($target,'WRONG BACKUP')}.GetNewClosure()}
 if($Mode -eq 'permission_drift'){$ctx.Hooks.GetSecurity={param($path)
  $value=& $ctx.SecurityReader $path
  if($ctx.Drift -and $path.EndsWith('core.dll')){return $value+'|SYNTHETIC PERMISSION CHANGE'}
  return $value
 }.GetNewClosure()}
 if($Mode -eq 'distinct_permissions'){$ctx.Hooks.GetSecurity={param($path) return (& $ctx.SecurityReader $path)+'|SYNTHETIC-PER-PATH:'+ $path}.GetNewClosure()}
 return $ctx
}

$tempBase=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$testRoot=Join-Path $tempBase ('diannow-runtime-tests-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($testRoot)
$passed=[Collections.Generic.List[string]]::new()
try{
 $cases=@('success','distinct_permissions','copy_partial','copy_second','file_locked','smoke_fail','smoke_throw','hash_mismatch','new_start_failure','stop_timeout','stop_throw','backup_bad','backup_tampered','rollback_write_failure','rollback_stop_failure','old_start_failure','permission_drift','foreign_stop','new_path','preexisting_stop','preexisting_engine_stop','preexisting_restart','credential_source','source_changed','unresolved_prior_update','stopped_success','stopped_copy_failure')
 foreach($mode in $cases){
  $running=$mode -notin @('stopped_success','stopped_copy_failure')
  $ctx=New-Fixture (Join-Path $testRoot $mode) $mode $running
  if($mode -eq 'new_path'){[IO.File]::WriteAllText((Join-Path $ctx.source 'new.dll'),'NEW FILE')}
  if($mode -eq 'credential_source'){[IO.File]::WriteAllText((Join-Path $ctx.source 'connection.dpapi'),'SYNTHETIC UNWANTED CONFIG')}
  if($mode -eq 'unresolved_prior_update'){
   $prior=Join-Path $ctx.backups 'runtime-prior';[void][IO.Directory]::CreateDirectory($prior)
   [IO.File]::WriteAllText((Join-Path $prior 'manifest.json'),(@{installRoot=$ctx.installed;wasRunning=$true} | ConvertTo-Json))
  }
  $foreignName=switch($mode){'preexisting_stop'{'stop.request'} 'preexisting_engine_stop'{'engine-stop.request'} 'preexisting_restart'{'restart.request'} default{$null}}
  if($foreignName){[IO.File]::WriteAllText((Join-Path $ctx.state $foreignName),'PREEXISTING OPERATOR REQUEST')}
  $held=$null
  try{
   if($mode -eq 'file_locked'){$held=[IO.FileStream]::new((Join-Path $ctx.installed 'RemoteDesktop.exe'),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)}
   $result=Invoke-RuntimeUpdate -SourceRoot $ctx.source -InstallRoot $ctx.installed -StateDirectory $ctx.state -BackupRoot $ctx.backups -Hooks $ctx.Hooks
  }finally{if($held){$held.Dispose()}}
  if($mode -in @('success','stopped_success','distinct_permissions')){
   Assert-Check $result.ok ('Success failed: '+$result.error+' / '+$result.rollbackError)
   foreach($relative in @('RemoteDesktop.exe','_internal\core.dll','_internal\same.dat')){Assert-Check ((Get-FileHash -LiteralPath (Join-Path $ctx.installed $relative)).Hash -eq (Get-FileHash -LiteralPath (Join-Path $ctx.source $relative)).Hash) 'Success bytes differ'}
   Assert-Check ($ctx.Starts -eq [int]$running -and $ctx.Stops -eq [int]$running) 'Initial running state was not preserved'
  }elseif($mode -in @('backup_tampered','rollback_write_failure','rollback_stop_failure','permission_drift')){
   Assert-Check (-not $result.ok -and $result.rollbackAttempted -and -not $result.rollbackVerified -and $null -ne $result.rollbackError) ('Unsafe rollback was not reported: '+$mode+' '+($result | ConvertTo-Json -Compress))
  }elseif($mode -eq 'old_start_failure'){
   Assert-Check (-not $result.ok -and $result.rollbackVerified -and $result.restoredBackendHealthy -eq $false -and $null -ne $result.rollbackError) 'Old backend health failure was hidden'
  }elseif($mode -eq 'foreign_stop'){
   Assert-Check (-not $result.ok -and $result.rollbackVerified -and $ctx.Starts -eq 0 -and $null -ne $result.rollbackError) 'Concurrent operator stop was ignored'
   Assert-Check ([IO.File]::ReadAllText((Join-Path $ctx.state 'stop.request')) -eq 'EXTERNAL STOP') 'Foreign stop marker was removed'
  }elseif($mode -eq 'new_path'){
   Assert-Check (-not $result.ok -and $result.phase -eq 'permission_review_required' -and $result.pendingPermissionPaths.Count -eq 1 -and $ctx.Stops -eq 0 -and $ctx.Starts -eq 0) 'New permissions were not deferred before stop'
   Assert-Check (-not(Test-Path -LiteralPath (Join-Path $ctx.installed 'new.dll'))) 'New path was created without review'
  }elseif($mode -in @('backup_bad','preexisting_stop','preexisting_engine_stop','preexisting_restart','credential_source','unresolved_prior_update')){
   Assert-Check (-not $result.ok -and -not $result.rollbackAttempted -and $ctx.Stops -eq 0 -and $ctx.Starts -eq 0) ('Preflight/backup failure touched backend: '+$mode)
   if($foreignName){Assert-Check ([IO.File]::ReadAllText((Join-Path $ctx.state $foreignName)) -eq 'PREEXISTING OPERATOR REQUEST') 'Preexisting request was changed'}
  }else{
   Assert-Check (-not $result.ok -and $result.rollbackVerified -and $null -eq $result.rollbackError) ('Rollback failed: '+$mode+' '+($result | ConvertTo-Json -Compress))
   $expectedStarts=if($mode -eq 'new_start_failure'){2}else{[int]$running}
   Assert-Check ($ctx.Starts -eq $expectedStarts) ('Original running state not restored: '+$mode+' starts='+$ctx.Starts)
   if($running){Assert-Check ($result.restoredBackendHealthy -eq $true) 'Old backend health was not verified'}
  }
  if($mode -notin @('success','stopped_success','distinct_permissions','backup_tampered','rollback_write_failure','rollback_stop_failure')){
   foreach($path in $ctx.Hashes.Keys){Assert-Check ((Get-FileHash -LiteralPath $path).Hash -eq $ctx.Hashes[$path]) ('Original data not restored: '+$mode)}
  }
  foreach($path in $ctx.Security.Keys){Assert-Check ((Get-RuntimeSecurity $path) -ceq $ctx.Security[$path]) ('Real SDDL changed: '+$mode)}
  Assert-Check ([IO.File]::ReadAllText((Join-Path $ctx.installed 'keep-user.txt')) -eq 'USER DATA') 'Unlisted user file changed'
  Assert-Check ([IO.File]::ReadAllText((Join-Path $ctx.state 'connection.dpapi')) -eq 'SYNTHETIC CONFIG ONLY') 'Configuration changed'
  if($mode -notin @('preexisting_stop','preexisting_engine_stop','preexisting_restart','foreign_stop','permission_drift','backup_tampered','rollback_write_failure','rollback_stop_failure')){
   foreach($name in @('stop.request','engine-stop.request')){Assert-Check (-not(Test-Path -LiteralPath (Join-Path $ctx.state $name))) ('Owned marker leaked: '+$mode)}
  }
  $passed.Add($mode)
 }
 foreach($file in @('runtime-update.psm1','update-desktop-runtime.ps1')){
  $tokens=$null;$parseErrors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $file),[ref]$tokens,[ref]$parseErrors)
  Assert-Check (-not $parseErrors.Count) 'Updater syntax failed'
  $writers=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -in @('Set-Acl','icacls','Register-ScheduledTask')},$true))
  Assert-Check ($writers.Count -eq 0) 'Security/task setter found'
  Assert-Check (-not($ast.Extent.Text -match '(?i)SetAccessControl|SetNamedSecurityInfo|SetSecurityInfo|\-Verb\s+RunAs')) 'Security mutation or UAC path found'
 }
 @{passed=$passed.Count;cases=$passed.ToArray();realTempIO=$true;realSddlUnchanged=$true;backendAndFrozenSmoke='doubles';productionOperations=$false;permissionWrites=$false} | ConvertTo-Json -Depth 4
}finally{
 $absolute=(Resolve-Path -LiteralPath $testRoot).Path
 if($absolute -ne [IO.Path]::GetFullPath($testRoot) -or -not $absolute.StartsWith($tempBase+'\diannow-runtime-tests-',[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe test cleanup path'}
 Remove-Item -LiteralPath $absolute -Recurse -Force
}
