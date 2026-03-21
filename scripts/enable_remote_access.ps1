param(
    [int]$Port = 18000
)

$ErrorActionPreference = "Stop"

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    throw "Run this script from an elevated PowerShell window."
}

$ruleName = "True Learning System Docker $Port"

$existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if (-not $existingRule) {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -Profile Private,Public `
        -RemoteAddress LocalSubnet | Out-Null
}

Set-Service -Name com.docker.service -StartupType Automatic

Get-NetFirewallRule -DisplayName $ruleName | Select-Object DisplayName, Enabled, Profile, Direction, Action
Get-Service com.docker.service | Select-Object Status, StartType, Name, DisplayName
