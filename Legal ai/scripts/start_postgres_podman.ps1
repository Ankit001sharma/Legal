# Podman Postgres + pgvector on host port 5435 (does not conflict with podman-vector-db on 5432)
$ErrorActionPreference = "Stop"
$ContainerName = "legalai-postgres"
$Image = "docker.io/pgvector/pgvector:pg16"

$other = podman ps --format "{{.Names}} {{.Ports}}" 2>$null | Select-String "5435->"
if ($other -and ($other -notmatch $ContainerName)) {
    Write-Warning "Another container may already use host port 5435."
}

$existing = podman ps -a --filter "name=^${ContainerName}$" --format "{{.Names}}" 2>$null
if ($existing -eq $ContainerName) {
    $running = podman ps --filter "name=^${ContainerName}$" --format "{{.Names}}" 2>$null
    if ($running -ne $ContainerName) {
        Write-Host "Starting $ContainerName ..."
        podman start $ContainerName | Out-Null
    } else {
        Write-Host "$ContainerName already running on port 5435."
    }
} else {
    Write-Host "Creating $ContainerName (host port 5435) ..."
    podman run -d `
        --name $ContainerName `
        -e POSTGRES_USER=legalai `
        -e POSTGRES_PASSWORD=legalai `
        -e POSTGRES_DB=legalai `
        -p 5435:5432 `
        -v legalai_pgdata:/var/lib/postgresql/data `
        $Image | Out-Null
}

Start-Sleep -Seconds 4
$env:PGPASSWORD = "legalai"
$ok = psql -U legalai -h localhost -p 5435 -d legalai -c "SELECT 1" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Cannot connect to $ContainerName on port 5435. Output: $ok"
}

psql -U legalai -h localhost -p 5435 -d legalai -c "CREATE EXTENSION IF NOT EXISTS vector;" | Out-Null
Write-Host ""
Write-Host "Postgres + pgvector ready (legalai-postgres):"
Write-Host "  DATABASE_URL=postgresql://legalai:legalai@localhost:5435/legalai"
Write-Host ""
Write-Host "NOTE: podman-vector-db on port 5432 is a DIFFERENT database - do not mix URLs."
Write-Host "Stop:  podman stop $ContainerName"
