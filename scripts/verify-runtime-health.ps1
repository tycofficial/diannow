# Reuse the actual wrapper functions with scoped task/process/clock doubles.
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'update-desktop-runtime.ps1'),[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw 'Wrapper syntax invalid'}
$definitions=@{}
foreach($name in @('Test-RuntimeHealth','Start-AndCheckRuntime','Test-FrozenFileRuntime')){
 $node=$ast.Find({param($item) $item -is [Management.Automation.Language.FunctionDefinitionAst] -and $item.Name -eq $name},$true)
 if(-not $node){throw ('Missing production function '+$name)}
 $definitions[$name]=$node.Extent.Text
}
$base=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$root=Join-Path $base ('diannow-health-tests-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($root)
try{
 & {
  param($definitions,$root)
  $stateDir=$root;$installedExe=Join-Path $root 'SYNTHETIC.exe';$taskName='SYNTHETIC_NO_REAL_TASK'
  $mock=@{task='Running';hostPath=$installedExe;enginePath=$installedExe;missing=$false}
  function Get-Process {param([int]$Id,$ErrorAction)
   if($Id -notin @(100,200) -or $mock.missing){throw 'Synthetic missing PID'}
   return [pscustomobject]@{Path=$(if($Id -eq 100){$mock.hostPath}else{$mock.enginePath})}
  }
  function Get-ScheduledTask {param($TaskName)
   if($TaskName -ne 'SYNTHETIC_NO_REAL_TASK'){throw 'Unexpected task name'}
   return [pscustomobject]@{State=$mock.task}
  }
  . ([scriptblock]::Create($definitions['Test-RuntimeHealth']))
  $since=(Get-Date).AddSeconds(-1);$epoch=[datetime]'1970-01-01T00:00:00Z'
  $fresh=@{running=$true;updated=((Get-Date).ToUniversalTime()-$epoch.ToUniversalTime()).TotalSeconds;hostPid=100;enginePid=200}
  $path=Join-Path $root 'host-status.json';[IO.File]::WriteAllText($path,($fresh | ConvertTo-Json))
  if(-not(Test-RuntimeHealth $since)){throw 'Fresh, owned host and engine should pass'}
  $mock.enginePath='WRONG';if(Test-RuntimeHealth $since){throw 'Wrong engine path accepted'};$mock.enginePath=$installedExe
  $mock.hostPath='WRONG';if(Test-RuntimeHealth $since){throw 'Wrong host path accepted'};$mock.hostPath=$installedExe
  $mock.missing=$true;if(Test-RuntimeHealth $since){throw 'Dead PID accepted'};$mock.missing=$false
  $mock.task='Ready';if(Test-RuntimeHealth $since){throw 'Stopped task accepted'};$mock.task='Running'
  $fresh.updated-=60;[IO.File]::WriteAllText($path,($fresh | ConvertTo-Json))
  if(Test-RuntimeHealth $since){throw 'Old status accepted as restart success'}
  if(Test-RuntimeHealth ($since.AddSeconds(-120))){throw 'Stale heartbeat accepted'}
  [IO.File]::WriteAllText($path,'invalid JSON');if(Test-RuntimeHealth $since){throw 'Invalid status accepted'}
  [IO.File]::Delete($path);if(Test-RuntimeHealth $since){throw 'Missing status accepted'}
 } $definitions $root
 & {
  param($definitions)
  $taskName='SYNTHETIC_NO_REAL_TASK';$clock=@{now=[datetime]'2026-01-01T00:00:00Z';ticks=0;starts=0;ready=$false;healthy=$false}
  function Get-Date {return $clock.now}
  function Start-Sleep {param($Milliseconds) $clock.now=$clock.now.AddMilliseconds($Milliseconds);$clock.ticks++;if($clock.ticks -eq 2){$clock.ready=$true}}
  function Get-ScheduledTask {param($TaskName) if($TaskName -ne 'SYNTHETIC_NO_REAL_TASK'){throw 'Unexpected task'};return [pscustomobject]@{State=$(if($clock.ready){'Ready'}else{'Running'})}}
  function Start-ScheduledTask {param($TaskName) if($TaskName -ne 'SYNTHETIC_NO_REAL_TASK'){throw 'Unexpected task'};$clock.starts++;$clock.ready=$false;$clock.healthy=$true}
  function Test-RuntimeHealth {param($Since) return $clock.healthy}
  . ([scriptblock]::Create($definitions['Start-AndCheckRuntime']))
  if(-not(Start-AndCheckRuntime) -or $clock.starts -ne 1 -or $clock.ticks -lt 2){throw 'Did not wait for draining task before one start'}
  $clock.ticks=3;$clock.starts=0;$clock.ready=$false;$clock.healthy=$false
  if(Start-AndCheckRuntime){throw 'Unhealthy running task accepted'}
  if($clock.starts -ne 0 -or $clock.ticks -lt 180 -or $clock.ticks -gt 185){throw 'Startup deadline was not bounded'}
 } $definitions
 & {
  param($definitions,$root)
  $installedExe=Join-Path $root 'SYNTHETIC.exe';$report=Join-Path $root 'smoke.json'
  $mock=@{wait=$true;exit=0;kills=0;disposes=0;started=0}
  function Start-Process {param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru)
   if($FilePath -ne $installedExe -or $WindowStyle -ne 'Hidden' -or $ArgumentList[0] -ne '--file-self-test'){throw 'Unexpected process request'}
   $mock.started++
   $process=[pscustomobject]@{HasExited=$false;ExitCode=$mock.exit;Context=$mock}
   $process | Add-Member ScriptMethod WaitForExit {param($budget) if($budget -notin @(30000,5000)){throw 'Unexpected wait budget'};return $this.Context.wait}
   $process | Add-Member ScriptMethod Kill {$this.Context.kills++;$this.HasExited=$true}
   $process | Add-Member ScriptMethod Dispose {$this.Context.disposes++}
   return $process
  }
  . ([scriptblock]::Create($definitions['Test-FrozenFileRuntime']))
  [IO.File]::WriteAllText($report,'{"ok":true,"frozen":true,"fileProtocol":1}')
  if(-not(Test-FrozenFileRuntime $report)){throw 'Valid frozen report rejected'}
  [IO.File]::WriteAllText($report,'{"ok":true,"frozen":true,"fileProtocol":2}')
  if(Test-FrozenFileRuntime $report){throw 'Incompatible protocol accepted'}
  $mock.exit=1;if(Test-FrozenFileRuntime $report){throw 'Failed process accepted'}
  $mock.wait=$false;if(Test-FrozenFileRuntime $report){throw 'Hanging smoke accepted'}
  if($mock.started -ne 4 -or $mock.kills -ne 1 -or $mock.disposes -ne 4){throw 'Owned smoke process cleanup incorrect'}
 } $definitions $root
 @{passed=$true;actualProductionFunctions=3;checks='fresh heartbeat/PID/path/task; draining-task wait and deadline; smoke version/exit/timeout/owned cleanup';processTaskAndClock='scoped doubles';statusFileIO='real temp file';productionOperations=$false} | ConvertTo-Json
}finally{
 $absolute=(Resolve-Path -LiteralPath $root).Path
 if($absolute -ne [IO.Path]::GetFullPath($root) -or -not $absolute.StartsWith($base+'\diannow-health-tests-',[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe health-test cleanup path'}
 Remove-Item -LiteralPath $absolute -Recurse -Force
}
