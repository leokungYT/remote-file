@echo off
setlocal EnableExtensions
cd /d "%~dp0"

::  Turn the VPN on or off (Surfshark over WireGuard).
::
::    vpn-toggle.bat            toggle: connected -> off, otherwise -> on
::    vpn-toggle.bat th-bkk     same, for that .conf
::    vpn-toggle.bat /auto      make sure it is connected, quietly
::                              (used by the scheduled task via vpn-silent.vbs)
::
::  Just a launcher - all the real work and all Thai text lives in vpn.ps1.
::  KEEP THIS FILE PURE ASCII (see the note in install-vpn.bat).

if not exist "%~dp0vpn.ps1" (
    echo [ERROR] vpn.ps1 not found - keep it next to vpn-toggle.bat
    pause
    exit /b 1
)

set "ACT=Toggle"
set "TUN=%~1"
if /i "%~1"=="/auto" (
    set "ACT=Auto"
    set "TUN=%~2"
)

net session >nul 2>&1
if errorlevel 1 (
    :: quiet mode must never pop a UAC prompt - the scheduled task is
    :: registered with "Run with highest privileges" so it never lands here
    if /i "%ACT%"=="Auto" exit /b 2
    echo Requesting Administrator rights - click "Yes" on the prompt...
    if "%~1"=="" (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    ) else (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%~1' -Verb RunAs"
    )
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0vpn.ps1" -Action %ACT% -Tunnel "%TUN%"
set "RC=%errorlevel%"

if /i "%ACT%"=="Auto" exit /b %RC%
timeout /t 8 2>nul
exit /b %RC%
