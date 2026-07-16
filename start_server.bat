@echo off
title Remote File Manager - Server

echo ===============================================
echo   Remote File Manager - Server Setup
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

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python first.
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Install dependencies
echo Installing dependencies...
pip install flask flask-socketio >nul 2>&1
echo Done.
echo.

:: Open firewall port
echo Opening firewall port %SERVER_PORT%...
netsh advfirewall firewall add rule name="RemoteFileManager" dir=in action=allow protocol=TCP localport=%SERVER_PORT% >nul 2>&1
echo.

:: Run server
echo Starting server on http://0.0.0.0:%SERVER_PORT%
echo.
python server.py

pause
