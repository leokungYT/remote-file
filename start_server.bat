@echo off
title Remote File Manager - Server
cd /d "%~dp0"

echo ===============================================
echo   Remote File Manager - Server
echo ===============================================
echo.

:: ----- CONFIG (edit here) -----------------------
:: Secret key for web session
set SECRET_KEY=532662739b6ba4299fea8253dbfbcad3

:: Shared secret for agents (must match child machines)
set AGENT_SECRET=2ec990f60382a004d664f06f99a3e7f5

:: Web UI Password (รหัสผ่านเข้าเว็บ เปลี่ยนตรงนี้ได้เลย)
set WEB_PASSWORD=nuuboyshop

:: Port
set SERVER_PORT=5000
:: ------------------------------------------------

echo   Port        : %SERVER_PORT%
echo   Agent Secret: %AGENT_SECRET%
echo.

:: ===== [1/5] auto-update server.py from GitHub (main) =====
echo [1/5] Skipping auto-update (using local server.py to keep our login changes)...
goto safter
:sbad
:sskip
:safter

:: ===== [2/5] agent.py — ใช้ไฟล์ในเครื่อง (ปิดการดึงทับจาก GitHub) =====
:: เดิมบรรทัดนี้ curl agent.py จาก GitHub มาเขียนทับทุกครั้งที่เปิด server
:: ทำให้โค้ดใหม่ในเครื่อง (clone MuMu / รันไฟล์ .bat) หายไป แล้วเครื่องลูกที่ดึง
:: /agent.py ไปอัปเดตได้แต่ของเก่า → ขึ้น "Unknown action: run_file"
:: ถ้าจะกลับไปดึงจาก GitHub ให้ push โค้ดในเครื่องขึ้น GitHub ก่อน แล้วค่อยเปิดกลับ
echo [2/5] Skipping agent.py auto-update (using local agent.py)...
goto aafter
:abad
:askip
:aafter
echo.

:: ===== [3/5] Check Python =====
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python first.
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

:: ===== [4/5] Install dependencies + open firewall =====
echo [4/5] Checking dependencies + firewall...
pip install flask flask-socketio >nul 2>&1
netsh advfirewall firewall add rule name="RemoteFileManager" dir=in action=allow protocol=TCP localport=%SERVER_PORT% >nul 2>&1

:: ===== ensure Tailscale Serve + Funnel is up =====
:: agents connect via https://server.tail8db58a.ts.net (works on Tailscale AND under VPN)
:: Funnel must stay on; these commands are idempotent (safe to re-run each start)
set "TSEXE=C:\Program Files\Tailscale\tailscale.exe"
if exist "%TSEXE%" (
    echo Ensuring Tailscale Serve/Funnel is up ...
    "%TSEXE%" serve --bg %SERVER_PORT% >nul 2>&1
    "%TSEXE%" funnel --bg %SERVER_PORT% >nul 2>&1
)

:: ===== [5/5] Run server =====
echo [5/5] Starting server on http://0.0.0.0:%SERVER_PORT%
echo.
python server.py

pause
