param(
    [Parameter(Mandatory = $true)][string]$PriorReleasePath,
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [Parameter(Mandatory = $true)][string]$KeyBackupPath,
    [Parameter(Mandatory = $true)][string]$ProjectName,
    [Parameter(Mandatory = $true)][string]$CurrentEnvironmentFile,
    [Parameter(Mandatory = $true)][string]$CurrentSecretDirectory
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$prior = [IO.Path]::GetFullPath($PriorReleasePath).TrimEnd('\', '/')
$backup = [IO.Path]::GetFullPath($BackupPath).TrimEnd('\', '/')
$keys = [IO.Path]::GetFullPath($KeyBackupPath).TrimEnd('\', '/')
$priorEnvironment = Join-Path $prior 'deploy/stack.env'
$priorRuntime = Join-Path $prior 'deploy/runtime'
$proofPath = Join-Path $priorRuntime 'drain.ok'
$markerPath = Join-Path $priorRuntime 'maintenance.block'
$requiredFiles = @(
    'manifest.json', 'stack.env', 'config/compose.yaml', 'config/nginx.conf',
    'config/container-entrypoint.sh', 'data/postgres.dump', 'data/minio_data.tar.gz',
    'data/redis_data.tar.gz', 'data/images.tar'
)
$secretNames = @('postgres_password', 'redis_password', 'minio_access_key', 'minio_secret_key', 'session_key', 'oauth_keys', 'smoke_password')

if (-not (Test-Path -LiteralPath $priorEnvironment) -or -not (Test-Path -LiteralPath $markerPath) -or
    -not (Test-Path -LiteralPath $proofPath)) {
    throw 'Matching prior release, maintenance marker, and generated drain proof are required.'
}
if (-not (Test-Path -LiteralPath $backup) -or -not (Test-Path -LiteralPath $keys)) {
    throw 'Existing data and separate key backups are required before upgrade.'
}
$manifest = Get-Content -LiteralPath (Join-Path $backup 'manifest.json') -Raw | ConvertFrom-Json
if ($manifest.project -ne $ProjectName -or $manifest.schema -ne '0013_publish_oauth_attempt_order') {
    throw 'Backup project or schema does not match the maintained 0013 stack.'
}
$oldCommit = (& git -C $prior rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $oldCommit -ne $manifest.commit -or (& git -C $prior status --porcelain)) {
    throw 'Prior release checkout is not the clean commit recorded by the backup.'
}
if ((Get-FileHash -LiteralPath $priorEnvironment -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath $CurrentEnvironmentFile -Algorithm SHA256).Hash -or
    (Get-FileHash -LiteralPath $priorEnvironment -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath (Join-Path $backup 'stack.env') -Algorithm SHA256).Hash) {
    throw 'Active configuration differs from the prior release backup.'
}
foreach ($config in @(@('compose.yaml', 'compose.yaml'), @('frontend/nginx.conf', 'nginx.conf'),
                     @('deploy/container-entrypoint.sh', 'container-entrypoint.sh'))) {
    $blob = [string](& git -C $prior rev-parse "HEAD:$($config[0])")
    $saved = [string](& git -C $prior hash-object "--path=$($config[0])" (Join-Path $backup 'config' $config[1]))
    if ($LASTEXITCODE -ne 0 -or $blob.Trim() -ne $saved.Trim()) {
        throw 'Prior release deployment config does not match its backup.'
    }
}
$hashes = @{}
foreach ($line in Get-Content -LiteralPath (Join-Path $backup 'sha256.txt')) {
    if ($line -notmatch '^([A-Fa-f0-9]{64})  (.+)$') { throw 'Invalid backup checksum listing.' }
    $relative = $Matches[2].Replace([IO.Path]::DirectorySeparatorChar, '/')
    if ([IO.Path]::IsPathRooted($Matches[2]) -or $relative.StartsWith('/') -or
        $relative -match '(^|/)\.\.(/|$)' -or $hashes.ContainsKey($relative)) {
        throw 'Unsafe or duplicate backup checksum path.'
    }
    $file = Join-Path $backup $relative
    if (-not (Test-Path -LiteralPath $file) -or (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $Matches[1]) {
        throw 'Prior release backup checksum failed.'
    }
    $hashes[$relative] = $true
}
foreach ($name in $requiredFiles) {
    if (-not $hashes.ContainsKey($name)) { throw "Prior release backup lacks checked file: $name" }
}
foreach ($name in $secretNames) {
    $saved = Join-Path $keys $name
    $active = Join-Path $CurrentSecretDirectory $name
    if (-not (Test-Path -LiteralPath $saved) -or -not (Test-Path -LiteralPath $active) -or
        (Get-FileHash -LiteralPath $saved -Algorithm SHA256).Hash -ne $manifest.secret_sha256.$name -or
        (Get-FileHash -LiteralPath $active -Algorithm SHA256).Hash -ne $manifest.secret_sha256.$name) {
        throw "Prior release secret backup mismatch: $name"
    }
}
$proof = Get-Content -LiteralPath $proofPath -Raw | ConvertFrom-Json
foreach ($service in @('generation-worker', 'legacy-generation-worker', 'publish-worker', 'operation-scheduler')) {
    $container = ([string]::Join('', [string[]]@(& docker compose --env-file $CurrentEnvironmentFile `
        --profile workers --profile scheduler ps -q $service))).Trim()
    if ($LASTEXITCODE -ne 0 -or $container) { throw "Prior $service must be stopped for the entire backup interval." }
}
$api = ([string](& docker compose --env-file $CurrentEnvironmentFile ps -a -q api)).Trim()
if ($LASTEXITCODE -ne 0 -or -not $api) { throw 'Prior API container is missing.' }
$observed = [string](& docker inspect --format '{{.Id}}|{{.State.StartedAt}}|{{.State.FinishedAt}}|{{.State.ExitCode}}|{{.State.OOMKilled}}' $api)
$running = [string](& docker inspect --format '{{.State.Running}}' $api)
if ($LASTEXITCODE -ne 0 -or $running.Trim() -ne 'false' -or $observed.Trim() -ne $proof.instance) {
    throw 'Prior API run does not match its generated clean-drain proof.'
}
$finishedAt = [DateTimeOffset]::Parse(($observed -split '\|')[2])
$createdAt = [DateTimeOffset]::new([DateTime]$manifest.created_utc)
if ($createdAt -lt $finishedAt) { throw 'Backup predates the completed API drain.' }
foreach ($service in @('api', 'frontend', 'postgres', 'redis', 'minio')) {
    $container = ([string](& docker compose --env-file $CurrentEnvironmentFile ps -a -q $service)).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $container) { throw "Prior $service container is missing." }
    $imageId = [string](& docker inspect --format '{{.Image}}' $container)
    if ($LASTEXITCODE -ne 0 -or $imageId.Trim() -ne $manifest.images.$service) {
        throw "Prior $service image differs from the backup manifest."
    }
}
Write-Host 'Prior 0013 release, maintained API drain, image identities, backup hashes, and separate keys match.'
