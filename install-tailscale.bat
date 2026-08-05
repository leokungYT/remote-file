@echo off
title Install Tailscale VPN (unattended) - Remote File Agent
cd /d "%~dp0"

:: ================================================================
::  ลง Tailscale + เข้า network อัตโนมัติ (ไม่ต้องล็อกอินทีละเครื่อง)
::  ทำครั้งเดียวต่อเครื่อง แล้วเครื่องจะได้ IP คงที่ (100.x.x.x)
::
::  วิธีเตรียม:
::   1) ไปที่  https://login.tailscale.com/admin/settings/keys
::   2) สร้าง "Auth key" แบบ Reusable (ติ๊ก Reusable) แล้วก็อปมา
::   3) วางแทน  PASTE_YOUR_AUTHKEY_HERE  ด้านล่าง
::   4) รันไฟล์นี้ที่เครื่องลูก (คลิกขวา > Run as administrator)
:: ================================================================

set "AUTHKEY=PASTE_YOUR_AUTHKEY_HERE"

if "%AUTHKEY%"=="PASTE_YOUR_AUTHKEY_HERE" (
    echo [ERROR] ยังไม่ได้ใส่ AUTHKEY - เปิดไฟล์นี้แล้วแก้บรรทัด set "AUTHKEY=..." ก่อน
    pause
    exit /b 1
)

:: --- ต้องเป็น admin ---
net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] กรุณารันไฟล์นี้แบบ Run as administrator
    pause
    exit /b 1
)

:: --- ลง Tailscale ถ้ายังไม่มี ---
where tailscale >nul 2>&1
if %errorlevel%==0 goto joinnet

echo [1/2] Installing Tailscale ...
:: ลองผ่าน winget ก่อน (เงียบ) - Win10 1809+/Win11
winget install --id tailscale.tailscale -e --silent --accept-package-agreements --accept-source-agreements >nul 2>&1
where tailscale >nul 2>&1
if %errorlevel%==0 goto joinnet

:: fallback: ดาวน์โหลด installer มารันเอง
echo     (winget ใช้ไม่ได้ - ดาวน์โหลด installer แทน)
curl -k -L --retry 3 --connect-timeout 15 "https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe" -o "tailscale-setup.exe"
if not exist "tailscale-setup.exe" (
    echo [ERROR] ดาวน์โหลด Tailscale ไม่ได้
    pause
    exit /b 1
)
start /wait "" "tailscale-setup.exe" /S
del /q "tailscale-setup.exe" >nul 2>&1

:joinnet
echo [2/2] Joining Tailscale network (unattended) ...
tailscale up --authkey %AUTHKEY% --unattended
if errorlevel 1 (
    echo [ERROR] เข้า network ไม่สำเร็จ - ตรวจ AUTHKEY ว่ายังไม่หมดอายุ
    pause
    exit /b 1
)

echo.
echo [OK] เชื่อม Tailscale แล้ว - IP ของเครื่องนี้:
tailscale ip -4
echo.
echo    * เอา IP ของเครื่อง "server" (100.x.x.x) ไปตั้งเป็น SERVER_URL ที่เครื่องลูก
echo      เช่น  http://100.x.x.x:5000
echo.
pause
