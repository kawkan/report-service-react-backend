param(
  [Parameter(Mandatory = $true)]
  [string]$BackupFile,

  [string]$DatabaseUrl = "",
  [string]$PsqlPath = ""
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

if (-not (Test-Path $BackupFile)) {
  throw "Backup file not found: $BackupFile"
}

if (-not $DatabaseUrl) {
  $DatabaseUrl = Read-EnvValue "DATABASE_URL"
}

if (-not $DatabaseUrl) {
  throw "DATABASE_URL not found. Pass -DatabaseUrl or set DATABASE_URL in .env"
}

if (-not $PsqlPath) {
  $PsqlPath = Find-Executable "psql" @(
    "C:\Program Files\PostgreSQL\18\bin\psql.exe",
    "C:\Program Files\PostgreSQL\18\pgAdmin 4\runtime\psql.exe",
    "C:\Program Files\PostgreSQL\17\bin\psql.exe",
    "C:\Program Files\PostgreSQL\16\bin\psql.exe"
  )
}

if (-not $PsqlPath) {
  throw "psql not found. Install PostgreSQL tools or pass -PsqlPath"
}

Write-Host "Restoring from:"
Write-Host $BackupFile
Write-Host ""
Write-Host "Recommended: restore into an empty/new database to avoid duplicate data."

& $PsqlPath "--dbname=$DatabaseUrl" "--file=$BackupFile"

if ($LASTEXITCODE -ne 0) {
  throw "Restore failed"
}

Write-Host "Restore completed"
