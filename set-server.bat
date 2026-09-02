@echo off
cd /d "%~dp0"
echo ==================================================
echo    SET MASTER SERVER + UNIQUE NAME for this PC
echo ==================================================
echo    Master LAN IP example: 192.168.1.121  (survives WARP)
echo.
set "SRVIP="
set /p "SRVIP=1) Type master IP then Enter: "
if not defined SRVIP (
    echo.
    echo [CANCEL] no IP entered.
    echo.
    goto :end
)

echo.
echo    IMPORTANT: give THIS pc a UNIQUE name (different on every PC),
echo    otherwise 2 PCs with the same name show as ONE in the dashboard.
echo    Example: pc_2  pc_3  pc_20
echo.
set "PCNAME="
set /p "PCNAME=2) Type UNIQUE name for this PC then Enter: "
if not defined PCNAME (
    echo.
    echo [CANCEL] no name entered.
    echo.
    goto :end
)

echo.
echo Writing config.json ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set-server.ps1" -ip "%SRVIP%" -name "%PCNAME%"

echo.
echo Restarting agent ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*agent.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
start "" pythonw agent.py

echo.
echo [OK] Done.  name=%PCNAME%   server=http://%SRVIP%:5000
echo      (this PC should now appear as "%PCNAME%" in the dashboard)
echo.

:end
echo (press any key to close)
pause >nul
