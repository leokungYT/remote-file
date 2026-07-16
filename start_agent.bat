@echo off
cd /d "%~dp0"

:: ----- CONFIG (fallback; config.json can also provide these) -----
set SERVER_URL=http://26.155.240.151:5000
set AGENT_SECRET=2ec990f60382a004d664f06f99a3e7f5
set ALLOWED_PATHS=
:: ----------------------------------------------------------------

:: make sure dependencies are installed (quiet)
python -m pip install "python-socketio[client]" websocket-client requests pystray Pillow >nul 2>&1

:: launch the agent HIDDEN with pythonw (no console) - shows only a tray icon
start "" pythonw agent.py

:: close this window right away
exit /b
