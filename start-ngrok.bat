@echo off
title ngrok Tunnel - Remote File Manager
cd /d "%~dp0"

:: ====== เปิด server (พอร์ต 5000) ออกอินเทอร์เน็ตด้วย ngrok ======
:: วิธีใช้: เปิด start_server.bat ก่อน (server รันที่ :5000) แล้วค่อยเปิดไฟล์นี้
:: ครั้งแรกต้องมี authtoken จาก https://dashboard.ngrok.com/get-started/your-authtoken
:: ใส่ token ตรง NGROK_TOKEN ข้างล่าง (ใส่ครั้งเดียว ครั้งต่อไปไม่ต้องใส่อีก)
:: ==================================================================

:: ----- ใส่ authtoken ของคุณตรงนี้ (ครั้งแรกครั้งเดียว) -----
set "NGROK_TOKEN="
:: ------------------------------------------------------------

set "NG=ngrok.exe"
set "PORT=5000"
set "ZIP=ngrok.zip"
set "NG_URL=https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"

:: ----- [1/3] ดาวน์โหลด ngrok ถ้ายังไม่มี (ครั้งเดียว) -----
if not exist "%NG%" (
    echo [1/3] Downloading ngrok ...
    curl -k -L --retry 3 --connect-timeout 15 "%NG_URL%" -o "%ZIP%"
    if not exist "%ZIP%" (
        echo [ERROR] ดาวน์โหลด ngrok ไม่ได้ ตรวจอินเทอร์เน็ตแล้วลองใหม่
        pause
        exit /b 1
    )
    powershell -NoProfile -Command "try { Expand-Archive -Path '%ZIP%' -DestinationPath '.' -Force } catch { exit 1 }"
    del /q "%ZIP%" >nul 2>&1
    if not exist "%NG%" (
        echo [ERROR] แตกไฟล์ ngrok ไม่สำเร็จ
        pause
        exit /b 1
    )
    echo     [OK] ได้ ngrok.exe แล้ว
) else (
    echo [1/3] มี ngrok.exe อยู่แล้ว - ข้าม
)

:: ----- [2/3] ใส่ authtoken (ถ้ากรอกไว้ข้างบน) -----
if not "%NGROK_TOKEN%"=="" (
    echo [2/3] Saving authtoken ...
    "%NG%" config add-authtoken %NGROK_TOKEN%
) else (
    echo [2/3] ไม่ได้กรอก NGROK_TOKEN ในไฟล์นี้ - ใช้ token ที่เคยบันทึกไว้
)

:: ----- [3/3] เปิด tunnel -----
echo.
echo ================================================================
echo   ngrok Tunnel - Remote File Manager
echo   * ต้องเปิด start_server.bat ไว้ก่อน (server ที่ localhost:%PORT%)
echo   * มองหาบรรทัด Forwarding:  https://xxxx-xx-xx.ngrok-free.app
echo   * เอา URL นั้นส่งให้คนอื่นเปิดในเบราว์เซอร์ได้เลย
echo   * ครั้งแรกที่เปิดจะเจอหน้าเตือนของ ngrok ให้กด "Visit Site"
echo   * อย่าปิดหน้าต่างนี้ (ปิด = tunnel ดับ URL ใช้ไม่ได้)
echo   * URL จะเปลี่ยนทุกครั้งที่เปิดใหม่ (แผนฟรี)
echo ================================================================
echo.

"%NG%" http %PORT%

echo.
echo (tunnel ปิดแล้ว)
pause
