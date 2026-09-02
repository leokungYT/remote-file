@echo off
setlocal enabledelayedexpansion
title Update RFM server.py from GitHub

REM ================================================================
REM  Run this on the MASTER pc (pc_1, Admin cmd) to pull the latest
REM  server.py from GitHub and restart the server.
REM  (GitHub instead of the old master 100.80.76.47 which is retired.)
REM ================================================================

set "MASTER=https://raw.githubusercontent.com/leokungYT/remote-file/main"

REM --- หา repo (โฟลเดอร์ที่มี server.py + start_server.bat) ---
set "REPO="
if exist "%CD%\server.py" set "REPO=%CD%"
if not defined REPO for /d %%U in ("C:\Users\*") do if exist "%%U\Downloads\remote\remote-file\server.py" set "REPO=%%U\Downloads\remote\remote-file"
if not defined REPO if exist "C:\remote-file\server.py" set "REPO=C:\remote-file"
if not defined REPO if exist "D:\remote-file\server.py" set "REPO=D:\remote-file"
if not defined REPO (
    echo [ERROR] หา repo ไม่เจอ - เปิด cmd ในโฟลเดอร์ remote-file แล้วรันใหม่
    pause & exit /b 1
)
cd /d "%REPO%"
echo Repo: %REPO%
echo.

echo [1/4] backup + download server.py ใหม่จากแม่ ...
copy /y server.py server.py.bak >nul 2>&1
curl -s -f "%MASTER%/server.py" -o server.py.new
if errorlevel 1 ( echo [ERROR] โหลดไม่สำเร็จ - เครื่องแม่ยังเปิดอยู่ไหม? & pause & exit /b 1 )
REM กันไฟล์เพี้ยน: ต้องมี route balance อยู่จริง
findstr /c:"balance-upload" server.py.new >nul
if errorlevel 1 ( echo [ERROR] ไฟล์ที่โหลดมาไม่ถูกต้อง & del server.py.new >nul 2>&1 & pause & exit /b 1 )
move /y server.py.new server.py >nul
echo    OK - server.py ใหม่พร้อม (มีปุ่ม balance)
echo.

echo [2/4] ปิด server เก่า (ถ้ามี) ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*server.py*' -and ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 /nobreak >nul

echo [3/4] เปิด server ใหม่ (โค้ดใหม่) ...
set "SECRET_KEY=532662739b6ba4299fea8253dbfbcad3"
set "AGENT_SECRET=2ec990f60382a004d664f06f99a3e7f5"
set "WEB_PASSWORD=nuuboyshop"
set "SERVER_PORT=5000"
start "RFM-Server" cmd /k python server.py

echo [4/4] Done! Open http://localhost:5000 on this pc - build (top-left) must be today.
echo        Backup dashboard now shows the .xml breakdown inside like PES.
echo.
timeout /t 4 /nobreak >nul
endlocal
