# ═══════════════════════════════════════════════════════════════════
#  BULK-BOOTSTRAP.ps1 - รัน BOOTSTRAP-AGENT.ps1 บนทุก Tailscale machine
#  (รันจากเครื่อง server ครั้งเดียว = ทุกเครื่อง recovery พร้อมกัน)
#
#  Usage:
#   1) ตั้ง $AdminPassword เป็นรหัสผ่าน Administrator ของเครื่อง remote
#      (ถ้าทุกเครื่องใช้รหัสเดียวกัน)
#   2) รัน: .\BULK-BOOTSTRAP.ps1
# ═══════════════════════════════════════════════════════════════════

$AdminUser = "Administrator"
$AdminPassword = Read-Host -AsSecureString -Prompt "รหัสผ่าน Administrator ของเครื่อง remote"
$Cred = New-Object System.Management.Automation.PSCredential($AdminUser, $AdminPassword)

# --- 1) ดึงรายชื่อ Tailscale machines ---
Write-Host "`n[1/3] ดึงรายชื่อเครื่องจาก Tailscale..." -ForegroundColor Yellow
$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (-not (Test-Path $ts)) {
    Write-Host "[ERR] Tailscale CLI ไม่พบที่ $ts" -ForegroundColor Red
    exit 1
}
$lines = & $ts status | Where-Object { $_ -match "^\s*\d+\.\d+\.\d+\.\d+\s+" }
$machines = @()
foreach ($line in $lines) {
    $parts = $line -split '\s+', 6
    if ($parts.Count -ge 4) {
        $ip = $parts[0]
        $name = $parts[1]
        $status = $parts[4..($parts.Count-1)] -join ' '
        # ข้าม offline + ข้าม server เอง
        if ($status -match "offline") { continue }
        if ($name -eq "server") { continue }
        $machines += [PSCustomObject]@{ IP = $ip; Name = $name }
    }
}
Write-Host "       เจอ $($machines.Count) เครื่อง online" -ForegroundColor Green
$machines | Format-Table -AutoSize

# --- 2) รัน bootstrap บนทุกเครื่องพร้อมกัน (parallel) ---
Write-Host "`n[2/3] ยิง BOOTSTRAP-AGENT.ps1 ไปทุกเครื่องพร้อมกัน..." -ForegroundColor Yellow
$RemoteCmd = "iwr https://raw.githubusercontent.com/leokungYT/remote-file/main/BOOTSTRAP-AGENT.ps1 -UseB | iex"

$jobs = foreach ($m in $machines) {
    Start-Job -Name $m.Name -ScriptBlock {
        param($ip, $name, $cred, $cmd)
        try {
            # ใช้ WinRM (ต้องเปิดก่อน: winrm quickconfig)
            Invoke-Command -ComputerName $ip -Credential $cred -ScriptBlock {
                param($c)
                powershell -ExecutionPolicy Bypass -Command $c 2>&1
            } -ArgumentList $cmd -ErrorAction Stop -AsJob:$false
            "[OK] $name ($ip)"
        } catch {
            "[FAIL] $name ($ip): $($_.Exception.Message)"
        }
    } -ArgumentList $m.IP, $m.Name, $Cred, $RemoteCmd
}

Write-Host "       รอทุก job จบ (timeout 120s)..." -ForegroundColor Yellow
Wait-Job -Job $jobs -Timeout 120 | Out-Null

# --- 3) รายงานผล ---
Write-Host "`n[3/3] สรุปผล:" -ForegroundColor Yellow
foreach ($j in $jobs) {
    $out = Receive-Job -Job $j
    $color = if ($out -like "*OK*") { "Green" } else { "Red" }
    Write-Host "  $out" -ForegroundColor $color
}
Get-Job | Remove-Job -Force

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "  BULK BOOTSTRAP เสร็จ - เช็ค dashboard ว่าออนไลน์กี่เครื่อง" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
