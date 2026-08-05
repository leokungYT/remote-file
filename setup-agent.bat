@echo off
title Setup Remote File Agent - ONE CLICK
cd /d "%~dp0"

:: =====================================================================
::  ONE-CLICK agent setup: Python + Tailscale + deps + name + autostart
::
::  USAGE:   setup-agent.bat 5      (5 = PC number, becomes name "pc_5")
::           or just double-click and type the number when asked
::
::  EDIT ONCE: paste your Tailscale REUSABLE auth key below
::  (get it from https://login.tailscale.com/admin/settings/keys - use the Copy button)
:: =====================================================================
set "AUTHKEY=tskey-auth-kBvdL67G1d11CNTRL-dfZtZokA133WjKo9sQGz23WFEPVRuh6XL"
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
echo [3/6] Joining Tailscale ...
"%TS%" up --authkey %AUTHKEY% --unattended
if errorlevel 1 echo     [WARN] authkey join failed - if a browser opened, log in with the same account as the server

:: --- [4/6] set unique name (pc_<num>) in config.json ---
echo [4/6] Setting name = pc_%PCNUM% ...
powershell -NoProfile -Command "$c = Get-Content 'config.json' -Raw | ConvertFrom-Json; $c.name = 'pc_%PCNUM%'; ($c | ConvertTo-Json) | Set-Content 'config.json' -Encoding utf8"

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
