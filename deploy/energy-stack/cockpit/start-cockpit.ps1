#requires -Version 7.0
# The Vigil cockpit dev launcher (workstation).
#
# The canonical cockpit is the `cockpit` compose service on Pi-lab
# (http://192.168.20.10:8765/). This script is the local dev loop: sources
# .env.local, kills any prior cockpit backend on :8765, then runs uvicorn —
# which serves BOTH the /api/vigil/* endpoints and the single-file board
# same-origin on :8765 (no separate frontend server, no build step).
#
# First-time setup:
#   1. Copy .env.example -> .env.local; fill in INFLUXDB_TOKEN
#   2. .\install-shortcut.ps1   # creates Cockpit.lnk on the desktop
# Then double-click the Cockpit icon any time.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = $PSScriptRoot
$LogDir    = Join-Path $ScriptDir 'logs'
$Port      = 8765

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

# ---- 1. Load .env.local ------------------------------------------------

$EnvFile = Join-Path $ScriptDir '.env.local'
if (-not (Test-Path $EnvFile)) {
  Write-Host ''
  Write-Host "Missing $EnvFile" -ForegroundColor Red
  Write-Host 'First-time setup:'
  Write-Host '  1. Copy .env.example to .env.local'
  Write-Host '  2. Fill in INFLUXDB_TOKEN and confirm the URLs'
  Read-Host 'Press Enter to close'
  exit 1
}

$required = @('INFLUXDB_URL','INFLUXDB_TOKEN','INFLUXDB_ORG','INFLUXDB_BUCKET')
$loaded   = @{}
foreach ($line in Get-Content $EnvFile) {
  $trim = $line.Trim()
  if (-not $trim -or $trim.StartsWith('#')) { continue }
  $kv = $trim -split '=', 2
  if ($kv.Length -ne 2) { continue }
  $k = $kv[0].Trim()
  $v = $kv[1].Trim().Trim('"').Trim("'")
  [Environment]::SetEnvironmentVariable($k, $v, 'Process')
  $loaded[$k] = $v
}
$missing = $required | Where-Object { -not $loaded.ContainsKey($_) -or -not $loaded[$_] }
if ($missing) {
  Write-Host ''
  Write-Host '.env.local is missing values for:' -ForegroundColor Red
  $missing | ForEach-Object { Write-Host "  $_" }
  Read-Host 'Press Enter to close'
  exit 1
}

$env:PYTHONPATH = $ScriptDir

# ---- 2. Targeted cleanup of a prior cockpit backend on :8765 ----------

$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) {
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)" -ErrorAction SilentlyContinue
  if (-not $proc) { continue }
  $cmd = if ($proc.CommandLine) { $proc.CommandLine } else { '' }
  if ($cmd -match 'backend\.app:app|cockpit[\\/]+backend') {
    Write-Host "  stopping prior backend (PID $($proc.ProcessId)) on :$Port"
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
  } else {
    $head = $cmd.Substring(0, [Math]::Min(80, $cmd.Length))
    Write-Warning "  PID $($proc.ProcessId) on :$Port is NOT cockpit-owned (cmd: $head). Leaving alone."
  }
}
Start-Sleep -Milliseconds 400

# ---- 3. Spawn uvicorn (serves API + board on :8765) -------------------

$Log = Join-Path $LogDir 'backend.log'
$Cmd = "Set-Location '$ScriptDir'; uvicorn backend.app:app --host 127.0.0.1 --port $Port *> '$Log'"
Write-Host "Starting cockpit on :$Port (log: $Log)..." -ForegroundColor Cyan
$proc = Start-Process pwsh -ArgumentList '-NoExit','-Command',$Cmd -PassThru -WindowStyle Hidden

# ---- 4. Wait for health, open browser --------------------------------

$deadline = (Get-Date).AddSeconds(30)
$ok = $false
while ((Get-Date) -lt $deadline) {
  try {
    $r = Invoke-WebRequest -Uri "http://localhost:$Port/api/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    if ($r.StatusCode -lt 500) { $ok = $true; break }
  } catch { Start-Sleep -Milliseconds 500 }
}

Write-Host ''
if ($ok) { Write-Host 'Cockpit is up:' -ForegroundColor Green }
else { Write-Host 'Health check timed out — opening anyway; refresh shortly.' -ForegroundColor Yellow }
Write-Host "  http://localhost:$Port/            (the board)"
Write-Host "  http://localhost:$Port/api/vigil/now  (raw JSON)"
Start-Process "http://localhost:$Port/"
Write-Host ''
Write-Host "Backend PID $($proc.Id)   log: $Log"
Write-Host 'To stop: run stop-cockpit.ps1 (or re-run start-cockpit.ps1).'
