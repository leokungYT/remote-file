@echo off
title Reconnect + update agent (turn off VPN)
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    exit /b
)

echo ================================================================
echo   Turn off VPN, restart agent so it auto-updates + reconnects
echo ================================================================
echo.

echo [1/4] Turning OFF Cloudflare WARP + Surfshark ...
set "WARPCLI=%ProgramFiles%\Cloudflare\Cloudflare WARP\warp-cli.exe"
if exist "%WARPCLI%" ( "%WARPCLI%" disconnect >nul 2>&1 )
taskkill /F /IM "Cloudflare WARP.exe" >nul 2>&1
taskkill /F /IM "Surfshark.exe" >nul 2>&1
taskkill /F /IM "Surfshark Service.exe" >nul 2>&1
net stop "Surfshark Service" >nul 2>&1

echo [2/4] Flushing DNS + waiting for network ...
ipconfig /flushdns >nul
timeout /t 8 /nobreak >nul

echo [3/4] Stopping old agent ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and $_.CommandLine -like '*agent.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 3 /nobreak >nul

echo [4/4] Starting agent (it will pull the new version + reconnect) ...
schtasks /Run /TN "RemoteFileAgent" >nul 2>&1
powershell -NoProfile -Command "if (-not (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*agent.py*' })) { $a = Get-ChildItem -Path C:\Users\*\Downloads\remote\remote-file\agent.py,C:\remote-file\agent.py,D:\remote-file\agent.py -ErrorAction SilentlyContinue | Select-Object -First 1; if ($a) { Start-Process pythonw -ArgumentList $a.FullName -WorkingDirectory $a.DirectoryName } }" >nul 2>&1

echo.
echo ================================================================
echo   [DONE] Agent is restarting + auto-updating to the new URL.
echo   It should show ONLINE again in ~30-60 seconds.
echo   After that you can turn the VPN back ON - it will stay online.
echo ================================================================
echo.
pause
