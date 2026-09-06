@echo off
setlocal EnableExtensions
title Setup public tunnel for the MASTER server (pc_1)
cd /d "%~dp0"

:: ================================================================
::  Run this ON THE MASTER pc (pc_1) as Administrator, inside the
::  remote-file folder (the one that has server.py).  One time only.
::
::  Get it there with (cmd, inside the remote-file folder):
::    curl -s https://raw.githubusercontent.com/leokungYT/remote-file/main/setup-master-tunnel.bat -o setup-master-tunnel.bat && setup-master-tunnel.bat
::
::  What it does:
::   1. pulls the latest server.py / agent.py / tunnel scripts from GitHub
::   2. installs simple-websocket (WebSocket support for the server)
::   3. asks for a GitHub token once (saved to github-token.txt, never committed)
::   4. downloads cloudflared.exe if missing
::   5. registers scheduled task "RemoteFileTunnel" (at logon, hidden) and starts it
::      -> opens a Cloudflare quick tunnel to :5000 and publishes its URL to GitHub
::   6. restarts server.py so the new code is live
::
::  After this, agents that cannot reach the master through Tailscale/LAN
::  (WARP on both sides) read the URL from GitHub and connect through the tunnel.
::  KEEP THIS FILE PURE ASCII (see the note in install-vpn.bat).
:: ================================================================

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting Administrator rights - click "Yes" on the prompt...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

if not exist "%~dp0server.py" (
    echo [ERROR] server.py not found here - put this file in the remote-file folder and run again.
    pause
    exit /b 1
)

set "RAW=https://raw.githubusercontent.com/leokungYT/remote-file/main"

echo ================================================================
echo   Master public tunnel setup   (folder: %~dp0)
echo ================================================================
echo.

echo [1/6] Pulling latest files from GitHub ...
call :fetch server.py "balance-upload"
call :fetch agent.py "RemoteFileManagerAgent_SingleInstance"
call :fetch master-tunnel.ps1 "master-tunnel"
call :fetch master_tunnel_hidden.vbs "master-tunnel.ps1"
call :fetch run_server_forever.bat "server.py"
call :fetch server_hidden.vbs "run_server_forever"
echo.

echo [2/6] Installing simple-websocket (WebSocket for the server) ...
python -m pip install -q simple-websocket >nul 2>&1
if errorlevel 1 (
    echo     [WARN] pip install failed - tunnel still works, connections just stay on polling
) else (
    echo     OK
)
echo.

echo [3/6] GitHub token (lets this pc publish the tunnel URL for the agents) ...
if exist "github-token.txt" goto tokenok
echo     Create one at:  https://github.com/settings/personal-access-tokens/new
echo       Repository access : Only select repositories -^> remote-file
echo       Permissions       : Contents = Read and write
echo     Paste it below and press Enter.
set "GHT="
set /p "GHT=    Token: "
if not defined GHT (
    echo     [WARN] no token given - the tunnel will run but its URL will NOT be published.
    echo            Put the token in github-token.txt later; it is picked up within 2 minutes.
    goto tokendone
)
>"github-token.txt" echo %GHT%
echo     saved to github-token.txt
goto tokendone
:tokenok
echo     github-token.txt already exists - keeping it
:tokendone
echo.

echo [4/6] cloudflared.exe ...
if exist "cloudflared.exe" (
    echo     already here
) else (
    echo     downloading ^(about 60 MB^) ...
    curl -k -L --retry 3 --connect-timeout 20 "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -o "cloudflared.exe"
)
if not exist "cloudflared.exe" (
    echo [ERROR] cloudflared.exe missing - check the internet and run again.
    pause
    exit /b 1
)
echo.

echo [5/6] Registering scheduled task "RemoteFileTunnel" (at logon, hidden) ...
schtasks /Create /TN "RemoteFileTunnel" /TR "wscript.exe \"%~dp0master_tunnel_hidden.vbs\"" /SC ONLOGON /RU "%USERNAME%" /IT /RL HIGHEST /F >nul 2>&1
if errorlevel 1 schtasks /Create /TN "RemoteFileTunnel" /TR "wscript.exe \"%~dp0master_tunnel_hidden.vbs\"" /SC ONLOGON /RL HIGHEST /F >nul 2>&1
if errorlevel 1 (
    echo [ERROR] could not register the scheduled task.
    pause
    exit /b 1
)
:: stop an old copy (if any) so the new script starts clean
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*master-tunnel.ps1*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
del /q "tunnel-url.txt" >nul 2>&1
schtasks /Run /TN "RemoteFileTunnel" >nul 2>&1
echo     started
echo.

echo [6/6] Restarting server.py (new code + WebSocket) - agents reconnect in a few seconds ...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*server.py*' -and ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
timeout /t 8 /nobreak >nul
netstat -ano | findstr ":5000 " | findstr /i LISTENING >nul 2>&1
if errorlevel 1 (
    echo     server did not come back by itself - launching run_server_forever.bat ^(minimized^)
    start "" wscript.exe "%~dp0server_hidden.vbs"
) else (
    echo     server restarted by its own loop
)
echo.

echo Waiting for the tunnel URL (up to 90s) ...
set "N=0"
:waiturl
if exist "tunnel-url.txt" goto showurl
set /a N+=1
if %N% geq 30 goto showurl
timeout /t 3 /nobreak >nul
goto waiturl
:showurl
echo.
echo ================================================================
if exist "tunnel-url.txt" (
    echo   Public URL of this master:
    type "tunnel-url.txt"
) else (
    echo   [WARN] no tunnel URL yet - see master-tunnel.log
)
echo.
echo   master-tunnel.log (last lines):
powershell -NoProfile -Command "if (Test-Path 'master-tunnel.log') { Get-Content 'master-tunnel.log' -Tail 6 }"
echo.
echo   Next steps:
echo    1. open http://localhost:5000  - build (top-left) must show today's date
echo    2. tick all agents -^> "update agent" so they get the new agent.py
echo    3. agents outside this LAN find this master through GitHub in ~2-5 min
echo ================================================================
echo.
pause
exit /b 0

:: ---------------------------------------------------------------
:: :fetch <file> "<marker>"  - download <file> from GitHub main,
::   keep the current copy unless the download contains <marker>
:: ---------------------------------------------------------------
:fetch
del /q "%~1.new" >nul 2>&1
curl -s -f -L --connect-timeout 20 --max-time 120 "%RAW%/%~1" -o "%~1.new"
if not exist "%~1.new" (
    echo     [SKIP] %~1 - download failed, keeping the current file
    exit /b 0
)
findstr /c:%2 "%~1.new" >nul 2>&1
if errorlevel 1 (
    echo     [SKIP] %~1 - downloaded file looks wrong, keeping the current file
    del /q "%~1.new" >nul 2>&1
    exit /b 0
)
if exist "%~1" copy /y "%~1" "%~1.bak" >nul 2>&1
move /y "%~1.new" "%~1" >nul
echo     OK   %~1
exit /b 0
