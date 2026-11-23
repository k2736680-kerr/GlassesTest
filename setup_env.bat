@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ========================================
echo  GlassesTest setup (.venv in this folder)
echo ========================================
echo.

set "PY="
where py >nul 2>&1 && (
  for /f "delims=" %%I in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%I"
)
if not defined PY (
  where python >nul 2>&1 && for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%I"
)
if not defined PY if exist "D:\AutoTestTools\Tools\Python-3.12.10\python.exe" (
  set "PY=D:\AutoTestTools\Tools\Python-3.12.10\python.exe"
)

if not defined PY (
  echo [FAIL] Python was not found.
  echo Install Python 3.12, or use D:\AutoTestTools\Tools\Python-3.12.10\
  echo.
  pause
  exit /b 1
)

echo Python: %PY%
"%PY%" -c "import sys; print(sys.version)"
if errorlevel 1 (
  echo [FAIL] Python could not start.
  pause
  exit /b 1
)

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo.
  echo Creating .venv ...
  "%PY%" -m venv "%~dp0.venv"
  if errorlevel 1 (
    echo [FAIL] Could not create .venv
    pause
    exit /b 1
  )
) else (
  echo Reusing existing .venv.
)

set "VPY=%~dp0.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
  echo [FAIL] pip upgrade failed
  pause
  exit /b 1
)

echo.
echo Installing requirements.txt ...
"%VPY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo [FAIL] Dependency install failed
  pause
  exit /b 1
)

echo.
echo uiautomator2 init ...
"%VPY%" -m uiautomator2 init
echo.

set "ADB="
if exist "%~dp0runtime\platform-tools\adb.exe" (
  set "ADB=%~dp0runtime\platform-tools\adb.exe"
) else (
  where adb >nul 2>&1 && set "ADB=adb"
)
if defined ADB (
  echo ADB: %ADB%
  "%ADB%" version
) else (
  echo [WARN] adb was not found. Put it in runtime\platform-tools\
)

echo.
echo ========================================
echo  Done.
echo  Debug:  .venv\Scripts\python.exe -m desktop
echo  Pack:   powershell -ExecutionPolicy Bypass -File .\打包桌面exe.ps1
echo ========================================
if /I not "%~1"=="nopause" pause
endlocal
