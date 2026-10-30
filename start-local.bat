@echo off
title EnergyWatch AI - local
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto :run
py -3.12 -m venv .venv || goto :no_python
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :eof
:run
if not exist ".env" copy ".env.example" ".env" >nul
echo EnergyWatch AI is starting - open http://localhost:8000 in your browser (Ctrl+C to stop).
start "" /min cmd /c "timeout /t 8 /nobreak >nul & start http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
goto :eof
:no_python
echo Python 3.12 was not found. Install it from https://www.python.org/downloads/
pause
