@echo off
cd /d "%~dp0"
echo ==================================================
echo    INSTALL MASTER WATCHDOG  (run on the MASTER pc)
echo    - starts the server now (if down)
echo    - checks every 2 minutes, auto-restarts if down
echo ==================================================
echo.

net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Please RUN AS ADMINISTRATOR.
    echo   Right-click install-watchdog.bat  then  "Run as administrator"
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0server-watchdog.bat" (
    echo [ERROR] server-watchdog.bat is missing in this folder.
    echo         Make sure the latest files are copied here first.
    echo.
    pause
    exit /b 1
)

echo Registering scheduled task (RemoteFileServerWatchdog, every 2 min) ...
schtasks /Create /TN "RemoteFileServerWatchdog" /TR "\"%~dp0server-watchdog.bat\"" /SC MINUTE /MO 2 /RU "%USERNAME%" /IT /RL HIGHEST /F
if errorlevel 1 (
    echo   ^(retry without /IT ...^)
    schtasks /Create /TN "RemoteFileServerWatchdog" /TR "\"%~dp0server-watchdog.bat\"" /SC MINUTE /MO 2 /RL HIGHEST /F
)

echo.
echo Starting the server now (if it is not already up) ...
call "%~dp0server-watchdog.bat"

echo.
echo [OK] Watchdog installed. Server auto-restarts if it goes down.
echo      Open  localhost:5000  on this PC to see the dashboard.
echo.
pause
