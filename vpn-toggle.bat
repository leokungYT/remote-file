@echo off
chcp 65001 >nul
title VPN Toggle - Surfshark (WireGuard)
setlocal EnableExtensions EnableDelayedExpansion

:: ============================================================================
::  เปิด/ปิด VPN  (Surfshark ผ่าน WireGuard)
::
::  ดับเบิลคลิก           = สลับสถานะ (ต่ออยู่ -> ตัด / ยังไม่ต่อ -> ต่อ)
::  vpn-toggle.bat th-bkk = สลับสถานะเซิร์ฟเวอร์ที่ระบุ
::  vpn-toggle.bat /auto  = ต่อให้แน่ใจว่าติด (เงียบ) - ใช้โดย Task Scheduler
::
::  ไฟล์ .conf เก็บที่ %LOCALAPPDATA%\SurfsharkVPN  (มี private key - อย่าขึ้น git)
::  ติดตั้งครั้งแรกด้วย install-vpn.bat
:: ============================================================================

:: ---------------------------------------------------------------------------
::  KEEP_LAN=1 -> วงแลน (10.x / 172.16-31.x / 192.168.x) ยังเข้าได้
::                remote-file server ที่ :5000 ใช้ผ่านวงแลนได้ตามปกติ
::  KEEP_LAN=0 -> ดูดทราฟฟิกทั้งหมดเข้า VPN
:: ---------------------------------------------------------------------------
set "KEEP_LAN=1"

set "WG=%ProgramFiles%\WireGuard\wireguard.exe"
if defined VPN_CONF_DIR (set "CONF_DIR=%VPN_CONF_DIR%") else (set "CONF_DIR=%LOCALAPPDATA%\SurfsharkVPN")
set "ACTIVE_DIR=%CONF_DIR%\_active"

:: เรียกเครื่องมือจาก System32 เต็ม path - กัน Git Bash/MSYS ใน PATH มาทับ find/ping/curl
set "SYS=%SystemRoot%\System32"

:: 0.0.0.0/0 ที่หักช่วง private ออก (RFC1918) - ให้วงแลนวิ่งนอก tunnel
set "LAN_SAFE_IPS=0.0.0.0/5, 8.0.0.0/7, 11.0.0.0/8, 12.0.0.0/6, 16.0.0.0/4, 32.0.0.0/3, 64.0.0.0/2, 128.0.0.0/3, 160.0.0.0/5, 168.0.0.0/6, 172.0.0.0/12, 172.32.0.0/11, 172.64.0.0/10, 172.128.0.0/9, 173.0.0.0/8, 174.0.0.0/7, 176.0.0.0/4, 192.0.0.0/9, 192.128.0.0/11, 192.160.0.0/13, 192.169.0.0/16, 192.170.0.0/15, 192.172.0.0/14, 192.176.0.0/12, 192.192.0.0/10, 193.0.0.0/8, 194.0.0.0/7, 196.0.0.0/6, 200.0.0.0/5, 208.0.0.0/4"

:: ----- อ่าน argument -----
set "SILENT="
set "ENSURE="
set "WANT="
if /i "%~1"=="/auto" (
    set "SILENT=1"
    set "ENSURE=1"
    set "WANT=%~2"
) else (
    set "WANT=%~1"
)

:: ----- [1/4] ขอสิทธิ์ Administrator (WireGuard ต้องใช้ตอนสร้าง tunnel service) -----
net session >nul 2>&1
if errorlevel 1 (
    if defined SILENT exit /b 2
    echo [1/4] ขอสิทธิ์ Administrator ...
    if "%~1"=="" (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    ) else (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%~1' -Verb RunAs"
    )
    exit /b
)

:: ----- [2/4] ตรวจว่าติดตั้ง WireGuard แล้วหรือยัง -----
if not exist "%WG%" (
    if defined SILENT exit /b 3
    echo [ERROR] ไม่พบ WireGuard ที่ "%WG%"
    echo         รัน install-vpn.bat เพื่อติดตั้งให้อัตโนมัติ
    pause
    exit /b 1
)

:: ----- [3/4] หาไฟล์ .conf ที่จะใช้ -----
if not exist "%CONF_DIR%" mkdir "%CONF_DIR%" 2>nul

set "TUNNEL="
if not "%WANT%"=="" (
    set "TUNNEL=%WANT%"
    if not exist "%CONF_DIR%\!TUNNEL!.conf" (
        if defined SILENT exit /b 4
        echo [ERROR] ไม่พบไฟล์ "%CONF_DIR%\!TUNNEL!.conf"
        echo.
        echo         ไฟล์ที่มีอยู่:
        dir /b "%CONF_DIR%\*.conf" 2>nul || echo         ^(ไม่มีเลย^)
        pause
        exit /b 1
    )
) else (
    if exist "%CONF_DIR%\preferred.txt" (
        set /p TUNNEL=<"%CONF_DIR%\preferred.txt"
        if not exist "%CONF_DIR%\!TUNNEL!.conf" set "TUNNEL="
    )
    if not defined TUNNEL (
        for /f "delims=" %%F in ('dir /b /on "%CONF_DIR%\*.conf" 2^>nul') do (
            if not defined TUNNEL set "TUNNEL=%%~nF"
        )
    )
)

if not defined TUNNEL (
    if defined SILENT exit /b 4
    goto NOCONF
)

set "SVC=WireGuardTunnel$%TUNNEL%"

:: ----- [4/4] ตัดสินใจ -----
::   /auto : ต่ออยู่แล้ว -> จบ  / ไม่ได้ต่อ -> ต่อ
::   ปกติ  : ต่ออยู่แล้ว -> ตัด / ไม่ได้ต่อ -> ต่อ
sc query "%SVC%" >nul 2>&1
if errorlevel 1 goto CONNECT

sc query "%SVC%" | "%SYS%\find.exe" "RUNNING" >nul 2>&1
if not errorlevel 1 (
    if defined ENSURE (
        echo   [OK] VPN ต่ออยู่แล้ว  [%TUNNEL%]
        exit /b 0
    )
    goto DISCONNECT
)

:: service มีแต่ไม่ทำงาน - ลองสตาร์ตก่อน ถ้าไม่ขึ้นค่อยล้างแล้วสร้างใหม่
if not defined SILENT echo   [!] tunnel ไม่ทำงาน - กำลังสตาร์ตใหม่ ...
sc start "%SVC%" >nul 2>&1
"%SYS%\ping.exe" -n 4 127.0.0.1 >nul
sc query "%SVC%" | "%SYS%\find.exe" "RUNNING" >nul 2>&1
if not errorlevel 1 (
    if defined SILENT exit /b 0
    echo   [OK] VPN กลับมาแล้ว
    call :SHOWIP
    goto END
)
if not defined SILENT echo   [!] สตาร์ตไม่ขึ้น - ล้างของค้างแล้วสร้าง tunnel ใหม่
"%WG%" /uninstalltunnelservice "%TUNNEL%" >nul 2>&1
"%SYS%\ping.exe" -n 4 127.0.0.1 >nul
goto CONNECT


:CONNECT
if not defined SILENT (
    echo.
    echo ================================================================
    echo   กำลังเปิด VPN  ...  [%TUNNEL%]
    if "%KEEP_LAN%"=="1" (
        echo   โหมด: เว้นวงแลนไว้  ^(server ที่ 192.168.x.x ยังเข้าได้^)
    ) else (
        echo   โหมด: ดูดทราฟฟิกทั้งหมด  ^(วงแลนจะเข้าไม่ได้^)
    )
    echo ================================================================
)

set "USE_CONF=%CONF_DIR%\%TUNNEL%.conf"

if "%KEEP_LAN%"=="1" (
    if not exist "%ACTIVE_DIR%" mkdir "%ACTIVE_DIR%" 2>nul
    :: สร้างสำเนาที่แก้ AllowedIPs ใหม่ทุกครั้ง - ต้นฉบับไม่ถูกแตะ
    :: ถ้าต้นฉบับมี ::/0 จะคง ::/0 ไว้ ไม่ให้ IPv6 รั่วออกนอก tunnel
    if exist "%ACTIVE_DIR%\%TUNNEL%.conf" del /q "%ACTIVE_DIR%\%TUNNEL%.conf" >nul 2>&1
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = Get-Content -LiteralPath '%CONF_DIR%\%TUNNEL%.conf'; $r = '%LAN_SAFE_IPS%'; if ($c -match '::/0') { $r = $r + ', ::/0' }; ($c -replace '^\s*AllowedIPs\s*=.*', ('AllowedIPs = ' + $r)) | Set-Content -LiteralPath '%ACTIVE_DIR%\%TUNNEL%.conf' -Encoding ascii"
    if not exist "%ACTIVE_DIR%\%TUNNEL%.conf" (
        if defined SILENT exit /b 5
        echo [ERROR] สร้าง config แบบเว้นวงแลนไม่สำเร็จ - ลองตั้ง KEEP_LAN=0 ในไฟล์นี้
        pause
        exit /b 1
    )
    set "USE_CONF=%ACTIVE_DIR%\%TUNNEL%.conf"
)

if defined SILENT (
    "%WG%" /installtunnelservice "!USE_CONF!" >nul 2>&1
) else (
    "%WG%" /installtunnelservice "!USE_CONF!"
)
if errorlevel 1 (
    if defined SILENT exit /b 6
    echo.
    echo [ERROR] เปิด tunnel ไม่สำเร็จ - ดู log ที่แอป WireGuard หรือ Event Viewer
    pause
    exit /b 1
)
"%SYS%\ping.exe" -n 6 127.0.0.1 >nul

sc query "%SVC%" | "%SYS%\find.exe" "RUNNING" >nul 2>&1
if errorlevel 1 (
    "%WG%" /uninstalltunnelservice "%TUNNEL%" >nul 2>&1
    if defined SILENT exit /b 7
    echo.
    echo [ERROR] tunnel service ไม่ได้ทำงาน - config อาจหมดอายุ ลองโหลด .conf ใหม่
    echo         ล้างของค้างให้แล้ว
    pause
    exit /b 1
)

:: ให้ WireGuard ยก tunnel เองตอนเปิดเครื่อง
sc config "%SVC%" start= auto >nul 2>&1

if defined SILENT exit /b 0
echo.
echo   [OK] VPN เปิดแล้ว
call :SHOWIP
goto END


:DISCONNECT
echo.
echo ================================================================
echo   กำลังปิด VPN  ...  [%TUNNEL%]
echo ================================================================
"%WG%" /uninstalltunnelservice "%TUNNEL%"
"%SYS%\ping.exe" -n 4 127.0.0.1 >nul
if exist "%ACTIVE_DIR%\%TUNNEL%.conf" del /q "%ACTIVE_DIR%\%TUNNEL%.conf" >nul 2>&1
echo.
echo   [OK] VPN ปิดแล้ว - กลับมาใช้เน็ตปกติ
echo   * ถ้าตั้ง auto-connect ไว้ มันจะต่อกลับตอนเข้าเครื่องครั้งหน้า
call :SHOWIP
goto END


:NOCONF
echo.
echo ================================================================
echo   ยังไม่มีไฟล์ config
echo ================================================================
echo.
echo   รัน  install-vpn.bat  แทน - มันจะพาทำทีละขั้นจนต่อ VPN ได้เลย
echo ================================================================
"%SYS%\timeout.exe" /t 10 2>nul || "%SYS%\ping.exe" -n 11 127.0.0.1 >nul
exit /b 1


:SHOWIP
echo.
echo   ---------------------------------------------
for /f "delims=" %%I in ('"%SYS%\curl.exe" -s --max-time 8 https://ipinfo.io/ip 2^>nul') do echo    IP ตอนนี้    : %%I
for /f "delims=" %%C in ('"%SYS%\curl.exe" -s --max-time 8 https://ipinfo.io/country 2^>nul') do echo    ประเทศ      : %%C
echo   ---------------------------------------------
exit /b 0


:END
echo.
"%SYS%\timeout.exe" /t 6 2>nul || "%SYS%\ping.exe" -n 7 127.0.0.1 >nul
endlocal
