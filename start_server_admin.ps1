# start_server_admin.ps1
# Starts the UIA-X MCP server elevated (as Administrator).
#
# Use this when the automation target (e.g. Quicken) runs elevated.
# UIPI blocks all window messages and input injection from lower-integrity
# processes, making automation impossible without matching integrity levels.
#
# ALTERNATIVE: Restart the target app without "Run as administrator" —
# most apps (including Quicken, manifest level=asInvoker) work fine
# without admin rights.
#
# Usage:
#   Right-click this file → "Run with PowerShell" (will prompt for UAC)
#   OR from an already-elevated terminal:
#     powershell -ExecutionPolicy Bypass -File start_server_admin.ps1

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Check if already elevated
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin   = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    # Re-launch elevated
    Start-Process powershell.exe `
        -ArgumentList "-ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`"" `
        -Verb RunAs
    exit
}

Write-Host "Starting UIA-X MCP server (elevated)..." -ForegroundColor Green
Write-Host "Server will listen on http://0.0.0.0:8000"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

Set-Location $ScriptDir
python -m uiax.server
