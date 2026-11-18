#Requires -Version 5.1
<#
.SYNOPSIS
  Safe NSIS bootstrap for building OpenAgent-Setup.exe.

.DESCRIPTION
  Detects an existing NSIS installation (makensis.exe) and, only when missing
  (or when -Force is passed), installs a known NSIS version. Never installs
  silently without being run explicitly — simply executing this script IS the
  explicit opt-in. Use -WhatIf to preview.

  Resolution order:
    1. makensis on PATH
    2. Standard install locations (Program Files / LocalAppData / Chocolatey)
    3. winget install NSIS.NSIS.<Version> (preferred, official manifest)
    4. Direct download of the known-good installer from SourceForge

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/install-nsis.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/install-nsis.ps1 -Version 3.10 -Force
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string]$Version = "3.10",
  [switch]$Force
)

$ErrorActionPreference = "Stop"

function Find-Makensis {
  # Always returns a true array (even for 0 or 1 results) so that
  # indexing ([0]) and .Count behave predictably.
  $found = @()
  $onPath = Get-Command makensis.exe -ErrorAction SilentlyContinue
  if ($onPath) { $found += $onPath.Source }
  foreach ($candidate in @(
    "$env:ProgramFiles\NSIS\makensis.exe",
    "${env:ProgramFiles(x86)}\NSIS\makensis.exe",
    "$env:LOCALAPPDATA\Programs\NSIS\makensis.exe",
    "$env:ChocolateyInstall\bin\makensis.exe"
  )) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) { $found += $candidate }
  }
  return (, @($found | Select-Object -Unique))
}

$existing = Find-Makensis
if (($existing.Count -gt 0) -and -not $Force) {
  Write-Host "NSIS already installed:"
  foreach ($p in $existing) { Write-Host "  $p" }
  & $existing[0] /VERSION
  Write-Host "Installation path: $(Split-Path $existing[0])"
  Write-Host "Nothing to do. Re-run with -Force to reinstall."
  exit 0
}

if ($Force -and ($existing.Count -gt 0)) {
  Write-Host "Existing NSIS detected but -Force passed; proceeding with reinstall."
}

$WingetAvailable = $null -ne (Get-Command winget.exe -ErrorAction SilentlyContinue)

if ($PSCmdlet.ShouldProcess("NSIS $Version", "Install")) {
  if ($WingetAvailable) {
    Write-Host "Installing NSIS $Version via winget (NSIS.NSIS.$Version)..."
    & winget install --id "NSIS.NSIS" --version $Version --silent `
      --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "winget install exited with code $LASTEXITCODE; falling back to direct download."
    }
  } else {
    Write-Host "winget not available; using direct download fallback."
  }

  $afterWinget = Find-Makensis
  if ($afterWinget.Count -eq 0) {
    $url = "https://downloads.sourceforge.net/project/nsis/NSIS%203/$Version/nsis-$Version-setup.exe"
    $tmp = Join-Path $env:TEMP "nsis-$Version-setup.exe"
    Write-Host "Downloading NSIS $Version from $url ..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    Write-Host "Running NSIS installer (per-machine default, /S silent)..."
    Start-Process -FilePath $tmp -ArgumentList "/S" -Wait
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
  }

  $verified = Find-Makensis
  if ($verified.Count -eq 0) {
    Write-Error "NSIS installation failed: makensis.exe not found after install."
    exit 1
  }
  Write-Host "NSIS installed successfully:"
  foreach ($p in $verified) { Write-Host "  $p" }
  & $verified[0] /VERSION
  Write-Host "Installation path: $(Split-Path $verified[0])"
  Write-Host "Next: npm run package:windows  (then makensis tools/installer/windows/installer.nsi)"
}
