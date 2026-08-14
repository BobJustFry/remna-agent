# Backup PostgreSQL data directory (while stack can stay up; prefer stop db for cold copy).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "data\postgres"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$destRoot = Join-Path $root "data\backups"
$dest = Join-Path $destRoot "postgres-$stamp"

if (-not (Test-Path $src)) {
  Write-Error "DB path not found: $src"
}

New-Item -ItemType Directory -Force -Path $destRoot | Out-Null
Write-Host "Stopping db for consistent backup..."
Push-Location $root
try {
  docker compose stop db | Out-Null
  Write-Host "Copying $src -> $dest"
  Copy-Item -Path $src -Destination $dest -Recurse -Force
  Write-Host "Starting db..."
  docker compose start db | Out-Null
} finally {
  Pop-Location
}

Write-Host "Backup ready: $dest"
