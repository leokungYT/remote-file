# Install watchdog: check every 2 min, restart the master server if :5000 is down.
$here = $PSScriptRoot
$vbs  = Join-Path $here 'watchdog_hidden.vbs'
if (-not (Test-Path $vbs)) {
    Write-Host "[ERROR] watchdog_hidden.vbs not found in this folder - it must exist first."
    exit 1
}
$a = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"' + $vbs + '"')
$t = New-ScheduledTaskTrigger -AtLogOn
$r = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 2)
$t.Repetition = $r.Repetition
$p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive
Register-ScheduledTask -TaskName 'RemoteFileServerWatchdog' -Action $a -Trigger $t -Principal $p -Force | Out-Null
Start-ScheduledTask -TaskName 'RemoteFileServerWatchdog'
Write-Host "[OK] Watchdog installed and started."
Write-Host "     The server is checked every 2 minutes and auto-restarted if it goes down."
