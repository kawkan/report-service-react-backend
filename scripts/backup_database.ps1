param(
  [string]$DatabaseUrl = "",
  [string]$OutputDir = "",
  [string]$PgDumpPath = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Read-EnvValue {
  param([string]$Name)

  $envPath = Join-Path $ProjectRoot ".env"
  if (-not (Test-Path $envPath)) {
    return ""
  }

  $line = Get-Content -LiteralPath $envPath -Encoding UTF8 |
    Where-Object { $_ -match "^\s*$Name\s*=" } |
    Select-Object -First 1

  if (-not $line) {
    return ""
  }

  return (($line -replace "^\s*$Name\s*=", "").Trim().Trim('"').Trim("'"))
}

function Find-Executable {
  param(
    [string]$Name,
    [string[]]$Candidates
  )

  $fromPath = Get-Command $Name -ErrorAction SilentlyContinue
  if ($fromPath) {
    return $fromPath.Source
  }

  foreach ($candidate in $Candidates) {
    if (Test-Path $candidate) {
      return $candidate
    }
  }

  return ""
}

if (-not $DatabaseUrl) {
  $DatabaseUrl = Read-EnvValue "DATABASE_URL"
}

if (-not $DatabaseUrl) {
  throw "DATABASE_URL not found. Pass -DatabaseUrl or set DATABASE_URL in .env"
}

if (-not $OutputDir) {
  $OutputDir = Join-Path $ProjectRoot "backups"
}

if (-not (Test-Path $OutputDir)) {
  New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

if (-not $PgDumpPath) {
  $PgDumpPath = Find-Executable "pg_dump" @(
    "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe",
    "C:\Program Files\PostgreSQL\18\pgAdmin 4\runtime\pg_dump.exe",
    "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe",
    "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"
  )
}

if (-not $PgDumpPath) {
  throw "pg_dump not found. Install PostgreSQL tools or pass -PgDumpPath"
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupFile = Join-Path $OutputDir "service_report-$timestamp.sql"

& $PgDumpPath `
  "--dbname=$DatabaseUrl" `
  "--file=$backupFile" `
  "--format=plain" `
  "--encoding=UTF8" `
  "--no-owner" `
  "--no-privileges"

if ($LASTEXITCODE -ne 0) {
  throw "Backup failed"
}

Write-Host "Backup completed:"
Write-Host $backupFile
