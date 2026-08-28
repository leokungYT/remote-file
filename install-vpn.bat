@echo off
setlocal EnableExtensions
cd /d "%~dp0"

::  One-click VPN installer (Surfshark over WireGuard).
::  Just a launcher - all the real work and all Thai text lives in vpn.ps1.
::  KEEP THIS FILE PURE ASCII: cmd.exe desyncs its byte offsets when a batch
::  file mixes multi-byte characters with "chcp 65001", which corrupts lines
::  into garbage commands. PowerShell reads UTF-8 correctly, batch does not.

if not exist "%~dp0vpn.ps1" (
    echo [ERROR] vpn.ps1 not found - keep it next to install-vpn.bat
    pause
    exit /b 1
)

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting Administrator rights - click "Yes" on the prompt...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0vpn.ps1" -Action Install
set "RC=%errorlevel%"

echo.
pause
exit /b %RC%
