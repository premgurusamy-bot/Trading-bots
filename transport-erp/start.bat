@echo off
title Transport ERP - running (close this window to stop)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe ( echo Run setup.bat first. & pause & exit /b 1 )
for /f "tokens=2 delims==" %%p in ('findstr /b "APP_PORT=" .env 2^>nul') do set PORT=%%p
if "%PORT%"=="" set PORT=8100
start "" /b cmd /c "timeout /t 4 >nul & start http://127.0.0.1:%PORT%"
.venv\Scripts\python.exe -m app.main
pause
