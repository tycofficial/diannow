$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $project
$python = Join-Path $project '.venv\Scripts\python.exe'
$stdlib = & $python -c "import sysconfig; print(sysconfig.get_path('stdlib'))"
$cacheRoot=if(Test-Path -LiteralPath 'D:\'){ 'D:\CodexBuildCache\remote-desktop' }else{Join-Path $project 'outputs\pyinstaller'}
$arguments = @('-m','PyInstaller','--noconfirm','--onedir','--windowed','--name','RemoteDesktop','--paths','scripts','--collect-all','av','--collect-all','aiortc','--collect-all','pyaudiowpatch','--collect-all','dxcam','--collect-all','cv2','--distpath','outputs/host','--workpath',$cacheRoot,'--specpath','outputs')
$runtimeCertificates = Join-Path $stdlib 'codex-rejected-cas.pem'
if (Test-Path -LiteralPath $runtimeCertificates) { $arguments += @('--add-data',($runtimeCertificates+';.')) }
$arguments += 'scripts/desktop_host.py'
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
# Keep the elevated backend in a protected, unpacked installation. The desktop
# file remains a normal one-file launcher and asks Task Scheduler to start it.
$arguments=$arguments | ForEach-Object { if($_ -eq '--onedir'){'--onefile'}elseif($_ -eq 'outputs/host'){'outputs/exe'}else{$_} }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Desktop launcher build failed.' }
