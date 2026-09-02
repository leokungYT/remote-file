@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo ==================================================
echo    INSTALL MASTER WATCHDOG (run on the MASTER pc)
echo ==================================================
echo    - starts the server now (if it is down)
echo    - checks every 2 minutes and auto-restarts it
echo.
echo    IMPORTANT: right-click this file - Run as administrator
echo.

net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Please RUN AS ADMINISTRATOR
    echo   right-click install-watchdog.bat  then  "Run as administrator"
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-watchdog.ps1"

echo.
echo Waiting for server to come up ...
set "OK=0"
for /L %%i in (1,1,20) do (
    if !OK! EQU 0 (
        netstat -ano | findstr ":5000 " | findstr /i LISTENING >nul 2>&1
        if not errorlevel 1 set "OK=1"
        if !OK! EQU 0 ping -n 2 127.0.0.1 >nul
    )
)
if "%OK%"=="1" ( echo [OK] Server is UP on :5000 - watchdog will keep it alive. ) else ( echo [WARN] Server not up yet - it may take a few more seconds. )
echo.
pause
