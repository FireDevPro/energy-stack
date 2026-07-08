#requires -Version 7.0
# Stops the Cockpit backend on :8765 plus its hidden pwsh wrapper. Targeted:
# only kills processes whose command line matches the cockpit pattern.
# Unrelated python on that port is left alone.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Stop-CockpitOnPort {
  param([int]$Port, [string]$Pattern, [string]$Label)
  $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)" -ErrorAction SilentlyContinue
    if (-not $proc) { continue }
    $cmd = if ($proc.CommandLine) { $proc.CommandLine } else { '' }
    if ($cmd -match $Pattern) {
      Write-Host "Stopping $Label (PID $($proc.ProcessId)) on :$Port"
      Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    } else {
      $head = $cmd.Substring(0, [Math]::Min(80, $cmd.Length))
      Write-Warning "PID $($proc.ProcessId) on :$Port is NOT cockpit-owned (cmd: $head). Leaving alone."
    }
  }
}

Stop-CockpitOnPort -Port 8765 -Pattern 'backend\.app:app|cockpit[\\/]+backend' -Label 'backend'

# Reap any orphaned pwsh wrapper (hidden launcher child that survived
# after its inner uvicorn exited).
Get-CimInstance Win32_Process -Filter "Name = 'pwsh.exe'" |
  Where-Object { $_.CommandLine -match 'backend\.app:app' } |
  ForEach-Object {
    Write-Host "Stopping wrapper pwsh (PID $($_.ProcessId))"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }

Write-Host ''
Write-Host 'Cockpit stopped.'
Start-Sleep -Seconds 2
