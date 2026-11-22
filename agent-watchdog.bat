@echo off
:: ════════════════════════════════════════════════════════════════
::  agent-watchdog.bat - keep agent.py alive AND connected
::  Runs from Task Scheduler every 5 minutes (install-agent-watchdog.bat)
::  - If agent.py process is not running -> start it
::  - agent.py จะ self-exit ถ้า disconnected > 90s (STALE_DISCONNECT_SEC)
::    watchdog รอบถัดไปจะเห็น process ไม่อยู่ -> start ใหม่
::    ครอบคลุมเคส: ExpressVPN Lock บล็อก, DNS ค้าง, socket.io ค้าง
:: ════════════════════════════════════════════════════════════════
cd /d "%~dp0"

powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*agent.py*' }) { exit 0 } else { exit 1 }"

if errorlevel 1 (
    echo [Watchdog] agent is DOWN - flushing DNS + restarting...
    ipconfig /flushdns >nul 2>&1
    start "" pythonw "%~dp0agent.py"
) else (
    echo [Watchdog] agent is running - nothing to do.
)
exit
