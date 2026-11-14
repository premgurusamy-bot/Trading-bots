@echo off
setlocal
title Options Desk - Setup
cd /d "%~dp0"
echo ============================================================
echo  Options Desk setup (NIFTY / BANK NIFTY) - PAPER mode default
echo ============================================================

where py >nul 2>nul
if errorlevel 1 (
  echo [X] Python launcher "py" not found. Install Python 3.11 or newer from https://www.python.org/downloads/
  echo     During install tick "Add python.exe to PATH". Then run setup.bat again.
  pause & exit /b 1
)
rem Pick the newest SUPPORTED Python (tested: 3.14, 3.13, 3.12, 3.11)
set PYV=
for %%v in (3.14 3.13 3.12 3.11) do (
  if not defined PYV (
    py -%%v -c "import sys" >nul 2>nul && set PYV=%%v
  )
)
if not defined PYV (
  echo [X] No supported Python found. Install Python 3.13 or 3.14 from https://www.python.org/downloads/
  echo     Installed versions:
  py -0
  pause & exit /b 1
)
echo [*] Using Python %PYV%
where npm >nul 2>nul
if errorlevel 1 (
  echo [X] Node.js not found. Install the LTS version from https://nodejs.org/ then run setup.bat again.
  pause & exit /b 1
)

if exist backend\data\trading.db (
  echo [*] Backing up existing database...
  if not exist backups mkdir backups
  for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set TS=%%i
  copy /y backend\data\trading.db "backups\trading-%TS%.db" >nul
)

echo [1/5] Creating Python virtual environment...
if exist backend\.venv\Scripts\python.exe (
  for /f %%i in ('backend\.venv\Scripts\python.exe -c "import sys;print(*sys.version_info[:2],sep=chr(46))"') do set VENVV=%%i
)
if exist backend\.venv if not "%VENVV%"=="%PYV%" (
  echo [*] Rebuilding virtual environment for Python %PYV%...
  rmdir /s /q backend\.venv
)
if not exist backend\.venv\Scripts\python.exe py -%PYV% -m venv backend\.venv || goto :fail
echo [2/5] Installing Python packages...
backend\.venv\Scripts\python.exe -m pip install --upgrade pip >nul
rem --only-binary: use ready-made Windows packages only; never try to compile (no Rust / Visual C++ needed)
backend\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r backend\requirements.txt
if errorlevel 1 (
  echo.
  echo [X] Could not install a Python package. Most likely there is no ready-made Windows package
  echo     for your Python version yet. Install Python 3.13 from https://www.python.org/downloads/
  echo     then run setup.bat again ^(it picks a supported version automatically^).
  goto :fail
)
echo [3/5] Installing and building the dashboard...
pushd frontend
call npm ci --no-audit --no-fund || (popd & goto :fail)
call npm run build || (popd & goto :fail)
popd
echo [4/5] Preparing configuration...
if not exist .env copy .env.example .env >nul
echo [5/5] Initialising database (migrations)...
pushd backend
.venv\Scripts\python.exe -c "from app.db import run_migrations; run_migrations(); print('database ready')" || (popd & goto :fail)
popd

echo.
echo  Setup complete. Next: double-click start.bat
echo  Then open http://127.0.0.1:8000 , create your password, and add Dhan credentials in Settings.
pause
exit /b 0

:fail
echo.
echo [X] Setup failed. See the messages above and docs\TROUBLESHOOTING.md
pause
exit /b 1
