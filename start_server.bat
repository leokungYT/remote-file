@echo off
title Remote File Manager - Server
cd /d "%~dp0"

echo ===============================================
echo   Remote File Manager - Server
echo ===============================================
echo.

:: ----- CONFIG (edit here) -----------------------
:: Secret key for web session
set SECRET_KEY=532662739b6ba4299fea8253dbfbcad3

:: Shared secret for agents (must match child machines)
set AGENT_SECRET=2ec990f60382a004d664f06f99a3e7f5

:: Port
set SERVER_PORT=5000
:: ------------------------------------------------

echo   Port        : %SERVER_PORT%
echo   Agent Secret: %AGENT_SECRET%
echo.

:: ===== [1/5] auto-update server.py from GitHub (main) =====
echo [1/5] Updating server.py from GitHub...
del /q "server.py.new" >nul 2>&1
curl -k -L --retry 3 --retry-delay 2 --connect-timeout 15 --max-time 60 "https://raw.githubusercontent.com/leokungYT/remote-file/main/server.py" -o "server.py.new" >nul 2>&1
if not exist "server.py.new" goto sskip
findstr /C:"WEB_UI_HTML" "server.py.new" >nul 2>&1
if errorlevel 1 goto sbad
move /y "server.py.new" "server.py" >nul
echo     [OK] server.py updated from GitHub.
goto safter
:sbad
del /q "server.py.new" >nul 2>&1
echo     [SKIP] downloaded server.py invalid - keeping current
goto safter
:sskip
echo     [SKIP] cannot reach GitHub - keeping current server.py
:safter

:: ===== [2/5] auto-update agent.py from GitHub (server เสิร์ฟให้เครื่องลูกดึงไปอัปเดต) =====
echo [2/5] Updating agent.py from GitHub...
del /q "agent.py.new" >nul 2>&1
curl -k -L --retry 3 --retry-delay 2 --connect-timeout 15 --max-time 60 "https://raw.githubusercontent.com/leokungYT/remote-file/main/agent.py" -o "agent.py.new" >nul 2>&1
if not exist "agent.py.new" goto askip
findstr /C:"RemoteFileManagerAgent_SingleInstance" "agent.py.new" >nul 2>&1
if errorlevel 1 goto abad
move /y "agent.py.new" "agent.py" >nul
echo     [OK] agent.py updated from GitHub.
goto aafter
:abad
del /q "agent.py.new" >nul 2>&1
echo     [SKIP] downloaded agent.py invalid - keeping current
goto aafter
:askip
echo     [SKIP] cannot reach GitHub - keeping current agent.py
:aafter
echo.

:: ===== [3/5] Check Python =====
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python first.
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

:: ===== [4/5] Install dependencies + open firewall =====
echo [4/5] Checking dependencies + firewall...
pip install flask flask-socketio >nul 2>&1
netsh advfirewall firewall add rule name="RemoteFileManager" dir=in action=allow protocol=TCP localport=%SERVER_PORT% >nul 2>&1

:: ===== [5/5] Run server =====
echo [5/5] Starting server on http://0.0.0.0:%SERVER_PORT%
echo.
python server.py

pause
