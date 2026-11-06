$ErrorActionPreference='Stop'
$principal=[Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
 $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'"'
 Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -Verb RunAs -ArgumentList $arguments -WindowStyle Hidden -Wait
 exit
}
$program=Join-Path $env:ProgramFiles 'DiannaoRemoteDesktop\RemoteDesktop.exe'
$state=Join-Path $env:LOCALAPPDATA 'CodexDesktopViewer\desktop-service\network-rule.json'
try{
 if(-not(Test-Path -LiteralPath $program)){throw 'Protected desktop host is not installed.'}
 $policy=New-Object -ComObject HNetCfg.FwPolicy2
 foreach($rule in @($policy.Rules)){
  if($rule.ApplicationName -ieq $program -and $rule.Direction -eq 1 -and $rule.Action -eq 0 -and $rule.Protocol -in @(17,256)){$rule.Enabled=$false}
 }
 $name='Diannao Remote Desktop WebRTC UDP'
 try{$rule=$policy.Rules.Item($name)}catch{$rule=New-Object -ComObject HNetCfg.FWRule;$rule.Name=$name}
 $rule.ApplicationName=$program;$rule.Protocol=17;$rule.Direction=1;$rule.Action=1;$rule.Enabled=$true;$rule.Profiles=2147483647;$rule.InterfaceTypes='All';$rule.EdgeTraversal=$true
 try{$policy.Rules.Item($name) | Out-Null}catch{$policy.Rules.Add($rule)}
 @{ok=$true;program=$program;protocol='UDP';rule=$name;updated=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $state -Encoding utf8
}catch{
 @{ok=$false;error=$_.Exception.Message;position=$_.InvocationInfo.PositionMessage;updated=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $state -Encoding utf8
 throw
}
