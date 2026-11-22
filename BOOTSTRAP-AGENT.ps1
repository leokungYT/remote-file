# BOOTSTRAP-AGENT.ps1 - one-liner recovery + install agent on remote
# Usage on remote PowerShell:
#   iwr https://raw.githubusercontent.com/leokungYT/remote-file/main/BOOTSTRAP-AGENT.ps1 -UseB | iex

$ErrorActionPreference = "Continue"
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  BOOTSTRAP-AGENT - download + install + start agent" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

$RepoDir = "$env:USERPROFILE\Downloads\remote\remote-file"
$RepoZip = "$env:TEMP\remote-file.zip"
$RepoUrl = "https://github.com/leokungYT/remote-file/archive/refs/heads/main.zip"

Write-Host ""
Write-Host "[1/7] kill stuck python/ExpressVPN..." -ForegroundColor Yellow
taskkill /F /IM python.exe 2>$null | Out-Null
taskkill /F /IM pythonw.exe 2>$null | Out-Null
taskkill /F /IM ExpressVPN.exe 2>$null | Out-Null
Start-Sleep -Seconds 2

Write-Host "[2/7] flush DNS + reset DNS to DHCP..." -ForegroundColor Yellow
ipconfig /flushdns | Out-Null
Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object {
    Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses -ErrorAction SilentlyContinue
}
ipconfig /flushdns | Out-Null

Write-Host "[3/7] wake Tailscale..." -ForegroundColor Yellow
$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (Test-Path $ts) { & $ts status | Out-Null }
Test-NetConnection login.tailscale.com -Port 443 -InformationLevel Quiet -WarningAction SilentlyContinue | Out-Null

Write-Host "[4/7] download latest remote-file zip from GitHub..." -ForegroundColor Yellow
if (Test-Path $RepoZip) { Remove-Item $RepoZip -Force }
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $RepoUrl -OutFile $RepoZip -UseBasicParsing -TimeoutSec 60
    Write-Host "       download OK" -ForegroundColor Green
} catch {
    Write-Host "[ERR] download failed: $_" -ForegroundColor Red
    Write-Host "      flush DNS + retry..." -ForegroundColor Yellow
    ipconfig /flushdns | Out-Null
    Start-Sleep -Seconds 3
    try {
        Invoke-WebRequest -Uri $RepoUrl -OutFile $RepoZip -UseBasicParsing -TimeoutSec 60
    } catch {
        Write-Host "[FAIL] still cannot download - check internet + ExpressVPN" -ForegroundColor Red
        exit 1
    }
}

Write-Host "[5/7] extract + install to $RepoDir..." -ForegroundColor Yellow
$TmpExtract = "$env:TEMP\remote-file-extract"
if (Test-Path $TmpExtract) { Remove-Item $TmpExtract -Recurse -Force }
Expand-Archive -Path $RepoZip -DestinationPath $TmpExtract -Force
$SrcFolder = Get-ChildItem $TmpExtract -Directory | Select-Object -First 1
if (-not (Test-Path $RepoDir)) { New-Item -ItemType Directory -Path $RepoDir -Force | Out-Null }
Copy-Item -Path "$($SrcFolder.FullName)\*" -Destination $RepoDir -Recurse -Force
Remove-Item $TmpExtract -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $RepoZip -Force -ErrorAction SilentlyContinue

Write-Host "[6/7] install agent-watchdog (scheduled task every 5 min)..." -ForegroundColor Yellow
Set-Location $RepoDir
if (Test-Path "install-agent-watchdog.bat") {
    & cmd /c "install-agent-watchdog.bat" 2>&1 | Out-Null
    Write-Host "       watchdog installed" -ForegroundColor Green
}

Write-Host "[7/7] start agent.py..." -ForegroundColor Yellow
if (Test-Path "$RepoDir\agent.py") {
    $pyw = "pythonw.exe"
    if (-not (Get-Command pythonw -ErrorAction SilentlyContinue)) {
        $pyw = "python.exe"
        if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
            Write-Host "[FAIL] python not found - install Python 3.11+ first" -ForegroundColor Red
            exit 1
        }
    }
    Start-Process -FilePath $pyw -ArgumentList "$RepoDir\agent.py" -WorkingDirectory $RepoDir -WindowStyle Hidden
    Write-Host "       agent.py started" -ForegroundColor Green
} else {
    Write-Host "[FAIL] agent.py missing after extract" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  BOOTSTRAP DONE - check dashboard in 10-30s" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "If still offline - run this script again, or reboot" -ForegroundColor Yellow
Write-Host ""
