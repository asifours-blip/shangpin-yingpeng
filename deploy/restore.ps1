param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [Parameter(Mandatory = $true)][string]$KeyBackupPath,
    [Parameter(Mandatory = $true)][string]$RestoreProjectName,
    [Parameter(Mandatory = $true)][ValidateRange(1024, 65535)][int]$RestoreHttpPort
)

$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7 -or ($PSVersionTable.PSVersion.Major -eq 7 -and $PSVersionTable.PSVersion.Minor -lt 4)) {
    throw 'PowerShell 7.4 or newer is required.'
}
$PSNativeCommandUseErrorActionPreference = $true
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backup = [IO.Path]::GetFullPath($BackupPath).TrimEnd('\', '/')
$keys = [IO.Path]::GetFullPath($KeyBackupPath).TrimEnd('\', '/')
$environmentFile = Join-Path $PSScriptRoot 'stack.env'
$secretDirectory = Join-Path $PSScriptRoot 'secrets'
$runtimeDirectory = Join-Path $PSScriptRoot 'runtime'
$secretNames = @('postgres_password', 'redis_password', 'minio_access_key', 'minio_secret_key', 'session_key', 'oauth_keys', 'smoke_password')

function Invoke-Compose([string[]]$arguments) {
    & docker compose --env-file $environmentFile @arguments
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed (exit $LASTEXITCODE)" }
}

function Restore-Volume([string]$name) {
    $volume = "${RestoreProjectName}_${name}"
    & docker volume create $volume *> $null
    if ($LASTEXITCODE -ne 0) { throw "Cannot create $name volume." }
    & docker run --rm --network none --mount "type=volume,src=$volume,dst=/data" `
        --mount "type=bind,src=$(Join-Path $backup 'data'),dst=/backup,readonly" busybox:1.37.0 `
        tar -C /data -xzf "/backup/$name.tar.gz"
    if ($LASTEXITCODE -ne 0) { throw "Cannot restore $name volume." }
}

if ($RestoreProjectName -notmatch '^[a-z][a-z0-9-]{2,40}$') { throw 'Invalid restore project name.' }
if (-not (Test-Path -LiteralPath $backup) -or -not (Test-Path -LiteralPath $keys)) { throw 'Backup and separate key backup must exist.' }
if ($keys.Equals($backup, [StringComparison]::OrdinalIgnoreCase) -or
    $keys.StartsWith($backup + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
    $backup.StartsWith($keys + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Key backup must be separate from data backup.'
}
if (Test-Path -LiteralPath $environmentFile) { throw 'Restore target has deploy/stack.env; use a clean source directory.' }
if (Test-Path -LiteralPath $secretDirectory) { throw 'Restore target has deploy/secrets; refusing overwrite.' }
if (Test-Path -LiteralPath $runtimeDirectory) { throw 'Restore target has deploy/runtime; refusing overwrite.' }

$manifest = Get-Content -LiteralPath (Join-Path $backup 'manifest.json') -Raw | ConvertFrom-Json
if ($manifest.project -eq $RestoreProjectName) { throw 'Restore project must differ from source project.' }
$currentCommit = (& git -C $repo rev-parse HEAD).Trim()
if ($currentCommit -ne $manifest.commit) { throw 'Restore source commit does not match backup manifest.' }
if (& git -C $repo status --porcelain) { throw 'Restore requires a clean matching release checkout.' }
foreach ($config in @(@('compose.yaml', 'compose.yaml'), @('frontend/nginx.conf', 'nginx.conf'),
                     @('deploy/container-entrypoint.sh', 'container-entrypoint.sh'))) {
    $currentBlob = [string](& git -C $repo rev-parse "HEAD:$($config[0])")
    if ($LASTEXITCODE -ne 0) { throw 'Cannot identify release configuration.' }
    $savedBlob = [string](& git -C $repo hash-object "--path=$($config[0])" (Join-Path $backup 'config' $config[1]))
    if ($LASTEXITCODE -ne 0 -or $currentBlob.Trim() -ne $savedBlob.Trim()) {
        throw 'Deployment configuration does not match backup.'
    }
}
foreach ($line in Get-Content -LiteralPath (Join-Path $backup 'sha256.txt')) {
    if ($line -notmatch '^([A-Fa-f0-9]{64})  (.+)$') { throw 'Invalid backup hash manifest.' }
    $file = Join-Path $backup $Matches[2]
    if (-not (Test-Path -LiteralPath $file) -or (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $Matches[1]) {
        throw 'Backup integrity check failed.'
    }
}
foreach ($name in $secretNames) {
    $keyFile = Join-Path $keys $name
    if (-not (Test-Path -LiteralPath $keyFile)) { throw "Missing separately stored secret: $name" }
    if ((Get-FileHash -LiteralPath $keyFile -Algorithm SHA256).Hash -ne $manifest.secret_sha256.$name) {
        throw "Secret backup does not match data backup: $name"
    }
}

foreach ($kind in @('ps -a', 'network ls', 'volume ls')) {
    $parts = $kind.Split(' ')
    $format = if ($kind -eq 'volume ls') { '{{.Name}}' } else { '{{.ID}}' }
    $existing = [string](& docker @parts --filter "label=com.docker.compose.project=$RestoreProjectName" --format $format)
    if ($existing -and $existing.Trim()) { throw 'Restore project already has Docker resources; refusing overwrite.' }
}

foreach ($name in @('postgres_data', 'redis_data', 'minio_data')) {
    $PSNativeCommandUseErrorActionPreference = $false
    try {
        & docker volume inspect "${RestoreProjectName}_${name}" *> $null
        $exists = $LASTEXITCODE -eq 0
    } finally {
        $PSNativeCommandUseErrorActionPreference = $true
    }
    if ($exists) { throw "Restore target volume exists: $name; refusing overwrite." }
}
foreach ($service in @('backend', 'frontend', 'postgres', 'redis', 'minio')) {
    $PSNativeCommandUseErrorActionPreference = $false
    try {
        & docker image inspect "${RestoreProjectName}-${service}:stage6" *> $null
        $tagExists = $LASTEXITCODE -eq 0
    } finally {
        $PSNativeCommandUseErrorActionPreference = $true
    }
    if ($tagExists) { throw "Restore image tag exists for $service; refusing overwrite." }
}

Push-Location $repo
try {
    & docker load -i (Join-Path $backup 'data/images.tar') *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot restore matching image archive.' }
    foreach ($service in @('api', 'frontend', 'postgres', 'redis', 'minio')) {
        $imageId = $manifest.images.$service
        if (-not $imageId) { throw "Backup lacks $service image identity." }
        & docker image inspect $imageId *> $null
        if ($LASTEXITCODE -ne 0) { throw "Matching $service image unavailable." }
    }
    & docker tag $manifest.images.api "${RestoreProjectName}-backend:stage6"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot tag backend image.' }
    & docker tag $manifest.images.frontend "${RestoreProjectName}-frontend:stage6"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot tag frontend image.' }
    foreach ($service in @('postgres', 'redis', 'minio')) {
        & docker tag $manifest.images.$service "${RestoreProjectName}-${service}:stage6"
        if ($LASTEXITCODE -ne 0) { throw "Cannot tag restored $service image." }
    }

    New-Item -ItemType Directory -Path $secretDirectory, $runtimeDirectory | Out-Null
    foreach ($name in $secretNames) {
        Copy-Item -LiteralPath (Join-Path $keys $name) -Destination (Join-Path $secretDirectory $name)
    }
    $template = Get-Content -LiteralPath (Join-Path $backup 'stack.env')
    $template = $template | ForEach-Object {
        if ($_ -match '^STUDIO_PROJECT_NAME=') { "STUDIO_PROJECT_NAME=$RestoreProjectName" }
        elseif ($_ -match '^STUDIO_HTTP_PORT=') { "STUDIO_HTTP_PORT=$RestoreHttpPort" }
        elseif ($_ -match '^STUDIO_PUBLIC_BASE_URL=') { "STUDIO_PUBLIC_BASE_URL=http://127.0.0.1:$RestoreHttpPort" }
        else { $_ }
    }
    $template += "STUDIO_POSTGRES_IMAGE=${RestoreProjectName}-postgres:stage6"
    $template += "STUDIO_REDIS_IMAGE=${RestoreProjectName}-redis:stage6"
    $template += "STUDIO_MINIO_IMAGE=${RestoreProjectName}-minio:stage6"
    $template | Set-Content -LiteralPath $environmentFile -Encoding utf8
    Set-Content -LiteralPath (Join-Path $runtimeDirectory 'maintenance.block') -Value 'restore' -NoNewline

    Restore-Volume 'minio_data'
    Restore-Volume 'redis_data'
    Invoke-Compose @('up', '-d', '--wait', '--no-build', 'postgres', 'redis', 'minio')
    $pg = ([string](& docker compose --env-file $environmentFile ps -q postgres)).Trim()
    if (-not $pg) { throw 'PostgreSQL restore container missing.' }
    & docker cp (Join-Path $backup 'data/postgres.dump') "${pg}:/tmp/studio-stage6.dump"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot copy PostgreSQL dump.' }
    & docker exec $pg pg_restore -U studio --no-owner --clean --if-exists -d studio /tmp/studio-stage6.dump
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL restore failed.' }
    & docker exec $pg rm -f /tmp/studio-stage6.dump *> $null
    $revision = ([string](& docker exec $pg psql -U studio -d studio -Atc 'SELECT version_num FROM alembic_version')).Trim()
    if ($revision -eq '0012_publish_oauth') {
        Write-Host '0012 data restored in isolation under maintenance. Current 0013 API was not started. Use matching old code for rollback or run the explicit upgrade-0013 sequence.'
        return
    } elseif ($revision -eq '0013_publish_oauth_attempt_order') {
        Write-Host '0013 data restored in isolation under maintenance. Current 0014 API was not started; use the matching 0013 release or the explicit upgrade-0014 sequence.'
        return
    } elseif ($revision -ne '0014_operation_plans') {
        throw 'Unsupported restored schema; maintenance remains enabled.'
    }
    Invoke-Compose @('up', '-d', '--wait', '--no-build', 'api', 'frontend')
    Write-Host 'Restore created an isolated project under maintenance. Run smoke and verify data/media/credentials before maintenance-off.'
} finally {
    Pop-Location
}
