# Build a single-file GlassesTest.exe (desktop window, no browser).
# Run: powershell -ExecutionPolicy Bypass -File .\打包桌面exe.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Py = $null
if (Test-Path "D:\AutoTestTools\Tools\Python-3.12.10\python.exe") {
  $Py = "D:\AutoTestTools\Tools\Python-3.12.10\python.exe"
} elseif (Test-Path ".\.venv\Scripts\python.exe") {
  $Py = (Resolve-Path ".\.venv\Scripts\python.exe").Path
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  $Py = "python"
} else {
  throw "python not found"
}

Write-Host "Python: $Py"
& $Py -m pip install -q -r requirements.txt pyinstaller pillow
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

$Bundle = Join-Path $Root "bundled_tools"
New-Item -ItemType Directory -Force -Path $Bundle | Out-Null

$AdbSrc = Join-Path $Root "runtime\platform-tools"
$FfSrc = Join-Path $Root "runtime\ffmpeg\bin"
foreach ($name in @("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")) {
  $src = Join-Path $AdbSrc $name
  if (-not (Test-Path $src)) { throw "missing $src" }
  Copy-Item $src (Join-Path $Bundle $name) -Force
}
$ff = Join-Path $FfSrc "ffprobe.exe"
if (-not (Test-Path $ff)) { throw "missing $ff" }
Copy-Item $ff (Join-Path $Bundle "ffprobe.exe") -Force

$Dist = Join-Path $Root "_dist"
New-Item -ItemType Directory -Force -Path $Dist | Out-Null
$Work = Join-Path $Dist "pyi_work"
if (Test-Path $Work) { Remove-Item $Work -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Work | Out-Null

$AddData = @(
  @( (Join-Path $Root "bundled_tools"), "bundled_tools" ),
  @( (Join-Path $Root "glasstest"), "glasstest" ),
  @( (Join-Path $Root "desktop"), "desktop" ),
  @( (Join-Path $Root "task_config.json"), "." ),
  @( (Join-Path $Root "selectors.json"), "." ),
  @( (Join-Path $Root "device_registry.json"), "." ),
  @( (Join-Path $Root "import_validation_config.json"), "." ),
  @( (Join-Path $Root "图像视频标准输出规格.md"), "." )
)

# 单文件：解压目录改到 exe 同目录，避开 %TEMP%\_MEI* 的
# “Could not create temporary directory!”（杀软/策略常见）。
$args = @(
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onefile",
  "--windowed",
  "--runtime-tmpdir", ".",
  "--name", "GlassesTest",
  "--distpath", $Dist,
  "--workpath", $Work,
  "--specpath", $Work,
  "--paths", $Root,
  "--collect-all", "uiautomator2",
  "--collect-all", "adbutils",
  "--hidden-import", "PIL",
  "--hidden-import", "PIL.Image",
  "--hidden-import", "PIL.ImageTk",
  "--hidden-import", "desktop.app",
  "--hidden-import", "desktop.worker",
  "--hidden-import", "desktop.runner",
  "--hidden-import", "glasstest.report_bundle",
  "--hidden-import", "glasstest.run_complex_flow",
  "--hidden-import", "glasstest.run_resolution_check"
)
foreach ($pair in $AddData) {
  $args += @("--add-data", ("{0};{1}" -f $pair[0], $pair[1]))
}
$args += (Join-Path $Root "desktop_entry.py")

Write-Host "Running PyInstaller (this may take several minutes)..."
& $Py @args
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Exe = Join-Path $Dist "GlassesTest.exe"
if (-not (Test-Path $Exe)) { throw "exe missing: $Exe" }

# 清掉上一次目录版残留，避免误点。
$OldDir = Join-Path $Dist "GlassesTest"
if (Test-Path $OldDir) {
  Remove-Item $OldDir -Recurse -Force -ErrorAction SilentlyContinue
}
$OldBat = Join-Path $Dist "启动GlassesTest.bat"
if (Test-Path $OldBat) {
  Remove-Item $OldBat -Force -ErrorAction SilentlyContinue
}

Copy-Item (Join-Path $Root "分发说明.txt") (Join-Path $Dist "分发说明.txt") -Force

# 打包工作目录不必留给分发对象
if (Test-Path $Work) {
  Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
}
# 本机跑 exe 会产生 GlassesTest_data，分发前清掉，只留 exe（说明可选）
$dataBeside = Join-Path $Dist "GlassesTest_data"
if (Test-Path $dataBeside) {
  Remove-Item $dataBeside -Recurse -Force -ErrorAction SilentlyContinue
}
Get-ChildItem $Dist -Force | Where-Object {
  $_.Name -notin @("GlassesTest.exe", "分发说明.txt")
} | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "OK: $Exe"
Get-Item $Exe | Format-List FullName, Length, LastWriteTime
Write-Host "分发时只需拷贝: $Exe"
