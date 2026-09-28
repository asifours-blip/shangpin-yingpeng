param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('init', 'up', 'maintenance-on', 'maintenance-off', 'backup', 'upgrade-0013', 'upgrade-0014', 'restore', 'smoke')]
    [string]$Action,
    [string]$BackupPath,
    [string]$KeyBackupPath,
    [string]$PriorReleasePath,
    [string]$RestoreProjectName,
    [int]$RestoreHttpPort = 0
)

$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7 -or ($PSVersionTable.PSVersion.Major -eq 7 -and $PSVersionTable.PSVersion.Minor -lt 4)) {
    throw 'PowerShell 7.4 or newer is required.'
}
$PSNativeCommandUseErrorActionPreference = $true
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$environmentFile = Join-Path $PSScriptRoot 'stack.env'
$secretDirectory = Join-Path $PSScriptRoot 'secrets'
$runtimeDirectory = Join-Path $PSScriptRoot 'runtime'
$maintenanceMarker = Join-Path $runtimeDirectory 'maintenance.block'
$secretNames = @('postgres_password', 'redis_password', 'minio_access_key', 'minio_secret_key', 'session_key', 'oauth_keys', 'smoke_password')

function New-RandomHex([int]$bytes) {
    return [Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes($bytes)).ToLowerInvariant()
}

function Read-StackSetting([string]$name) {
    $line = Get-Content -LiteralPath $environmentFile | Where-Object { $_ -match "^$name=" } | Select-Object -Last 1
    if (-not $line) { throw "Missing setting $name in deploy/stack.env" }
    return ($line -split '=', 2)[1]
}

function Invoke-Compose([string[]]$arguments) {
    & docker compose --env-file $environmentFile @arguments
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed (exit $LASTEXITCODE)" }
}

function Assert-Docker {
    & docker info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Docker daemon is unavailable; no container action was performed.' }
}

function Assert-Initialized {
    if (-not (Test-Path -LiteralPath $environmentFile)) { throw 'Run init, then edit deploy/stack.env.' }
    $project = Read-StackSetting 'STUDIO_PROJECT_NAME'
    if ($project -notmatch '^[a-z][a-z0-9-]{2,40}$') { throw 'Invalid STUDIO_PROJECT_NAME.' }
    foreach ($name in $secretNames) {
        $path = Join-Path $secretDirectory $name
        if (-not (Test-Path -LiteralPath $path) -or (Get-Item -LiteralPath $path).Length -eq 0) {
            throw "Missing deployment secret file: $name"
        }
    }
    if (-not (Test-Path -LiteralPath $runtimeDirectory)) {
        New-Item -ItemType Directory -Path $runtimeDirectory | Out-Null
    }
    return $project
}

function Assert-Maintained {
    if (-not (Test-Path -LiteralPath $maintenanceMarker)) { throw 'Enable maintenance first.' }
    foreach ($service in @('api', 'generation-worker', 'legacy-generation-worker', 'publish-worker', 'operation-scheduler')) {
        $container = ([string]::Join('', [string[]]@(& docker compose --env-file $environmentFile --profile workers --profile scheduler ps -q $service))).Trim()
        if ($LASTEXITCODE -ne 0) { throw "Cannot inspect $service" }
        if ($container) {
            $running = (& docker inspect --format '{{.State.Running}}' $container).Trim()
            if ($running -eq 'true') { throw "$service is still running; backup/upgrade refused." }
        }
    }
}

function Stop-Application {
    # The nginx marker blocks new callbacks and writes before graceful shutdown.
    Set-Content -LiteralPath $maintenanceMarker -Value 'maintenance' -NoNewline
    $drainProof = Join-Path $runtimeDirectory 'drain.ok'
    foreach ($service in @('generation-worker', 'legacy-generation-worker', 'publish-worker', 'operation-scheduler')) {
        $container = ([string]::Join('', [string[]]@(& docker compose --env-file $environmentFile --profile workers --profile scheduler ps -q $service))).Trim()
        if ($container) { throw "$service is running; drain and stop it before migration." }
    }
    $runningApi = ([string]::Join('', [string[]]@(& docker compose --env-file $environmentFile ps -q api))).Trim()
    $apiContainer = ([string]::Join('', [string[]]@(& docker compose --env-file $environmentFile ps -a -q api))).Trim()
    if (-not $runningApi) {
        if (-not $apiContainer) { Assert-Maintained; return }
        if (-not (Test-Path -LiteralPath $drainProof)) {
            throw 'Stopped API has no matching clean-drain proof; keep maintenance and investigate.'
        }
        $proof = Get-Content -LiteralPath $drainProof -Raw | ConvertFrom-Json
        $current = & docker inspect --format '{{.Id}}|{{.State.StartedAt}}|{{.State.FinishedAt}}|{{.State.ExitCode}}|{{.State.OOMKilled}}' $apiContainer
        if ($LASTEXITCODE -ne 0 -or $current -ne $proof.instance) {
            throw 'Stopped API run does not match its clean-drain proof; keep maintenance.'
        }
        Assert-Maintained
        return
    }
    if (Test-Path -LiteralPath $drainProof) { Remove-Item -LiteralPath $drainProof }
    $stoppedAt = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    Invoke-Compose @('stop', '-t', '120', 'api')
    $container = ([string]::Join('', [string[]]@(& docker compose --env-file $environmentFile ps -a -q api))).Trim()
    if ($container -ne $runningApi) { throw 'API container changed during drain; keep maintenance.' }
    $exitCode = (& docker inspect --format '{{.State.ExitCode}}' $container).Trim()
    $oom = (& docker inspect --format '{{.State.OOMKilled}}' $container).Trim()
    $messages = & docker logs --since $stoppedAt $container 2>&1
    $cleanMarkers = @('Shutting down', 'Application shutdown complete', 'Finished server process')
    $complete = @($cleanMarkers | Where-Object { $messages | Select-String -SimpleMatch $_ -Quiet }).Count -eq $cleanMarkers.Count
    if ($exitCode -notin @('0', '143') -or $oom -eq 'true' -or -not $complete -or
        ($messages | Select-String -Pattern 'Cancel .*graceful shutdown|graceful shutdown exceeded' -Quiet)) {
        throw 'API did not prove a clean drain; keep maintenance and investigate unknown outcomes.'
    }
    $instance = & docker inspect --format '{{.Id}}|{{.State.StartedAt}}|{{.State.FinishedAt}}|{{.State.ExitCode}}|{{.State.OOMKilled}}' $container
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect drained API instance.' }
    @{ instance = $instance } | ConvertTo-Json -Compress | Set-Content -LiteralPath $drainProof -NoNewline
    Assert-Maintained
}

function Copy-VolumeToBackup([string]$project, [string]$volume, [string]$destination) {
    & docker run --rm --network none --mount "type=volume,src=${project}_${volume},dst=/data,readonly" `
        --mount "type=bind,src=$destination,dst=/backup" busybox:1.37.0 `
        tar -C /data -czf "/backup/$volume.tar.gz" .
    if ($LASTEXITCODE -ne 0) { throw "Volume backup failed: $volume" }
}

function Write-Backup([string]$project, [string]$destination, [string]$keysDestination) {
    Assert-Maintained
    if (Test-Path -LiteralPath $destination) { throw 'Backup target exists; refusing overwrite.' }
    if (Test-Path -LiteralPath $keysDestination) { throw 'Key backup target exists; refusing overwrite.' }
    if ($keysDestination.Equals($destination, [StringComparison]::OrdinalIgnoreCase) -or
        $keysDestination.StartsWith($destination + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        $destination.StartsWith($keysDestination + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Key backup must be in a separate directory from data backup.'
    }
    New-Item -ItemType Directory -Path $destination | Out-Null
    $dataDirectory = Join-Path $destination 'data'
    New-Item -ItemType Directory -Path $dataDirectory, $keysDestination | Out-Null
    Copy-Item -LiteralPath $environmentFile -Destination (Join-Path $destination 'stack.env')
    $configDirectory = Join-Path $destination 'config'
    New-Item -ItemType Directory -Path $configDirectory | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo 'compose.yaml') -Destination (Join-Path $configDirectory 'compose.yaml')
    Copy-Item -LiteralPath (Join-Path $repo 'frontend/nginx.conf') -Destination (Join-Path $configDirectory 'nginx.conf')
    Copy-Item -LiteralPath (Join-Path $repo 'deploy/container-entrypoint.sh') -Destination (Join-Path $configDirectory 'container-entrypoint.sh')
    foreach ($name in $secretNames) {
        Copy-Item -LiteralPath (Join-Path $secretDirectory $name) -Destination (Join-Path $keysDestination $name)
    }
    $pg = ([string](& docker compose --env-file $environmentFile ps -q postgres)).Trim()
    if (-not $pg) { throw 'PostgreSQL container is not running.' }
    & docker exec $pg sh -ec 'pg_dump -U studio -Fc studio > /tmp/studio-stage6.dump'
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed.' }
    & docker cp "${pg}:/tmp/studio-stage6.dump" (Join-Path $dataDirectory 'postgres.dump')
    if ($LASTEXITCODE -ne 0) { throw 'Copying pg_dump failed.' }
    & docker exec $pg rm -f /tmp/studio-stage6.dump | Out-Null
    Invoke-Compose @('stop', '-t', '30', 'minio', 'redis')
    try {
        Copy-VolumeToBackup $project 'minio_data' $dataDirectory
        Copy-VolumeToBackup $project 'redis_data' $dataDirectory
    } finally {
        Invoke-Compose @('up', '-d', '--wait', 'minio', 'redis')
    }
    $imageIds = @{}
    foreach ($service in @('api', 'frontend', 'postgres', 'redis', 'minio')) {
        $container = ([string]::Join('', [string[]]@(& docker compose --env-file $environmentFile ps -a -q $service))).Trim()
        if ($container) {
            $imageIds[$service] = (& docker inspect --format '{{.Image}}' $container).Trim()
        } elseif ($service -in @('api', 'frontend')) {
            # The 0012 fixture deliberately has no running API/frontend yet.
            Invoke-Compose @('build', $service)
            $tag = if ($service -eq 'api') { "${project}-backend:stage6" } else { "${project}-frontend:stage6" }
            $imageIds[$service] = (& docker image inspect --format '{{.Id}}' $tag).Trim()
        } else {
            throw "Required middleware container absent: $service"
        }
    }
    $commit = (& git -C $repo rev-parse HEAD).Trim()
    if (& git -C $repo status --porcelain) { throw 'Backup requires a clean release checkout.' }
    $revision = ([string](& docker exec $pg psql -U studio -d studio -Atc 'SELECT version_num FROM alembic_version')).Trim()
    $keyFingerprints = @{}
    foreach ($name in $secretNames) {
        $keyFingerprints[$name] = (Get-FileHash -LiteralPath (Join-Path $keysDestination $name) -Algorithm SHA256).Hash
    }
    $manifest = @{ project = $project; commit = $commit; schema = $revision; images = $imageIds;
                   secret_sha256 = $keyFingerprints; created_utc = [DateTime]::UtcNow.ToString('o') }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $destination 'manifest.json') -Encoding utf8
    & docker save -o (Join-Path $dataDirectory 'images.tar') @($imageIds.Values) 'busybox:1.37.0'
    if ($LASTEXITCODE -ne 0) { throw 'Image backup failed.' }
    Get-ChildItem -LiteralPath $destination -Recurse -File | Where-Object Name -ne 'sha256.txt' |
        Get-FileHash -Algorithm SHA256 | ForEach-Object { "$($_.Hash)  $($_.Path.Substring($destination.Length + 1))" } |
        Set-Content -LiteralPath (Join-Path $destination 'sha256.txt') -Encoding ascii
    Write-Host "Backup written to $destination; secrets copied separately to $keysDestination."
}

Push-Location $repo
try {
    if ($Action -eq 'init') {
        if (-not (Test-Path -LiteralPath $secretDirectory)) { New-Item -ItemType Directory -Path $secretDirectory | Out-Null }
        if (-not (Test-Path -LiteralPath $runtimeDirectory)) { New-Item -ItemType Directory -Path $runtimeDirectory | Out-Null }
        if (-not (Test-Path -LiteralPath $environmentFile)) {
            Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'stack.env.example') -Destination $environmentFile
        }
        foreach ($name in $secretNames) {
            $path = Join-Path $secretDirectory $name
            if (Test-Path -LiteralPath $path) { continue }
            $value = if ($name -eq 'oauth_keys') {
                @{ v1 = [Convert]::ToBase64String([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)) } | ConvertTo-Json -Compress
            } else { New-RandomHex 32 }
            [System.IO.File]::WriteAllText($path, $value, [System.Text.UTF8Encoding]::new($false))
        }
        Write-Host 'Local deployment config and unique secret files created. Review deploy/stack.env; no credentials printed.'
        return
    }

    if ($Action -eq 'restore') {
        if (-not $BackupPath -or -not $KeyBackupPath -or -not $RestoreProjectName -or -not $RestoreHttpPort) {
            throw 'Specify -BackupPath, separate -KeyBackupPath, -RestoreProjectName, and -RestoreHttpPort.'
        }
        Assert-Docker
        & (Join-Path $PSScriptRoot 'restore.ps1') -BackupPath $BackupPath -KeyBackupPath $KeyBackupPath `
            -RestoreProjectName $RestoreProjectName -RestoreHttpPort $RestoreHttpPort
        if (-not $?) { throw 'Restore failed.' }
        return
    }

    if ($Action -eq 'upgrade-0013') {
        throw 'This public snapshot has no 0013 release history. Use matching prior release materials outside this snapshot.'
    }
    $project = Assert-Initialized
    Assert-Docker
    if ($Action -eq 'up') {
        Invoke-Compose @('up', '-d', '--wait', 'postgres', 'redis', 'minio')
        $pg = ([string](& docker compose --env-file $environmentFile ps -q postgres)).Trim()
        $schema = (& docker exec $pg psql -U studio -d studio -Atc "SELECT COALESCE(to_regclass('public.alembic_version')::text, '')").Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect database schema.' }
        if (-not $schema) { Invoke-Compose @('--profile', 'tools', 'run', '--rm', 'migrate') }
        elseif ((& docker exec $pg psql -U studio -d studio -Atc 'SELECT version_num FROM alembic_version').Trim() -ne '0014_operation_plans') {
            throw 'Existing database requires the explicit upgrade-0014 sequence; 0012 must first use the historical 0013 release.'
        }
        Invoke-Compose @('up', '-d', '--wait', 'api', 'frontend')
        return
    }
    if ($Action -eq 'maintenance-on') {
        Stop-Application
        return
    }
    if ($Action -eq 'maintenance-off') {
        Invoke-Compose @('up', '-d', '--wait', 'api', 'frontend')
        Invoke-Compose @('restart', 'frontend')
        $drainProof = Join-Path $runtimeDirectory 'drain.ok'
        if (Test-Path -LiteralPath $drainProof) { Remove-Item -LiteralPath $drainProof }
        if (Test-Path -LiteralPath $maintenanceMarker) { Remove-Item -LiteralPath $maintenanceMarker }
        return
    }
    if ($Action -eq 'backup') {
        if (-not $BackupPath -or -not $KeyBackupPath) { throw 'Specify -BackupPath and separate -KeyBackupPath.' }
        Write-Backup $project ([System.IO.Path]::GetFullPath($BackupPath).TrimEnd('\', '/')) ([System.IO.Path]::GetFullPath($KeyBackupPath).TrimEnd('\', '/'))
        return
    }
    if ($Action -eq 'upgrade-0014') {
        if (-not $BackupPath -or -not $KeyBackupPath -or -not $PriorReleasePath) {
            throw 'Specify the existing 0013 -BackupPath, separate -KeyBackupPath, and -PriorReleasePath.'
        }
        $prior = [IO.Path]::GetFullPath($PriorReleasePath).TrimEnd('\', '/')
        & (Join-Path $PSScriptRoot 'verify-upgrade-source.ps1') -PriorReleasePath $prior `
            -BackupPath $BackupPath -KeyBackupPath $KeyBackupPath -ProjectName $project `
            -CurrentEnvironmentFile $environmentFile -CurrentSecretDirectory $secretDirectory
        if (-not $?) { throw 'Prior 0013 backup verification failed; no migration was run.' }
        Copy-Item -LiteralPath (Join-Path $prior 'deploy/runtime/drain.ok') `
            -Destination (Join-Path $runtimeDirectory 'drain.ok')
        Stop-Application
        $pg = ([string](& docker compose --env-file $environmentFile ps -q postgres)).Trim()
        $current = (& docker exec $pg psql -U studio -d studio -Atc 'SELECT version_num FROM alembic_version').Trim()
        if ($current -ne '0013_publish_oauth_attempt_order') { throw 'Expected 0013; no migration was run.' }
        Invoke-Compose @('--profile', 'tools', 'run', '--rm', 'migrate-0014')
        Invoke-Compose @('up', '-d', '--wait', 'api', 'frontend')
        Invoke-Compose @('restart', 'frontend')
        # Keep maintenance until the operator runs smoke and explicitly opens the gate.
        Write-Host '0014 upgraded. Run smoke, inspect operation runs and existing assets, then maintenance-off.'
        return
    }
    if ($Action -eq 'smoke') {
        Invoke-Compose @('exec', '-T', 'api', '/usr/local/bin/studio-entrypoint', 'verify')
        $port = Read-StackSetting 'STUDIO_HTTP_PORT'
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/healthz" -UseBasicParsing
        if ($response.StatusCode -ne 200) { throw 'Frontend liveness failed.' }
        foreach ($path in @('/', '/publish-accounts')) {
            $page = Invoke-WebRequest -Uri "http://127.0.0.1:$port$path" -UseBasicParsing
            if ($page.StatusCode -ne 200 -or $page.Headers['Content-Type'] -notmatch '^text/html') {
                throw "Frontend page failed: $path"
            }
        }
        Write-Host 'Dependencies, schema, frontend pages passed; authenticated feature smoke remains separate.'
        return
    }
} finally {
    Pop-Location
}
