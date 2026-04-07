param(
    [string]$BaseUrl = "https://localhost",
    [string]$Email = "",
    [string]$Password = "",
    [switch]$SkipAuth,
    [switch]$Insecure
)

$ErrorActionPreference = "Stop"
$script:OriginalCertificateValidationCallback = $null
$script:OriginalSecurityProtocol = $null

function Enable-InsecureTlsMode {
    if (-not $Insecure) {
        return
    }
    try {
        Add-Type -TypeDefinition @'
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
public static class TlsBypass {
    public static bool Validate(object sender, X509Certificate certificate, X509Chain chain, SslPolicyErrors sslPolicyErrors) {
        return true;
    }
}
'@ -ErrorAction SilentlyContinue | Out-Null
        $method = [TlsBypass].GetMethod("Validate")
        $callback = [System.Delegate]::CreateDelegate([System.Net.Security.RemoteCertificateValidationCallback], $method)
        $script:OriginalCertificateValidationCallback = [System.Net.ServicePointManager]::ServerCertificateValidationCallback
        $script:OriginalSecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
        [System.Net.ServicePointManager]::ServerCertificateValidationCallback = $callback
    }
    catch {
        throw "Failed to enable insecure TLS mode for local rehearsal."
    }
}

function Disable-InsecureTlsMode {
    if (-not $Insecure) {
        return
    }
    try {
        [System.Net.ServicePointManager]::ServerCertificateValidationCallback = $script:OriginalCertificateValidationCallback
        if ($null -ne $script:OriginalSecurityProtocol) {
            [System.Net.ServicePointManager]::SecurityProtocol = $script:OriginalSecurityProtocol
        }
    }
    catch {
    }
}

function Invoke-SmokeRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Url,
        [object]$Body = $null,
        [Microsoft.PowerShell.Commands.WebRequestSession]$Session = $null,
        [hashtable]$Headers = @{}
    )

    $requestHeaders = @{ Accept = "application/json" }
    foreach ($key in $Headers.Keys) {
        $requestHeaders[$key] = $Headers[$key]
    }

    $invokeParams = @{
        Method      = $Method
        Uri         = $Url
        Headers     = $requestHeaders
    }

    if ($null -ne $Session) {
        $invokeParams["WebSession"] = $Session
    }

    if ($null -ne $Body) {
        $invokeParams["Body"] = ($Body | ConvertTo-Json -Depth 8)
        $invokeParams["ContentType"] = "application/json; charset=utf-8"
    }

    return Invoke-RestMethod @invokeParams
}

function Invoke-SmokeWebRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Url,
        [object]$Body = $null,
        [Microsoft.PowerShell.Commands.WebRequestSession]$Session = $null,
        [hashtable]$Headers = @{},
        [switch]$AllowHttpError
    )

    $requestHeaders = @{ Accept = "application/json" }
    foreach ($key in $Headers.Keys) {
        $requestHeaders[$key] = $Headers[$key]
    }

    $invokeParams = @{
        Method          = $Method
        Uri             = $Url
        Headers         = $requestHeaders
        UseBasicParsing = $true
    }

    if ($null -ne $Session) {
        $invokeParams["WebSession"] = $Session
    }

    if ($null -ne $Body) {
        $invokeParams["Body"] = ($Body | ConvertTo-Json -Depth 8)
        $invokeParams["ContentType"] = "application/json; charset=utf-8"
    }

    try {
        return Invoke-WebRequest @invokeParams
    }
    catch {
        if ($AllowHttpError -and $null -ne $_.Exception.Response) {
            $errorResponse = $_.Exception.Response
            $content = ""
            if ($_.ErrorDetails -and -not [string]::IsNullOrWhiteSpace($_.ErrorDetails.Message)) {
                $content = [string]$_.ErrorDetails.Message
            }
            try {
                if ([string]::IsNullOrWhiteSpace($content) -and $errorResponse -is [System.Net.HttpWebResponse]) {
                    $stream = $errorResponse.GetResponseStream()
                    if ($null -ne $stream) {
                        $reader = New-Object System.IO.StreamReader($stream)
                        $content = $reader.ReadToEnd()
                        $reader.Dispose()
                    }
                }
                elseif ([string]::IsNullOrWhiteSpace($content) -and $null -ne $errorResponse.Content) {
                    $content = [string]$errorResponse.Content
                }
            }
            catch {
                $content = ""
            }

            return [pscustomobject]@{
                StatusCode = [int]$errorResponse.StatusCode
                Headers    = $errorResponse.Headers
                Content    = $content
            }
        }
        throw
    }
}

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        throw $Message
    }
}

function Get-BaseOrigin {
    param([string]$Url)
    $uri = [System.Uri]$Url
    return "{0}://{1}" -f $uri.Scheme, $uri.Authority
}

function Convert-SmokeResponseBody {
    param($Response)
    if ($null -eq $Response -or [string]::IsNullOrWhiteSpace($Response.Content)) {
        return $null
    }
    return ($Response.Content | ConvertFrom-Json)
}

$normalizedBaseUrl = ($BaseUrl.TrimEnd("/"))
$origin = Get-BaseOrigin -Url $normalizedBaseUrl
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

Enable-InsecureTlsMode
try {
    Write-Host "[1/7] health"
    $health = Invoke-SmokeRequest -Method "GET" -Url "$normalizedBaseUrl/health" -Session $session
    Assert-True ($null -ne $health) "Health endpoint returned no payload."
    Write-Host ("  health ok: {0}" -f ($health | ConvertTo-Json -Compress))

    Write-Host "[2/7] auth me"
    $meBefore = Invoke-SmokeRequest -Method "GET" -Url "$normalizedBaseUrl/api/auth/me" -Session $session
    Write-Host ("  authenticated before login: {0}" -f [bool]$meBefore.authenticated)

    if (-not $SkipAuth) {
        Assert-True (-not [string]::IsNullOrWhiteSpace($Email)) "Email is required unless -SkipAuth is used."
        Assert-True (-not [string]::IsNullOrWhiteSpace($Password)) "Password is required unless -SkipAuth is used."

        $loginPayload = @{
            email    = $Email
            password = $Password
        }

        if (-not [bool]$meBefore.authenticated) {
            Write-Host "[3/7] csrf login posture"
            $csrfLoginResponse = Invoke-SmokeWebRequest `
                -Method "POST" `
                -Url "$normalizedBaseUrl/api/auth/login" `
                -Body $loginPayload `
                -Session $session `
                -Headers @{ Origin = "https://evil.example" } `
                -AllowHttpError
            $csrfLoginPayload = Convert-SmokeResponseBody -Response $csrfLoginResponse
            Assert-True ($csrfLoginResponse.StatusCode -eq 403) "Cross-site login probe should return HTTP 403."
            Assert-True ($csrfLoginPayload.detail -eq "csrf validation failed") "Cross-site login probe should fail CSRF validation."
            Write-Host ("  csrf guard ok: {0}" -f $csrfLoginResponse.StatusCode)

            Write-Host "[4/7] login"
            $loginResponse = Invoke-SmokeWebRequest `
                -Method "POST" `
                -Url "$normalizedBaseUrl/api/auth/login" `
                -Body $loginPayload `
                -Session $session `
                -Headers @{ Origin = $origin }
            $login = Convert-SmokeResponseBody -Response $loginResponse
            Assert-True ([bool]$login.authenticated) "Login did not authenticate the user."
            $setCookie = $loginResponse.Headers["Set-Cookie"]
            Assert-True (-not [string]::IsNullOrWhiteSpace($setCookie)) "Login response did not set a session cookie."
            if ($normalizedBaseUrl.StartsWith("https://")) {
                Assert-True ($setCookie -match "(?i);\s*secure\b") "Expected Secure on auth cookie for HTTPS smoke test."
            }
            Assert-True ($setCookie -match "(?i);\s*samesite=(lax|strict)") "Expected SameSite=Lax or Strict on auth cookie."
            Write-Host ("  logged in as: {0}" -f $login.user.email)
            Write-Host ("  session cookie ok: {0}" -f $setCookie)
        } else {
            Write-Host "[3/7] csrf login posture skipped (already authenticated)"
            Write-Host "[4/7] login skipped (already authenticated)"
        }
    } else {
        Write-Host "[3/7] csrf login posture skipped by flag"
        Write-Host "[4/7] login skipped by flag"
    }

    Write-Host "[5/7] session + stats"
    $meAfter = Invoke-SmokeRequest -Method "GET" -Url "$normalizedBaseUrl/api/auth/me" -Session $session
    if (-not $SkipAuth) {
        Assert-True ([bool]$meAfter.authenticated) "Expected authenticated session after login."
        $sessions = Invoke-SmokeRequest -Method "GET" -Url "$normalizedBaseUrl/api/auth/sessions" -Session $session
        Assert-True (($sessions.sessions | Measure-Object).Count -ge 1) "Expected at least one active auth session."
        $stats = Invoke-SmokeRequest -Method "GET" -Url "$normalizedBaseUrl/api/stats" -Session $session
        Assert-True ($null -ne $stats) "Stats endpoint returned no payload."
        Write-Host ("  stats ok: {0}" -f ($stats | ConvertTo-Json -Compress))
    } else {
        Write-Host "  auth-protected checks skipped"
    }

    Write-Host "[6/7] page html"
    $historyResponse = Invoke-WebRequest -Method GET -Uri "$normalizedBaseUrl/history" -WebSession $session -MaximumRedirection 0 -ErrorAction SilentlyContinue -UseBasicParsing
    if (-not $SkipAuth) {
        Assert-True ($historyResponse.StatusCode -eq 200) "History page did not return HTTP 200."
        Assert-True (-not [string]::IsNullOrWhiteSpace($historyResponse.Content)) "History page returned empty HTML."
        Write-Host ("  history page ok: {0}" -f $historyResponse.StatusCode)
    } else {
        Assert-True (($historyResponse.StatusCode -eq 200) -or ($historyResponse.StatusCode -eq 307)) "History page should return 200 or redirect to login."
        Write-Host ("  history page status: {0}" -f $historyResponse.StatusCode)
    }

    Write-Host "[7/7] rollout auth config"
    $configCheck = & python scripts\verify_rollout_auth_config.py --json 2>$null
    if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq 1) {
        $configPayload = $configCheck | ConvertFrom-Json
        Assert-True ($null -ne $configPayload) "Rollout auth config script returned no payload."
        Write-Host ("  config ok={0} failures={1} warnings={2}" -f [bool]$configPayload.ok, [int]$configPayload.failures, [int]$configPayload.warnings)
    }
    else {
        throw "verify_rollout_auth_config.py did not run successfully."
    }

    Write-Host ""
    Write-Host "Smoke test passed."
}
finally {
    Disable-InsecureTlsMode
}
