# master-tunnel.ps1 - keep a PUBLIC entry to the master server alive (run on the MASTER pc, e.g. pc_1)
#
#   Problem: the master runs bots with Cloudflare WARP on 24/7. WARP kills Tailscale on the master,
#            so agents outside the master's LAN cannot reach it (Tailscale dead, old Funnel dead).
#   Fix:     open a Cloudflare quick tunnel (free, no account needed) from this pc to :5000 and
#            publish its URL to GitHub (branch "pointer", file master-url.txt). Agents that cannot
#            reach the master through Tailscale/LAN read that file and connect through the tunnel.
#            Cloudflare and GitHub are both reachable with WARP on (WARP is Cloudflare itself).
#
#   Not meant to be run by hand - setup-master-tunnel.bat registers scheduled task "RemoteFileTunnel"
#   that runs it hidden (master_tunnel_hidden.vbs) at logon. For testing:
#       powershell -NoProfile -ExecutionPolicy Bypass -File master-tunnel.ps1 -Once -NoPublish
#
#   Files next to this script:
#       github-token.txt      REQUIRED  GitHub token (fine-grained: repo remote-file, Contents: read+write)
#       cloudflare-token.txt  optional  named-tunnel token -> fixed hostname instead of a random one;
#       tunnel-fixed-url.txt            then put that fixed https URL here (it is what gets published)
#       tunnel-url.txt        output    current public URL
#       master-tunnel.log     output    log (rotated at 2 MB)
#
#   KEEP THIS FILE PURE ASCII (Windows PowerShell 5.1 reads UTF-8 without BOM as ANSI).
param(
    [switch]$Once,        # start, publish once, then stop the tunnel and exit (testing)
    [switch]$NoPublish,   # never touch GitHub (testing)
    [int]$Port = 5000
)

$ErrorActionPreference = 'Continue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$here     = $PSScriptRoot
$Cf       = Join-Path $here 'cloudflared.exe'
$CfUrl    = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
$TokenF   = Join-Path $here 'github-token.txt'
$CfTokenF = Join-Path $here 'cloudflare-token.txt'
$FixedF   = Join-Path $here 'tunnel-fixed-url.txt'
$UrlF     = Join-Path $here 'tunnel-url.txt'
$LogF     = Join-Path $here 'master-tunnel.log'
$CfLogF   = Join-Path $here 'cloudflared.log'
$CfOutF   = Join-Path $here 'cloudflared.out.log'
$Repo     = 'leokungYT/remote-file'
$Branch   = 'pointer'
$FileIn   = 'master-url.txt'
$Metrics  = '127.0.0.1:20241'

function Log([string]$m) {
    $line = ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m)
    Write-Host $line
    try {
        if ((Test-Path $LogF) -and ((Get-Item $LogF).Length -gt 2MB)) { Remove-Item $LogF -Force }
        Add-Content -Path $LogF -Value $line -Encoding UTF8
    } catch {}
}

function Read-First([string]$path) {
    if (-not (Test-Path $path)) { return $null }
    $v = Get-Content -Path $path -TotalCount 1 -ErrorAction SilentlyContinue
    if ($null -eq $v) { return $null }
    $v = ([string]$v).Trim()
    if ($v -eq '') { return $null }
    return $v
}

# ---- single instance ---------------------------------------------------------
$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Global\RFM_MasterTunnel', [ref]$created)
if (-not $created) { Log 'another master-tunnel.ps1 is already running - exit'; exit 0 }

# ---- cloudflared.exe ---------------------------------------------------------
if (-not (Test-Path $Cf)) {
    Log 'downloading cloudflared.exe ...'
    try { Invoke-WebRequest -Uri $CfUrl -OutFile $Cf -UseBasicParsing -TimeoutSec 300 } catch { Log ('download failed: ' + $_.Exception.Message) }
    if (-not (Test-Path $Cf)) { Log 'no cloudflared.exe - exit'; exit 1 }
}

function Stop-OldCloudflared {
    Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$Metrics*" -or $_.CommandLine -like "*127.0.0.1:$Port*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Start-Cloudflared {
    Stop-OldCloudflared
    $cfTok = Read-First $CfTokenF
    if ($cfTok) {
        # named tunnel (fixed hostname, configured in the Cloudflare Zero Trust dashboard)
        $cfArgs = "tunnel run --protocol http2 --no-autoupdate --metrics $Metrics --token $cfTok"
    } else {
        # quick tunnel (random *.trycloudflare.com hostname, no account)
        $cfArgs = "tunnel --url http://127.0.0.1:$Port --protocol http2 --no-autoupdate --metrics $Metrics"
    }
    Remove-Item $CfLogF, $CfOutF -Force -ErrorAction SilentlyContinue
    try {
        return Start-Process -FilePath $Cf -ArgumentList $cfArgs -WindowStyle Hidden -PassThru `
                             -RedirectStandardError $CfLogF -RedirectStandardOutput $CfOutF
    } catch {
        Log ('cannot start cloudflared: ' + $_.Exception.Message)
        return $null
    }
}

function Get-TunnelUrl {
    $fixed = Read-First $FixedF
    if ($fixed) { return $fixed.TrimEnd('/') }
    try {
        $q = Invoke-RestMethod -Uri "http://$Metrics/quicktunnel" -TimeoutSec 3
        if ($q.hostname) { return ('https://' + $q.hostname) }
    } catch {}
    try {
        $m = Select-String -Path $CfLogF -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -Last 1
        if ($m) { return $m.Matches[0].Value }
    } catch {}
    return $null
}

function Test-ServerUp {
    try {
        $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        return ($null -ne $c)
    } catch {
        $n = netstat -ano | Select-String (':{0} ' -f $Port) | Select-String 'LISTENING'
        return ($null -ne $n)
    }
}

function Test-PublicUrl([string]$url) {
    try {
        $r = Invoke-WebRequest -Uri ($url + '/agent.py') -UseBasicParsing -TimeoutSec 20
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Get-PointerFirstUrl([string]$text) {
    foreach ($l in ($text -split "`n")) {
        $s = $l.Trim()
        if ($s -eq '' -or $s.StartsWith('#')) { continue }
        return $s.TrimEnd('/')
    }
    return $null
}

function Publish-Url([string]$url) {
    if ($NoPublish) { Log "(NoPublish) would publish $url"; return $true }
    $token = Read-First $TokenF
    if (-not $token) {
        Log 'github-token.txt missing or empty - cannot publish the URL (agents outside this LAN will not find the master)'
        return $false
    }
    $api = "https://api.github.com/repos/$Repo/contents/$FileIn"
    $h = @{
        Authorization          = "Bearer $token"
        Accept                 = 'application/vnd.github+json'
        'User-Agent'           = 'rfm-master-tunnel'
        'X-GitHub-Api-Version' = '2022-11-28'
    }
    $sha = $null
    try {
        $cur = Invoke-RestMethod -Uri ($api + '?ref=' + $Branch) -Headers $h -TimeoutSec 30
        $sha = $cur.sha
        $curText = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(($cur.content -replace '\s', '')))
        if ((Get-PointerFirstUrl $curText) -eq $url) { Log "pointer on GitHub already = $url"; return $true }
    } catch {
        Log ('read pointer: ' + $_.Exception.Message + ' (will create it)')
    }
    $body = "# Remote File Manager - public entry of the master server`r`n" +
            "# written by master-tunnel.ps1 on $env:COMPUTERNAME at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`r`n" +
            "# agents read the first non-comment line - do not edit by hand while the tunnel task runs`r`n" +
            $url + "`r`n"
    $payload = @{
        message = ('master-url: ' + $url)
        branch  = $Branch
        content = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($body))
    }
    if ($sha) { $payload['sha'] = $sha }
    try {
        Invoke-RestMethod -Method Put -Uri $api -Headers $h -Body (ConvertTo-Json $payload -Compress) `
                          -ContentType 'application/json' -TimeoutSec 30 | Out-Null
        Log "published to GitHub ($Branch/$FileIn): $url"
        return $true
    } catch {
        Log ('publish FAILED: ' + $_.Exception.Message)
        return $false
    }
}

# ---- main loop ---------------------------------------------------------------
Log ('master-tunnel start (port {0}, once={1}, publish={2})' -f $Port, [bool]$Once, (-not [bool]$NoPublish))
Remove-Item $UrlF -Force -ErrorAction SilentlyContinue
$p = $null
while ($true) {
    $p = Start-Cloudflared
    if (-not $p) { if ($Once) { break }; Start-Sleep 30; continue }

    $url = $null
    $t0 = Get-Date
    while (-not $url -and -not $p.HasExited -and ((Get-Date) - $t0).TotalSeconds -lt 90) {
        Start-Sleep 3
        $url = Get-TunnelUrl
    }
    if (-not $url) {
        Log 'no tunnel URL after 90s - restarting cloudflared in 30s'
        try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
        if ($Once) { break }
        Start-Sleep 30
        continue
    }
    Set-Content -Path $UrlF -Value $url -Encoding ASCII
    Log "tunnel up: $url"
    $published = Publish-Url $url
    if ($Once) { break }

    $bad = 0
    $lastHealth = Get-Date
    $lastPubRetry = Get-Date
    $lastVerify = Get-Date
    while (-not $p.HasExited) {
        Start-Sleep 20
        $now = Get-Date
        # token added later / GitHub hiccup -> keep trying to publish
        if (-not $published -and ($now - $lastPubRetry).TotalSeconds -ge 120) {
            $lastPubRetry = $now
            $published = Publish-Url $url
        }
        # health: only judge the tunnel while the server itself is listening
        # (a new hostname needs ~1 min of DNS propagation, so wait before counting)
        if (($now - $lastHealth).TotalSeconds -ge 120 -and ($now - $t0).TotalSeconds -ge 90) {
            $lastHealth = $now
            if (Test-ServerUp) {
                if (Test-PublicUrl $url) { $bad = 0 } else { $bad++; Log "public URL check failed ($bad/3)" }
                if ($bad -ge 3) {
                    Log 'tunnel looks dead - restarting cloudflared'
                    try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
                    break
                }
            }
        }
        # re-assert the pointer now and then (no write happens if it already matches)
        if ($published -and ($now - $lastVerify).TotalMinutes -ge 30) {
            $lastVerify = $now
            $published = Publish-Url $url
        }
    }
    Log 'cloudflared exited - restarting in 5s'
    Start-Sleep 5
}
if ($Once -and $p) { try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {} }
Log 'master-tunnel exit'
