@echo off
title Fix Tailscale - Remote File Agent
cd /d "%~dp0"

:: =====================================================================
::  Fix a stuck Tailscale ("Starting..." forever / node shows offline
::  on the admin page / "tailscale up" hangs with no output).
::
::  Run as administrator. Safe to run any number of times.
::
::  Order of attack (stops as soon as "server" answers):
::    1. close the tray app
::    2. restart the Tailscale service, set it to start automatically
::    3. tailscale up  (with --timeout so it can never hang)
::    4. still dead -> reinstall over the top; this rebuilds the wintun
::       adapter, which is what actually breaks when tailscaled will not
::       come up.  The login is kept.
::    5. clean re-auth with the reusable key
::    6. verify with a ping, print the log tail if it still failed
::
::  KEEP THIS FILE PURE ASCII (see the note in install-vpn.bat).
:: =====================================================================
set "AUTHKEY=tskey-auth-kp7xKNvxMp11CNTRL-ehkrQGtb9rWoKRjAzNu4rWkwzYUZkQPTQ"
set "TS=C:\Program Files\Tailscale\tailscale.exe"
set "GUI=C:\Program Files\Tailscale\tailscale-ipn.exe"
set "LOG=C:\ProgramData\Tailscale\tailscaled.log1.txt"
set "MSIURL=https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi"
set "MSIFILE=%TEMP%\tailscale-setup.msi"

net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Right-click this file - Run as administrator
    pause
    exit /b 1
)

echo ===============================================
echo   Fix Tailscale connection
echo ===============================================
echo.

echo [1/6] Closing the Tailscale tray app ...
taskkill /f /im tailscale-ipn.exe >nul 2>&1

echo [2/6] Restarting the Tailscale service ...
net stop Tailscale >nul 2>&1
sc config Tailscale start= auto >nul 2>&1
net start Tailscale >nul 2>&1
timeout /t 5 /nobreak >nul

if not exist "%TS%" goto reinstall

echo [3/6] Reconnecting ...
"%TS%" up --timeout=30s --unattended >nul 2>&1
ping -n 2 server >nul 2>&1
if not errorlevel 1 goto verify

:reinstall
echo [4/6] Did not come up - reinstalling Tailscale over the top ...
echo       (downloading, about 40 MB - please wait)
del /q "%MSIFILE%" >nul 2>&1
curl -k -L --retry 3 --connect-timeout 20 "%MSIURL%" -o "%MSIFILE%"
if not exist "%MSIFILE%" (
    echo       [ERROR] Download failed - check the internet on this PC.
    goto verify
)
msiexec /i "%MSIFILE%" /quiet /norestart
timeout /t 20 /nobreak >nul
del /q "%MSIFILE%" >nul 2>&1
sc config Tailscale start= auto >nul 2>&1
net start Tailscale >nul 2>&1
timeout /t 5 /nobreak >nul

echo [5/6] Clean re-auth with the shared key ...
"%TS%" up --reset --force-reauth --unattended --timeout=60s --authkey %AUTHKEY%
timeout /t 5 /nobreak >nul

:verify
echo [6/6] Checking ...
echo.
echo   --- this PC's Tailscale IP ---
"%TS%" ip -4
echo.
echo   --- can we see the server? ---
ping -n 2 server
echo.

start "" "%GUI%"

ping -n 1 server >nul 2>&1
if not errorlevel 1 goto ok

echo ===============================================
echo   [FAILED] server is still unreachable.
echo   Last lines of the Tailscale log:
echo ===============================================
powershell -NoProfile -Command "if (Test-Path '%LOG%') { Get-Content '%LOG%' -Tail 25 } else { 'no log file at %LOG%' }"
echo ===============================================
echo   Send a screenshot of everything above.
echo ===============================================
echo.
pause
exit /b 1

:ok
echo ===============================================
echo   [OK] Connected. The agent turns green in ~5s
echo        by itself - no need to restart it.
echo ===============================================
echo.
pause
exit /b 0
