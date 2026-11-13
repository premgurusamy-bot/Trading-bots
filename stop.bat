@echo off
cd /d "%~dp0"
for /f "tokens=2 delims==" %%p in ('findstr /b "APP_PORT=" .env 2^>nul') do set PORT=%%p
if "%PORT%"=="" set PORT=8000
echo Stopping the Options Desk server on port %PORT% ...
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if ($c) { $c | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }; 'Stopped.' } else { 'Not running.' }"
echo Note: open paper positions are saved and restored on next start. In LIVE mode, exit positions BEFORE stopping.
pause
