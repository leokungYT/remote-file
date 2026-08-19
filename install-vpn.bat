@echo off
chcp 65001 >nul
title ติดตั้ง VPN อัตโนมัติ - Surfshark
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

:: ============================================================================
::  ตัวติดตั้ง VPN แบบคลิกเดียว  (Surfshark ผ่าน WireGuard)
::
::  ดับเบิลคลิกไฟล์นี้ครั้งเดียว แล้วมันจะ:
::    1. ติดตั้ง WireGuard ให้ (ถ้ายังไม่มี)
::    2. เก็บอีเมล/รหัสผ่าน Surfshark แบบเข้ารหัส DPAPI
::    3. คัดลอกรหัสผ่านให้ + เปิดหน้าโหลด config ให้ (ล็อกอินครั้งเดียวจบ)
::    4. ต่อ VPN ทันทีที่ได้ไฟล์ config
::    5. ตั้งให้ต่อ VPN เองทุกครั้งที่เปิดเครื่อง + คอยเช็คทุก 5 นาที
::    6. สร้างช็อตคัตเปิด/ปิด VPN บนเดสก์ท็อป
::
::  รันซ้ำได้เรื่อยๆ ไม่พัง (idempotent)
:: ============================================================================

set "SYS=%SystemRoot%\System32"
set "WG=%ProgramFiles%\WireGuard\wireguard.exe"
set "CONF_DIR=%LOCALAPPDATA%\SurfsharkVPN"
set "SETUP=%~dp0vpn-setup.ps1"
set "TOGGLE=%~dp0vpn-toggle.bat"
set "PSX=powershell -NoProfile -ExecutionPolicy Bypass -File"

echo.
echo  ==============================================================
echo    ติดตั้ง VPN อัตโนมัติ  -  Surfshark + WireGuard
echo  ==============================================================

:: ----- ขอสิทธิ์ Administrator -----
net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ขอสิทธิ์ Administrator ... กด "Yes" ในหน้าต่างที่เด้งขึ้นมา
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: ----- ตรวจไฟล์ประกอบว่าครบ -----
for %%F in ("%SETUP%" "%TOGGLE%" "%~dp0vpn-silent.vbs") do (
    if not exist "%%~F" (
        echo.
        echo  [ERROR] ไม่พบไฟล์ "%%~nxF" - ต้องอยู่โฟลเดอร์เดียวกับ install-vpn.bat
        pause
        exit /b 1
    )
)

:: =====================================================================
echo.
echo  [1/6] ตรวจ WireGuard ...
:: =====================================================================
if exist "%WG%" (
    echo        มีอยู่แล้ว - ข้าม
) else (
    echo        ยังไม่มี - กำลังติดตั้งผ่าน winget ^(รอสักครู่^) ...
    winget install --id WireGuard.WireGuard --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity >nul 2>&1
    if not exist "%WG%" (
        echo.
        echo  [ERROR] ติดตั้ง WireGuard ไม่สำเร็จ
        echo          โหลดเองได้ที่ https://www.wireguard.com/install/
        pause
        exit /b 1
    )
    echo        [OK] ติดตั้งแล้ว
)

:: =====================================================================
echo.
echo  [2/6] ตรวจบัญชี Surfshark ที่เก็บไว้ ...
:: =====================================================================
if not exist "%CONF_DIR%" mkdir "%CONF_DIR%" 2>nul

set "HASCRED="
for /f "delims=" %%A in ('%PSX% "%SETUP%" -Action HasCred 2^>nul') do set "HASCRED=%%A"

if /i "!HASCRED!"=="YES" (
    set "ACCEMAIL="
    for /f "delims=" %%A in ('%PSX% "%SETUP%" -Action ShowEmail 2^>nul') do set "ACCEMAIL=%%A"
    echo        มีอยู่แล้ว: !ACCEMAIL!
) else (
    echo        ยังไม่มี - ใส่บัญชี Surfshark ^(รหัสผ่านจะไม่แสดงบนจอ^)
    echo.
    %PSX% "%SETUP%" -Action SaveCred >nul
    if errorlevel 1 (
        echo  [ERROR] เก็บบัญชีไม่สำเร็จ
        pause
        exit /b 1
    )
    echo        [OK] เก็บแบบเข้ารหัสแล้ว ^(DPAPI - เครื่องอื่นถอดไม่ได้^)
)

:: =====================================================================
echo.
echo  [3/6] ตรวจไฟล์ config ของ WireGuard ...
:: =====================================================================
dir /b "%CONF_DIR%\*.conf" >nul 2>&1
if not errorlevel 1 goto HAVECONF

echo        ยังไม่มี - ต้องโหลดจากเว็บครั้งเดียว
echo.
%PSX% "%SETUP%" -Action CopyPass >nul 2>&1
set "ACCEMAIL="
for /f "delims=" %%A in ('%PSX% "%SETUP%" -Action ShowEmail 2^>nul') do set "ACCEMAIL=%%A"

echo  --------------------------------------------------------------
echo    กำลังเปิดหน้าเว็บ Surfshark ให้ ...
echo.
echo    อีเมล        : !ACCEMAIL!
echo    รหัสผ่าน     : คัดลอกใส่คลิปบอร์ดให้แล้ว - กด Ctrl+V ได้เลย
echo.
echo    ทำตามนี้:
echo      1. ล็อกอินด้วยอีเมล/รหัสผ่านข้างบน
echo      2. หน้า Manual setup ^> WireGuard  ^>  กด "I don't have a key pair"
echo      3. กด Generate new key pair
echo      4. เลือกประเทศที่ต้องการ  ^>  กด Download
echo      5. ลากไฟล์ .conf ที่โหลดมา มาวางในโฟลเดอร์ที่เปิดค้างไว้
echo.
echo    (หน้าต่างนี้จะรอจนกว่าจะเจอไฟล์ - ไม่ต้องปิด)
echo  --------------------------------------------------------------

:: ใช้ explorer.exe เปิด เพื่อให้เบราว์เซอร์รันสิทธิ์ปกติ ไม่ใช่ Administrator
"%SYS%\..\explorer.exe" "%CONF_DIR%"
"%SYS%\..\explorer.exe" "https://my.surfshark.com/vpn/manual-setup/main/wireguard"

set /a WAITED=0
:WAITLOOP
dir /b "%CONF_DIR%\*.conf" >nul 2>&1
if not errorlevel 1 goto GOTCONF
"%SYS%\ping.exe" -n 6 127.0.0.1 >nul
set /a WAITED+=5
if !WAITED! GEQ 900 goto WAITTIMEOUT
<nul set /p "=."
goto WAITLOOP

:WAITTIMEOUT
echo.
echo.
echo  [!] รอ 15 นาทีแล้วยังไม่เจอไฟล์ .conf
echo      เอาไฟล์ไปวางที่ "%CONF_DIR%" แล้วรัน install-vpn.bat ใหม่อีกรอบ
%PSX% "%SETUP%" -Action ClearClip >nul 2>&1
pause
exit /b 1

:GOTCONF
echo.
echo        [OK] เจอไฟล์ config แล้ว
%PSX% "%SETUP%" -Action ClearClip >nul 2>&1

:HAVECONF
:: เลือกเซิร์ฟเวอร์: ใช้ preferred.txt ถ้ามีและยังใช้ได้ ไม่งั้นเอาไฟล์แรก
set "TUNNEL="
if exist "%CONF_DIR%\preferred.txt" (
    set /p TUNNEL=<"%CONF_DIR%\preferred.txt"
    if not exist "%CONF_DIR%\!TUNNEL!.conf" set "TUNNEL="
)
if not defined TUNNEL (
    for /f "delims=" %%F in ('dir /b /on "%CONF_DIR%\*.conf" 2^>nul') do (
        if not defined TUNNEL set "TUNNEL=%%~nF"
    )
)
> "%CONF_DIR%\preferred.txt" echo !TUNNEL!
echo        เซิร์ฟเวอร์ที่จะใช้: !TUNNEL!

:: =====================================================================
echo.
echo  [4/6] ต่อ VPN ...
:: =====================================================================
call "%TOGGLE%" /auto "!TUNNEL!"
set "RC=!errorlevel!"

if "!RC!"=="0" (
    echo        [OK] ต่อแล้ว
) else (
    echo.
    if "!RC!"=="4" echo  [ERROR] หาไฟล์ config ไม่เจอ
    if "!RC!"=="5" echo  [ERROR] แก้ config แบบเว้นวงแลนไม่สำเร็จ
    if "!RC!"=="6" echo  [ERROR] WireGuard สร้าง tunnel ไม่สำเร็จ
    if "!RC!"=="7" echo  [ERROR] tunnel ขึ้นแล้วแต่ไม่ทำงาน - config อาจหมดอายุ ลองโหลดใหม่
    if "!RC!"=="2" echo  [ERROR] สิทธิ์ไม่พอ
    if "!RC!"=="3" echo  [ERROR] ไม่พบ WireGuard
    echo          ^(รหัส !RC!^) - ลองรัน vpn-toggle.bat ตรงๆ เพื่อดูข้อความเต็ม
    pause
    exit /b 1
)

:: =====================================================================
echo.
echo  [5/6] ตั้งให้ต่อ VPN เองอัตโนมัติ ...
:: =====================================================================
set "TASKRC="
for /f "delims=" %%A in ('%PSX% "%SETUP%" -Action MakeTask -ScriptDir "%~dp0." 2^>nul') do set "TASKRC=%%A"
if /i "!TASKRC!"=="TASK-OK" (
    echo        [OK] สร้าง Scheduled Task แล้ว - ต่อเองตอนเข้าเครื่อง + เช็คซ้ำทุก 5 นาที
) else (
    echo        [!] สร้าง Scheduled Task ไม่สำเร็จ
    echo            ไม่เป็นไร - WireGuard ตั้ง service เป็น auto-start ไว้แล้ว
    echo            ยังต่อเองตอนเปิดเครื่องได้ แค่ไม่มีตัวคอยเช็คซ้ำให้
)

:: =====================================================================
echo.
echo  [6/6] สร้างช็อตคัตบนเดสก์ท็อป ...
:: =====================================================================
powershell -NoProfile -Command "$d=[Environment]::GetFolderPath('Desktop'); $w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut((Join-Path $d 'VPN Toggle.lnk')); $s.TargetPath='%TOGGLE%'; $s.WorkingDirectory='%~dp0.'; $s.IconLocation='%WG%,0'; $s.Description='เปิด/ปิด VPN คลิกเดียว'; $s.Save()" >nul 2>&1
if exist "%USERPROFILE%\Desktop\VPN Toggle.lnk" (
    echo        [OK] "VPN Toggle" อยู่บนเดสก์ท็อปแล้ว
) else (
    echo        [!] สร้างช็อตคัตไม่สำเร็จ - เปิด vpn-toggle.bat ตรงๆ ได้เหมือนกัน
)

:: =====================================================================
echo.
echo  ==============================================================
echo    เสร็จแล้ว
echo  ==============================================================
for /f "delims=" %%I in ('"%SYS%\curl.exe" -s --max-time 8 https://ipinfo.io/ip 2^>nul') do echo    IP ตอนนี้   : %%I
for /f "delims=" %%C in ('"%SYS%\curl.exe" -s --max-time 8 https://ipinfo.io/country 2^>nul') do echo    ประเทศ     : %%C
echo.
echo    ต่อไปนี้ไม่ต้องทำอะไรอีก - เปิดเครื่องมาก็ต่อ VPN เอง
echo.
echo    อยากปิด/เปิดเอง  : ดับเบิลคลิก "VPN Toggle" บนเดสก์ท็อป
echo    อยากสลับประเทศ   : โหลด .conf เพิ่มไปวางที่
echo                       %CONF_DIR%
echo                       แล้วสั่ง  vpn-toggle.bat ชื่อไฟล์
echo    เลิกต่ออัตโนมัติ : uninstall-vpn.bat
echo  ==============================================================
echo.
pause
endlocal
