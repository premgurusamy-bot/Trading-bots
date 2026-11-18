@echo off
title Transport ERP - running for phones (close this window to stop)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe ( echo Run setup.bat first. & pause & exit /b 1 )
findstr /r /b "ERP_PASSWORD=..*" .env >nul 2>nul
if errorlevel 1 (
  echo [X] Set a password first: open .env in Notepad and fill in ERP_PASSWORD=
  echo     Phones and other devices must log in with it.
  pause & exit /b 1
)
for /f "tokens=2 delims==" %%p in ('findstr /b "APP_PORT=" .env 2^>nul') do set PORT=%%p
if "%PORT%"=="" set PORT=8100
echo.
echo  Type one of these addresses into the Transport ERP app on your phone:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=* delims= " %%b in ("%%a") do echo     %%b:%PORT%
echo  (same Wi-Fi as this PC; a 100.x.x.x address is Tailscale and works over mobile data)
echo.
set APP_HOST=0.0.0.0
.venv\Scripts\python.exe -m app.main
pause
