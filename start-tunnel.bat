@echo off
title Cloudflare Tunnel - Remote File Manager
cd /d "%~dp0"

:: ====== เปิด server (พอร์ต 5000) ให้เข้าถึงจากภายนอกได้ โดยไม่ต้องลง VPN ที่เครื่องลูก ======
:: วิธีใช้: เปิด start_server.bat ก่อน (server รันที่ :5000) แล้วค่อยเปิดไฟล์นี้
:: จะได้ URL แบบ https://xxxxx.trycloudflare.com -> เอาไปใส่เป็น SERVER_URL ที่เครื่องลูก
:: ==========================================================================================

set "CF=cloudflared.exe"
set "PORT=5000"
set "CF_URL=https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"

:: ----- ดาวน์โหลด cloudflared ถ้ายังไม่มี (ครั้งเดียว) -----
if not exist "%CF%" (
    echo [1/2] Downloading cloudflared ...
    curl -k -L --retry 3 --connect-timeout 15 "%CF_URL%" -o "%CF%"
    if not exist "%CF%" (
        echo [ERROR] ดาวน์โหลด cloudflared ไม่ได้ ตรวจอินเทอร์เน็ตแล้วลองใหม่
        pause
        exit /b 1
    )
)

echo.
echo ================================================================
echo   Cloudflare Tunnel - Remote File Manager
echo   * ต้องเปิด start_server.bat ไว้ก่อน (server ที่ localhost:%PORT%)
echo   * มองหาบรรทัด URL:  https://xxxxx.trycloudflare.com
echo   * เอา URL นั้นไปตั้งเป็น SERVER_URL ที่เครื่องลูก
echo   * อย่าปิดหน้าต่างนี้ (ปิด = tunnel ดับ)
echo ================================================================
echo.

:: ----- เปิด quick tunnel (ไม่ต้องมีบัญชี) -----
"%CF%" tunnel --url http://localhost:%PORT%

echo.
echo (tunnel ปิดแล้ว)
pause
