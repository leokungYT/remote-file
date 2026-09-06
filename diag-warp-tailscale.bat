@echo off
setlocal EnableExtensions
title Diagnose WARP + Tailscale on this pc
cd /d "%~dp0"

:: ================================================================
::  Collects everything needed to see WHY Tailscale dies when WARP
::  is on (or proves it does not).  Run on a bot pc while WARP is ON,
::  and on pc_1.  Output goes to diag-<computername>.txt next to this
::  file and is shown on screen - send that file back.
::  Read-only: changes nothing.  KEEP THIS FILE PURE ASCII.
:: ================================================================

set "OUT=%~dp0diag-%COMPUTERNAME%.txt"
set "W=%ProgramFiles%\Cloudflare\Cloudflare WARP\warp-cli.exe"
set "TS=%ProgramFiles%\Tailscale\tailscale.exe"

> "%OUT%" echo ===== diag %COMPUTERNAME%  %date% %time% =====
>>"%OUT%" echo.

>>"%OUT%" echo ----- WARP -----
if exist "%W%" (
    >>"%OUT%" "%W%" --version 2>&1
    >>"%OUT%" "%W%" --accept-tos status 2>&1
    >>"%OUT%" "%W%" --accept-tos settings 2>&1
    >>"%OUT%" echo --- registration ---
    >>"%OUT%" "%W%" --accept-tos registration show 2>&1
    >>"%OUT%" echo --- split tunnel hosts ---
    >>"%OUT%" "%W%" --accept-tos tunnel host list 2>&1
    >>"%OUT%" echo --- split tunnel ips ---
    >>"%OUT%" "%W%" --accept-tos tunnel ip list 2>&1
) else (
    >>"%OUT%" echo warp-cli not installed
)
>>"%OUT%" echo.

>>"%OUT%" echo ----- Tailscale -----
if exist "%TS%" (
    >>"%OUT%" "%TS%" version 2>&1
    >>"%OUT%" sc query Tailscale 2>&1
    >>"%OUT%" echo --- status (self + first lines) ---
    >>"%OUT%" "%TS%" status 2>&1
    >>"%OUT%" echo --- self json ---
    >>"%OUT%" "%TS%" status --self --json 2>&1
    >>"%OUT%" echo --- netcheck ---
    >>"%OUT%" "%TS%" netcheck 2>&1
    >>"%OUT%" echo --- prefs ---
    >>"%OUT%" "%TS%" debug prefs 2>&1
    >>"%OUT%" echo --- ping master via tailscale (100.73.104.54) ---
    >>"%OUT%" "%TS%" ping --c 2 --timeout 5s 100.73.104.54 2>&1
) else (
    >>"%OUT%" echo tailscale not installed
)
>>"%OUT%" echo.

>>"%OUT%" echo ----- network -----
>>"%OUT%" ipconfig /all 2>&1
>>"%OUT%" echo --- routes (default + 100.x) ---
>>"%OUT%" route print -4 2>&1
>>"%OUT%" echo --- reach master :5000 via tailscale / lan ---
>>"%OUT%" curl -s -m 8 -o NUL -w "tailscale 100.73.104.54:5000 -> http=%%{http_code} exit=%%{exitcode}\n" http://100.73.104.54:5000/agent.py 2>&1
>>"%OUT%" curl -s -m 8 -o NUL -w "lan 192.168.1.121:5000 -> http=%%{http_code} exit=%%{exitcode}\n" http://192.168.1.121:5000/agent.py 2>&1
>>"%OUT%" curl -s -m 8 -o NUL -w "github raw -> http=%%{http_code}\n" https://raw.githubusercontent.com/leokungYT/remote-file/pointer/master-url.txt 2>&1
>>"%OUT%" echo.

>>"%OUT%" echo ----- tailscaled log (last 60 lines) -----
powershell -NoProfile -Command "$f = Get-ChildItem 'C:\ProgramData\Tailscale\tailscaled.log*.txt' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if ($f) { Get-Content $f.FullName -Tail 60 } else { 'no log file' }" >> "%OUT%" 2>&1

echo.
type "%OUT%"
echo.
echo ================================================================
echo   Saved to: %OUT%
echo   Send this file back (or paste its content).
echo ================================================================
pause
