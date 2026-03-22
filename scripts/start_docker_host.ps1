param(
    [int]$Port = 18000
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$volumeName = "true-learning-system_tls_app_data"
$dockerDesktopExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$hostDataDir = Join-Path $projectRoot "data"

function Wait-DockerReady {
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerReady) {
            return
        }
        Start-Sleep -Seconds 5
    }
    throw "Docker engine did not become ready within 3 minutes."
}

function Test-DockerReady {
    try {
        docker version --format "{{.Server.Version}}" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Start-DockerDesktopIfNeeded {
    if (Test-DockerReady) {
        return
    }

    if (-not (Test-Path $dockerDesktopExe)) {
        throw "Docker Desktop was not found at: $dockerDesktopExe"
    }

    Start-Process $dockerDesktopExe | Out-Null
    Wait-DockerReady
}

function Get-TailscaleAccessInfo {
    $tailscaleCmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if (-not $tailscaleCmd) {
        return $null
    }

    try {
        $statusJson = & $tailscaleCmd.Source status --json 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $statusJson) {
            return $null
        }

        $status = $statusJson | ConvertFrom-Json
        if (-not $status.Self) {
            return $null
        }

        $dnsName = [string]$status.Self.DNSName
        if ($dnsName.EndsWith(".")) {
            $dnsName = $dnsName.TrimEnd(".")
        }

        $tailscaleIps = @($status.Self.TailscaleIPs)
        $ipv4 = $tailscaleIps | Where-Object { $_ -match '^\d+\.' } | Select-Object -First 1
        $ipv6 = $tailscaleIps | Where-Object { $_ -like "*:*" } | Select-Object -First 1

        return [pscustomobject]@{
            DNSName = $dnsName
            IPv4    = $ipv4
            IPv6    = $ipv6
        }
    } catch {
        return $null
    }
}

function Ensure-DataVolumeSeeded {
    $volumeExists = docker volume ls --format "{{.Name}}" | Where-Object { $_ -eq $volumeName }
    if (-not $volumeExists) {
        docker volume create $volumeName | Out-Null
    }

    $existingFiles = docker run --rm -v "${volumeName}:/dst" --entrypoint sh true-learning-system-app -lc "find /dst -mindepth 1 -maxdepth 1 | head -n 1"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Docker data volume."
    }

    if ($existingFiles) {
        return
    }

    if (-not (Test-Path $hostDataDir)) {
        throw "Host data directory was not found: $hostDataDir"
    }

    docker run --rm -v "${volumeName}:/dst" -v "${hostDataDir}:/src:ro" --entrypoint sh true-learning-system-app -lc "cp -a /src/. /dst/"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to seed Docker data volume from host data directory."
    }
}

function Show-Urls {
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.InterfaceAlias -ne "Tailscale" -and
            $_.InterfaceAlias -notlike "vEthernet*"
        } |
        Select-Object -ExpandProperty IPAddress -Unique
    $tailscaleInfo = Get-TailscaleAccessInfo

    Write-Host ""
    Write-Host "Docker app is running."
    Write-Host "Local URL: http://localhost:$Port"
    foreach ($ip in $ips) {
        Write-Host "LAN URL:   http://$ip`:$Port"
    }
    if ($tailscaleInfo) {
        if ($tailscaleInfo.DNSName) {
            Write-Host "Tailnet URL: http://$($tailscaleInfo.DNSName):$Port"
        }
        if ($tailscaleInfo.IPv4) {
            Write-Host "Tailnet IP:  http://$($tailscaleInfo.IPv4):$Port"
        }
    }
    Write-Host ""
}

Push-Location $projectRoot
try {
    Start-DockerDesktopIfNeeded
    Ensure-DataVolumeSeeded
    $env:TLS_PORT = "$Port"
    docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up -d failed."
    }
    Show-Urls
} finally {
    Pop-Location
}
