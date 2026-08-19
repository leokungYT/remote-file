@echo off
:: ════════════════════════════════════════════════════════════════
::  agent-watchdog.bat - keep agent.py alive
::  Runs from Task Scheduler every 5 minutes (install-agent-watchdog.bat)
::  If agent.py is not running -> start it hidden again.
:: ════════════════════════════════════════════════════════════════
cd /d "%~dp0"

powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*agent.py*' }) { exit 0 } else { exit 1 }"

if errorlevel 1 (
    echo [Watchdog] agent is DOWN - starting it again...
    start "" pythonw "%~dp0agent.py"
) else (
    echo [Watchdog] agent is running - nothing to do.
)
exit
