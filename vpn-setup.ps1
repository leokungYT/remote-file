# ---------------------------------------------------------------------------
#  Helper for install-vpn.bat / vpn-toggle.bat
#  Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less files as
#  ANSI, so Thai text here would be mangled. Thai lives in the .bat files.
#
#  Credentials are stored with DPAPI (ConvertFrom-SecureString), so the file
#  can only be decrypted by this Windows user on this machine.
# ---------------------------------------------------------------------------
param(
    [Parameter(Mandatory = $true)][string]$Action,
    [string]$Email,
    [string]$Password,
    [string]$ScriptDir
)

$ErrorActionPreference = 'Stop'

$dir      = Join-Path $env:LOCALAPPDATA 'SurfsharkVPN'
$credFile = Join-Path $dir 'account.cred'
$taskName = 'Surfshark VPN AutoConnect'

function Ensure-Dir {
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}

function Protect-Field([string]$v) {
    ConvertTo-SecureString $v -AsPlainText -Force | ConvertFrom-SecureString
}

function Unprotect-Field([string]$key) {
    if (-not (Test-Path -LiteralPath $credFile)) { return $null }
    $line = @(Get-Content -LiteralPath $credFile | Where-Object { $_ -like "$key=*" })
    if ($line.Count -eq 0) { return $null }
    $sec = ConvertTo-SecureString $line[0].Substring($key.Length + 1)
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

function SecureString-ToPlain($sec) {
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

switch ($Action) {

    'HasCred' {
        if (Test-Path -LiteralPath $credFile) { 'YES' } else { 'NO' }
    }

    'SaveCred' {
        Ensure-Dir
        if (-not $Email)    { $Email = Read-Host 'Surfshark email' }
        if (-not $Password) { $Password = SecureString-ToPlain (Read-Host 'Surfshark password' -AsSecureString) }
        Set-Content -LiteralPath $credFile -Encoding ascii -Value @(
            "EMAIL=$(Protect-Field $Email)"
            "PASS=$(Protect-Field $Password)"
        )
        # owner + SYSTEM only
        icacls $credFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" "SYSTEM:(R,W)" | Out-Null
        'OK'
    }

    'ShowEmail' {
        $e = Unprotect-Field 'EMAIL'
        if ($e) { $e } else { '(none)' }
    }

    'CopyPass' {
        $p = Unprotect-Field 'PASS'
        if ($p) { Set-Clipboard -Value $p; 'COPIED' } else { 'NONE' }
    }

    'CopyEmail' {
        $e = Unprotect-Field 'EMAIL'
        if ($e) { Set-Clipboard -Value $e; 'COPIED' } else { 'NONE' }
    }

    'ClearClip' {
        Set-Clipboard -Value ' '
        'CLEARED'
    }

    'MakeTask' {
        if (-not $ScriptDir) { throw 'MakeTask needs -ScriptDir' }
        $vbs = Join-Path $ScriptDir 'vpn-silent.vbs'
        if (-not (Test-Path -LiteralPath $vbs)) { throw "missing $vbs" }

        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

        # NOTE: do not name this $action - PowerShell variable names are
        # case-insensitive, so it would collide with the [string]$Action
        # parameter and silently get cast to a string.
        $taskAction = New-ScheduledTaskAction `
            -Execute (Join-Path $env:SystemRoot 'System32\wscript.exe') `
            -Argument ('"' + $vbs + '"')

        # two triggers: once at logon, then re-check every 5 minutes forever
        $tLogon  = New-ScheduledTaskTrigger -AtLogOn
        $tRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
                       -RepetitionInterval (New-TimeSpan -Minutes 5)

        $principal = New-ScheduledTaskPrincipal `
            -UserId "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType Interactive -RunLevel Highest

        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

        Register-ScheduledTask -TaskName $taskName -Action $taskAction `
            -Trigger @($tLogon, $tRepeat) -Principal $principal -Settings $settings | Out-Null

        'TASK-OK'
    }

    'TaskStatus' {
        $t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($t) { "EXISTS state=$($t.State)" } else { 'MISSING' }
    }

    'RemoveTask' {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        'REMOVED'
    }

    default { throw "unknown action: $Action" }
}
