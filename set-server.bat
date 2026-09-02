@echo off
cd /d "%~dp0"

:: ================= DEFAULT MASTER IP =================
:: 192.168.1.121 = pc_1 (the master) on the LOCAL LAN.
:: Use the LAN IP (not Tailscale 100.73.104.54) because WARP/VPN on the
:: bot PCs breaks Tailscale, but WARP does NOT capture 192.168.x (LAN),
:: so the agent stays connected even while a bot has WARP ON.
:: All bot PCs are on the 192.168.1.0/24 LAN with pc_1.
:: NOTE: the master pc (pc_1) MUST keep WARP OFF.
:: If the master ever changes, edit this one line only.
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
