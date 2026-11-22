# BULK-BOOTSTRAP.ps1 - run BOOTSTRAP-AGENT.ps1 on all Tailscale machines
# Usage (on server):
#   iwr https://raw.githubusercontent.com/leokungYT/remote-file/main/BULK-BOOTSTRAP.ps1 -UseB -OutFile $env:TEMP\bulk.ps1
#   & $env:TEMP\bulk.ps1

$AdminUser = "Administrator"
$AdminPassword = Read-Host -AsSecureString -Prompt "Admin password for remote machines"
$Cred = New-Object System.Management.Automation.PSCredential($AdminUser, $AdminPassword)

Write-Host ""
Write-Host "[1/3] enumerate Tailscale machines..." -ForegroundColor Yellow

$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (-not (Test-Path $ts)) {
    Write-Host "[ERR] Tailscale CLI not found at $ts" -ForegroundColor Red
    exit 1
}

$lines = & $ts status | Where-Object { $_ -match "^\s*\d+\.\d+\.\d+\.\d+\s+" }
$machines = @()
foreach ($line in $lines) {
    $parts = $line -split '\s+', 6
    if ($parts.Count -ge 4) {
        $ip = $parts[0]
        $name = $parts[1]
        $status = $parts[4..($parts.Count - 1)] -join ' '
        if ($status -match "offline") { continue }
        if ($name -eq "server") { continue }
        $machines += [PSCustomObject]@{ IP = $ip; Name = $name }
    }
}
Write-Host "       found $($machines.Count) online machines" -ForegroundColor Green
$machines | Format-Table -AutoSize

Write-Host ""
Write-Host "[2/3] fire BOOTSTRAP-AGENT.ps1 at every machine in parallel..." -ForegroundColor Yellow

$RemoteCmd = "iwr https://raw.githubusercontent.com/leokungYT/remote-file/main/BOOTSTRAP-AGENT.ps1 -UseB | iex"

$jobs = foreach ($m in $machines) {
    Start-Job -Name $m.Name -ScriptBlock {
        param($ip, $name, $cred, $cmd)
        try {
            Invoke-Command -ComputerName $ip -Credential $cred -ScriptBlock {
                param($c)
                powershell -ExecutionPolicy Bypass -Command $c 2>&1
            } -ArgumentList $cmd -ErrorAction Stop
            "[OK]   $name ($ip)"
        } catch {
            "[FAIL] $name ($ip): $($_.Exception.Message)"
        }
    } -ArgumentList $m.IP, $m.Name, $Cred, $RemoteCmd
}

Write-Host "       waiting (timeout 180s)..." -ForegroundColor Yellow
Wait-Job -Job $jobs -Timeout 180 | Out-Null

Write-Host ""
Write-Host "[3/3] results:" -ForegroundColor Yellow
foreach ($j in $jobs) {
    $out = Receive-Job -Job $j
    $color = if ("$out" -like "*OK*") { "Green" } else { "Red" }
    foreach ($line in $out) { Write-Host "  $line" -ForegroundColor $color }
}
Get-Job | Remove-Job -Force

Write-Host ""
Write-Host "BULK BOOTSTRAP DONE - check dashboard for online count" -ForegroundColor Cyan
