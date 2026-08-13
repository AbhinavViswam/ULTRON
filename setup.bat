@echo off
setlocal
cd /d "%~dp0"

echo ==================================================
echo   Ultron - first time setup
echo ==================================================
echo.

REM ---- Find a Python interpreter -------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"

if not defined PY (
  echo [ERROR] Python was not found on this computer.
  echo.
  echo   1. Install Python 3.10 or newer from https://www.python.org/downloads/
  echo   2. Tick "Add python.exe to PATH" during installation
  echo   3. Run this file again
  echo.
  pause
  exit /b 1
)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.10 or newer is required.
  %PY% --version
  pause
  exit /b 1
)

echo Using Python:
%PY% --version
echo.

REM ---- Virtual environment --------------------------------------------------
if exist "venv\Scripts\python.exe" (
  echo Virtual environment already present, reusing it.
) else (
  echo Creating virtual environment...
  %PY% -m venv venv
  if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    pause
    exit /b 1
  )
)

set "VPY=%~dp0venv\Scripts\python.exe"

REM ---- Dependencies ---------------------------------------------------------
echo.
echo Installing dependencies. This downloads roughly 1.5 GB and can take
echo several minutes on a normal connection. Leave this window open.
echo.
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [ERROR] Dependency installation failed. Check your internet connection
  echo and run this file again - it will pick up where it left off.
  pause
  exit /b 1
)

REM ---- Optional browser engine ---------------------------------------------
echo.
echo Ultron can drive a web browser for you. This needs a browser engine
echo (about 300 MB). You can skip it and everything else still works.
choice /C YN /M "Install the browser engine now"
if errorlevel 2 goto skip_browser
"%VPY%" -m playwright install chromium
:skip_browser

REM ---- Shortcut -------------------------------------------------------------
echo.
echo Creating desktop shortcut...
"%VPY%" install_shortcuts.py

echo.
echo ==================================================
echo   Setup complete.
echo.
echo   1. Launch "Ultron" from your desktop
echo   2. Right-click the orb and choose Settings
echo   3. Paste your own OpenRouter or Google API key
echo.
echo   A free OpenRouter key: https://openrouter.ai/keys
echo ==================================================
echo.
pause
