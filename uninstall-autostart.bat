@echo off
title Uninstall Agent Auto-Start
set "TASK_NAME=RemoteFileAgent"

echo Removing auto-start task: %TASK_NAME%
echo.

net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Please run as Administrator.
    pause
    exit /b 1
)

schtasks /Delete /TN "%TASK_NAME%" /F
echo.
echo Done. Auto-start removed.
pause
