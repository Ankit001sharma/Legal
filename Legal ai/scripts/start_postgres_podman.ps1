# Postgres + pgvector for local dev (port 5435).
# Tries Podman first; falls back to docker compose if Podman is down.
param(
    [switch]$StartPodmanMachine,
    [switch]$DockerOnly
)

$ErrorActionPreference = "Stop"
$ContainerName = "legalai-postgres"
$PodmanImage = "docker.io/pgvector/pgvector:pg16"
$DbHost = "127.0.0.1"
$DbPort = 5435
$LegalAi = Split-Path -Parent $PSScriptRoot

. (Join-Path $PSScriptRoot "ensure_podman.ps1")

function Test-PostgresPort {
    param([int]$TimeoutSec = 2)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect($DbHost, $DbPort, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne([TimeSpan]::FromSeconds($TimeoutSec), $false)
        if ($ok -and $client.Connected) {
            $client.Close()
            return $true
        }
        $client.Close()
    } catch {
        # port closed
    }
    return $false
}

function Invoke-Psql {
    param([string]$Sql)
    $env:PGPASSWORD = "legalai"
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & psql -U legalai -h $DbHost -p $DbPort -d legalai -c $Sql 2>&1 | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return $code
}

function Test-PsqlReady {
  return (Invoke-Psql "SELECT 1") -eq 0
}

function Wait-PostgresReady {
    param([int]$MaxSeconds = 90)
    $deadline = (Get-Date).AddSeconds($MaxSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PostgresPort) {
            if (Get-Command psql -ErrorAction SilentlyContinue) {
                if ((Test-PsqlReady)) { return $true }
            } else {
                Start-Sleep -Seconds 3
                return $true
            }
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Start-PostgresPodman {
    Assert-PodmanSocket -StartMachine:$StartPodmanMachine
    $other = podman ps --format "{{.Names}} {{.Ports}}" 2>$null | Select-String "5435->"
    if ($other -and ($other -notmatch $ContainerName)) {
        Write-Warning "Another container may already use host port 5435."
    }

    $existing = podman ps -a --filter "name=^${ContainerName}$" --format "{{.Names}}" 2>$null
    if ($existing -eq $ContainerName) {
        $running = podman ps --filter "name=^${ContainerName}$" --format "{{.Names}}" 2>$null
        if ($running -eq $ContainerName -and -not (Test-PostgresPort)) {
            Write-Host "$ContainerName listed as running but port $DbPort is closed - restarting ..."
            podman restart $ContainerName | Out-Null
        } elseif ($running -ne $ContainerName) {
            Write-Host "Starting $ContainerName ..."
            podman start $ContainerName | Out-Null
        } else {
            Write-Host "$ContainerName running (Podman)."
        }
    } else {
        Write-Host "Creating $ContainerName via Podman (host port $DbPort) ..."
        podman run -d `
            --name $ContainerName `
            --memory=2g `
            -e POSTGRES_USER=legalai `
            -e POSTGRES_PASSWORD=legalai `
            -e POSTGRES_DB=legalai `
            -p "${DbPort}:5432" `
            -v legalai_pgdata:/var/lib/postgresql/data `
            $PodmanImage `
            postgres `
            -c shared_buffers=256MB `
            -c maintenance_work_mem=128MB `
            -c effective_cache_size=512MB `
            -c work_mem=8MB `
            -c max_connections=100 | Out-Null
    }
}

function Start-PostgresDockerCompose {
    if (-not (Test-DockerCompose)) {
        throw "Docker Compose not available. Install Docker Desktop or start Podman."
    }
    Write-Host "Starting postgres via docker compose (host port $DbPort) ..."
    Push-Location $LegalAi
    try {
        docker compose up -d postgres | Out-Host
    } finally {
        Pop-Location
    }
}

function Ensure-PostgresExtension {
    $null = Invoke-Psql "CREATE EXTENSION IF NOT EXISTS vector;"
}

function Print-Ready {
    Write-Host ""
    Write-Host "Postgres + pgvector ready ($ContainerName):"
    Write-Host "  DATABASE_URL=postgresql://legalai:legalai@${DbHost}:${DbPort}/legalai"
    Write-Host ""
    Write-Host "NOTE: podman-vector-db on 5432 is a DIFFERENT database - do not mix URLs."
    Write-Host "NOTE: Postgres tuning flags apply on NEW containers only; recreate with:"
    Write-Host "  podman rm -f $ContainerName; .\start_postgres_podman.ps1"
}

if (Test-PostgresPort) {
    Write-Host "Postgres already accepting connections on ${DbHost}:${DbPort}."
    Ensure-PostgresExtension
    Print-Ready
    exit 0
}

$started = $false
if (-not $DockerOnly) {
    try {
        Start-PostgresPodman
        $started = $true
    } catch {
        Write-Warning $_.Exception.Message
        if (Test-DockerCompose) {
            Write-Host "Falling back to docker compose ..."
            Start-PostgresDockerCompose
            $started = $true
        } else {
            throw
        }
    }
} else {
    Start-PostgresDockerCompose
    $started = $true
}

if (-not $started) {
    throw "Failed to start Postgres."
}

Write-Host "Waiting for Postgres on ${DbHost}:${DbPort} ..."
if (-not (Wait-PostgresReady)) {
    throw "Postgres did not become ready on ${DbHost}:${DbPort}. Check: podman ps -a OR docker compose ps"
}

Ensure-PostgresExtension
Print-Ready
