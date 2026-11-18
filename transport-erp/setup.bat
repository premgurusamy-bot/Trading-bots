@echo off
setlocal
title Transport ERP - Setup
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo [X] Python not found. Install Python 3.13 from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause & exit /b 1
)
set PYV=
for %%v in (3.14 3.13 3.12 3.11) do (
  if not defined PYV ( py -%%v -c "import sys" >nul 2>nul && set PYV=%%v )
)
if not defined PYV ( echo [X] Need Python 3.11 - 3.14. & pause & exit /b 1 )
echo [1/3] Creating virtual environment with Python %PYV%...
if not exist .venv\Scripts\python.exe py -%PYV% -m venv .venv || goto :fail
echo [2/3] Installing packages...
.venv\Scripts\python.exe -m pip install --upgrade pip >nul
.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt || goto :fail
echo [3/3] Preparing settings...
if not exist .env copy .env.example .env >nul
echo.
echo  Setup complete. Open the .env file in Notepad and paste your Claude API key after ANTHROPIC_API_KEY=
echo  (optional - without it the ERP runs in rules mode). Then double-click start.bat
pause
exit /b 0
:fail
echo [X] Setup failed - see the messages above.
pause
exit /b 1
