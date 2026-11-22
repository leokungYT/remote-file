# SETUP-AGENT-SPLIT.ps1 - make agent.py run via its own pythonw copy
# so ExpressVPN split tunneling can exclude it (without affecting bot)
#
# Usage on remote PowerShell:
#   iwr https://raw.githubusercontent.com/leokungYT/remote-file/main/SETUP-AGENT-SPLIT.ps1 -UseB | iex

$ErrorActionPreference = "Continue"
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  SETUP-AGENT-SPLIT - isolated pythonw for agent" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1) find real pythonw.exe
$pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    $candidates = @(
        "C:\Program Files\Python311\pythonw.exe",
        "C:\Program Files\Python310\pythonw.exe",
        "C:\Program Files\Python312\pythonw.exe",
        "C:\Python311\pythonw.exe"
    )
    $pyw = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $pyw) { Write-Host "[FAIL] pythonw.exe not found" -ForegroundColor Red; exit 1 }
Write-Host "[1/4] source pythonw: $pyw" -ForegroundColor Yellow

# 2) copy to isolated path (agent-only)
$agentPy = "$env:USERPROFILE\Downloads\remote\remote-file\pythonw-agent.exe"
Copy-Item -Path $pyw -Destination $agentPy -Force
Write-Host "[2/4] copied to: $agentPy" -ForegroundColor Green

# 3) stop existing agent + restart via the isolated exe
Write-Host "[3/4] restart agent.py via isolated exe..." -ForegroundColor Yellow
Get-Process -Name "pythonw" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*agent.py*"
} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$agentScript = "$env:USERPROFILE\Downloads\remote\remote-file\agent.py"
if (-not (Test-Path $agentScript)) {
    Write-Host "[FAIL] agent.py not found at $agentScript" -ForegroundColor Red
    exit 1
}
Start-Process -FilePath $agentPy -ArgumentList $agentScript `
    -WorkingDirectory (Split-Path $agentScript) -WindowStyle Hidden
Write-Host "       agent started via pythonw-agent.exe" -ForegroundColor Green

# 4) update watchdog to use isolated exe
Write-Host "[4/4] update agent-watchdog.bat to use isolated exe..." -ForegroundColor Yellow
$watchdog = "$env:USERPROFILE\Downloads\remote\remote-file\agent-watchdog.bat"
if (Test-Path $watchdog) {
    $content = Get-Content $watchdog -Raw
    $content = $content -replace 'start "" pythonw "%~dp0agent.py"',
                                  'start "" "%~dp0pythonw-agent.exe" "%~dp0agent.py"'
    Set-Content -Path $watchdog -Value $content -Encoding ASCII
    Write-Host "       watchdog updated" -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  DONE! Next steps on this machine:" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1. Open ExpressVPN app"
Write-Host "  2. Options -> General -> Split Tunneling -> Settings"
Write-Host "  3. Select: 'Do not allow selected apps to use the VPN'"
Write-Host "  4. Add this file:"
Write-Host ""
Write-Host "       $agentPy" -ForegroundColor Yellow
Write-Host ""
Write-Host "  5. Also add:"
Write-Host "       C:\Program Files\Tailscale\tailscaled.exe" -ForegroundColor Yellow
Write-Host "       C:\Program Files\Tailscale\tailscale.exe" -ForegroundColor Yellow
Write-Host ""
Write-Host "  6. Click OK/Save"
Write-Host ""
Write-Host "  agent.py will never go through ExpressVPN again"
Write-Host "  bot still uses the regular pythonw.exe (gets VPN rotation)"
Write-Host ""
