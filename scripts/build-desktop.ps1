$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $project
$python = Join-Path $project '.venv\Scripts\python.exe'
$stdlib = & $python -c "import sysconfig; print(sysconfig.get_path('stdlib'))"
$arguments = @('-m','PyInstaller','--noconfirm','--onefile','--windowed','--name','RemoteDesktop','--paths','scripts','--collect-all','av','--collect-all','aiortc','--collect-all','pyaudiowpatch','--collect-all','dxcam','--distpath','outputs/exe','--workpath','outputs/pyinstaller','--specpath','outputs')
$runtimeCertificates = Join-Path $stdlib 'codex-rejected-cas.pem'
if (Test-Path -LiteralPath $runtimeCertificates) { $arguments += @('--add-data',($runtimeCertificates+';.')) }
$arguments += 'scripts/desktop_host.py'
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
