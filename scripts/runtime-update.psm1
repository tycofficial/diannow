# Data-only updates of existing runtime files. No security setters or elevation.
Set-StrictMode -Version Latest

function Get-RuntimeSecurity([string]$Path) {
 $sections=[Security.AccessControl.AccessControlSections]'Owner,Group,Access'
 return (Get-Acl -LiteralPath $Path -ErrorAction Stop).GetSecurityDescriptorSddlForm($sections)
}

function Assert-RuntimeAncestors([string]$Path) {
 $ancestor=[IO.DirectoryInfo]::new([IO.Path]::GetFullPath($Path))
 while($null -ne $ancestor){
  if($ancestor.Exists -and ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Reparse points are not supported for runtime updates.'}
  $ancestor=$ancestor.Parent
 }
}

function Get-RuntimeTree([string]$Root) {
 $absolute=[IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar)
 Assert-RuntimeAncestors $absolute
 if(-not(Test-Path -LiteralPath $absolute -PathType Container)){throw 'Runtime root must be an existing directory.'}
 $items=[Collections.Generic.List[object]]::new();$queue=[Collections.Generic.Queue[string]]::new()
 $queue.Enqueue($absolute);$items.Add([pscustomobject]@{Path=$absolute;Relative='';Directory=$true})
 while($queue.Count){
  foreach($item in Get-ChildItem -LiteralPath $queue.Dequeue() -Force -ErrorAction Stop){
   if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse points are not supported for runtime updates.'}
   if($item.PSObject.Properties['LinkType'] -and $item.LinkType -eq 'HardLink'){throw 'Hard-linked runtime files need manual review.'}
   $items.Add([pscustomobject]@{Path=$item.FullName;Relative=$item.FullName.Substring($absolute.Length+1);Directory=[bool]$item.PSIsContainer})
   if($item.PSIsContainer){$queue.Enqueue($item.FullName)}
  }
 }
 return ,$items.ToArray()
}

function Copy-NewRuntimeBytes([string]$Source,[string]$Target) {
 $readerStream=[IO.FileStream]::new($Source,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  $writerStream=[IO.FileStream]::new($Target,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
  try{$readerStream.CopyTo($writerStream,1048576);$writerStream.Flush($true)}finally{$writerStream.Dispose()}
 }finally{$readerStream.Dispose()}
}

function Write-RuntimeBytes([string]$Source,[string]$Target) {
 # Open (never Create/Replace) preserves the existing file object and its own
 # security descriptor/other streams. A failed partial write is rolled back.
 $readerStream=[IO.FileStream]::new($Source,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try{
  $writerStream=[IO.FileStream]::new($Target,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::None)
  try{$writerStream.SetLength(0);$readerStream.CopyTo($writerStream,1048576);$writerStream.Flush($true)}finally{$writerStream.Dispose()}
 }finally{$readerStream.Dispose()}
}

function Write-NewRuntimeJson([string]$Path,$Value) {
 $data=[Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 8))
 $stream=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$stream.Write($data,0,$data.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}

function Assert-RuntimeSecurity($Snapshot,[hashtable]$Hooks) {
 foreach($entry in $Snapshot){
  if(-not(Test-Path -LiteralPath $entry.Path) -or (& $Hooks.GetSecurity $entry.Path) -cne $entry.Security){throw ('Existing permissions changed; no permission repair is authorized: '+$entry.Relative)}
 }
}

function Assert-NoUnresolvedRuntimeUpdate([string]$BackupBase,[string]$InstallRoot) {
 if(-not(Test-Path -LiteralPath $BackupBase -PathType Container)){return}
 foreach($directory in Get-ChildItem -LiteralPath $BackupBase -Directory -Filter 'runtime-*' -Force){
  if($directory.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Backup path needs manual review.'}
  $manifest=Join-Path $directory.FullName 'manifest.json'
  if(-not(Test-Path -LiteralPath $manifest)){continue}
  try{
   $info=Get-Item -LiteralPath $manifest -Force
   if($info.Length -gt 16777216 -or ($info.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Invalid manifest'}
   $prior=Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
   if($prior.installRoot -ne $InstallRoot){continue}
   $complete=Join-Path $directory.FullName 'committed.json';$failed=Join-Path $directory.FullName 'failed.json'
   if(Test-Path -LiteralPath $complete){$commit=Get-Content -LiteralPath $complete -Raw -Encoding UTF8 | ConvertFrom-Json;if($commit.ok -and $commit.phase -eq 'committed'){continue}}
   if(Test-Path -LiteralPath $failed){$failure=Get-Content -LiteralPath $failed -Raw -Encoding UTF8 | ConvertFrom-Json;if($failure.rollbackVerified -and (-not $prior.wasRunning -or $failure.restoredBackendHealthy)){continue}}
  }catch{throw 'Prior update journal cannot be validated; manual review is required.'}
  throw 'A prior update has no verified commit/recovery; review its retained backup before retrying.'
 }
}

function Set-OwnedRuntimeStop([string]$StateDirectory,[string]$Token) {
 $path=Join-Path $StateDirectory 'stop.request'
 if(Test-Path -LiteralPath $path){if([IO.File]::ReadAllText($path) -cne $Token){throw 'A stop request from another operation is pending.'};return}
 $stream=[IO.FileStream]::new($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
 try{$data=[Text.Encoding]::UTF8.GetBytes($Token);$stream.Write($data,0,$data.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}

function Clear-OwnedRuntimeStop([string]$StateDirectory,[string]$Token,[bool]$StopRequested) {
 $stop=Join-Path $StateDirectory 'stop.request'
 if(Test-Path -LiteralPath $stop){
  if([IO.File]::ReadAllText($stop) -cne $Token){throw 'Stop request ownership changed; request retained for operator review.'}
  [IO.File]::Delete($stop)
 }
 $engine=Join-Path $StateDirectory 'engine-stop.request'
 if(Test-Path -LiteralPath $engine){
  if(-not $StopRequested -or [IO.File]::ReadAllText($engine).Length -ne 0){throw 'An unrelated engine stop request was retained.'}
  [IO.File]::Delete($engine)
 }
 if(Test-Path -LiteralPath (Join-Path $StateDirectory 'restart.request')){throw 'A concurrent restart request was retained for operator review.'}
}

function Invoke-RuntimeUpdate {
 [CmdletBinding()]
 param([Parameter(Mandatory=$true)][string]$SourceRoot,[Parameter(Mandatory=$true)][string]$InstallRoot,
       [Parameter(Mandatory=$true)][string]$StateDirectory,[Parameter(Mandatory=$true)][string]$BackupRoot,
       [Parameter(Mandatory=$true)][hashtable]$Hooks)
 foreach($key in @('IsRunning','Stop','StartAndCheck','Smoke')){if(-not $Hooks.ContainsKey($key)){throw ('Missing runtime operation: '+$key)}}
 if(-not $Hooks.ContainsKey('GetSecurity')){$Hooks.GetSecurity={param($path) Get-RuntimeSecurity $path}}
 if(-not $Hooks.ContainsKey('Write')){$Hooks.Write={param($source,$target,$phase) Write-RuntimeBytes $source $target}}
 if(-not $Hooks.ContainsKey('Backup')){$Hooks.Backup={param($source,$target) Copy-NewRuntimeBytes $source $target}}
 $result=[ordered]@{ok=$false;phase='preflight';backup=$null;error=$null;rollbackAttempted=$false;rollbackVerified=$false;restoredBackendHealthy=$null;rollbackError=$null;pendingPermissionPaths=@();permissionWrites=$false;permissionsVerified=$false}
 $changes=[Collections.Generic.List[object]]::new();$touched=[Collections.Generic.List[object]]::new()
 $security=[Collections.Generic.List[object]]::new();$original=[Collections.Generic.List[object]]::new()
 $stopRequested=$false;$wasRunning=$false;$token=[guid]::NewGuid().ToString('N')
 try{
  $source=[IO.Path]::GetFullPath($SourceRoot).TrimEnd('\');$installed=[IO.Path]::GetFullPath($InstallRoot).TrimEnd('\');$backupBase=[IO.Path]::GetFullPath($BackupRoot).TrimEnd('\')
  foreach($pair in @(@($source,$installed),@($installed,$source),@($backupBase,$installed),@($installed,$backupBase),@($backupBase,$source),@($source,$backupBase))){
   if($pair[0] -eq $pair[1] -or $pair[0].StartsWith($pair[1]+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Source, installation and backup trees must be separate.'}
  }
  $StateDirectory=[IO.Path]::GetFullPath($StateDirectory).TrimEnd('\')
  Assert-RuntimeAncestors $StateDirectory
  foreach($tree in @($source,$installed,$backupBase)){
   if($StateDirectory -eq $tree -or $StateDirectory.StartsWith($tree+'\',[StringComparison]::OrdinalIgnoreCase) -or $tree.StartsWith($StateDirectory+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Desktop state/configuration must be outside runtime and backup trees.'}
  }
  if(-not(Test-Path -LiteralPath $StateDirectory -PathType Container)){throw 'Existing desktop state directory is required.'}
  foreach($name in @('stop.request','engine-stop.request','restart.request')){if(Test-Path -LiteralPath (Join-Path $StateDirectory $name)){throw ('Pending operator request retained: '+$name)}}
  $sourceTree=Get-RuntimeTree $source;$installedTree=Get-RuntimeTree $installed
  if(-not(Test-Path -LiteralPath (Join-Path $source 'RemoteDesktop.exe') -PathType Leaf)){throw 'Built runtime executable is missing.'}
  $targets=@{};foreach($item in $installedTree){$targets[$item.Relative]=$item;$security.Add([pscustomobject]@{Path=$item.Path;Relative=$item.Relative;Security=(& $Hooks.GetSecurity $item.Path)})}
  $pending=[Collections.Generic.List[string]]::new()
  foreach($item in $sourceTree){
   if($item.Relative -eq ''){continue}
   if([IO.Path]::GetFileName($item.Path) -match '(?i)(\.dpapi$|^\.env($|\.)|^connection\.json$|-credentials\.json$)'){throw 'Runtime source contains configuration/credential data; update refused.'}
   if(-not $targets.ContainsKey($item.Relative)){$pending.Add($item.Relative);continue}
   $target=$targets[$item.Relative]
   if($item.Directory -ne $target.Directory){throw ('File/directory type change needs manual review: '+$item.Relative)}
   if($item.Directory){continue}
   $oldHash=(Get-FileHash -LiteralPath $target.Path -Algorithm SHA256).Hash;$newHash=(Get-FileHash -LiteralPath $item.Path -Algorithm SHA256).Hash
   $entry=[pscustomobject]@{Relative=$item.Relative;Source=$item.Path;Target=$target.Path;OldHash=$oldHash;NewHash=$newHash;Security=(& $Hooks.GetSecurity $target.Path);Backup=$null}
   $original.Add($entry)
   if($oldHash -ne $newHash){
    if((Get-Item -LiteralPath $target.Path -Force).Attributes -band [IO.FileAttributes]::ReadOnly){$pending.Add($item.Relative+' (read-only)');continue}
    $changes.Add($entry)
   }
  }
  if($pending.Count){$result.phase='permission_review_required';$result.pendingPermissionPaths=$pending.ToArray();$result.error='New paths or read-only changes need explicit review; backend and installation were not changed.';return [pscustomobject]$result}
  $wasRunning=[bool](& $Hooks.IsRunning)
  Assert-RuntimeAncestors $backupBase;Assert-NoUnresolvedRuntimeUpdate $backupBase $installed
  [void][IO.Directory]::CreateDirectory($backupBase)
  $backup=Join-Path $backupBase ('runtime-'+$token);[void][IO.Directory]::CreateDirectory($backup);$result.backup=$backup;$result.phase='backup'
  $number=0
  foreach($entry in $changes){
   $entry.Backup=Join-Path $backup ('old-'+($number++).ToString('D5')+'.bin');& $Hooks.Backup $entry.Target $entry.Backup
   if((Get-FileHash -LiteralPath $entry.Backup -Algorithm SHA256).Hash -ne $entry.OldHash){throw 'Runtime backup verification failed.'}
  }
  Assert-RuntimeSecurity $security $Hooks
  foreach($entry in $original){if((Get-FileHash -LiteralPath $entry.Target -Algorithm SHA256).Hash -ne $entry.OldHash){throw 'Installation changed during backup; backend was not stopped.'}}
  Write-NewRuntimeJson (Join-Path $backup 'manifest.json') @{protocol=1;installRoot=$installed;wasRunning=$wasRunning;files=$changes.ToArray();permissions=$security.ToArray();stopToken=$token}
  $result.phase='stop'
  if($wasRunning){$stopRequested=$true;Set-OwnedRuntimeStop $StateDirectory $token;if(-not(& $Hooks.Stop)){throw 'Existing backend did not stop gracefully.'}}
  if(& $Hooks.IsRunning){throw 'Backend is still running; runtime files were not modified.'}
  $result.phase='copy';Assert-RuntimeSecurity $security $Hooks
  foreach($entry in $changes){
   if((& $Hooks.GetSecurity $entry.Target) -cne $entry.Security){throw 'Target permissions changed before writing.'}
   if((Get-FileHash -LiteralPath $entry.Source -Algorithm SHA256).Hash -ne $entry.NewHash -or (Get-FileHash -LiteralPath $entry.Target -Algorithm SHA256).Hash -ne $entry.OldHash){throw 'Source or installation changed before writing.'}
   $touched.Add($entry)  # Undo intent precedes a possibly partial write.
   & $Hooks.Write $entry.Source $entry.Target 'install'
   if((Get-FileHash -LiteralPath $entry.Target -Algorithm SHA256).Hash -ne $entry.NewHash){throw 'Updated runtime hash mismatch.'}
  }
  Assert-RuntimeSecurity $security $Hooks;$result.phase='smoke'
  if(-not(& $Hooks.Smoke (Join-Path $backup 'file-runtime-self-test.json'))){throw 'Frozen runtime self-test failed.'}
  $result.phase='start';Clear-OwnedRuntimeStop $StateDirectory $token $stopRequested
  if($wasRunning -and -not(& $Hooks.StartAndCheck)){throw 'Updated backend did not become healthy.'}
  Assert-RuntimeSecurity $security $Hooks;$result.permissionsVerified=$true
  foreach($entry in $original){if((Get-FileHash -LiteralPath $entry.Target -Algorithm SHA256).Hash -ne $entry.NewHash){throw 'Final runtime verification failed.'}}
  $result.phase='commit';Write-NewRuntimeJson (Join-Path $backup 'committed.json') @{ok=$true;phase='committed';verifiedFiles=$original.Count;updatedFiles=$changes.Count;backendStarted=$wasRunning}
  $result.ok=$true;$result.phase='committed';$result.verifiedFiles=$original.Count;$result.updatedFiles=$changes.Count;$result.backendStarted=$wasRunning
 }catch{
  $result.ok=$false;$result.error=$_.Exception.Message
  if($stopRequested -or $touched.Count){
   $result.rollbackAttempted=$true
   try{
    if($touched.Count -and (& $Hooks.IsRunning)){$stopRequested=$true;Set-OwnedRuntimeStop $StateDirectory $token;if(-not(& $Hooks.Stop)){throw 'Cannot safely stop the updated backend for rollback.'}}
    foreach($entry in $touched){if((Get-FileHash -LiteralPath $entry.Backup -Algorithm SHA256).Hash -ne $entry.OldHash){throw 'Backup changed; rollback refused to copy unverified bytes.'}}
    for($index=$touched.Count-1;$index -ge 0;$index--){
     $entry=$touched[$index]
     # A locked file can reject Open before any write. Do not turn an intact
     # original into a rollback failure by attempting another needless write.
     if((Get-FileHash -LiteralPath $entry.Target -Algorithm SHA256).Hash -ne $entry.OldHash){& $Hooks.Write $entry.Backup $entry.Target 'rollback'}
    }
    foreach($entry in $original){if((Get-FileHash -LiteralPath $entry.Target -Algorithm SHA256).Hash -ne $entry.OldHash){throw 'Rollback content verification failed.'}}
    Assert-RuntimeSecurity $security $Hooks;$result.permissionsVerified=$true;$result.rollbackVerified=$true
    Clear-OwnedRuntimeStop $StateDirectory $token $stopRequested
    if($wasRunning){$result.restoredBackendHealthy=[bool](& $Hooks.StartAndCheck);if(-not $result.restoredBackendHealthy){throw 'Original bytes/permissions restored, but old backend health check failed.'}}
   }catch{$result.rollbackError=$_.Exception.Message}
  }
  if($result.backup){try{Write-NewRuntimeJson (Join-Path $result.backup 'failed.json') $result}catch{}}
 }
 return [pscustomobject]$result
}

Export-ModuleMember -Function Invoke-RuntimeUpdate,Get-RuntimeSecurity,Write-RuntimeBytes
