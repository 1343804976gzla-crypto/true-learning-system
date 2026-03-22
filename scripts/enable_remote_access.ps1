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

function Ensure-FirewallRule {
    param(
        [string]$DisplayName,
        [string[]]$RemoteAddress
    )

    $existingRule = Get-NetFirewallRule -DisplayName $DisplayName -ErrorAction SilentlyContinue
    if ($existingRule) {
        Set-NetFirewallRule -DisplayName $DisplayName -Enabled True -Action Allow -Profile Private,Public | Out-Null
        return
    }

    New-NetFirewallRule `
        -DisplayName $DisplayName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $Port `
        -Profile Private,Public `
        -RemoteAddress $RemoteAddress | Out-Null
}

$lanRuleName = "True Learning System Docker LAN $Port"
$tailscaleRuleName = "True Learning System Docker Tailscale $Port"

Ensure-FirewallRule -DisplayName $lanRuleName -RemoteAddress @("LocalSubnet")
Ensure-FirewallRule -DisplayName $tailscaleRuleName -RemoteAddress @("100.64.0.0/10", "fd7a:115c:a1e0::/48")

Set-Service -Name com.docker.service -StartupType Automatic

Get-NetFirewallRule -DisplayName $lanRuleName, $tailscaleRuleName |
    Select-Object DisplayName, Enabled, Profile, Direction, Action
Get-Service com.docker.service | Select-Object Status, StartType, Name, DisplayName
