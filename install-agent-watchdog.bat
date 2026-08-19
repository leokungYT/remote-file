@echo off
title Install Agent Watchdog (auto-restart agent if it dies)
cd /d "%~dp0"

set "TASK_NAME=RemoteFileAgent Watchdog"

echo ===============================================
echo   Install Agent Watchdog
echo   Task : %TASK_NAME%
echo   Runs : agent-watchdog.bat  every 5 minutes
echo   Does : if agent.py is not running -> start it
echo ===============================================
echo.

net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Please run THIS file as Administrator.
    pause
    exit /b 1
)

schtasks /Create /TN "%TASK_NAME%" /TR "\"%~dp0agent-watchdog.bat\"" /SC MINUTE /MO 5 /RL HIGHEST /F
if errorlevel 1 (
    echo [ERROR] Failed to create the scheduled task.
    pause
    exit /b 1
)

echo.
echo [OK] Watchdog installed - agent will be restarted within 5 minutes if it dies.
echo   Run now      : schtasks /Run /TN "%TASK_NAME%"
echo   Remove       : schtasks /Delete /TN "%TASK_NAME%" /F
echo.

schtasks /Run /TN "%TASK_NAME%" >nul 2>&1
echo [OK] Ran once now.
echo.
pause
