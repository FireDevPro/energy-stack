#requires -Version 7.0
# One-time setup: creates a "Cockpit" shortcut on the desktop that
# invokes start-cockpit.ps1 via pwsh with ExecutionPolicy Bypass.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir   = $PSScriptRoot
$Launcher    = Join-Path $ScriptDir 'start-cockpit.ps1'
$Stopper     = Join-Path $ScriptDir 'stop-cockpit.ps1'
$DesktopPath = [Environment]::GetFolderPath('Desktop')
$pwshPath    = (Get-Command pwsh).Source

foreach ($p in @($Launcher, $Stopper)) {
  if (-not (Test-Path $p)) { Write-Error "Missing script: $p"; exit 1 }
}

$WshShell = New-Object -ComObject WScript.Shell

function New-LnkShortcut {
  param([string]$Path, [string]$Script, [string]$Description, [string]$NoExitFlag)
  $sc = $WshShell.CreateShortcut($Path)
  $sc.TargetPath       = $pwshPath
  $sc.Arguments        = "$NoExitFlag -ExecutionPolicy Bypass -File `"$Script`""
  $sc.WorkingDirectory = $ScriptDir
  $sc.Description      = $Description
  $sc.WindowStyle      = 1
  $sc.IconLocation     = "$pwshPath,0"
  $sc.Save()
  Write-Host "Created: $Path"
}

New-LnkShortcut -Path (Join-Path $DesktopPath 'Cockpit.lnk') -Script $Launcher -Description 'Controller Cockpit launcher' -NoExitFlag '-NoExit'
New-LnkShortcut -Path (Join-Path $DesktopPath 'Stop Cockpit.lnk') -Script $Stopper -Description 'Stop Controller Cockpit' -NoExitFlag ''
