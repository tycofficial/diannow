param([switch]$HostOnly,[string]$PythonPath,[string]$BuildRoot)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $project
$python = if($PythonPath){$PythonPath}else{Join-Path $project '.venv\Scripts\python.exe'}
if(-not $BuildRoot){$BuildRoot=$project}
$outputRoot=Join-Path $BuildRoot 'outputs'
$hostOutput=Join-Path $outputRoot 'host'
$launcherOutput=Join-Path $outputRoot 'exe'
$specRoot=Join-Path $project 'outputs'
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
New-Item -ItemType Directory -Path $specRoot -Force | Out-Null
$stdlib = & $python -c "import sysconfig; print(sysconfig.get_path('stdlib'))"
$cacheRoot=Join-Path $BuildRoot 'work\pyinstaller-local'
$arguments = @('-m','PyInstaller','--noconfirm','--onedir','--windowed','--name','RemoteDesktop','--paths','scripts','--collect-all','av','--collect-all','aiortc','--collect-all','pyaudiowpatch','--collect-all','dxcam','--collect-all','cv2','--distpath',$hostOutput,'--workpath',$cacheRoot,'--specpath',$specRoot)
$runtimeCertificates = Join-Path $stdlib 'codex-rejected-cas.pem'
if (Test-Path -LiteralPath $runtimeCertificates) { $arguments += @('--add-data',($runtimeCertificates+';.')) }
$arguments += @('--uac-admin','scripts/desktop_host.py')
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
if($HostOnly){exit}
# Keep the elevated backend in a protected, unpacked installation. The desktop
# file remains a normal one-file launcher and asks Task Scheduler to start it.
$arguments=$arguments | Where-Object {$_ -ne '--uac-admin'} | ForEach-Object { if($_ -eq '--onedir'){'--onefile'}elseif($_ -eq $hostOutput){$launcherOutput}else{$_} }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Desktop launcher build failed.' }
