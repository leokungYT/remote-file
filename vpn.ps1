# ============================================================================
#  VPN (Surfshark ผ่าน WireGuard) - ตรรกะทั้งหมดอยู่ในไฟล์นี้
#
#  ไม่เรียกไฟล์นี้ตรงๆ - ใช้ผ่าน:
#     install-vpn.bat     ติดตั้งครั้งแรก
#     vpn-toggle.bat      เปิด/ปิด
#     uninstall-vpn.bat   ถอนออก
#
#  หมายเหตุสำหรับคนแก้ไฟล์: ไฟล์นี้ต้องเซฟเป็น UTF-8 "พร้อม BOM"
#  ไม่งั้น Windows PowerShell 5.1 จะอ่านเป็น ANSI แล้วภาษาไทยเพี้ยน
#
#  เหตุผลที่ย้ายมาจาก .bat: cmd.exe อ่านไฟล์ที่มีตัวอักษรหลาย byte
#  ร่วมกับ chcp 65001 แล้ว byte offset เพี้ยน โดยเฉพาะหลัง goto
#  ทำให้ข้อความไทยถูกตัดกลางตัวกลายเป็นคำสั่ง
# ============================================================================
param(
    [ValidateSet('Install', 'Toggle', 'Auto', 'Uninstall', 'Status')]
    [string]$Action = 'Toggle',
    [string]$Tunnel = ''
)

# ---------------------------------------------------------------------------
#  ตั้งค่า: $KeepLan = $true  -> วงแลน (10.x / 172.16-31.x / 192.168.x) ยังเข้าได้
#                               remote-file server ที่ :5000 ใช้ผ่านวงแลนได้ปกติ
#           $KeepLan = $false -> ดูดทราฟฟิกทั้งหมดเข้า VPN
# ---------------------------------------------------------------------------
$KeepLan = $true

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
chcp 65001 > $null

$ConfDir   = Join-Path $env:LOCALAPPDATA 'SurfsharkVPN'
$ActiveDir = Join-Path $ConfDir '_active'
$CredFile  = Join-Path $ConfDir 'account.cred'
$PrefFile  = Join-Path $ConfDir 'preferred.txt'
$WgExe     = Join-Path $env:ProgramFiles 'WireGuard\wireguard.exe'
$TaskName  = 'Surfshark VPN AutoConnect'
$SetupUrl  = 'https://my.surfshark.com/vpn/manual-setup/main/wireguard'

# 0.0.0.0/0 ที่หักช่วง private (RFC1918) ออก - ให้วงแลนวิ่งนอก tunnel
$LanSafeIps = '0.0.0.0/5, 8.0.0.0/7, 11.0.0.0/8, 12.0.0.0/6, 16.0.0.0/4, ' +
              '32.0.0.0/3, 64.0.0.0/2, 128.0.0.0/3, 160.0.0.0/5, 168.0.0.0/6, ' +
              '172.0.0.0/12, 172.32.0.0/11, 172.64.0.0/10, 172.128.0.0/9, ' +
              '173.0.0.0/8, 174.0.0.0/7, 176.0.0.0/4, 192.0.0.0/9, ' +
              '192.128.0.0/11, 192.160.0.0/13, 192.169.0.0/16, 192.170.0.0/15, ' +
              '192.172.0.0/14, 192.176.0.0/12, 192.192.0.0/10, 193.0.0.0/8, ' +
              '194.0.0.0/7, 196.0.0.0/6, 200.0.0.0/5, 208.0.0.0/4'

# ---------------------------------------------------------------------------
#  helper
# ---------------------------------------------------------------------------
function Say([string]$t, [string]$c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Line { Write-Host ('  ' + ('-' * 58)) -ForegroundColor DarkGray }
function Head([string]$t) {
    Write-Host ''
    Write-Host ('  ' + ('=' * 58)) -ForegroundColor Cyan
    Write-Host ('    ' + $t) -ForegroundColor Cyan
    Write-Host ('  ' + ('=' * 58)) -ForegroundColor Cyan
}

function Ensure-ConfDir {
    if (-not (Test-Path -LiteralPath $ConfDir)) {
        New-Item -ItemType Directory -Path $ConfDir -Force | Out-Null
    }
}

function Svc([string]$name) { 'WireGuardTunnel$' + $name }

function Get-SvcState([string]$name) {
    $s = Get-Service -Name (Svc $name) -ErrorAction SilentlyContinue
    if (-not $s) { return 'none' }
    if ($s.Status -eq 'Running') { return 'running' }
    return 'stopped'
}

function Get-AllTunnelSvc {
    Get-Service -Name 'WireGuardTunnel$*' -ErrorAction SilentlyContinue
}

function Pick-Tunnel([string]$want) {
    if ($want) {
        if (Test-Path -LiteralPath (Join-Path $ConfDir "$want.conf")) { return $want }
        return $null
    }
    if (Test-Path -LiteralPath $PrefFile) {
        $p = (Get-Content -LiteralPath $PrefFile -TotalCount 1).Trim()
        if ($p -and (Test-Path -LiteralPath (Join-Path $ConfDir "$p.conf"))) { return $p }
    }
    $f = @(Get-ChildItem -LiteralPath $ConfDir -Filter '*.conf' -File -ErrorAction SilentlyContinue |
           Sort-Object Name)
    if ($f.Count -gt 0) { return $f[0].BaseName }
    return $null
}

function Show-Ip {
    Write-Host ''
    Line
    try {
        $ip = (Invoke-RestMethod -Uri 'https://ipinfo.io/json' -TimeoutSec 10)
        Say ("   IP ตอนนี้  : " + $ip.ip) 'White'
        Say ("   ประเทศ    : " + $ip.country + "   " + $ip.city) 'White'
    } catch {
        Say '   (เช็ค IP ไม่ได้ - อาจยังไม่มีเน็ต)' 'DarkYellow'
    }
    Line
}

# ----- บัญชี (เข้ารหัสด้วย DPAPI - ถอดได้เฉพาะ user นี้บนเครื่องนี้) -----
function Protect-Field([string]$v) {
    ConvertTo-SecureString $v -AsPlainText -Force | ConvertFrom-SecureString
}
function Unprotect-Field([string]$key) {
    if (-not (Test-Path -LiteralPath $CredFile)) { return $null }
    $line = @(Get-Content -LiteralPath $CredFile | Where-Object { $_ -like "$key=*" })
    if ($line.Count -eq 0) { return $null }
    $sec = ConvertTo-SecureString $line[0].Substring($key.Length + 1)
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}
function Save-Cred([string]$em, [string]$pw) {
    Ensure-ConfDir
    Set-Content -LiteralPath $CredFile -Encoding ascii -Value @(
        "EMAIL=$(Protect-Field $em)"
        "PASS=$(Protect-Field $pw)"
    )
    icacls $CredFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" "SYSTEM:(R,W)" | Out-Null
}
function SecureToPlain($sec) {
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

# ----- แก้ AllowedIPs ให้เว้นวงแลน (ต้นฉบับไม่ถูกแตะ) -----
function New-PatchedConf([string]$name) {
    if (-not (Test-Path -LiteralPath $ActiveDir)) {
        New-Item -ItemType Directory -Path $ActiveDir -Force | Out-Null
    }
    $src = Join-Path $ConfDir   "$name.conf"
    $dst = Join-Path $ActiveDir "$name.conf"
    if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }

    $c = Get-Content -LiteralPath $src
    $r = $LanSafeIps
    # ถ้าต้นฉบับมี ::/0 ต้องคงไว้ ไม่งั้น IPv6 จะรั่วออกนอก tunnel
    if ($c -match '::/0') { $r = $r + ', ::/0' }
    ($c -replace '^\s*AllowedIPs\s*=.*', ('AllowedIPs = ' + $r)) |
        Set-Content -LiteralPath $dst -Encoding ascii
    return $dst
}

# ----- ต่อ / ตัด -----
function Connect-Vpn([string]$name, [bool]$quiet = $false) {
    if (-not $quiet) {
        Write-Host ''
        Say "  กำลังเปิด VPN ... [$name]" 'Yellow'
        if ($KeepLan) { Say '  โหมด: เว้นวงแลนไว้ (เข้า server ที่ 192.168.x.x ได้)' 'DarkGray' }
        else          { Say '  โหมด: ดูดทราฟฟิกทั้งหมด (วงแลนจะเข้าไม่ได้)'        'DarkGray' }
    }

    $conf = Join-Path $ConfDir "$name.conf"
    if ($KeepLan) {
        try { $conf = New-PatchedConf $name }
        catch {
            if (-not $quiet) { Say "  [ERROR] แก้ config ไม่สำเร็จ: $_" 'Red' }
            return 5
        }
    }

    & $WgExe /installtunnelservice $conf 2>&1 | Out-Null
    Start-Sleep -Seconds 5

    if ((Get-SvcState $name) -ne 'running') {
        & $WgExe /uninstalltunnelservice $name 2>&1 | Out-Null
        if (-not $quiet) {
            Say '  [ERROR] tunnel ไม่ทำงาน - config อาจใช้ไม่ได้หรือหมดอายุ' 'Red'
            Say '          ลองโหลด .conf ใหม่จาก my.surfshark.com'          'Red'
        }
        return 7
    }

    # ให้ WireGuard ยก tunnel เองตอนเปิดเครื่อง
    & sc.exe config (Svc $name) start= auto | Out-Null
    return 0
}

function Disconnect-Vpn([string]$name) {
    Write-Host ''
    Say "  กำลังปิด VPN ... [$name]" 'Yellow'
    & $WgExe /uninstalltunnelservice $name 2>&1 | Out-Null
    Start-Sleep -Seconds 3
    $p = Join-Path $ActiveDir "$name.conf"
    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }
}

# ----- Scheduled Task -----
function Install-AutoTask([string]$dir) {
    $vbs = Join-Path $dir 'vpn-silent.vbs'
    if (-not (Test-Path -LiteralPath $vbs)) { throw "ไม่พบ $vbs" }

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

    # อย่าตั้งชื่อตัวแปรว่า $action - PowerShell ไม่สนตัวพิมพ์ใหญ่เล็ก
    # มันจะไปชนกับ param [string]$Action แล้วโดนแปลงเป็น string
    $taskAction = New-ScheduledTaskAction `
        -Execute (Join-Path $env:SystemRoot 'System32\wscript.exe') `
        -Argument ('"' + $vbs + '"')
    $tLogon  = New-ScheduledTaskTrigger -AtLogOn
    $tRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
                   -RepetitionInterval (New-TimeSpan -Minutes 5)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
                     -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                    -DontStopIfGoingOnBatteries -StartWhenAvailable `
                    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

    Register-ScheduledTask -TaskName $TaskName -Action $taskAction `
        -Trigger @($tLogon, $tRepeat) -Principal $principal -Settings $settings | Out-Null
}

function New-DesktopShortcut([string]$dir) {
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'VPN Toggle.lnk'
    $w = New-Object -ComObject WScript.Shell
    $s = $w.CreateShortcut($lnk)
    $s.TargetPath       = Join-Path $dir 'vpn-toggle.bat'
    $s.WorkingDirectory = $dir
    $s.IconLocation     = "$WgExe,0"
    $s.Description      = 'เปิด/ปิด VPN คลิกเดียว'
    $s.Save()
    return (Test-Path -LiteralPath $lnk)
}

# ===========================================================================
#  ACTIONS
# ===========================================================================
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

switch ($Action) {

# ---------------------------------------------------------------------------
'Install' {
    Head 'ติดตั้ง VPN อัตโนมัติ  -  Surfshark + WireGuard'

    # ----- [1/6] WireGuard -----
    Write-Host ''
    Say '  [1/6] ตรวจ WireGuard ...' 'White'
    if (Test-Path -LiteralPath $WgExe) {
        Say '        มีอยู่แล้ว - ข้าม'
    } else {
        Say '        ยังไม่มี - กำลังติดตั้งผ่าน winget (รอสักครู่) ...'
        & winget install --id WireGuard.WireGuard --exact --silent `
            --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | Out-Null
        if (-not (Test-Path -LiteralPath $WgExe)) {
            Say '  [ERROR] ติดตั้ง WireGuard ไม่สำเร็จ' 'Red'
            Say '          โหลดเองที่ https://www.wireguard.com/install/' 'Red'
            exit 1
        }
        Say '        [OK] ติดตั้งแล้ว' 'Green'
    }

    # ----- [2/6] บัญชี -----
    Ensure-ConfDir
    Write-Host ''
    Say '  [2/6] ตรวจบัญชี Surfshark ที่เก็บไว้ ...' 'White'
    if (Test-Path -LiteralPath $CredFile) {
        Say ('        มีอยู่แล้ว: ' + (Unprotect-Field 'EMAIL'))
    } else {
        Say '        ยังไม่มี - ใส่บัญชี Surfshark (รหัสผ่านจะไม่แสดงบนจอ)'
        Write-Host ''
        $em = Read-Host '        อีเมล'
        $pw = SecureToPlain (Read-Host '        รหัสผ่าน' -AsSecureString)
        Save-Cred $em $pw
        Say '        [OK] เก็บแบบเข้ารหัสแล้ว (DPAPI - เครื่องอื่นถอดไม่ได้)' 'Green'
    }

    # ----- [3/6] config -----
    Write-Host ''
    Say '  [3/6] ตรวจไฟล์ config ของ WireGuard ...' 'White'
    $have = @(Get-ChildItem -LiteralPath $ConfDir -Filter '*.conf' -File -ErrorAction SilentlyContinue)
    if ($have.Count -eq 0) {
        Say '        ยังไม่มี - ต้องโหลดจากเว็บครั้งเดียว'
        $pw = Unprotect-Field 'PASS'
        if ($pw) { Set-Clipboard -Value $pw }
        Write-Host ''
        Line
        Say ('    อีเมล      : ' + (Unprotect-Field 'EMAIL')) 'White'
        Say  '    รหัสผ่าน   : ก๊อปใส่คลิปบอร์ดให้แล้ว - กด Ctrl+V ได้เลย' 'White'
        Write-Host ''
        Say  '    ทำตามนี้:' 'Yellow'
        Say  '      1. ล็อกอินด้วยอีเมล/รหัสผ่านข้างบน'
        Say  '      2. หน้า Manual setup > WireGuard > กด "I don''t have a key pair"'
        Say  '      3. กด Generate new key pair'
        Say  '      4. เลือกประเทศที่ต้องการ > กด Download'
        Say  '      5. ลากไฟล์ .conf มาวางในโฟลเดอร์ที่เปิดค้างไว้'
        Write-Host ''
        Say  '    (หน้าต่างนี้จะรอจนกว่าจะเจอไฟล์ - ไม่ต้องปิด)' 'DarkGray'
        Line

        # explorer.exe = เปิดด้วยสิทธิ์ปกติ ไม่ใช่ Administrator
        Start-Process explorer.exe -ArgumentList $ConfDir
        Start-Process explorer.exe -ArgumentList $SetupUrl

        $waited = 0
        while ($true) {
            $have = @(Get-ChildItem -LiteralPath $ConfDir -Filter '*.conf' -File -ErrorAction SilentlyContinue)
            if ($have.Count -gt 0) { break }
            Start-Sleep -Seconds 5
            $waited += 5
            Write-Host '.' -NoNewline
            if ($waited -ge 900) {
                Write-Host ''
                Say '  [!] รอ 15 นาทีแล้วยังไม่เจอไฟล์ .conf' 'Red'
                Say  "      เอาไฟล์ไปวางที่ $ConfDir แล้วรัน install-vpn.bat ใหม่" 'Red'
                Set-Clipboard -Value ' '
                exit 1
            }
        }
        Write-Host ''
        Say '        [OK] เจอไฟล์ config แล้ว' 'Green'
        Set-Clipboard -Value ' '
    }

    $t = Pick-Tunnel $Tunnel
    if (-not $t) { Say '  [ERROR] เลือกไฟล์ config ไม่ได้' 'Red'; exit 4 }
    Set-Content -LiteralPath $PrefFile -Value $t -Encoding ascii
    Say ('        เซิร์ฟเวอร์ที่จะใช้: ' + $t)

    # ----- [4/6] ต่อ -----
    Write-Host ''
    Say '  [4/6] ต่อ VPN ...' 'White'
    if ((Get-SvcState $t) -eq 'running') {
        Say '        ต่ออยู่แล้ว - ข้าม' 'Green'
    } else {
        $rc = Connect-Vpn $t
        if ($rc -ne 0) {
            Say '        ลองรัน vpn-toggle.bat ตรงๆ เพื่อดูข้อความเต็ม' 'Red'
            exit $rc
        }
        Say '        [OK] ต่อแล้ว' 'Green'
    }

    # ----- [5/6] auto-connect -----
    Write-Host ''
    Say '  [5/6] ตั้งให้ต่อ VPN เองอัตโนมัติ ...' 'White'
    try {
        Install-AutoTask $ScriptDir
        Say '        [OK] ต่อเองตอนเข้าเครื่อง + เช็คซ้ำทุก 5 นาที' 'Green'
    } catch {
        Say "        [!] สร้าง Scheduled Task ไม่สำเร็จ: $_" 'DarkYellow'
        Say  '            WireGuard ยังตั้ง service เป็น auto-start ไว้ให้แล้ว' 'DarkYellow'
    }

    # ----- [6/6] ช็อตคัต -----
    Write-Host ''
    Say '  [6/6] สร้างช็อตคัตบนเดสก์ท็อป ...' 'White'
    try {
        if (New-DesktopShortcut $ScriptDir) { Say '        [OK] "VPN Toggle" อยู่บนเดสก์ท็อปแล้ว' 'Green' }
        else { Say '        [!] สร้างไม่สำเร็จ - เปิด vpn-toggle.bat ตรงๆ ได้เหมือนกัน' 'DarkYellow' }
    } catch {
        Say '        [!] สร้างไม่สำเร็จ - เปิด vpn-toggle.bat ตรงๆ ได้เหมือนกัน' 'DarkYellow'
    }

    Head 'เสร็จแล้ว'
    Show-Ip
    Write-Host ''
    Say '    ต่อไปนี้ไม่ต้องทำอะไรอีก - เปิดเครื่องมาก็ต่อ VPN เอง' 'Green'
    Write-Host ''
    Say  '    เปิด/ปิดเอง   : ดับเบิลคลิก "VPN Toggle" บนเดสก์ท็อป'
    Say  '    สลับประเทศ    : โหลด .conf เพิ่มไปวางที่'
    Say  "                    $ConfDir"
    Say  '                    แล้วสั่ง  vpn-toggle.bat ชื่อไฟล์'
    Say  '    เลิกต่ออัตโนมัติ: uninstall-vpn.bat'
    Write-Host ''
    exit 0
}

# ---------------------------------------------------------------------------
'Toggle' {
    if (-not (Test-Path -LiteralPath $WgExe)) {
        Say '  [ERROR] ไม่พบ WireGuard - รัน install-vpn.bat ก่อน' 'Red'
        exit 3
    }
    $t = Pick-Tunnel $Tunnel
    if (-not $t) {
        Head 'ยังไม่มีไฟล์ config'
        Write-Host ''
        Say '    รัน  install-vpn.bat  แทน - มันจะพาทำทีละขั้นจนต่อ VPN ได้เลย' 'Yellow'
        Write-Host ''
        exit 4
    }

    switch (Get-SvcState $t) {
        'running' {
            Disconnect-Vpn $t
            Write-Host ''
            Say '  [OK] VPN ปิดแล้ว - กลับมาใช้เน็ตปกติ' 'Green'
            Say  '  * ถ้าตั้ง auto-connect ไว้ มันจะต่อกลับตอนเข้าเครื่องครั้งหน้า' 'DarkGray'
            Show-Ip
            exit 0
        }
        'stopped' {
            Say "  [!] tunnel ค้างอยู่ - กำลังสตาร์ตใหม่ ... [$t]" 'DarkYellow'
            Start-Service -Name (Svc $t) -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 3
            if ((Get-SvcState $t) -eq 'running') {
                Write-Host ''
                Say '  [OK] VPN กลับมาแล้ว' 'Green'
                Show-Ip
                exit 0
            }
            Say '  [!] สตาร์ตไม่ขึ้น - ล้างของค้างแล้วสร้าง tunnel ใหม่' 'DarkYellow'
            & $WgExe /uninstalltunnelservice $t 2>&1 | Out-Null
            Start-Sleep -Seconds 3
        }
    }

    $rc = Connect-Vpn $t
    if ($rc -ne 0) { exit $rc }
    Write-Host ''
    Say '  [OK] VPN เปิดแล้ว' 'Green'
    Show-Ip
    exit 0
}

# ---------------------------------------------------------------------------
#  ใช้โดย Scheduled Task - เงียบ ไม่ถาม ไม่ตัดการเชื่อมต่อที่ใช้ได้อยู่
'Auto' {
    if (-not (Test-Path -LiteralPath $WgExe)) { exit 3 }
    $t = Pick-Tunnel $Tunnel
    if (-not $t) { exit 4 }

    switch (Get-SvcState $t) {
        'running' { exit 0 }
        'stopped' {
            Start-Service -Name (Svc $t) -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 3
            if ((Get-SvcState $t) -eq 'running') { exit 0 }
            & $WgExe /uninstalltunnelservice $t 2>&1 | Out-Null
            Start-Sleep -Seconds 3
        }
    }
    exit (Connect-Vpn $t $true)
}

# ---------------------------------------------------------------------------
'Status' {
    $t = Pick-Tunnel $Tunnel
    if (-not $t) { Say '  ยังไม่มี config' 'DarkYellow'; exit 4 }
    Say ("  tunnel : $t")
    Say ("  สถานะ  : " + (Get-SvcState $t))
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) { Say ("  auto   : ตั้งไว้แล้ว (" + $task.State + ")") }
    else       { Say  '  auto   : ยังไม่ได้ตั้ง' }
    Show-Ip
    exit 0
}

# ---------------------------------------------------------------------------
'Uninstall' {
    Head 'เลิกใช้ VPN อัตโนมัติ'

    Write-Host ''
    Say '  [1/4] ลบ Scheduled Task ...' 'White'
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Say '        [OK] ลบแล้ว' 'Green'

    Write-Host ''
    Say '  [2/4] ตัด VPN + ลบ tunnel service ...' 'White'
    $svcs = @(Get-AllTunnelSvc)
    if ($svcs.Count -eq 0) {
        Say '        ไม่มี tunnel ค้างอยู่'
    } else {
        foreach ($s in $svcs) {
            $n = $s.Name.Substring('WireGuardTunnel$'.Length)
            Say "        - $n"
            if (Test-Path -LiteralPath $WgExe) { & $WgExe /uninstalltunnelservice $n 2>&1 | Out-Null }
            else { & sc.exe delete $s.Name | Out-Null }
        }
        Start-Sleep -Seconds 3
    }
    if (Test-Path -LiteralPath $ActiveDir) {
        Remove-Item -LiteralPath $ActiveDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host ''
    Say '  [3/4] ลบช็อตคัตบนเดสก์ท็อป ...' 'White'
    $desk = [Environment]::GetFolderPath('Desktop')
    foreach ($l in @('VPN Toggle.lnk', 'VPN - Surfshark.lnk')) {
        $p = Join-Path $desk $l
        if (Test-Path -LiteralPath $p) {
            Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue
            Say "        - ลบ $l"
        }
    }

    Write-Host ''
    Say '  [4/4] จะลบไฟล์ config และบัญชีที่เก็บไว้ด้วยไหม?' 'White'
    Say  "        โฟลเดอร์: $ConfDir"
    Say  '        (ถ้าลบ ต้องโหลด .conf ใหม่ตอนติดตั้งรอบหน้า)' 'DarkGray'
    Write-Host ''
    $ans = Read-Host '        พิมพ์ y แล้ว Enter เพื่อลบ / Enter เฉยๆ เพื่อเก็บไว้'
    if ($ans -eq 'y') {
        if (Test-Path -LiteralPath $ConfDir) {
            Remove-Item -LiteralPath $ConfDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        Say '        [OK] ลบแล้ว' 'Green'
    } else {
        Say '        เก็บไว้ตามเดิม'
    }

    Head 'เรียบร้อย - VPN ไม่ต่ออัตโนมัติแล้ว'
    Show-Ip
    Write-Host ''
    Say '    ยังไม่ได้ถอน WireGuard ออก' 'DarkGray'
    Say '    ถ้าจะถอนด้วย: winget uninstall WireGuard.WireGuard' 'DarkGray'
    Write-Host ''
    exit 0
}

}
