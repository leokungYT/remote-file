@echo off
title Remote File Manager - Agent (Auto Update from GitHub)
cd /d "%~dp0"

:: ===== CONFIG (must match the server) ============
set "SERVER_URL=http://26.155.240.151:5000"
set "AGENT_SECRET=2ec990f60382a004d664f06f99a3e7f5"
set "AGENT_ID="
set "ALLOWED_PATHS=C:\Users\Administrator\Desktop\pes"
:: ===== GitHub source =============================
set "REPO=leokungYT/remote-file"
set "ZIP_NAME=remote_update.zip"
set "EXTRACT_DIR=update_temp"
:: ================================================

echo ===============================================
echo   Remote File Manager - Agent Auto Update
echo   Source: github.com/%REPO%
echo ===============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python first: https://www.python.org/downloads/
    pause
    exit /b 1
)

if exist "%EXTRACT_DIR%" rd /s /q "%EXTRACT_DIR%"
if exist "%ZIP_NAME%" del /q "%ZIP_NAME%"

echo [1/4] Downloading latest code from GitHub (branch: main)...
curl -k -L --retry 3 --retry-delay 3 --connect-timeout 15 "https://github.com/%REPO%/archive/refs/heads/main.zip" -o "%ZIP_NAME%" >nul 2>&1
call :extract
if defined SOURCE_FOLDER goto update

echo     (branch main not found - trying master)
if exist "%EXTRACT_DIR%" rd /s /q "%EXTRACT_DIR%"
if exist "%ZIP_NAME%" del /q "%ZIP_NAME%"
curl -k -L --retry 3 --retry-delay 3 --connect-timeout 15 "https://github.com/%REPO%/archive/refs/heads/master.zip" -o "%ZIP_NAME%" >nul 2>&1
call :extract

:update
echo [3/4] Updating agent.py...
if not defined SOURCE_FOLDER (
    echo     [WARN] Download failed - keeping current agent.py
    goto run
)
if not exist "%SOURCE_FOLDER%\agent.py" (
    echo     [WARN] agent.py not found in repo - keeping current
    goto run
)
findstr /C:"upload_start" "%SOURCE_FOLDER%\agent.py" >nul 2>&1
if errorlevel 1 (
    echo     [WARN] Repo agent.py is an old version - keeping current
    goto run
)
copy /y "%SOURCE_FOLDER%\agent.py" "agent.py" >nul
echo     [OK] agent.py updated from GitHub

:run
if exist "%EXTRACT_DIR%" rd /s /q "%EXTRACT_DIR%" >nul 2>&1
if exist "%ZIP_NAME%" del /q "%ZIP_NAME%" >nul 2>&1
echo.
echo [4/4] Starting agent...  (Allowed: %ALLOWED_PATHS%)
echo.
python agent.py
echo.
echo Agent stopped. Press any key to close.
pause >nul
exit /b

:extract
set "SOURCE_FOLDER="
if not exist "%ZIP_NAME%" exit /b
echo [2/4] Extracting...
powershell -NoProfile -Command "try { Expand-Archive -Path '%ZIP_NAME%' -DestinationPath '%EXTRACT_DIR%' -Force } catch { exit 1 }" >nul 2>&1
for /d %%f in ("%EXTRACT_DIR%\*") do set "SOURCE_FOLDER=%%f"
exit /b
