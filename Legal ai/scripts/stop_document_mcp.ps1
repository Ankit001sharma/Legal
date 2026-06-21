# Stop document-mcp listeners on port 8003 (Youngser P3-8)
param(
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
$PidFile = Join-Path $ScriptDir ".document_mcp.pid"

function Get-Port8003Pids {
    $found = @()
    netstat -ano | Select-String ":8003.*LISTENING" | ForEach-Object {
        $parts = ($_.Line -split '\s+') | Where-Object { $_ }
        if ($parts.Count -ge 1 -and $parts[-1] -match '^\d+$') {
            $found += [int]$parts[-1]
        }
    }
    return ($found | Select-Object -Unique)
}

$pids = Get-Port8003Pids

if (Test-Path $PidFile) {
    $saved = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($saved -match '^\d+$') {
        $pids += [int]$saved
    }
}

$pids = $pids | Select-Object -Unique

if ($pids.Count -eq 0) {
    if (-not $Quiet) { Write-Host "No document-mcp listener on port 8003." }
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
    exit 0
}

foreach ($procId in $pids) {
    if (-not $Quiet) { Write-Host "Stopping PID $procId (port 8003)..." }
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 1

$remaining = Get-Port8003Pids
if ($remaining.Count -gt 0) {
    Write-Error "Port 8003 still in use by PID(s): $($remaining -join ', ')"
}

if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
if (-not $Quiet) { Write-Host "document-mcp stopped." }
