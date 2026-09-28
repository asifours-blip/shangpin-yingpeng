param()

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$secretDir = Join-Path $repo 'deploy/secrets/public-flow'
if (-not (Test-Path -LiteralPath $secretDir)) {
    New-Item -ItemType Directory -Path $secretDir | Out-Null
}

foreach ($name in @('postgres_password', 'redis_password', 'minio_access_key', 'minio_secret_key', 'session_key', 'oauth_keys')) {
    $path = Join-Path $secretDir $name
    if (Test-Path -LiteralPath $path) { continue }
    $value = if ($name -eq 'oauth_keys') {
        @{v1 = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))} | ConvertTo-Json -Compress
    } else {
        [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
    }
    [IO.File]::WriteAllText($path, $value, [Text.UTF8Encoding]::new($false))
}

if (Get-NetTCPConnection -LocalPort 18260 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Showcase port 18260 is already in use; no services were started.'
}

Write-Output 'public_flow_secrets_ready=true port_18260_free=true'
