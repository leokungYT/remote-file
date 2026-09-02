# ติดตั้ง watchdog: เช็กทุก 2 นาที ถ้า server (:5000) ดับ -> ปลุกกลับเอง
$here = $PSScriptRoot
$vbs  = Join-Path $here 'watchdog_hidden.vbs'
if (-not (Test-Path $vbs)) {
    Write-Host "[ERROR] ไม่พบ watchdog_hidden.vbs ในโฟลเดอร์นี้ - ต้องมีไฟล์นี้ก่อน"
    exit 1
}
$a = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"' + $vbs + '"')
$t = New-ScheduledTaskTrigger -AtLogOn
$r = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 2)
$t.Repetition = $r.Repetition
$p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive
Register-ScheduledTask -TaskName 'RemoteFileServerWatchdog' -Action $a -Trigger $t -Principal $p -Force | Out-Null
Start-ScheduledTask -TaskName 'RemoteFileServerWatchdog'
Write-Host "[OK] ติดตั้ง watchdog + สั่งเช็ก/ปลุก server แล้ว"
Write-Host "     server จะถูกเช็กทุก 2 นาที - ดับเมื่อไรปลุกเองอัตโนมัติ"
