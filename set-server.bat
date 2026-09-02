@echo off
cd /d "%~dp0"

:: ================= DEFAULT MASTER IP =================
:: If the master ever changes IP, edit this one line only.
set "DEFAULT_IP=192.168.1.121"
:: ====================================================

echo ==================================================
echo    CONNECT AGENT TO MASTER
echo    (default master = %DEFAULT_IP%)
echo ==================================================
echo.
echo 1) Master IP  --  just press Enter to use %DEFAULT_IP%
set "SRVIP=%DEFAULT_IP%"
set /p "SRVIP=   [Enter = %DEFAULT_IP%] : "

echo.
echo 2) UNIQUE name for THIS pc  (must differ on every PC, e.g. pc_15, pc_16)
set "PCNAME="
set /p "PCNAME=   Name : "
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
echo [OK] Done.   name=%PCNAME%   server=http://%SRVIP%:5000
echo      (this PC should appear as "%PCNAME%" in the dashboard)
echo.

:end
echo (press any key to close)
pause >nul
