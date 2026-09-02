@echo off
:: Stop ALL VPNs so Tailscale works again and the agent can reach the master.
:: Run as Administrator (needs admin to stop services).
echo ==================================================
echo    STOP ALL VPN (WARP / Surfshark / Radmin / ...)
echo ==================================================
echo.

echo [1] Cloudflare WARP ...
if exist "%ProgramFiles%\Cloudflare\Cloudflare WARP\warp-cli.exe" "%ProgramFiles%\Cloudflare\Cloudflare WARP\warp-cli.exe" --accept-tos disconnect >nul 2>&1
if exist "%ProgramFiles(x86)%\Cloudflare\Cloudflare WARP\warp-cli.exe" "%ProgramFiles(x86)%\Cloudflare\Cloudflare WARP\warp-cli.exe" --accept-tos disconnect >nul 2>&1
net stop CloudflareWARP >nul 2>&1
taskkill /f /im "Cloudflare WARP.exe" >nul 2>&1
taskkill /f /im warp-svc.exe >nul 2>&1

echo [2] Surfshark ...
taskkill /f /im Surfshark.exe >nul 2>&1
taskkill /f /im "Surfshark.exe" >nul 2>&1
net stop "Surfshark Service" >nul 2>&1
net stop SurfsharkService >nul 2>&1
net stop "SurfsharkWireGuardService" >nul 2>&1
taskkill /f /im SurfsharkService.exe >nul 2>&1

echo [3] Radmin VPN ...
taskkill /f /im "Radmin VPN.exe" >nul 2>&1
net stop "Radmin VPN" >nul 2>&1
taskkill /f /im RvControlSvc.exe >nul 2>&1

echo [4] Others (Nord / OpenVPN / Proton) ...
net stop nordvpn >nul 2>&1
taskkill /f /im NordVPN.exe >nul 2>&1
taskkill /f /im openvpn.exe >nul 2>&1
taskkill /f /im openvpnserv.exe >nul 2>&1
taskkill /f /im ProtonVPN.exe >nul 2>&1

ping -n 4 127.0.0.1 >nul
echo.
echo ==================================================
echo  [OK] VPNs stopped. Tailscale should recover now.
echo       The agent will reconnect to the master shortly.
echo       (To run bots again: launch run.bat - it re-enables WARP)
echo ==================================================
echo.
pause
