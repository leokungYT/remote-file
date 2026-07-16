@echo off
title Remote File Manager - Agent

echo ===============================================
echo   Remote File Manager - Agent Setup
echo ===============================================
echo.

:: ----- CONFIG (edit here) -----------------------
:: Server IP - must match the main machine. Keep the http:// prefix.
set SERVER_URL=http://26.155.240.151:5000

:: Shared secret - must match the server
set AGENT_SECRET=2ec990f60382a004d664f06f99a3e7f5

:: Agent name (leave empty = use hostname automatically)
set AGENT_ID=

:: Allowed folders - leave empty to use the default baked in agent.py (pes + cookie-run)
:: Or set explicitly here (separate with ;) to override, e.g. C:\path1;C:\path2
set ALLOWED_PATHS=
:: ------------------------------------------------

echo   Server URL  : %SERVER_URL%
echo   Agent Secret: %AGENT_SECRET%
echo   Agent ID    : %AGENT_ID%
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
pip install "python-socketio[client]" websocket-client >nul 2>&1
echo Done.
echo.

:: Run agent
echo Starting agent...
echo.
python agent.py

pause
