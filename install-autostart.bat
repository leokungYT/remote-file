@echo off
title Install Agent Auto-Start (hidden + tray)
cd /d "%~dp0"

set "TASK_NAME=RemoteFileAgent"
set "AGENT_PY=%~dp0agent.py"

echo ===============================================
echo   Install Auto-Start - hidden, tray icon
echo ===============================================
echo   Task : %TASK_NAME%
echo   Runs : pythonw agent.py  (no window, tray icon)
echo   When : every logon (as administrator)
echo   NOTE : settings are read from config.json
echo ===============================================
echo.

:: --- require admin ---
net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Please run THIS file as Administrator.
    echo   Right-click install-autostart.bat  then  "Run as administrator"
    echo.
    pause
    exit /b 1
)

:: --- check python ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python first: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: --- install dependencies (incl. tray: pystray + Pillow) ---
echo Installing dependencies...
python -m pip install "python-socketio[client]" websocket-client requests pystray Pillow >nul 2>&1
echo Done.
echo.

:: --- register task: run hidden via pythonw (no console window) ---
schtasks /Create /TN "%TASK_NAME%" /TR "pythonw \"%AGENT_PY%\"" /SC ONLOGON /RL HIGHEST /F
if errorlevel 1 (
    echo [ERROR] Failed to create the scheduled task.
    pause
    exit /b 1
)

echo.
echo [OK] Installed. The agent will run hidden with a tray icon at every logon.
echo   To quit the agent: right-click the tray icon then choose Exit.
echo.
echo   Run now (no reboot):  schtasks /Run /TN "%TASK_NAME%"
echo   Remove auto-start:    schtasks /Delete /TN "%TASK_NAME%" /F
echo.

set /p RUNNOW="Start the agent now (hidden)? (Y/N): "
if /I "%RUNNOW%"=="Y" schtasks /Run /TN "%TASK_NAME%"

echo.
pause
