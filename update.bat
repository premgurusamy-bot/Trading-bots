@echo off
cd /d "%~dp0"
echo Backing up database and settings before update...
call backup.bat nopause
where git >nul 2>nul
if errorlevel 1 (
  echo git not found: download the new version manually, then run setup.bat.
  pause & exit /b 1
)
git pull --ff-only || (echo Update failed - local changes? See docs\TROUBLESHOOTING.md & pause & exit /b 1)
call setup.bat
