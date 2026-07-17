@echo off
cd /d "%~dp0"

:: ----- CONFIG (fallback; config.json can also provide these) -----
set SERVER_URL=http://26.155.240.151:5000
set AGENT_SECRET=2ec990f60382a004d664f06f99a3e7f5
set ALLOWED_PATHS=
:: ----------------------------------------------------------------

:: ===== [1/3] auto-update agent.py from server =====
echo [1/3] Updating agent.py from server...
del /q "agent.py.new" >nul 2>&1
curl -k -L --connect-timeout 10 --max-time 60 "%SERVER_URL%/agent.py" -o "agent.py.new" >nul 2>&1
if not exist "agent.py.new" goto skipupdate
:: verify the download looks like the real agent.py (not an error page)
findstr /C:"RemoteFileManagerAgent_SingleInstance" "agent.py.new" >nul 2>&1
if errorlevel 1 goto badfile
move /y "agent.py.new" "agent.py" >nul
echo     [OK] agent.py updated from server.
goto afterupdate
:badfile
del /q "agent.py.new" >nul 2>&1
echo     [SKIP] downloaded file invalid - keeping current agent.py
goto afterupdate
:skipupdate
echo     [SKIP] cannot reach server - keeping current agent.py
:afterupdate

:: ===== [2/3] make sure dependencies are installed (quiet) =====
echo [2/3] Checking dependencies...
python -m pip install "python-socketio[client]" websocket-client requests pystray Pillow >nul 2>&1

:: ===== [3/3] launch the agent HIDDEN with pythonw (tray icon + status window) =====
echo [3/3] Starting agent...
start "" pythonw agent.py

:: close this window right away
exit /b
