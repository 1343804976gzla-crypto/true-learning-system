param(
    [string]$OutputDir = "",
    [string]$FilePath = "",
    [Alias("Host")][string]$DbHost = "",
    [int]$Port = 0,
    [string]$Database = "",
    [string]$Username = "",
    [string]$Password = "",
    [string]$ContainerName = "",
    [string]$PgDumpCommand = "",
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

function Resolve-BackupTargetPath {
    param(
        [string]$ExplicitFilePath,
        [string]$ExplicitOutputDir,
        [string]$DatabaseName
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitFilePath)) {
        return [System.IO.Path]::GetFullPath($ExplicitFilePath)
    }

    $baseDir = if (-not [string]::IsNullOrWhiteSpace($ExplicitOutputDir)) {
        [System.IO.Path]::GetFullPath($ExplicitOutputDir)
    } else {
        Join-Path $projectRoot "data\backups\postgres"
    }
    New-Item -ItemType Directory -Force -Path $baseDir | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    return Join-Path $baseDir "$DatabaseName.pg-backup-$timestamp.dump"
}

function Invoke-NativeCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [hashtable]$Environment = @{},
        [string]$StdOutFile = "",
        [string]$StdErrFile = ""
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
    $psi.RedirectStandardOutput = -not [string]::IsNullOrWhiteSpace($StdOutFile)
    $psi.RedirectStandardError = $true

    foreach ($key in $Environment.Keys) {
        $psi.Environment[$key] = [string]$Environment[$key]
    }

    $process = [System.Diagnostics.Process]::Start($psi)
    if (-not [string]::IsNullOrWhiteSpace($StdOutFile)) {
        $outputStream = [System.IO.File]::Open($StdOutFile, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try {
            $process.StandardOutput.BaseStream.CopyTo($outputStream)
        } finally {
            $outputStream.Dispose()
        }
    }
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if (-not [string]::IsNullOrWhiteSpace($StdErrFile) -and $stderr) {
        Set-Content -LiteralPath $StdErrFile -Value $stderr -Encoding UTF8
    }
    if ($process.ExitCode -ne 0) {
        throw ("Command failed with exit code {0}: {1}`n{2}" -f $process.ExitCode, $FilePath, $stderr.Trim())
    }
}

$envFileValues = Get-EnvFileValues -Path (Join-Path $projectRoot ".env")
$resolvedHost = Resolve-Setting -ExplicitValue $DbHost -EnvName "POSTGRES_BIND_HOST" -DefaultValue "127.0.0.1"
$resolvedPort = if ($Port -gt 0) { $Port } else { [int](Resolve-Setting -ExplicitValue "" -EnvName "POSTGRES_PORT" -DefaultValue "15432") }
$resolvedDatabase = Resolve-Setting -ExplicitValue $Database -EnvName "POSTGRES_DB" -DefaultValue "true_learning_system"
$resolvedUsername = Resolve-Setting -ExplicitValue $Username -EnvName "POSTGRES_USER" -DefaultValue "tls"
$resolvedPassword = Resolve-Setting -ExplicitValue $Password -EnvName "POSTGRES_PASSWORD" -DefaultValue "change-me"
$resolvedContainerName = Resolve-Setting -ExplicitValue $ContainerName -EnvName "POSTGRES_CONTAINER_NAME" -DefaultValue "true-learning-system-postgres"
$resolvedPgDumpCommand = Resolve-Setting -ExplicitValue $PgDumpCommand -EnvName "PG_DUMP_EXE" -DefaultValue "pg_dump"
$targetPath = Resolve-BackupTargetPath -ExplicitFilePath $FilePath -ExplicitOutputDir $OutputDir -DatabaseName $resolvedDatabase

$dockerMode = $UseDockerExec.IsPresent
if (-not $dockerMode) {
    if (-not (Test-CommandExists $resolvedPgDumpCommand) -and (Test-DockerContainerRunning -Name $resolvedContainerName)) {
        $dockerMode = $true
    }
}

Write-Host ("Backing up PostgreSQL database '{0}' to '{1}'" -f $resolvedDatabase, $targetPath)

if ($dockerMode) {
    if (-not (Test-DockerContainerRunning -Name $resolvedContainerName)) {
        throw "Docker backup mode requested, but container '$resolvedContainerName' is not running."
    }
    $tempFileName = "/tmp/" + [System.IO.Path]::GetFileName($targetPath)
    Invoke-NativeCommand -FilePath "docker" -Arguments @(
        "exec",
        "-e", "PGPASSWORD=$resolvedPassword",
        $resolvedContainerName,
        "pg_dump",
        "-h", "localhost",
        "-p", "5432",
        "-U", $resolvedUsername,
        "-d", $resolvedDatabase,
        "-Fc",
        "-f", $tempFileName
    )
    Invoke-NativeCommand -FilePath "docker" -Arguments @(
        "cp",
        "$resolvedContainerName`:$tempFileName",
        $targetPath
    )
    Invoke-NativeCommand -FilePath "docker" -Arguments @(
        "exec",
        $resolvedContainerName,
        "rm",
        "-f",
        $tempFileName
    )
} else {
    if (-not (Test-CommandExists $resolvedPgDumpCommand)) {
        throw "pg_dump was not found. Install PostgreSQL client tools or rerun with -UseDockerExec."
    }
    Invoke-NativeCommand -FilePath $resolvedPgDumpCommand -Arguments @(
        "-h", $resolvedHost,
        "-p", "$resolvedPort",
        "-U", $resolvedUsername,
        "-d", $resolvedDatabase,
        "-Fc",
        "-f", $targetPath
    ) -Environment @{ PGPASSWORD = $resolvedPassword }
}

if (-not (Test-Path $targetPath)) {
    throw "Backup file was not created: $targetPath"
}

$fileInfo = Get-Item -LiteralPath $targetPath
if ($fileInfo.Length -le 0) {
    throw "Backup file is empty: $targetPath"
}

Write-Host ("Backup completed: {0} ({1} bytes)" -f $fileInfo.FullName, $fileInfo.Length)
