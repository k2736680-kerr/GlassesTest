# 刷新打包用的 adb / ffprobe（只拷贝 exe 真正需要的文件）。
# Run: powershell -ExecutionPolicy Bypass -File .\准备便携运行时.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Tools = "D:\AutoTestTools\Tools"
$AdbSrc = Join-Path $Tools "AndroidSdk\platform-tools"
$FfSrc = Join-Path $Tools "FFmpeg\bin\ffprobe.exe"

$AdbDst = Join-Path $Root "runtime\platform-tools"
$FfDstDir = Join-Path $Root "runtime\ffmpeg\bin"

New-Item -ItemType Directory -Force -Path $AdbDst | Out-Null
New-Item -ItemType Directory -Force -Path $FfDstDir | Out-Null

$adbNames = @("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")
if (Test-Path (Join-Path $AdbSrc "adb.exe")) {
  Write-Host "Copy ADB (3 files) -> runtime\platform-tools"
  foreach ($name in $adbNames) {
    $src = Join-Path $AdbSrc $name
    if (-not (Test-Path $src)) { throw "missing $src" }
    Copy-Item $src (Join-Path $AdbDst $name) -Force
  }
  Get-ChildItem $AdbDst -File | Where-Object { $adbNames -notcontains $_.Name } |
    Remove-Item -Force
} elseif (Test-Path (Join-Path $AdbDst "adb.exe")) {
  Write-Host "runtime\platform-tools already present"
} else {
  Write-Host "WARN: ADB source missing at $AdbSrc"
}

if (Test-Path $FfSrc) {
  Write-Host "Copy ffprobe -> runtime\ffmpeg\bin"
  Copy-Item $FfSrc (Join-Path $FfDstDir "ffprobe.exe") -Force
} elseif (Test-Path (Join-Path $FfDstDir "ffprobe.exe")) {
  Write-Host "runtime\ffmpeg already present"
} else {
  Write-Host "WARN: ffprobe missing at $FfSrc"
}

Write-Host "runtime ready"
Write-Host ("  adb     = " + (Test-Path (Join-Path $AdbDst "adb.exe")))
Write-Host ("  ffprobe = " + (Test-Path (Join-Path $FfDstDir "ffprobe.exe")))
