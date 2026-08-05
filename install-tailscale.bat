@echo off
title Install Tailscale VPN - Remote File Agent
cd /d "%~dp0"

:: ================================================================
::  Install Tailscale + join network with an auth key.
::  Run once per agent machine (Run as administrator).
::
::  PREPARE (once):
::   1) https://login.tailscale.com/admin/settings/keys
::   2) Generate auth key, tick "Reusable", copy it
::   3) Paste it below after AUTHKEY=  (keep the AUTHKEY= part!)
:: ================================================================

set "AUTHKEY=tskey-auth-kdx5YEnuKP11CNTRL-uQLvVKF3fn16nnvuAYjwm163PVE1V1VL"

:: full path to tailscale (PATH is not refreshed in this window right after install)
set "TS=C:\Program Files\Tailscale\tailscale.exe"

if "%AUTHKEY%"=="PASTE_YOUR_AUTHKEY_HERE" (
    echo.
    echo [ERROR] AUTHKEY not set. Open this file in Notepad and change the line:
    echo            set "AUTHKEY=tskey-auth-xxxxxxxx"
    echo         Keep the  AUTHKEY=  part, only replace PASTE_YOUR_AUTHKEY_HERE.
    echo.
    pause
    exit /b 1
)

:: --- must be Administrator ---
net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Please run this file as Administrator.
    pause
    exit /b 1
)

:: --- already installed? ---
if exist "%TS%" goto joinnet

echo [1/2] Installing Tailscale ...
:: try winget first (silent). works on Win10 1809+/Win11
winget install --id tailscale.tailscale -e --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
if exist "%TS%" goto joinnet

:: fallback: download + open the installer GUI (click Install -> Finish)
echo     (winget not available)
echo     Downloading Tailscale installer...
curl -k -L --retry 3 --connect-timeout 15 "https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe" -o "tailscale-setup.exe"
if not exist "tailscale-setup.exe" (
    echo [ERROR] Download failed. Install manually: https://tailscale.com/download/windows
    pause
    exit /b 1
)
echo.
echo     >>> Please click through the installer (Install then Finish) <<<
echo.
start /wait "" "tailscale-setup.exe"
del /q "tailscale-setup.exe" >nul 2>&1

if not exist "%TS%" (
    echo [ERROR] Tailscale still not found. Install it manually then run this again.
    pause
    exit /b 1
)

:joinnet
echo [2/2] Joining Tailscale network ...
"%TS%" up --authkey %AUTHKEY% --unattended
if errorlevel 1 (
    echo [ERROR] Join failed - check the AUTHKEY (maybe expired/invalid).
    pause
    exit /b 1
)

echo.
echo [OK] Connected. This PC Tailscale IP:
"%TS%" ip -4
echo.
echo    Servers: http://server:5000 , http://nuuboy:5000  (already in config.json)
echo    Next:    run start_agent.bat
echo.
pause
