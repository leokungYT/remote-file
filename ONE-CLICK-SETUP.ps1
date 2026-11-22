# ONE-CLICK-SETUP.ps1 - permanent fix: agent never disconnects when VPN swaps
# No ExpressVPN GUI manipulation needed - uses Windows persistent routes + Firewall
#
# Usage on remote PowerShell (as Administrator):
#   iwr https://raw.githubusercontent.com/leokungYT/remote-file/main/ONE-CLICK-SETUP.ps1 -UseB | iex

$ErrorActionPreference = "Continue"
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  ONE-CLICK-SETUP: permanent agent-never-disconnect fix" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Elevate to admin if not already
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[!] Need admin - relaunching elevated..." -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -Command `"iwr https://raw.githubusercontent.com/leokungYT/remote-file/main/ONE-CLICK-SETUP.ps1 -UseB | iex`""
    exit
}

$AgentDir = "$env:USERPROFILE\Downloads\remote\remote-file"

# ---------- STEP 1: install/update agent (bootstrap) ----------
Write-Host ""
Write-Host "[1/6] install/update agent code..." -ForegroundColor Yellow
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $zip = "$env:TEMP\rf.zip"
    Invoke-WebRequest "https://github.com/leokungYT/remote-file/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing -TimeoutSec 60
    $tmp = "$env:TEMP\rf-ext"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Select-Object -First 1
    if (-not (Test-Path $AgentDir)) { New-Item -ItemType Directory -Path $AgentDir -Force | Out-Null }
    Copy-Item "$($src.FullName)\*" -Destination $AgentDir -Recurse -Force
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Write-Host "       agent code updated" -ForegroundColor Green
} catch {
    Write-Host "[ERR] download/extract failed: $_" -ForegroundColor Red
    exit 1
}

# ---------- STEP 2: create isolated pythonw-agent.exe ----------
Write-Host "[2/6] create isolated pythonw-agent.exe..." -ForegroundColor Yellow
$pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    foreach ($p in @("C:\Program Files\Python311\pythonw.exe","C:\Program Files\Python310\pythonw.exe","C:\Program Files\Python312\pythonw.exe","C:\Python311\pythonw.exe")) {
        if (Test-Path $p) { $pyw = $p; break }
    }
}
if (-not $pyw) { Write-Host "[FAIL] no pythonw.exe found - install Python 3.11+" -ForegroundColor Red; exit 1 }
$AgentPy = "$AgentDir\pythonw-agent.exe"
Copy-Item $pyw $AgentPy -Force
Write-Host "       $AgentPy" -ForegroundColor Green

# ---------- STEP 3: find Tailscale + physical gateway ----------
Write-Host "[3/6] find network interfaces..." -ForegroundColor Yellow
$tsAdapter = Get-NetAdapter | Where-Object { $_.InterfaceDescription -match 'Tailscale' -and $_.Status -eq 'Up' } | Select-Object -First 1
$tsIp = $null; $tsIf = $null
if ($tsAdapter) {
    $tsIp = (Get-NetIPAddress -InterfaceIndex $tsAdapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1).IPAddress
    $tsIf = $tsAdapter.ifIndex
    Write-Host "       Tailscale: IF=$tsIf IP=$tsIp" -ForegroundColor Green
} else {
    Write-Host "       [WARN] Tailscale adapter not found" -ForegroundColor Yellow
}
$physRoute = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Where-Object { $_.InterfaceAlias -notmatch 'ExpressVPN|TAP|Wintun|Lightway|VPN|Tunnel|Tailscale' } |
    Sort-Object RouteMetric | Select-Object -First 1
$physGw = $null; $physIf = $null
if ($physRoute) {
    $physGw = $physRoute.NextHop
    $physIf = $physRoute.InterfaceIndex
    Write-Host "       physical: IF=$physIf gw=$physGw" -ForegroundColor Green
}

# ---------- STEP 4: persistent routes (ExpressVPN can't wipe) ----------
Write-Host "[4/6] add persistent routes (survive VPN rotation + reboot)..." -ForegroundColor Yellow
function AddRoute($net, $mask, $gw, $if) {
    & route delete $net mask $mask 2>$null | Out-Null
    $args = @("-p","add",$net,"mask",$mask,$gw,"metric","1")
    if ($if) { $args += @("IF",$if) }
    & route @args 2>&1 | Out-Null
}
if ($physGw) {
    AddRoute "192.168.0.0" "255.255.0.0" $physGw $physIf
    AddRoute "10.0.0.0"    "255.0.0.0"   $physGw $physIf
    AddRoute "172.16.0.0"  "255.240.0.0" $physGw $physIf
    Write-Host "       LAN routes (192.168, 10, 172.16) -> $physGw" -ForegroundColor Green
}
if ($tsIp -and $tsIf) {
    AddRoute "100.64.0.0" "255.192.0.0" $tsIp $tsIf
    Write-Host "       Tailscale route (100.64/10) -> $tsIp IF=$tsIf" -ForegroundColor Green
}

# ---------- STEP 5: restart agent via isolated exe ----------
Write-Host "[5/6] restart agent.py via pythonw-agent.exe..." -ForegroundColor Yellow
Get-Process -Name "pythonw*" -ErrorAction SilentlyContinue | Where-Object {
    try { $_.MainModule.FileName -eq $AgentPy } catch { $false }
} | Stop-Process -Force -ErrorAction SilentlyContinue
# Also kill any running agent.py (via regular pythonw)
Get-CimInstance Win32_Process -Filter "name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*agent.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
Start-Process -FilePath $AgentPy -ArgumentList "$AgentDir\agent.py" `
    -WorkingDirectory $AgentDir -WindowStyle Hidden
Write-Host "       agent started via $AgentPy" -ForegroundColor Green

# ---------- STEP 6: install watchdog ----------
Write-Host "[6/6] install/update agent-watchdog scheduled task..." -ForegroundColor Yellow
# Patch watchdog bat to use isolated exe
$watchdog = "$AgentDir\agent-watchdog.bat"
if (Test-Path $watchdog) {
    $c = Get-Content $watchdog -Raw
    $c = $c -replace 'start "" pythonw "%~dp0agent.py"',
                      'start "" "%~dp0pythonw-agent.exe" "%~dp0agent.py"'
    Set-Content -Path $watchdog -Value $c -Encoding ASCII
}
if (Test-Path "$AgentDir\install-agent-watchdog.bat") {
    & cmd /c "$AgentDir\install-agent-watchdog.bat" 2>&1 | Out-Null
    Write-Host "       watchdog scheduled every 5 min" -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  ALL DONE - no ExpressVPN GUI changes needed" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  What happened:" -ForegroundColor Cyan
Write-Host "   - Persistent routes for LAN + Tailscale (ExpressVPN cant wipe)"
Write-Host "   - Agent runs via pythonw-agent.exe (isolated exe)"
Write-Host "   - Watchdog revives agent if it dies"
Write-Host ""
Write-Host "  Check dashboard - this machine should stay online always" -ForegroundColor Yellow
Write-Host ""
