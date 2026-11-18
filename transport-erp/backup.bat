@echo off
cd /d "%~dp0"
if not exist backups mkdir backups
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set TS=%%i
copy /y data\erp.db "backups\erp-%TS%.db" && echo Backup saved to backups\erp-%TS%.db
pause
