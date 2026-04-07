param(
    [Parameter(Mandatory = $true)][string]$InputFile,
    [Alias("Host")][string]$DbHost = "",
    [int]$Port = 0,
    [string]$Database = "",
    [string]$Username = "",
    [string]$Password = "",
    [string]$ContainerName = "",
    [string]$PgRestoreCommand = "",
    [switch]$UseDockerExec
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot

function Get-EnvFileValues {
    param([string]$Path)

    $values = @{}
    if (-not (Test-Path $Path)) {
        return $values
    }

    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $separator = $trimmed.IndexOf("=")
        if ($separator -lt 1) {
            continue
        }
        $name = $trimmed.Substring(0, $separator).Trim()
        $value = $trimmed.Substring($separator + 1).Trim()
        if ($value.Length -ge 2 -and $value.StartsWith('"') -and $value.EndsWith('"')) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$name] = $value
    }
    return $values
}

function Resolve-Setting {
    param(
        [string]$ExplicitValue,
        [string]$EnvName,
        [string]$DefaultValue = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitValue)) {
        return $ExplicitValue
    }
    if (-not [string]::IsNullOrWhiteSpace((Get-Item -Path "Env:$EnvName" -ErrorAction SilentlyContinue).Value)) {
        return (Get-Item -Path "Env:$EnvName").Value
    }
    if ($envFileValues.ContainsKey($EnvName) -and -not [string]::IsNullOrWhiteSpace($envFileValues[$EnvName])) {
        return $envFileValues[$EnvName]
    }
    return $DefaultValue
}

function Test-CommandExists {
    param([string]$CommandName)
    if ([string]::IsNullOrWhiteSpace($CommandName)) {
        return $false
    }
    return $null -ne (Get-Command $CommandName -ErrorAction SilentlyContinue)
}

function Test-DockerContainerRunning {
    param([string]$Name)
    if (-not (Test-CommandExists "docker")) {
        return $false
    }
    $result = docker ps --filter "name=^$Name$" --format "{{.Names}}" 2>$null
    return ($LASTEXITCODE -eq 0) -and (($result | Select-Object -First 1) -eq $Name)
}

function Invoke-NativeCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [hashtable]$Environment = @{}
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    if ($null -ne $psi.ArgumentList) {
        foreach ($argument in $Arguments) {
            [void]$psi.ArgumentList.Add($argument)
        }
    } else {
        $quotedArguments = foreach ($argument in $Arguments) {
            if ($argument -match '[\s"]') {
                '"' + ($argument.Replace('"', '\"')) + '"'
            } else {
                $argument
            }
        }
        $psi.Arguments = [string]::Join(" ", $quotedArguments)
    }
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    foreach ($key in $Environment.Keys) {
        $psi.Environment[$key] = [string]$Environment[$key]
    }

    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw ("Command failed with exit code {0}: {1}`n{2}" -f $process.ExitCode, $FilePath, $stderr.Trim())
    }
    if ($stdout) {
        Write-Host $stdout.Trim()
    }
}

$envFileValues = Get-EnvFileValues -Path (Join-Path $projectRoot ".env")
$resolvedInputFile = [System.IO.Path]::GetFullPath($InputFile)
if (-not (Test-Path $resolvedInputFile)) {
    throw "Backup file does not exist: $resolvedInputFile"
}

$resolvedHost = Resolve-Setting -ExplicitValue $DbHost -EnvName "POSTGRES_BIND_HOST" -DefaultValue "127.0.0.1"
$resolvedPort = if ($Port -gt 0) { $Port } else { [int](Resolve-Setting -ExplicitValue "" -EnvName "POSTGRES_PORT" -DefaultValue "15432") }
$resolvedDatabase = Resolve-Setting -ExplicitValue $Database -EnvName "POSTGRES_DB" -DefaultValue "true_learning_system"
$resolvedUsername = Resolve-Setting -ExplicitValue $Username -EnvName "POSTGRES_USER" -DefaultValue "tls"
$resolvedPassword = Resolve-Setting -ExplicitValue $Password -EnvName "POSTGRES_PASSWORD" -DefaultValue "change-me"
$resolvedContainerName = Resolve-Setting -ExplicitValue $ContainerName -EnvName "POSTGRES_CONTAINER_NAME" -DefaultValue "true-learning-system-postgres"
$resolvedPgRestoreCommand = Resolve-Setting -ExplicitValue $PgRestoreCommand -EnvName "PG_RESTORE_EXE" -DefaultValue "pg_restore"

$dockerMode = $UseDockerExec.IsPresent
if (-not $dockerMode) {
    if (-not (Test-CommandExists $resolvedPgRestoreCommand) -and (Test-DockerContainerRunning -Name $resolvedContainerName)) {
        $dockerMode = $true
    }
}

Write-Host ("Restoring PostgreSQL database '{0}' from '{1}'" -f $resolvedDatabase, $resolvedInputFile)

if ($dockerMode) {
    if (-not (Test-DockerContainerRunning -Name $resolvedContainerName)) {
        throw "Docker restore mode requested, but container '$resolvedContainerName' is not running."
    }
    $tempFileName = "/tmp/" + [System.IO.Path]::GetFileName($resolvedInputFile)
    Invoke-NativeCommand -FilePath "docker" -Arguments @(
        "cp",
        $resolvedInputFile,
        "$resolvedContainerName`:$tempFileName"
    )
    try {
        Invoke-NativeCommand -FilePath "docker" -Arguments @(
            "exec",
            "-e", "PGPASSWORD=$resolvedPassword",
            $resolvedContainerName,
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "-h", "localhost",
            "-p", "5432",
            "-U", $resolvedUsername,
            "-d", $resolvedDatabase,
            $tempFileName
        )
    } finally {
        Invoke-NativeCommand -FilePath "docker" -Arguments @(
            "exec",
            $resolvedContainerName,
            "rm",
            "-f",
            $tempFileName
        )
    }
} else {
    if (-not (Test-CommandExists $resolvedPgRestoreCommand)) {
        throw "pg_restore was not found. Install PostgreSQL client tools or rerun with -UseDockerExec."
    }
    Invoke-NativeCommand -FilePath $resolvedPgRestoreCommand -Arguments @(
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "-h", $resolvedHost,
        "-p", "$resolvedPort",
        "-U", $resolvedUsername,
        "-d", $resolvedDatabase,
        $resolvedInputFile
    ) -Environment @{ PGPASSWORD = $resolvedPassword }
}

Write-Host "Restore completed."
