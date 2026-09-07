@echo off
setlocal EnableExtensions
:: warp-allow-tailscale.bat - make Cloudflare WARP leave Tailscale alone on this pc (consumer split tunnel).
::   Root cause (found 2026-09-07 in tailscaled.log on pc_1): with WARP on, every dial by tailscaled to the
::   control plane and the DERP relays failed with WSAEACCES ("forbidden by its access permissions") because
::   tailscaled binds its sockets to the physical NIC and WARP's firewall only lets tunnel traffic out.
::   Excluding Tailscale's hosts + DERP IPs from the tunnel lets those sockets through -> Tailscale stays online.
::   Verified on pc_1: WARP Connected + Tailscale Online + all 27 agents visible from another tailnet node.
::   DERP list comes from https://login.tailscale.com/derpmap/default (regenerate if Tailscale adds relays).
::   Run on every pc that uses WARP (master and bots). Also pushed to agents via run_file from the master.
:: WARP's firewall blocks tailscaled's interface-bound sockets (WSAEACCES) so control plane + DERP
:: relays die when WARP is on. Excluding them by host + IP lets tailscaled reach them directly.
:: Idempotent - safe to run again. Log -> warp-allow-tailscale.log next to this file
set "LOG=%~dp0warp-allow-tailscale.log"
set "W=%ProgramFiles%\Cloudflare\Cloudflare WARP\warp-cli.exe"
> "%LOG%" echo ===== warp excl %COMPUTERNAME% %date% %time% =====
if not exist "%W%" ( >>"%LOG%" echo warp-cli not installed & goto done )
taskkill /F /IM warp-cli.exe >nul 2>&1
for %%H in (*.tailscale.com controlplane.tailscale.com login.tailscale.com log.tailscale.com *.tailscale.io) do "%W%" --accept-tos tunnel host add %%H >nul 2>&1
for %%I in (199.38.181.104 2607:f740:f::bc 209.177.145.120 2607:f740:f::3eb 199.38.181.93 2607:f740:f::afd 199.38.181.103 2607:f740:f::e19 192.73.252.65 2607:f740:0:3f::287 192.73.252.134 2607:f740:0:3f::44c 208.111.34.178 2607:f740:0:3f::f4 172.237.72.43 2600:3c15::2000:6cff:fee4:d799 172.237.72.8 2600:3c15::2000:53ff:fe48:a668 172.237.72.79 2600:3c15::2000:adff:fe08:6fab 172.237.66.30 2600:3c15::2000:3dff:fe44:50aa 185.40.234.219 2a00:dd80:20::a25 185.40.234.113 2a00:dd80:20::8f 185.40.234.77 2a00:dd80:20::bcf 185.40.234.53 2a00:dd80:20::8a6) do "%W%" --accept-tos tunnel ip add %%I >nul 2>&1
for %%I in (185.40.234.176 2a00:dd80:20::e67 172.105.179.230 2400:8907::2000:ceff:fe8d:4f4e 172.105.166.103 2400:8907::2000:ccff:fe1f:80da 172.105.169.57 2400:8907::2000:2fff:fea7:57f4 68.183.90.120 2400:6180:100:d0::982:d001 172.238.6.180 2600:3c18::2000:60ff:fe0f:6e83 172.238.6.34 2600:3c18::2000:acff:fe8e:3ed5 172.238.6.179 2600:3c18::2000:3fff:fe80:3ebd 172.237.28.183 2600:3c18::2000:b1ff:fea9:4560 176.58.92.144 2a00:dd80:3a::b33 176.58.88.183 2a00:dd80:3a::dfa 176.58.92.254 2a00:dd80:3a::ed 209.177.156.94 2607:f740:100::c05 192.73.248.83 2607:f740:100::359 209.177.156.197 2607:f740:100::cad) do "%W%" --accept-tos tunnel ip add %%I >nul 2>&1
for %%I in (192.73.240.161 2607:f740:14::61c 192.73.240.121 2607:f740:14::40c 192.73.240.132 2607:f740:14::500 172.237.61.194 2600:3c0d::2000:d2ff:fe43:1790 172.237.61.197 2600:3c0d::2000:3bff:fe44:6166 172.237.61.190 2600:3c0d::2000:62ff:febe:2e67 209.177.158.246 2607:f740:e::811 209.177.158.15 2607:f740:e::b17 199.38.182.118 2607:f740:e::4c8 192.73.242.187 2607:f740:16::640 192.73.242.28 2607:f740:16::5c 192.73.242.204 2607:f740:16::c23 176.58.93.248 2a00:dd80:3c::807 176.58.93.147 2a00:dd80:3c::b09 176.58.93.154 2a00:dd80:3c::3d5) do "%W%" --accept-tos tunnel ip add %%I >nul 2>&1
for %%I in (102.67.165.90 2c0f:edb0:0:10::963 102.67.165.185 2c0f:edb0:0:10::b59 102.67.165.36 2c0f:edb0:0:10::599 192.73.243.135 2607:f740:17::476 192.73.243.229 2607:f740:17::4e4 192.73.243.141 2607:f740:17::475 192.73.244.245 2607:f740:c::646 208.111.40.12 2607:f740:c::10 208.111.40.216 2607:f740:c::e1b 176.58.90.147 2a00:dd80:3e::363 176.58.90.207 2a00:dd80:3e::c19 176.58.90.104 2a00:dd80:3e::f2e 45.159.97.144 2a00:dd80:14:10::335 45.159.97.61 2a00:dd80:14:10::20 45.159.97.233 2a00:dd80:14:10::34a) do "%W%" --accept-tos tunnel ip add %%I >nul 2>&1
for %%I in (103.6.84.152 2403:2500:8000:1::ef6 205.147.105.30 2403:2500:8000:1::5fb 205.147.105.78 2403:2500:8000:1::e9a 162.248.221.199 2607:f740:50::1d1 162.248.221.215 2607:f740:50::f10 162.248.221.248 2607:f740:50::ca4 45.159.98.196 2a00:dd80:40:100::316 45.159.98.253 2a00:dd80:40:100::3f 45.159.98.145 2a00:dd80:40:100::211 185.34.3.232 2a00:dd80:3f:100::76f 185.34.3.207 2a00:dd80:3f:100::a50 185.34.3.75 2a00:dd80:3f:100::97e 208.83.234.151 2001:19f0:c000:c586:5400:04ff:fe26:2ba6 208.83.233.233 2001:19f0:c000:c591:5400:04ff:fe26:2c5f 208.72.155.133 2001:19f0:c000:c564:5400:04ff:fe26:2ba8) do "%W%" --accept-tos tunnel ip add %%I >nul 2>&1
for %%I in (102.67.167.245 2c0f:edb0:2000:1::2e9 102.67.167.37 2c0f:edb0:2000:1::2c7 102.67.167.188 2c0f:edb0:2000:1::188 167.235.72.200 2a01:4f8:1c1c:47b6::1 49.12.193.137 2a01:4f8:1c1c:5c70::1 49.13.204.141 2a01:4f8:1c0c:7d06::1 5.161.218.233 2a01:4ff:f0:3db9::1 178.156.152.91 2a01:4ff:f0:3913::1 178.156.152.106 2a01:4ff:f0:3c8e::1 178.156.134.232 2a01:4ff:f0:28d4::1 65.109.143.62 2a01:4f9:c012:d55c::1 95.217.2.165 2a01:4f9:c012:cd74::1 157.180.28.32 2a01:4f9:c012:2e5b::1 192.200.0.105 192.200.0.116 192.200.0.106 192.200.0.107) do "%W%" --accept-tos tunnel ip add %%I >nul 2>&1
for %%I in (192.200.0.101 192.200.0.103 192.200.0.111 192.200.0.115 192.200.0.112 192.200.0.110) do "%W%" --accept-tos tunnel ip add %%I >nul 2>&1
>>"%LOG%" echo hosts:
"%W%" --accept-tos tunnel host list >>"%LOG%" 2>&1
>>"%LOG%" echo derp/control ips excluded:
"%W%" --accept-tos tunnel ip list 2>&1 | find /c "CLI exclude" >>"%LOG%"
:done
>>"%LOG%" echo ===== end =====
exit /b 0
