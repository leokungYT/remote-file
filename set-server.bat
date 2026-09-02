@echo off
cd /d "%~dp0"
echo ==================================================
echo    SET MASTER SERVER (that this agent connects to)
echo ==================================================
echo    Example:
echo      192.168.1.121   = master LAN IP  (survives WARP)
echo      100.80.76.47    = master Tailscale IP
echo.
set "SRVIP="
set /p "SRVIP=Type master IP then press Enter: "
if not defined SRVIP (
    echo.
    echo [CANCEL] no IP entered.
    echo.
    pause
    goto :end
)

echo.
echo Writing config.json ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set-server.ps1" -ip "%SRVIP%"

echo.
echo Restarting agent ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*agent.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
start "" pythonw agent.py

echo.
echo [OK] Done. Agent now connects to http://%SRVIP%:5000
echo      (LAN auto-discovery + Funnel are automatic backups too)
echo.

:end
echo (press any key to close)
pause >nul
