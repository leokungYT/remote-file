# ═══════════════════════════════════════════════════════════════════
#  BOOTSTRAP-AGENT.ps1 - one-liner กู้/ติดตั้ง agent ใหม่บน remote
#  Usage (บน remote, copy-paste 1 บรรทัดนี้ลง PowerShell):
#   iwr https://raw.githubusercontent.com/leokungYT/remote-file/main/BOOTSTRAP-AGENT.ps1 -UseB | iex
# ═══════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Continue"
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  BOOTSTRAP-AGENT - download + install + start agent" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

$RepoDir = "$env:USERPROFILE\Downloads\remote\remote-file"
$RepoZip = "$env:TEMP\remote-file.zip"
$RepoUrl = "https://github.com/leokungYT/remote-file/archive/refs/heads/main.zip"

# 1) kill stuck processes
Write-Host "`n[1/7] kill python/ExpressVPN ที่ค้าง..." -ForegroundColor Yellow
taskkill /F /IM python.exe 2>$null
taskkill /F /IM pythonw.exe 2>$null
taskkill /F /IM ExpressVPN.exe 2>$null
Start-Sleep -Seconds 2

# 2) flush DNS + reset network
Write-Host "[2/7] flush DNS + reset DNS เป็น DHCP..." -ForegroundColor Yellow
ipconfig /flushdns | Out-Null
Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object {
    Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses -ErrorAction SilentlyContinue
}
ipconfig /flushdns | Out-Null

# 3) ปลุก Tailscale
Write-Host "[3/7] ปลุก Tailscale..." -ForegroundColor Yellow
$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (Test-Path $ts) { & $ts status | Out-Null }
Test-NetConnection login.tailscale.com -Port 443 -InformationLevel Quiet -WarningAction SilentlyContinue | Out-Null

# 4) download latest zip
Write-Host "[4/7] download remote-file zip ล่าสุดจาก GitHub..." -ForegroundColor Yellow
if (Test-Path $RepoZip) { Remove-Item $RepoZip -Force }
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $RepoUrl -OutFile $RepoZip -UseBasicParsing -TimeoutSec 60
    Write-Host "       download OK" -ForegroundColor Green
} catch {
    Write-Host "[ERR] download ไม่ได้: $_" -ForegroundColor Red
    Write-Host "      ลอง flush DNS + retry ..." -ForegroundColor Yellow
    ipconfig /flushdns | Out-Null
    Start-Sleep -Seconds 3
    try { Invoke-WebRequest -Uri $RepoUrl -OutFile $RepoZip -UseBasicParsing -TimeoutSec 60 }
    catch { Write-Host "[FAIL] ยังโหลดไม่ได้ - เช็ค internet + ExpressVPN" -ForegroundColor Red; exit 1 }
}

# 5) extract + copy ไป $RepoDir
Write-Host "[5/7] extract + install ไป $RepoDir..." -ForegroundColor Yellow
$TmpExtract = "$env:TEMP\remote-file-extract"
if (Test-Path $TmpExtract) { Remove-Item $TmpExtract -Recurse -Force }
Expand-Archive -Path $RepoZip -DestinationPath $TmpExtract -Force
$SrcFolder = Get-ChildItem $TmpExtract -Directory | Select-Object -First 1
if (-not (Test-Path $RepoDir)) { New-Item -ItemType Directory -Path $RepoDir -Force | Out-Null }
Copy-Item -Path "$($SrcFolder.FullName)\*" -Destination $RepoDir -Recurse -Force
Remove-Item $TmpExtract -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $RepoZip -Force -ErrorAction SilentlyContinue

# 6) ติดตั้ง watchdog (ถ้ายังไม่มี)
Write-Host "[6/7] ติดตั้ง agent-watchdog (schedule ทุก 5 นาที)..." -ForegroundColor Yellow
Set-Location $RepoDir
if (Test-Path "install-agent-watchdog.bat") {
    & cmd /c "install-agent-watchdog.bat" 2>&1 | Out-Null
    Write-Host "       watchdog installed" -ForegroundColor Green
}

# 7) start agent
Write-Host "[7/7] start agent.py..." -ForegroundColor Yellow
if (Test-Path "$RepoDir\agent.py") {
    $pyw = "pythonw.exe"
    if (-not (Get-Command pythonw -ErrorAction SilentlyContinue)) {
        $pyw = "python.exe"
        if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
            Write-Host "[FAIL] ไม่มี python ในเครื่อง - ติดตั้ง Python 3.11+ ก่อน" -ForegroundColor Red
            exit 1
        }
    }
    Start-Process -FilePath $pyw -ArgumentList "$RepoDir\agent.py" -WorkingDirectory $RepoDir -WindowStyle Hidden
    Write-Host "       agent.py started" -ForegroundColor Green
} else {
    Write-Host "[FAIL] agent.py ไม่อยู่หลัง extract" -ForegroundColor Red
    exit 1
}

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "  BOOTSTRAP เสร็จ - เช็ค dashboard ว่าออนไลน์ใน 10-30s" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "`nถ้า offline อีก - รัน script นี้อีกครั้ง หรือ reboot เครื่อง`n"
