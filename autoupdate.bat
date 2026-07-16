@echo off
title Remote File Manager - Update Files from GitHub
cd /d "%~dp0"

:: ===== GitHub source =============================
set "REPO=leokungYT/remote-file"
set "ZIP_NAME=remote_update.zip"
set "EXTRACT_DIR=update_temp"
:: ================================================

echo ===============================================
echo   Remote File Manager - Update Files
echo   Source: github.com/%REPO%
echo ===============================================
echo.

if exist "%EXTRACT_DIR%" rd /s /q "%EXTRACT_DIR%"
if exist "%ZIP_NAME%" del /q "%ZIP_NAME%"

echo [1/3] Downloading from GitHub (branch main)...
curl -k -L --retry 3 --retry-delay 3 --connect-timeout 15 "https://github.com/%REPO%/archive/refs/heads/main.zip" -o "%ZIP_NAME%" >nul 2>&1
call :extract
if defined SOURCE_FOLDER goto copyfiles

echo     (branch main not found - trying master)
if exist "%EXTRACT_DIR%" rd /s /q "%EXTRACT_DIR%"
if exist "%ZIP_NAME%" del /q "%ZIP_NAME%"
curl -k -L --retry 3 --retry-delay 3 --connect-timeout 15 "https://github.com/%REPO%/archive/refs/heads/master.zip" -o "%ZIP_NAME%" >nul 2>&1
call :extract

:copyfiles
if not defined SOURCE_FOLDER (
    echo [ERROR] Download/extract failed. Check internet or repo name/branch.
    goto done
)
echo [2/3] Overwriting all files in this folder...
:: do NOT overwrite this running script (would corrupt it)
if exist "%SOURCE_FOLDER%\autoupdate.bat" del /q "%SOURCE_FOLDER%\autoupdate.bat" >nul 2>&1
xcopy /s /e /y "%SOURCE_FOLDER%\*" "%~dp0" >nul
if errorlevel 1 (
    echo     [ERROR] Copy failed.
) else (
    echo     [OK] All files overwritten from GitHub.
)

:done
echo [3/3] Cleaning up temporary files...
if exist "%EXTRACT_DIR%" rd /s /q "%EXTRACT_DIR%" >nul 2>&1
if exist "%ZIP_NAME%" del /q "%ZIP_NAME%" >nul 2>&1
echo.
echo ===============================================
echo   Done. Files updated. (Agent was NOT started)
echo ===============================================
pause
exit /b

:extract
set "SOURCE_FOLDER="
if not exist "%ZIP_NAME%" exit /b
echo [2/3] Extracting...
powershell -NoProfile -Command "try { Expand-Archive -Path '%ZIP_NAME%' -DestinationPath '%EXTRACT_DIR%' -Force } catch { exit 1 }" >nul 2>&1
for /d %%f in ("%EXTRACT_DIR%\*") do set "SOURCE_FOLDER=%%f"
exit /b
