@echo off
chcp 65001 >nul
title เลิกใช้ VPN อัตโนมัติ - Surfshark
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

:: ============================================================================
::  ยกเลิกทุกอย่างที่ install-vpn.bat ตั้งไว้
::    - ลบ Scheduled Task ที่ต่อ VPN อัตโนมัติ
::    - ตัด VPN + ลบ tunnel service ทั้งหมด
::    - ลบช็อตคัตบนเดสก์ท็อป
::    - ถามก่อนว่าจะลบ config/บัญชีที่เก็บไว้ด้วยไหม
::
::  ไม่ได้ถอน WireGuard ออก (ถ้าจะถอน: winget uninstall WireGuard.WireGuard)
:: ============================================================================

set "SYS=%SystemRoot%\System32"
set "WG=%ProgramFiles%\WireGuard\wireguard.exe"
set "CONF_DIR=%LOCALAPPDATA%\SurfsharkVPN"
set "SETUP=%~dp0vpn-setup.ps1"
set "PSX=powershell -NoProfile -ExecutionPolicy Bypass -File"

echo.
echo  ==============================================================
echo    เลิกใช้ VPN อัตโนมัติ
echo  ==============================================================

net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ขอสิทธิ์ Administrator ... กด "Yes" ในหน้าต่างที่เด้งขึ้นมา
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo  [1/4] ลบ Scheduled Task ...
if exist "%SETUP%" (
    %PSX% "%SETUP%" -Action RemoveTask >nul 2>&1
    echo        [OK] ลบแล้ว
) else (
    schtasks /delete /tn "Surfshark VPN AutoConnect" /f >nul 2>&1
    echo        [OK] ลบแล้ว ^(ผ่าน schtasks^)
)

echo.
echo  [2/4] ตัด VPN + ลบ tunnel service ...
set "FOUND="
for /f "tokens=2 delims=:" %%S in ('sc query type^= service state^= all ^| "%SYS%\find.exe" "SERVICE_NAME: WireGuardTunnel$"') do (
    set "SVCNAME=%%S"
    set "SVCNAME=!SVCNAME: =!"
    for /f "tokens=2 delims=$" %%T in ("!SVCNAME!") do (
        set "FOUND=1"
        echo        - !SVCNAME!
        if exist "%WG%" ("%WG%" /uninstalltunnelservice "%%T" >nul 2>&1) else (sc delete "!SVCNAME!" >nul 2>&1)
    )
)
if not defined FOUND echo        ไม่มี tunnel ค้างอยู่
"%SYS%\ping.exe" -n 4 127.0.0.1 >nul
if exist "%CONF_DIR%\_active" rd /s /q "%CONF_DIR%\_active" >nul 2>&1

echo.
echo  [3/4] ลบช็อตคัตบนเดสก์ท็อป ...
for %%L in ("VPN Toggle.lnk" "VPN - Surfshark.lnk") do (
    if exist "%USERPROFILE%\Desktop\%%~L" (
        del /q "%USERPROFILE%\Desktop\%%~L" >nul 2>&1
        echo        - ลบ %%~L
    )
)

echo.
echo  [4/4] จะลบไฟล์ config และบัญชีที่เก็บไว้ด้วยไหม?
echo        โฟลเดอร์: %CONF_DIR%
echo        ^(ถ้าลบ ต้องโหลด .conf ใหม่ตอนติดตั้งรอบหน้า^)
echo.
set "ANS="
set /p "ANS=        พิมพ์ y แล้วกด Enter เพื่อลบ / กด Enter เฉยๆ เพื่อเก็บไว้: "
if /i "!ANS!"=="y" (
    if exist "%CONF_DIR%" rd /s /q "%CONF_DIR%" >nul 2>&1
    echo        [OK] ลบแล้ว
) else (
    echo        เก็บไว้ตามเดิม
)

echo.
echo  ==============================================================
echo    เรียบร้อย - VPN ไม่ต่ออัตโนมัติแล้ว
echo  ==============================================================
for /f "delims=" %%I in ('"%SYS%\curl.exe" -s --max-time 8 https://ipinfo.io/ip 2^>nul') do echo    IP ตอนนี้   : %%I
echo.
pause
endlocal
