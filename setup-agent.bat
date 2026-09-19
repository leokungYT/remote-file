@echo off
title Setup Remote File Agent - ONE CLICK
cd /d "%~dp0"

:: =====================================================================
::  ONE-CLICK agent setup: Python + Tailscale + deps + name + autostart
::
::  USAGE:   setup-agent.bat 5      (5 = PC number, becomes name "pc_5")
::           or just double-click and type the number when asked
::
::  ACCOUNT: which Tailscale account this PC must join.
::  EDIT ONCE: paste a REUSABLE auth key created while logged in as TSACCOUNT
::  (https://login.tailscale.com/admin/settings/keys - use the Copy button).
::  Leave AUTHKEY empty -> a browser opens and you sign in with TSACCOUNT by hand.
:: =====================================================================
set "TSACCOUNT=tablehub1@gmail.com"
set "AUTHKEY="
:: =====================================================================

set "TS=C:\Program Files\Tailscale\tailscale.exe"
set "PYURL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
set "TSURL=https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe"

:: --- must be Administrator ---
net session >nul 2>&1
if errorlevel 1 ( echo [ERROR] Right-click this file - Run as administrator & pause & exit /b 1 )

:: --- PC number (for unique name) ---
set "PCNUM=%~1"
if "%PCNUM%"=="" set /p PCNUM=Enter PC number (e.g. 5):
if "%PCNUM%"=="" ( echo [ERROR] no number entered & pause & exit /b 1 )

:: --- [1/6] Python ---
where python >nul 2>&1
if %errorlevel%==0 goto haspy
echo [1/6] Installing Python 3.11 (silent) ...
curl -k -L --retry 3 --connect-timeout 15 "%PYURL%" -o py-setup.exe
start /wait "" py-setup.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
del /q py-setup.exe >nul 2>&1
set "PATH=%PATH%;C:\Program Files\Python311;C:\Program Files\Python311\Scripts"
goto pydone
:haspy
echo [1/6] Python already installed.
:pydone

:: --- [2/6] Tailscale ---
if exist "%TS%" ( echo [2/6] Tailscale already installed. & goto hasts )
echo [2/6] Installing Tailscale ...
curl -k -L --retry 3 --connect-timeout 15 "%TSURL%" -o ts-setup.exe
start /wait "" ts-setup.exe /S
for /L %%i in (1,1,30) do (
    if exist "%TS%" goto hasts
    timeout /t 1 >nul
)
:hasts
if not exist "%TS%" ( echo [ERROR] Tailscale not installed. Install manually then rerun. & pause & exit /b 1 )

:: --- [3/6] join Tailscale ---
echo [3/6] Joining Tailscale as %TSACCOUNT% ...

:: who is this PC logged in as right now (empty if not logged in)
set "CURACCT="
for /f "usebackq delims=" %%a in (`powershell -NoProfile -Command "try { (& '%TS%' status --json | ConvertFrom-Json).User.PSObject.Properties.Value.LoginName | Select-Object -First 1 } catch { '' }" 2^>nul`) do set "CURACCT=%%a"

if /i "%CURACCT%"=="%TSACCOUNT%" (
    echo     Already connected as %TSACCOUNT% - skip.
    goto tsdone
)
if not "%CURACCT%"=="" (
    echo     [!] This PC is logged in as %CURACCT% - logging out to switch to %TSACCOUNT% ...
    "%TS%" logout >nul 2>&1
)

if not "%AUTHKEY%"=="" (
    "%TS%" up --authkey %AUTHKEY% --unattended --force-reauth --timeout 45s
    if not errorlevel 1 goto tsdone
    echo     [WARN] authkey join failed - falling back to manual login.
)

echo     A browser will open - sign in with %TSACCOUNT%
"%TS%" up --force-reauth --timeout 180s
if errorlevel 1 echo     [WARN] Tailscale login not finished. Run:  "%TS%" up --force-reauth   and sign in with %TSACCOUNT%
:tsdone

:: --- [4/6] set unique name (pc_<num>) in config.json ---
echo [4/6] Setting name = pc_%PCNUM% ...
powershell -NoProfile -Command "$c = Get-Content 'config.json' -Raw | ConvertFrom-Json; $c.name = 'pc_%PCNUM%'; $j = $c | ConvertTo-Json; [System.IO.File]::WriteAllText((Join-Path (Get-Location) 'config.json'), $j, (New-Object System.Text.UTF8Encoding($false)))"

:: --- [5/6] dependencies ---
echo [5/6] Installing dependencies ...
python -m pip install -r requirements.txt >nul 2>&1

:: --- [6/6] autostart + start agent ---
echo [6/6] Enabling autostart + starting agent ...
schtasks /Create /TN "RemoteFileAgent" /TR "pythonw \"%~dp0agent.py\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1
start "" pythonw agent.py

echo.
echo ================================================================
echo   [DONE] pc_%PCNUM% - agent is starting.
echo   Tailscale IP of this PC:
"%TS%" ip -4
echo   Check the server web page - pc_%PCNUM% should appear.
echo ================================================================
pause
