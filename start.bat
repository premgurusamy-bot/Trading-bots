@echo off
title Options Desk - running (close this window or run stop.bat to stop)
cd /d "%~dp0"
if not exist backend\.venv\Scripts\python.exe (
  echo Not set up yet. Run setup.bat first.
  pause & exit /b 1
)
if not exist frontend\dist\index.html (
  echo Dashboard not built. Run setup.bat first.
  pause & exit /b 1
)
for /f "tokens=2 delims==" %%p in ('findstr /b "APP_PORT=" .env 2^>nul') do set PORT=%%p
if "%PORT%"=="" set PORT=8000
echo Starting Options Desk on http://127.0.0.1:%PORT%  (PAPER mode by default)
start "" /b cmd /c "timeout /t 4 >nul & start http://127.0.0.1:%PORT%"
cd backend
.venv\Scripts\python.exe -m app.main
echo.
echo Options Desk stopped.
pause
