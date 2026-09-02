@echo off
:: Watchdog: if the master server is not listening on :5000, (re)launch it.
:: Scheduled to run every couple of minutes so the master never stays down.
cd /d "%~dp0"
netstat -ano | findstr ":5000 " | findstr /i LISTENING >nul 2>&1
if errorlevel 1 (
    start "" wscript.exe "%~dp0server_hidden.vbs"
)
exit /b 0
