# Host /32 routes for remna-agent node IPs via the LAN default gateway.
# Online ping/SSH from Docker Desktop otherwise follow VupenVPN (Wintun):
# ICMP answers locally (TTL 64, <2 ms) and the dashboard mixes "VPN" vs "direct" RTTs.
#
#   powershell -File scripts/windows/sync-direct-routes.ps1
#   powershell -File scripts/windows/sync-direct-routes.ps1 -Remove
#
# Needs Administrator (New-NetRoute). Re-run after Vupen reconnect if /32s vanish.

[CmdletBinding()]
param(
  [switch]$Remove,
  [string]$DbContainer = "remna-agent-db-1",
  [string]$DbUser = "remna",
  [string]$DbName = "remna_agent"
)

$ErrorActionPreference = "Stop"

function Get-NodeHosts {
  $raw = docker exec $DbContainer psql -U $DbUser -d $DbName -Atc "SELECT host FROM nodes;"
  if ($LASTEXITCODE -ne 0) {
    throw "docker exec $DbContainer failed (is remna-agent up?)"
  }
  $ips = @()
  foreach ($line in $raw -split "`n") {
    $h = $line.Trim()
    if (-not $h) { continue }
    $addr = $null
    if ([System.Net.IPAddress]::TryParse($h, [ref]$addr) -and $addr.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork) {
      $b = $addr.GetAddressBytes()[0]
      if ($b -eq 10 -or $b -eq 127 -or $b -ge 224) { continue }
      if ($b -eq 192 -and $addr.GetAddressBytes()[1] -eq 168) { continue }
      if ($b -eq 172 -and $addr.GetAddressBytes()[1] -ge 16 -and $addr.GetAddressBytes()[1] -le 31) { continue }
      $ips += $h
    }
  }
  return $ips | Select-Object -Unique
}

function Get-LanDefault {
  $def = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -AddressFamily IPv4 |
    Where-Object {
      $_.NextHop -ne "0.0.0.0" -and
      $_.InterfaceAlias -notmatch "Vupen|Wintun|Loopback|WSL|vEthernet|VPN|TUN|Tap"
    } |
    Sort-Object RouteMetric, InterfaceMetric |
    Select-Object -First 1
  if (-not $def) {
    throw "No LAN default route (Ethernet / Wi-Fi). Is the cable/Wi-Fi up?"
  }
  return $def
}

$ident = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $ident.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Run elevated: New-NetRoute needs Administrator."
}

$ips = @(Get-NodeHosts)
if ($ips.Count -eq 0) {
  throw "No public IPv4 node hosts in $DbContainer.$DbName"
}

$lan = Get-LanDefault
$ifIndex = $lan.InterfaceIndex
$gw = $lan.NextHop
$alias = $lan.InterfaceAlias
Write-Host "LAN: $alias  gw $gw  ifIndex $ifIndex"
Write-Host ("Nodes: {0}" -f $ips.Count)

$added = 0
$removed = 0
$skipped = 0

foreach ($ip in $ips) {
  $prefix = "$ip/32"
  $existing = @(Get-NetRoute -DestinationPrefix $prefix -AddressFamily IPv4 -ErrorAction SilentlyContinue)

  if ($Remove) {
    foreach ($r in $existing) {
      if ($r.InterfaceIndex -eq $ifIndex -and $r.NextHop -eq $gw) {
        foreach ($store in @("ActiveStore", "PersistentStore")) {
          Remove-NetRoute -DestinationPrefix $prefix -InterfaceIndex $ifIndex -NextHop $gw -PolicyStore $store -Confirm:$false -ErrorAction SilentlyContinue
        }
        $removed += 1
        Write-Host "removed $prefix"
      }
    }
    continue
  }

  $already = $existing | Where-Object { $_.InterfaceIndex -eq $ifIndex -and $_.NextHop -eq $gw }
  if ($already) {
    $skipped += 1
    continue
  }

  New-NetRoute -DestinationPrefix $prefix -InterfaceIndex $ifIndex -NextHop $gw -RouteMetric 1 -PolicyStore PersistentStore | Out-Null
  $added += 1
  Write-Host "direct $prefix -> $gw"
}

if ($Remove) {
  Write-Host "Done: removed $removed"
} else {
  Write-Host "Done: added $added, already $skipped"
}
