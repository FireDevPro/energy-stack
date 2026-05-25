#requires -Version 7.0
# Controller Cockpit one-click launcher.
#
# Sources .env.local, kills any prior cockpit backend/frontend bound to
# :8765 / :5173, then spawns uvicorn (live mode) + Vite in two visible
# pwsh windows and opens the cockpit in the default browser.
#
# First-time setup:
#   1. Copy .env.example -> .env.local; fill in INFLUXDB_TOKEN
#   2. .\install-shortcut.ps1   # creates Cockpit.lnk on the desktop
# Then double-click the Cockpit icon any time.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir    = $PSScriptRoot
$RepoRoot     = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path
$FrontendDir  = Join-Path $ScriptDir 'frontend'
$LogDir       = Join-Path $ScriptDir 'logs'
$BackendPort  = 8765
$FrontendPort = 5173

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

$required = @('INFLUXDB_URL','INFLUXDB_TOKEN','INFLUXDB_ORG','INFLUXDB_BUCKET','LOKI_URL')
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

$env:COCKPIT_BACKEND_MODE = 'live'
$env:PYTHONPATH = $RepoRoot

# ---- 2. Targeted cleanup of prior cockpit processes -------------------

function Stop-CockpitOnPort {
  param([int]$Port, [string]$Pattern, [string]$Label)
  $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)" -ErrorAction SilentlyContinue
    if (-not $proc) { continue }
    $cmd = if ($proc.CommandLine) { $proc.CommandLine } else { '' }
    if ($cmd -match $Pattern) {
      Write-Host "  stopping prior $Label (PID $($proc.ProcessId)) on :$Port"
      Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    } else {
      $head = $cmd.Substring(0, [Math]::Min(80, $cmd.Length))
      Write-Warning "  PID $($proc.ProcessId) on :$Port is NOT cockpit-owned (cmd: $head). Leaving alone."
    }
  }
}

Write-Host 'Cleaning up prior cockpit processes...' -ForegroundColor Cyan
Stop-CockpitOnPort -Port $BackendPort  -Pattern 'tools\.cockpit\.backend\.app|tools[\\/]+cockpit[\\/]+backend' -Label 'backend'
Stop-CockpitOnPort -Port $FrontendPort -Pattern 'vite|tools[\\/]+cockpit[\\/]+frontend' -Label 'frontend'
Start-Sleep -Milliseconds 400

# ---- 3. Spawn backend + frontend in visible pwsh windows -------------

$BackendLog  = Join-Path $LogDir 'backend.log'
$FrontendLog = Join-Path $LogDir 'frontend.log'
$BackendCmd  = "Set-Location '$RepoRoot'; uvicorn tools.cockpit.backend.app:app --host 127.0.0.1 --port $BackendPort *> '$BackendLog'"
$FrontendCmd = "Set-Location '$FrontendDir'; npm run dev *> '$FrontendLog'"

Write-Host "Starting backend on :$BackendPort (log: $BackendLog)..." -ForegroundColor Cyan
$beProc = Start-Process pwsh -ArgumentList '-NoExit','-Command',$BackendCmd -PassThru -WindowStyle Hidden

Write-Host "Starting frontend on :$FrontendPort (log: $FrontendLog)..." -ForegroundColor Cyan
$feProc = Start-Process pwsh -ArgumentList '-NoExit','-Command',$FrontendCmd -PassThru -WindowStyle Hidden

# ---- 4. Wait for both health endpoints -------------------------------

function Wait-Url {
  param([string]$Url, [int]$TimeoutSec, [string]$Label)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
      if ($r.StatusCode -lt 500) { return $true }
    } catch { Start-Sleep -Milliseconds 500 }
  }
  Write-Warning "  $Label did not become healthy at $Url within ${TimeoutSec}s"
  return $false
}

Write-Host 'Waiting for services (Vite cold-start can take 30-60s)...' -ForegroundColor Cyan
$beOk = Wait-Url -Url "http://localhost:$BackendPort/api/health" -TimeoutSec 60 -Label 'backend'
$feOk = Wait-Url -Url "http://localhost:$FrontendPort/"            -TimeoutSec 90 -Label 'frontend'

# ---- 5. Open browser -------------------------------------------------

Write-Host ''
if ($beOk -and $feOk) {
  Write-Host 'Cockpit is up:' -ForegroundColor Green
} else {
  Write-Host 'Health check timed out — services may still be starting.' -ForegroundColor Yellow
  Write-Host 'Opening browser anyway; refresh after services finish booting.' -ForegroundColor Yellow
}
Write-Host "  http://localhost:$FrontendPort/"
Write-Host "  http://localhost:$BackendPort/api/snapshot (raw JSON)"
Start-Process "http://localhost:$FrontendPort/"
Write-Host ''
Write-Host "Backend  PID $($beProc.Id)   log: $BackendLog"
Write-Host "Frontend PID $($feProc.Id)   log: $FrontendLog"
Write-Host 'To stop: double-click the "Stop Cockpit" desktop icon, or run stop-cockpit.ps1.'
