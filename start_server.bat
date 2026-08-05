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

:: ===== [1/4] auto-update server.py from GitHub (main) =====
:: ดึงโค้ดใหม่ล่าสุดเองทุกครั้ง จะได้ไม่ต้องรัน autoupdate-file.bat แยก
echo [1/4] Updating server.py from GitHub...
del /q "server.py.new" >nul 2>&1
curl -k -L --retry 3 --retry-delay 2 --connect-timeout 15 --max-time 60 "https://raw.githubusercontent.com/leokungYT/remote-file/main/server.py" -o "server.py.new" >nul 2>&1
if not exist "server.py.new" goto skipupdate
:: verify the download looks like the real server.py (not an error page / empty)
findstr /C:"WEB_UI_HTML" "server.py.new" >nul 2>&1
if errorlevel 1 goto badfile
move /y "server.py.new" "server.py" >nul
echo     [OK] server.py updated from GitHub.
goto afterupdate
:badfile
del /q "server.py.new" >nul 2>&1
echo     [SKIP] downloaded file invalid - keeping current server.py
goto afterupdate
:skipupdate
echo     [SKIP] cannot reach GitHub - keeping current server.py
:afterupdate
echo.

:: ===== [2/4] Check Python =====
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python first.
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

:: ===== [3/4] Install dependencies + open firewall =====
echo [3/4] Checking dependencies + firewall...
pip install flask flask-socketio >nul 2>&1
netsh advfirewall firewall add rule name="RemoteFileManager" dir=in action=allow protocol=TCP localport=%SERVER_PORT% >nul 2>&1

:: ===== [4/4] Run server =====
echo [4/4] Starting server on http://0.0.0.0:%SERVER_PORT%
echo.
python server.py

pause
