@echo off
title Cloudflare Tunnel - Remote File Manager
cd /d "%~dp0"

:: ====== เปิด server (พอร์ต 5000) ออกอินเทอร์เน็ตด้วย Cloudflare Tunnel ======
:: วิธีใช้: เปิด start_server.bat ก่อน (server รันที่ :5000) แล้วค่อยเปิดไฟล์นี้
:: ==============================================================================

set "CF_EXE=%~dp0cloudflared.exe"
set "PORT=5000"
set "CF_URL=https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

:: ----- อ่าน Token จากไฟล์ cloudflare-token.txt (ถ้ามี) -----
set "CF_TOKEN="
if exist "cloudflare-token.txt" set /p CF_TOKEN=<cloudflare-token.txt

:: ----- [1/2] ดาวน์โหลด cloudflared ถ้ายังไม่มี (ครั้งเดียว) -----
if not exist "%CF_EXE%" (
    echo [1/2] Downloading cloudflared ...
    curl -k -L --retry 3 --connect-timeout 15 "%CF_URL%" -o "%CF_EXE%"
    if not exist "%CF_EXE%" (
        echo [ERROR] ดาวน์โหลด cloudflared ไม่ได้ ตรวจอินเทอร์เน็ตแล้วลองใหม่
        pause
        exit /b 1
    )
    echo     [OK] ได้ cloudflared.exe แล้ว
) else (
    echo [1/2] มี cloudflared.exe อยู่แล้ว - ข้าม
)

:: ----- [2/2] เปิด tunnel -----
echo.
echo ================================================================
echo   Cloudflare Tunnel - Remote File Manager
echo   * ต้องเปิด start_server.bat ไว้ก่อน (server ที่ localhost:%PORT%)
echo.

if not "%CF_TOKEN%"=="" (
    echo   * โหมด: Fixed Domain (ใช้ Tunnel Token จาก cloudflare-token.txt)
    echo   * รันคำสั่ง: cloudflared tunnel run
    echo ================================================================
    "%CF_EXE%" tunnel run --token %CF_TOKEN%
) else (
    echo   * โหมด: Quick Tunnel (URL สุ่ม ไม่ต้องใช้ Token)
    echo   * มองหาบรรทัดที่เขียนว่า:  https://xxxx-xxxx-xxxx.trycloudflare.com
    echo   * เอา URL นั้นส่งให้คนอื่นเปิดในเบราว์เซอร์ได้เลย
    echo   * อย่าปิดหน้าต่างนี้ (ปิด = tunnel ดับ URL ใช้ไม่ได้)
    echo ================================================================
    "%CF_EXE%" tunnel --url http://localhost:%PORT%
)

echo.
echo ================================================================
echo   (tunnel ปิดแล้ว หรือเกิดข้อผิดพลาด)
echo.
echo   หมายเหตุ: 
echo   - หากใส่ Token แล้ว Error อาจเป็นเพราะใช้ Token ผิดประเภท
echo   - Tunnel Token ต้องเอามาจากหน้า Cloudflare Zero Trust (ขึ้นต้นด้วย ey...)
echo   - API Token (ขึ้นต้นด้วย cfut_) จะใช้เปิด Tunnel โดยตรงไม่ได้
echo ================================================================
pause
