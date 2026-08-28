@echo off
title Fix Surfshark - couldn't reach systems
cd /d "%~dp0"

:: ============================================================
::  Fix Surfshark "The app couldn't reach Surfshark systems"
::  (internet/DNS/clock are OK, but the app is stuck or fighting WARP)
::  Steps: disconnect WARP -> close Surfshark -> flush DNS -> reopen
:: ============================================================

:: --- auto request Administrator ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    exit /b
)

echo ================================================================
echo   Fix Surfshark - "couldn't reach systems"
echo ================================================================
echo.

:: [1/5] Disconnect Cloudflare WARP (two full VPNs cannot run together)
echo [1/5] Disconnecting Cloudflare WARP (if installed)...
set "WARPCLI=%ProgramFiles%\Cloudflare\Cloudflare WARP\warp-cli.exe"
if exist "%WARPCLI%" (
    "%WARPCLI%" disconnect >nul 2>&1
    "%WARPCLI%" --accept-tos disconnect >nul 2>&1
    echo     WARP tunnel disconnected.
) else (
    echo     WARP not installed - skip.
)
taskkill /F /IM "Cloudflare WARP.exe" >nul 2>&1

:: [2/5] Close Surfshark app fully (clear stuck state)
echo [2/5] Closing Surfshark app...
taskkill /F /IM "Surfshark.exe" >nul 2>&1
taskkill /F /IM "Surfshark Service.exe" >nul 2>&1
taskkill /F /IM "SurfsharkService.exe" >nul 2>&1
taskkill /F /IM "Surfshark WireGuard.exe" >nul 2>&1
timeout /t 2 /nobreak >nul

:: [3/5] Flush DNS
echo [3/5] Flushing DNS cache...
ipconfig /flushdns >nul

:: [4/5] Restart Surfshark service (if present)
echo [4/5] Restarting Surfshark service...
net stop "Surfshark Service" >nul 2>&1
net start "Surfshark Service" >nul 2>&1
sc start "SurfsharkService" >nul 2>&1

:: [5/5] Reopen Surfshark app
echo [5/5] Reopening Surfshark...
set "SS1=%ProgramFiles%\Surfshark\Surfshark.exe"
set "SS2=%LOCALAPPDATA%\Programs\Surfshark\Surfshark.exe"
set "SS3=%ProgramFiles(x86)%\Surfshark\Surfshark.exe"
if exist "%SS1%" ( start "" "%SS1%" & goto opened )
if exist "%SS2%" ( start "" "%SS2%" & goto opened )
if exist "%SS3%" ( start "" "%SS3%" & goto opened )
echo     [!] Surfshark.exe not found - open it manually from Start menu.
goto done
:opened
echo     Surfshark reopened.
:done

echo.
echo ================================================================
echo   [DONE] Try logging in to Surfshark again now.
echo.
echo   If it STILL says "couldn't reach systems":
echo     - RESTART the PC (clears leftover VPN adapters) - most reliable
echo     - Or uninstall Cloudflare WARP if Surfshark is your game VPN
echo       (two full VPNs cannot run at the same time)
echo ================================================================
echo.
pause
