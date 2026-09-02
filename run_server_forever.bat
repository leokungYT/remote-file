@echo off
title Remote File Manager - Master Server (auto-restart)
cd /d "%~dp0"

:: ---- server config (same as start_server.bat) ----
set SECRET_KEY=532662739b6ba4299fea8253dbfbcad3
set AGENT_SECRET=2ec990f60382a004d664f06f99a3e7f5
set WEB_PASSWORD=nuuboyshop
set SERVER_PORT=5000

:: ---- make sure Tailscale Serve + Funnel are up (idempotent) ----
set "TSEXE=C:\Program Files\Tailscale\tailscale.exe"
if exist "%TSEXE%" (
    "%TSEXE%" serve --bg %SERVER_PORT% >nul 2>&1
    "%TSEXE%" funnel --bg %SERVER_PORT% >nul 2>&1
)

:: ---- keep the server alive: relaunch if it ever exits/crashes ----
:loop
echo [%date% %time%] starting server.py ...
python server.py
echo [%date% %time%] server.py exited (code %errorlevel%) - restarting in 5s ...
"%TSEXE%" serve --bg %SERVER_PORT% >nul 2>&1
"%TSEXE%" funnel --bg %SERVER_PORT% >nul 2>&1
timeout /t 5 /nobreak >nul
goto loop
