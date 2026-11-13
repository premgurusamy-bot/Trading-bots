@echo off
title Options Desk - running for phone app (close this window to stop)
cd /d "%~dp0"
if not exist backend\.venv\Scripts\python.exe (
  echo Not set up yet. Run setup.bat first.
  pause & exit /b 1
)
for /f "tokens=2 delims==" %%p in ('findstr /b "APP_PORT=" .env 2^>nul') do set PORT=%%p
if "%PORT%"=="" set PORT=8000
rem Listen on the network so the Android app can connect (login is still required for everything).
set APP_HOST=0.0.0.0
echo ==================================================================
echo  Options Desk for the Android app  (PAPER mode by default)
echo  In the phone app, enter ONE of these addresses:
echo ==================================================================
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { $kind = if ($_.IPAddress -like '100.*') { '  (Tailscale - works from anywhere)' } else { '  (home Wi-Fi - phone must be on the same Wi-Fi)' }; '   ' + $_.IPAddress + ':%PORT%' + $kind }"
echo.
echo  If Windows Firewall asks, allow Python on PRIVATE networks only.
echo  Keep this window open while you use the app. Do NOT port-forward your router.
echo ==================================================================
cd backend
.venv\Scripts\python.exe -m app.main
echo Options Desk stopped.
pause
