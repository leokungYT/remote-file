@echo off
cd /d "%~dp0"
echo ================================================
echo    ตั้งเครื่องแม่ (server) ที่ agent จะเกาะ
echo ================================================
echo    ตัวอย่าง IP ที่ใส่:
echo      192.168.1.121   = IP วง LAN ของเครื่องแม่  (รอด WARP)
echo      100.80.76.47    = Tailscale ของเครื่องแม่
echo.
set /p "SRVIP=พิมพ์ IP เครื่องแม่ แล้วกด Enter: "
if "%SRVIP%"=="" echo (ยกเลิก - ไม่ได้ใส่ IP) & pause & exit /b

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set-server.ps1" -ip "%SRVIP%"

echo.
echo กำลังรีสตาร์ท agent ให้ใช้ค่าใหม่...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*agent.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
start "" pythonw agent.py

echo.
echo [OK] เสร็จ! agent เกาะ http://%SRVIP%:5000 แล้ว
echo      (ยังมี LAN auto-discovery + Funnel เป็นตัวสำรองอัตโนมัติด้วย)
echo      ถ้า IP เป็นวง LAN -^> เปิด WARP ก็ยังเกาะได้
pause
