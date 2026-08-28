@echo off
setlocal EnableExtensions
cd /d "%~dp0"

::  Undo everything install-vpn.bat set up.
::  Just a launcher - all the real work and all Thai text lives in vpn.ps1.
::  KEEP THIS FILE PURE ASCII (see the note in install-vpn.bat).

if not exist "%~dp0vpn.ps1" (
    echo [ERROR] vpn.ps1 not found - keep it next to uninstall-vpn.bat
    pause
    exit /b 1
)

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting Administrator rights - click "Yes" on the prompt...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0vpn.ps1" -Action Uninstall
set "RC=%errorlevel%"

echo.
pause
exit /b %RC%
