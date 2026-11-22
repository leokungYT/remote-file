# EVPN-BYPASS-SETUP.ps1 - tell ExpressVPN to NOT tunnel LAN + Tailscale + agent
# Tries multiple CLI preferences + registry + reconnect to apply
#
# Usage on remote PowerShell (as Administrator):
#   iwr https://raw.githubusercontent.com/leokungYT/remote-file/main/EVPN-BYPASS-SETUP.ps1 -UseB | iex

$ErrorActionPreference = "Continue"
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  EVPN-BYPASS-SETUP: force ExpressVPN to allow LAN+Tailscale" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[!] need admin - elevating..." -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -Command `"iwr https://raw.githubusercontent.com/leokungYT/remote-file/main/EVPN-BYPASS-SETUP.ps1 -UseB | iex`""
    exit
}

# ---------- 1) find expressvpnctl ----------
Write-Host ""
Write-Host "[1/5] find expressvpnctl..." -ForegroundColor Yellow
$cli = $null
foreach ($p in @(
    "C:\Program Files (x86)\ExpressVPN\services\expressvpnctl.exe",
    "C:\Program Files\ExpressVPN\services\expressvpnctl.exe",
    "C:\Program Files (x86)\ExpressVPN\expressvpnctl.exe",
    "C:\Program Files\ExpressVPN\expressvpnctl.exe"
)) {
    if (Test-Path $p) { $cli = $p; break }
}
if (-not $cli) { Write-Host "[FAIL] expressvpnctl.exe not found" -ForegroundColor Red; exit 1 }
Write-Host "       $cli" -ForegroundColor Green

# ---------- 2) list all available preferences (discovery) ----------
Write-Host "[2/5] discover available preferences..." -ForegroundColor Yellow
$prefList = & $cli preferences list 2>&1
Write-Host "       (output below if any)" -ForegroundColor Gray
$prefList | Where-Object { $_ -match "split|lan|tunnel|lock|bypass" } | ForEach-Object {
    Write-Host "       > $_" -ForegroundColor DarkGray
}

# ---------- 3) try all plausible CLI commands ----------
Write-Host "[3/5] try CLI commands to allow LAN + disable Network Lock..." -ForegroundColor Yellow
$tries = @(
    @("preferences","set","allow_lan","true"),
    @("preferences","set","allow_local_network","true"),
    @("preferences","set","allow_access_to_local_network","true"),
    @("preferences","set","lan_access","true"),
    @("preferences","set","network_lock","off"),
    @("preferences","set","network_lock","none"),
    @("preferences","set","network-lock","off"),
    @("preferences","set","block_all_traffic","false"),
    @("preferences","set","split_tunneling","on"),
    @("preferences","set","split_tunnel","on"),
    @("preferences","set","inverse_split_tunnel","on"),
    @("options","set","allow_lan","true"),
    @("options","set","network_lock","off"),
    @("set","allow_lan","true"),
    @("set","network_lock","off")
)
$wins = @()
foreach ($t in $tries) {
    $out = & $cli @t 2>&1
    $okstr = "$out"
    if ($LASTEXITCODE -eq 0 -and $okstr -notmatch "error|unknown|invalid|fail") {
        $wins += ($t -join " ")
        Write-Host "       OK: $($t -join ' ')" -ForegroundColor Green
    }
}
if ($wins.Count -eq 0) {
    Write-Host "       [WARN] no CLI command accepted - CLI doesnt support split" -ForegroundColor Yellow
}

# ---------- 4) write all known ExpressVPN registry/config paths ----------
Write-Host "[4/5] write ExpressVPN config files (split tunnel exclude pythonw-agent)..." -ForegroundColor Yellow
$agentExe = "$env:USERPROFILE\Downloads\remote\remote-file\pythonw-agent.exe"
$tsExe = "C:\Program Files\Tailscale\tailscaled.exe"
$tsExe2 = "C:\Program Files\Tailscale\tailscale.exe"

# ExpressVPN keeps settings in various places - write to all
$configPaths = @(
    "$env:LOCALAPPDATA\ExpressVPN\settings.json",
    "$env:APPDATA\ExpressVPN\settings.json",
    "$env:PROGRAMDATA\ExpressVPN\settings.json"
)
foreach ($cfg in $configPaths) {
    $dir = Split-Path $cfg -Parent
    if (Test-Path $dir) {
        try {
            $existing = @{}
            if (Test-Path $cfg) {
                $existing = Get-Content $cfg -Raw | ConvertFrom-Json -ErrorAction SilentlyContinue
                if (-not $existing) { $existing = @{} }
            }
            # merge split tunnel settings
            $existing | Add-Member -NotePropertyName "split_tunneling_enabled" -NotePropertyValue $true -Force
            $existing | Add-Member -NotePropertyName "split_tunneling_mode" -NotePropertyValue "exclude" -Force
            $existing | Add-Member -NotePropertyName "split_tunneling_apps" -NotePropertyValue @($agentExe, $tsExe, $tsExe2) -Force
            $existing | Add-Member -NotePropertyName "allow_lan_access" -NotePropertyValue $true -Force
            $existing | Add-Member -NotePropertyName "network_lock" -NotePropertyValue $false -Force
            $existing | ConvertTo-Json -Depth 8 | Set-Content -Path $cfg -Encoding UTF8
            Write-Host "       wrote $cfg" -ForegroundColor Green
        } catch {
            Write-Host "       [skip] $cfg - $_" -ForegroundColor DarkGray
        }
    }
}

# Also try registry
foreach ($reg in @("HKCU:\Software\ExpressVPN","HKLM:\Software\ExpressVPN","HKLM:\Software\WOW6432Node\ExpressVPN")) {
    if (Test-Path $reg) {
        try {
            Set-ItemProperty -Path $reg -Name "AllowLanAccess" -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
            Set-ItemProperty -Path $reg -Name "NetworkLock" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue
            Set-ItemProperty -Path $reg -Name "SplitTunneling" -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
            Write-Host "       wrote $reg" -ForegroundColor Green
        } catch { }
    }
}

# ---------- 5) restart ExpressVPN to apply ----------
Write-Host "[5/5] restart ExpressVPN to apply settings..." -ForegroundColor Yellow
& $cli disconnect 2>&1 | Out-Null
Start-Sleep -Seconds 2
Stop-Process -Name "ExpressVPN" -Force -ErrorAction SilentlyContinue
Stop-Process -Name "expressvpn-ui" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
$ui = Get-ChildItem "C:\Program Files*\ExpressVPN\expressvpn-ui\ExpressVPN.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ui) {
    Start-Process -FilePath $ui.FullName -WindowStyle Hidden
    Write-Host "       ExpressVPN UI restarted" -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  DONE - verify in ExpressVPN UI: Options -> Split Tunneling" -ForegroundColor Cyan
Write-Host "  Should show these apps excluded:" -ForegroundColor Cyan
Write-Host "    - pythonw-agent.exe" -ForegroundColor Yellow
Write-Host "    - tailscaled.exe" -ForegroundColor Yellow
Write-Host "    - tailscale.exe" -ForegroundColor Yellow
Write-Host ""
Write-Host "  If UI still shows OFF - CLI/registry setup not supported on" -ForegroundColor Yellow
Write-Host "  this ExpressVPN version. Must set manually ONCE via GUI:" -ForegroundColor Yellow
Write-Host "    Options -> Advanced -> Split Tunneling -> Settings" -ForegroundColor Yellow
Write-Host "    'Do not allow selected apps to use the VPN'" -ForegroundColor Yellow
Write-Host "    Add 3 paths above" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
