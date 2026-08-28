@echo off
title Keep agent connected under Surfshark VPN
cd /d "%~dp0"

:: auto request admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    exit /b
)

set "TS=C:\Program Files\Tailscale"

echo ================================================================
echo   Keep the remote agent connected while Surfshark VPN is ON
echo ================================================================
echo.

echo [1/4] Restarting Tailscale so it reconnects cleanly...
net stop Tailscale >nul 2>&1
timeout /t 2 /nobreak >nul
net start Tailscale >nul 2>&1
"%TS%\tailscale.exe" up >nul 2>&1
timeout /t 3 /nobreak >nul

echo [2/4] Tailscale status (want to see peers = active):
"%TS%\tailscale.exe" status
echo.

echo [3/4] Opening Surfshark app...
set "SS1=%ProgramFiles%\Surfshark\Surfshark.exe"
set "SS2=%LOCALAPPDATA%\Programs\Surfshark\Surfshark.exe"
if exist "%SS1%" ( start "" "%SS1%" ) else if exist "%SS2%" ( start "" "%SS2%" )

echo [4/4] Copying Tailscale folder path to clipboard...
echo %TS%|clip
echo     (paste it in the Bypasser file box to jump there)

echo.
echo ================================================================
echo   NOW DO THIS IN SURFSHARK BY HAND (no command line exists):
echo   Settings (gear) -^> VPN settings -^> Bypasser -^> pick "Bypass VPN"
echo   -^> Add apps -^> add these 3 files:
echo        %TS%\tailscaled.exe      (MOST IMPORTANT)
echo        %TS%\tailscale.exe
echo        %TS%\tailscale-ipn.exe
echo   -^> Save, then Disconnect/Connect Surfshark once.
echo.
echo   After that the agent stays online even with Surfshark ON.
echo ================================================================
echo.
pause
