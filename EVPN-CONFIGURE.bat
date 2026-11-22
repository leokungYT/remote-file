@echo off
rem EVPN-CONFIGURE.bat - auto-config ExpressVPN:
rem   1) disable Lightway Turbo
rem   2) enable Split Tunneling + add IPs (100.64.0.0/10, 192.168.0.0/16)
rem   3) disable Network Lock
rem
rem Try CLI first, fallback to config file / registry if CLI doesnt support
rem Usage: right-click -> Run as Administrator

setlocal EnableDelayedExpansion
chcp 65001 >nul
title EVPN-CONFIGURE

rem --- elevate to admin if not already ---
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Need admin - relaunching elevated...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================================
echo   EVPN-CONFIGURE: Lightway Turbo off + Split Tunnel IPs
echo ============================================================
echo.

rem --- find expressvpnctl.exe ---
set "CLI="
for %%P in (
    "C:\Program Files (x86)\ExpressVPN\services\expressvpnctl.exe"
    "C:\Program Files\ExpressVPN\services\expressvpnctl.exe"
    "C:\Program Files (x86)\ExpressVPN\expressvpnctl.exe"
    "C:\Program Files\ExpressVPN\expressvpnctl.exe"
) do if exist %%P set "CLI=%%~P"
if "!CLI!"=="" (
    echo [FAIL] expressvpnctl.exe not found
    pause
    exit /b 1
)
echo [OK] CLI: !CLI!
echo.

rem --- 1) disable Lightway Turbo (try all plausible preference names) ---
echo [1/4] disable Lightway Turbo...
for %%K in (
    "lightway_turbo"
    "lightway-turbo"
    "turbo"
    "lightway_turbo_enabled"
    "lightway_multi_lane"
    "multi_lane"
) do (
    "!CLI!" preferences set %%~K false 2>nul >nul
    "!CLI!" preferences set %%~K off   2>nul >nul
)

rem --- 2) disable Network Lock (Internet Kill Switch) ---
echo [2/4] disable Network Lock / Kill Switch...
for %%K in (
    "network_lock"
    "network-lock"
    "block_all_traffic"
    "kill_switch"
    "internet_kill_switch"
) do (
    "!CLI!" preferences set %%~K off    2>nul >nul
    "!CLI!" preferences set %%~K false  2>nul >nul
    "!CLI!" preferences set %%~K none   2>nul >nul
)

rem --- 3) enable Split Tunneling + add IPs ---
echo [3/4] enable Split Tunneling + add bypass IPs...
for %%K in (
    "split_tunneling"
    "split-tunneling"
    "split_tunnel"
    "inverse_split_tunnel"
) do (
    "!CLI!" preferences set %%~K on   2>nul >nul
    "!CLI!" preferences set %%~K true 2>nul >nul
)
rem try add IPs via CLI (several command variants)
for %%I in ("100.64.0.0/10" "192.168.0.0/16") do (
    "!CLI!" split-tunnel add-ip %%~I                2>nul >nul
    "!CLI!" split-tunnel add %%~I                   2>nul >nul
    "!CLI!" preferences add split_tunnel_ips %%~I   2>nul >nul
    "!CLI!" preferences set split_tunnel_bypass_ips %%~I 2>nul >nul
)

rem --- 4) write ExpressVPN config files directly (fallback) ---
echo [4/4] write ExpressVPN settings.json config (fallback)...
powershell -NoProfile -Command ^
    "$agent = \"$env:USERPROFILE\Downloads\remote\remote-file\pythonw-agent.exe\";" ^
    "$ts1 = 'C:\Program Files\Tailscale\tailscaled.exe';" ^
    "$ts2 = 'C:\Program Files\Tailscale\tailscale.exe';" ^
    "$patch = @{" ^
    "  'lightway_turbo'=$false; 'turbo'=$false;" ^
    "  'network_lock'=$false; 'kill_switch'=$false;" ^
    "  'split_tunneling_enabled'=$true; 'split_tunneling_mode'='exclude';" ^
    "  'split_tunnel_bypass_ips'=@('100.64.0.0/10','192.168.0.0/16');" ^
    "  'split_tunneling_apps'=@($agent,$ts1,$ts2);" ^
    "  'allow_lan_access'=$true" ^
    "};" ^
    "foreach ($cfg in @(\"$env:LOCALAPPDATA\ExpressVPN\settings.json\",\"$env:APPDATA\ExpressVPN\settings.json\",\"$env:PROGRAMDATA\ExpressVPN\settings.json\")) {" ^
    "  $dir = Split-Path $cfg -Parent; if (Test-Path $dir) {" ^
    "    try { $cur = @{}; if (Test-Path $cfg) { $cur = Get-Content $cfg -Raw | ConvertFrom-Json -ErrorAction SilentlyContinue; if (-not $cur) { $cur = @{} } }" ^
    "    foreach ($k in $patch.Keys) { $cur | Add-Member -NotePropertyName $k -NotePropertyValue $patch[$k] -Force }" ^
    "    $cur | ConvertTo-Json -Depth 8 | Set-Content -Path $cfg -Encoding UTF8; Write-Host \"      wrote $cfg\" -ForegroundColor Green" ^
    "    } catch { Write-Host \"      skip $cfg\" -ForegroundColor DarkGray } } }" ^
    "foreach ($reg in @('HKCU:\Software\ExpressVPN','HKLM:\Software\ExpressVPN','HKLM:\Software\WOW6432Node\ExpressVPN')) {" ^
    "  if (Test-Path $reg) {" ^
    "    try { Set-ItemProperty -Path $reg -Name 'LightwayTurbo' -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue;" ^
    "          Set-ItemProperty -Path $reg -Name 'NetworkLock' -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue;" ^
    "          Set-ItemProperty -Path $reg -Name 'SplitTunneling' -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue;" ^
    "          Set-ItemProperty -Path $reg -Name 'AllowLanAccess' -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue;" ^
    "          Write-Host \"      wrote $reg\" -ForegroundColor Green } catch {} } }"

rem --- restart ExpressVPN to apply ---
echo.
echo [DONE] restart ExpressVPN...
"!CLI!" disconnect 2>nul >nul
timeout /t 2 /nobreak >nul
taskkill /F /IM ExpressVPN.exe 2>nul >nul
taskkill /F /IM expressvpn-ui.exe 2>nul >nul
timeout /t 3 /nobreak >nul
for %%P in (
    "C:\Program Files (x86)\ExpressVPN\expressvpn-ui\ExpressVPN.exe"
    "C:\Program Files\ExpressVPN\expressvpn-ui\ExpressVPN.exe"
) do if exist %%P (
    start "" %%P
    goto done
)
:done

echo.
echo ============================================================
echo   CHECK ExpressVPN UI:
echo     - Lightway Turbo -^> OFF
echo     - Split Tunneling -^> ON
echo     - Add IP: 100.64.0.0/10 and 192.168.0.0/16
echo     - Network Lock -^> OFF
echo.
echo   If CLI/config didn't apply (UI still ON), set manually once in GUI
echo ============================================================
pause
