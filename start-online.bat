@echo off
setlocal
title EnergyWatch AI - online with Cloudflare Tunnel
cd /d "%~dp0"
echo.
echo  =====================================================
echo    EnergyWatch AI - put the app online (Cloudflare)
echo  =====================================================
echo.

rem ---------- 1. Python environment (first run only) ----------
if exist ".venv\Scripts\python.exe" goto :env_ready
echo  [1/4] First run: creating the Python environment (2-5 min)...
py -3.12 -m venv .venv
if errorlevel 1 goto :no_python
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :pip_failed
:env_ready
echo  [1/4] Python environment ready.

rem ---------- 2. Settings ----------
if not exist ".env" copy ".env.example" ".env" >nul
findstr /r /c:"^ADMIN_API_KEY=..*" ".env" >nul
if errorlevel 1 (
  echo.
  echo  WARNING: ADMIN_API_KEY is empty in the .env file.
  echo  With a public link, anyone could approve actions or start chaos drills.
  echo  Open .env with Notepad and set for example:  ADMIN_API_KEY=choose-a-password
  echo.
)
echo  [2/4] Settings loaded from .env

rem ---------- 3. Start the app in its own window ----------
start "EnergyWatch AI - server (keep this window open)" ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo  [3/4] Starting the app...
set /a tries=0
:wait_app
timeout /t 2 /nobreak >nul
curl -s -o nul http://127.0.0.1:8000/health
if not errorlevel 1 goto :app_ready
set /a tries+=1
if %tries% lss 45 goto :wait_app
echo  The app did not start. Look at the "server" window for the error.
pause
exit /b 1
:app_ready
echo  [3/4] App running on http://localhost:8000

rem ---------- 4. Cloudflare Tunnel ----------
if exist "tools\cloudflared.exe" goto :tunnel
echo  [4/4] Downloading Cloudflare Tunnel (first run only)...
if not exist tools mkdir tools
curl -L --fail -o "tools\cloudflared.exe" https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
if errorlevel 1 goto :download_failed
:tunnel
echo.
echo  =====================================================
echo   Your public link appears below, in a box, like:
echo     https://something-random.trycloudflare.com
echo   Share it. Keep BOTH windows open while people use it.
echo   Press Ctrl+C here to go offline.
echo  =====================================================
echo.
"tools\cloudflared.exe" tunnel --no-autoupdate --url http://localhost:8000
goto :eof

:no_python
echo  Python 3.12 was not found. Install it from https://www.python.org/downloads/
echo  (tick "Add python.exe to PATH"), then run this file again.
pause
exit /b 1
:pip_failed
echo  Installing the libraries failed. Check your internet connection and run this file again.
pause
exit /b 1
:download_failed
echo  Could not download cloudflared. Download it manually from
echo  https://github.com/cloudflare/cloudflared/releases/latest  (cloudflared-windows-amd64.exe)
echo  and save it as tools\cloudflared.exe in this folder.
pause
exit /b 1
