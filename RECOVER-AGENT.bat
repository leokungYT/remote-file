@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title Recover Agent - kill stuck python + flush DNS + git pull + restart

echo ============================================================
echo   RECOVER-AGENT: กู้ agent ที่หลุด/ค้าง
echo ============================================================
echo.

cd /d "%~dp0"

echo [1/6] ฆ่า python.exe + pythonw.exe (agent + swap_bot ค้าง)...
taskkill /F /IM python.exe >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/6] ลบ ExpressVPN kill switch + flush DNS...
taskkill /F /IM ExpressVPN.exe >nul 2>&1
timeout /t 2 /nobreak >nul
ipconfig /flushdns >nul 2>&1

echo [3/6] reset DNS เป็น DHCP + Cloudflare ชั่วคราว...
powershell -NoProfile -Command "Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses -ErrorAction SilentlyContinue }" >nul 2>&1
ipconfig /flushdns >nul 2>&1

echo [4/6] ปลุก Tailscale...
if exist "C:\Program Files\Tailscale\tailscale.exe" (
    "C:\Program Files\Tailscale\tailscale.exe" status >nul 2>&1
)
ping -n 2 -w 1000 login.tailscale.com >nul 2>&1

echo [5/6] git pull remote-file (ถ้าเป็น git repo)...
if exist ".git" (
    git pull origin main
) else (
    echo [SKIP] not a git repo
)

echo [6/6] start agent.py + swap_bot.py (ถ้ามี)...
if exist "agent.py" (
    start "" pythonw "%~dp0agent.py"
    echo [OK] agent.py started
) else (
    echo [WARN] ไม่พบ agent.py
)
set "SB=%USERPROFILE%\Desktop\main\swap_bot\swap_bot.py"
if exist "!SB!" (
    start "" /D "%USERPROFILE%\Desktop\main\swap_bot" pythonw "!SB!"
    echo [OK] swap_bot.py started
)

echo.
echo ============================================================
echo   เสร็จ - ให้ไปเช็ค dashboard ว่าเครื่องนี้ online หรือยัง (10-30s)
echo ============================================================
pause
