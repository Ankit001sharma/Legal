# Podman helpers for Legal AI scripts (Windows).
$ErrorActionPreference = "Stop"

function Test-PodmanSocket {
    try {
        $null = podman info --format "{{.Host.Arch}}" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Start-PodmanMachine {
    Write-Host "Starting Podman machine (may take 1-3 min) ..."
    $machines = podman machine list --format "{{.Name}}" 2>$null
    if (-not $machines) {
        Write-Host "No Podman machine - running podman machine init ..."
        podman machine init | Out-Host
    }
    podman machine start | Out-Host
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        if (Test-PodmanSocket) {
            Write-Host "Podman machine is up."
            return
        }
    }
    throw "Podman machine did not start in time. Open Podman Desktop or run: podman machine start"
}

function Assert-PodmanSocket {
    param([switch]$StartMachine)
    if (Test-PodmanSocket) {
        return
    }
    if ($StartMachine) {
        Start-PodmanMachine
        return
    }
    throw @"
Podman is not running (cannot reach Podman socket).
Fix ONE of:
  1) podman machine start
  2) Open Podman Desktop and start the machine
  3) Use Docker instead: cd ""Legal ai""; docker compose up -d postgres
Then re-run: .\start_postgres_podman.ps1
"@
}

function Test-DockerCompose {
    try {
        $null = docker compose version 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}
