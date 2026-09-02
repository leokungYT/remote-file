@echo off
setlocal enabledelayedexpansion
title RFM remote deploy (server.py)

REM ================================================================
REM  Runs ON the master pc (pc_1). Uploaded next to _new_server.py.
REM  Replaces repo server.py with the uploaded build, then restarts
REM  the server ONCE (skips restart if a supervisor already did it,
REM  so there is never more than one server.py running).
REM ================================================================

set "SRC=%~dp0_new_server.py"
if not exist "%SRC%" (
    echo [ERROR] _new_server.py not found next to this bat.
    exit /b 1
)

REM --- verify the uploaded file: valid server + is the NEW build ---
findstr /c:"balance-upload" "%SRC%" >nul || ( echo [ERROR] uploaded file invalid ^(no balance marker^). & del "%SRC%" >nul 2>&1 & exit /b 1 )
findstr /c:"openBackupRich" "%SRC%" >nul || ( echo [ERROR] uploaded file is not the new build ^(no openBackupRich^). & del "%SRC%" >nul 2>&1 & exit /b 1 )

REM --- find repo (folder that has server.py + start_server.bat) ---
set "REPO="
for /d %%U in ("C:\Users\*") do if exist "%%U\Downloads\remote\remote-file\server.py" set "REPO=%%U\Downloads\remote\remote-file"
if not defined REPO if exist "C:\remote-file\server.py" set "REPO=C:\remote-file"
if not defined REPO if exist "D:\remote-file\server.py" set "REPO=D:\remote-file"
if not defined REPO ( echo [ERROR] repo not found. & exit /b 1 )
echo Repo: %REPO%

REM --- backup + replace server.py ---
copy /y "%REPO%\server.py" "%REPO%\server.py.bak" >nul 2>&1
copy /y "%SRC%" "%REPO%\server.py" >nul || ( echo [ERROR] copy failed. & exit /b 1 )
echo [OK] server.py replaced with the new build.

REM --- kill the old server (leave the agent alone) ---
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*server.py*' -and ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 3 /nobreak >nul

REM --- if a supervisor already brought it back, do NOT start another ---
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
    echo [OK] server is already back up ^(supervisor restarted it with the new code^).
    goto :done
)

REM --- else start it (prefer the durable runner) ---
cd /d "%REPO%"
if exist "%REPO%\run_server_forever.bat" (
    if exist "%REPO%\server_hidden.vbs" (
        start "" wscript "%REPO%\server_hidden.vbs"
    ) else (
        start "RFM-Server" cmd /c "run_server_forever.bat"
    )
) else (
    set "SECRET_KEY=532662739b6ba4299fea8253dbfbcad3"
    set "AGENT_SECRET=2ec990f60382a004d664f06f99a3e7f5"
    set "WEB_PASSWORD=nuuboyshop"
    set "SERVER_PORT=5000"
    start "RFM-Server" cmd /k python server.py
)
echo [OK] server started with the new code.

:done
del "%SRC%" >nul 2>&1
timeout /t 3 /nobreak >nul
echo [DONE] Deploy finished. Open localhost:5000 - build (top-left) must be today.
endlocal
