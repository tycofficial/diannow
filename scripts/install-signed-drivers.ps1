param([switch]$SkipDisplay)
$ErrorActionPreference='Stop'
$project=Split-Path $PSScriptRoot -Parent
$stateDir=Join-Path $env:LOCALAPPDATA 'CodexDesktopViewer\desktop-service'
Add-Type -TypeDefinition @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
public static class DiannaoSignedDevice {
 [StructLayout(LayoutKind.Sequential)] public struct Info {public uint size;public Guid cls;public uint devInst;public IntPtr reserved;}
 [DllImport("setupapi.dll",SetLastError=true)] static extern IntPtr SetupDiCreateDeviceInfoList(ref Guid cls,IntPtr parent);
 [DllImport("setupapi.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool SetupDiCreateDeviceInfoW(IntPtr set,string name,ref Guid cls,string description,IntPtr parent,uint flags,ref Info info);
 [DllImport("setupapi.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool SetupDiSetDeviceRegistryPropertyW(IntPtr set,ref Info info,uint property,byte[] value,uint size);
 [DllImport("setupapi.dll",SetLastError=true)] static extern bool SetupDiCallClassInstaller(uint function,IntPtr set,ref Info info);
 [DllImport("setupapi.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool SetupDiGetDeviceInstanceIdW(IntPtr set,ref Info info,StringBuilder id,uint length,out uint required);
 [DllImport("setupapi.dll")] static extern bool SetupDiDestroyDeviceInfoList(IntPtr set);
 [DllImport("newdev.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool UpdateDriverForPlugAndPlayDevicesW(IntPtr parent,string hardware,string inf,uint flags,out bool reboot);
 public static string Install(string inf,string hardware,string classId,string description,bool create) {
  Guid cls=new Guid(classId);IntPtr set=IntPtr.Zero;Info info=new Info();info.size=(uint)Marshal.SizeOf(typeof(Info));string instance="existing";
  try {
   if(create){
    set=SetupDiCreateDeviceInfoList(ref cls,IntPtr.Zero);if(set==new IntPtr(-1))throw new Win32Exception();
    if(!SetupDiCreateDeviceInfoW(set,description,ref cls,description,IntPtr.Zero,1,ref info))throw new Win32Exception();
    byte[] id=Encoding.Unicode.GetBytes(hardware+"\0\0");
    if(!SetupDiSetDeviceRegistryPropertyW(set,ref info,1,id,(uint)id.Length))throw new Win32Exception();
    if(!SetupDiCallClassInstaller(0x19,set,ref info))throw new Win32Exception();
    StringBuilder value=new StringBuilder(1024);uint required;
    if(SetupDiGetDeviceInstanceIdW(set,ref info,value,1024,out required))instance=value.ToString();
   }
   bool reboot;if(!UpdateDriverForPlugAndPlayDevicesW(IntPtr.Zero,hardware,inf,0,out reboot))throw new Win32Exception();
   return instance+";reboot="+reboot;
  }finally {if(set!=IntPtr.Zero&&set!=new IntPtr(-1))SetupDiDestroyDeviceInfoList(set);}
 }
}
"@
$results=@()
$base=Join-Path $project 'outputs\vb-cable\package'
$catalog=Join-Path $base 'vbaudio_cable64_win10.cat'
if((Get-AuthenticodeSignature -LiteralPath $catalog).Status -ne 'Valid'){throw 'VB-CABLE driver catalog signature is invalid.'}
$exists=@(Get-PnpDevice -PresentOnly -Class MEDIA -ErrorAction SilentlyContinue | Where-Object {$_.FriendlyName -match '^VB-Audio Virtual Cable$'})
try{if($exists.Count -and $exists[0].Status -eq 'OK'){$value='already-installed'}else{$value=[DiannaoSignedDevice]::Install((Join-Path $base 'vbMmeCable64_win10.inf'),'VBAudioVACWDM','4d36e96c-e325-11ce-bfc1-08002be10318','DiannaoVBCable',(-not $exists.Count))};$results+=@{device='VB-CABLE';ok=$true;result=$value}}catch{$results+=@{device='VB-CABLE';ok=$false;error=$_.Exception.Message}}
if(-not $SkipDisplay){
 $source=Join-Path $project 'outputs\virtual-display\package\VirtualDisplayDriver'
 if((Get-AuthenticodeSignature -LiteralPath (Join-Path $source 'mttvdd.cat')).Status -ne 'Valid'){throw 'Virtual display catalog signature is invalid.'}
 $target='C:\VirtualDisplayDriver'
 New-Item -ItemType Directory -Path $target -Force | Out-Null
 if(Test-Path -LiteralPath (Join-Path $target 'vdd_settings.xml')){Copy-Item -LiteralPath (Join-Path $target 'vdd_settings.xml') -Destination (Join-Path $stateDir 'vdd-settings-before.xml') -Force}
 Copy-Item -Path (Join-Path $source '*') -Destination $target -Force
 [xml]$config=Get-Content -LiteralPath (Join-Path $target 'vdd_settings.xml')
 $config.vdd_settings.gpu.friendlyname='AMD Radeon(TM) Vega 8 Graphics'
 $config.vdd_settings.options.HardwareCursor='false'
 $config.Save((Join-Path $target 'vdd_settings.xml'))
 $exists=@(Get-PnpDevice -PresentOnly -Class Display -ErrorAction SilentlyContinue | Where-Object {$_.FriendlyName -eq 'Virtual Display Driver'})
 try{if($exists.Count -and $exists[0].Status -eq 'OK'){$value='already-installed'}else{$value=[DiannaoSignedDevice]::Install((Join-Path $target 'MttVDD.inf'),'Root\MttVDD','4d36e968-e325-11ce-bfc1-08002be10318','DiannaoVirtualDisplay',(-not $exists.Count))};$results+=@{device='VirtualDisplay';ok=$true;result=$value}}catch{$results+=@{device='VirtualDisplay';ok=$false;error=$_.Exception.Message}}
 Start-Sleep -Seconds 3
 & (Join-Path $project '.venv\Scripts\python.exe') -c "import ctypes;ctypes.windll.user32.SetDisplayConfig(0,None,0,None,0x84)"
 & (Join-Path $project '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'display_target.py') --activate-virtual *> (Join-Path $stateDir 'virtual-display-activation.log')
}
& (Join-Path $project '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'audio_routing.py') *> (Join-Path $stateDir 'virtual-audio-formats.log')
$results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $stateDir 'driver-results.json') -Encoding utf8
