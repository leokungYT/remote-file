@echo off
title Remote File Manager - Agent (Auto Update)
cd /d "%~dp0"

:: ===== CONFIG (must match the server) ============
set SERVER_URL=http://26.155.240.151:5000
set AGENT_SECRET=2ec990f60382a004d664f06f99a3e7f5
set AGENT_ID=
set ALLOWED_PATHS=C:\Users\Administrator\Desktop\pes
:: ================================================

echo ===============================================
echo   Remote File Manager - Agent (Auto Update)
echo ===============================================
echo   Server URL  : %SERVER_URL%
echo   Allowed     : %ALLOWED_PATHS%
echo.

:: --- Check Python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python first.
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

:: --- Download latest agent.py from the server ---
echo Downloading latest agent.py from server...
if exist agent_new.py del /q agent_new.py

curl -sf -o agent_new.py "%SERVER_URL%/agent.py"
if not exist agent_new.py (
    echo curl unavailable/failed - trying Python downloader...
    python -c "import urllib.request; urllib.request.urlretrieve('%SERVER_URL%/agent.py','agent_new.py')" 2>nul
)

:: --- Verify the download is the real agent code ---
if exist agent_new.py (
    findstr /C:"upload_start" agent_new.py >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Download looks invalid - keeping current agent.py
        del /q agent_new.py >nul 2>&1
    ) else (
        move /Y agent_new.py agent.py >nul
        echo [OK] agent.py updated to latest version.
    )
) else (
    echo [WARN] Could not download - using existing agent.py
)
echo.

:: --- Make sure dependencies are installed ---
echo Checking dependencies...
pip install "python-socketio[client]" websocket-client requests >nul 2>&1
echo.

:: --- Run the agent ---
echo Starting agent...
echo.
python agent.py

echo.
echo Agent stopped. Press any key to close.
pause >nul
