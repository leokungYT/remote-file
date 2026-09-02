# อัปเดต server_urls (+ ชื่อเครื่องถ้าใส่) ใน config.json — เก็บ field อื่นครบ. ถูกเรียกจาก set-server.bat
param(
    [Parameter(Mandatory=$true)][string]$ip,
    [string]$name
)
$p = Join-Path $PSScriptRoot 'config.json'
if (Test-Path $p) {
    try { $c = Get-Content -Raw $p -ErrorAction Stop | ConvertFrom-Json } catch { $c = [pscustomobject]@{} }
} else {
    $c = [pscustomobject]@{}
}
$url = "http://{0}:5000" -f $ip
$c | Add-Member -NotePropertyName server_urls -NotePropertyValue @($url) -Force
if ($name) {
    $c | Add-Member -NotePropertyName name -NotePropertyValue $name -Force
}
if (-not $c.PSObject.Properties['agent_secret']) {
    $c | Add-Member -NotePropertyName agent_secret -NotePropertyValue '2ec990f60382a004d664f06f99a3e7f5' -Force
}
($c | ConvertTo-Json -Depth 12) | Set-Content -Encoding UTF8 $p
Write-Host ("[OK] server = {0}   name = {1}" -f $url, $name)
