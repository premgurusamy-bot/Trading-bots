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
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)"
if errorlevel 1 (
  echo [X] Python 3.11+ required. Found:
  py -3 --version
  pause & exit /b 1
)
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
if not exist backend\.venv py -3 -m venv backend\.venv || goto :fail
echo [2/5] Installing Python packages...
backend\.venv\Scripts\python.exe -m pip install --upgrade pip >nul
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt || goto :fail
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
