@echo off
cd /d "%~dp0"
if not exist backups mkdir backups
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set TS=%%i
if exist backend\data\trading.db (
  backend\.venv\Scripts\python.exe -c "import sqlite3; s=sqlite3.connect(r'backend\data\trading.db'); d=sqlite3.connect(r'backups\trading-%TS%.db'); s.backup(d); d.close(); s.close()"
  echo Database backed up to backups\trading-%TS%.db
) else (
  echo No database yet.
)
if exist .env copy /y .env "backups\env-%TS%.txt" >nul
echo (Dhan credentials live in Windows Credential Manager and are not copied.)
if not "%1"=="nopause" pause
