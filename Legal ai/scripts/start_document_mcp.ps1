# Start document-mcp with correct DATABASE_URL from document_core/.env (Youngser P3-8)
param(
    [switch]$Replace,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$LegalAi = Split-Path -Parent $PSScriptRoot
$DocumentCore = Join-Path (Split-Path -Parent $LegalAi) "document_core"
$EnvFile = Join-Path $DocumentCore ".env"
$PidFile = Join-Path $PSScriptRoot ".document_mcp.pid"

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

if (-not (Test-Path $EnvFile)) {
    Write-Error "Missing $EnvFile - copy from .env.example"
}

Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line -match "=") {
        $name, $value = $line.Split("=", 2)
        Set-Item -Path "env:$name" -Value $value
    }
}

$env:PYTHONPATH = "$DocumentCore;$LegalAi"

if ($Status) {
    $pids = Get-Port8003Pids
    Write-Host "Port 8003 listeners: $(if ($pids) { $pids -join ', ' } else { '(none)' })"
    if (Test-Path $PidFile) {
        Write-Host "Pidfile: $(Get-Content $PidFile -Raw)"
    }
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:8003/health" -TimeoutSec 5
        Write-Host "Health:"
        $resp | ConvertTo-Json -Depth 5
        $caps = @($resp.capabilities)
        if ($caps -contains "search_request_metadata") {
            Write-Host "Capability OK: search_request_metadata"
        } else {
            Write-Host "WARNING: missing search_request_metadata capability (stale MCP?)"
        }
    } catch {
        Write-Host "Health check failed: $_"
    }
    exit 0
}

$existing = Get-Port8003Pids
if ($existing.Count -gt 0) {
    if (-not $Replace) {
        Write-Error @"
Port 8003 already in use by PID(s): $($existing -join ', ').
Stop stale document-mcp or restart with -Replace:
  .\stop_document_mcp.ps1
  .\start_document_mcp.ps1 -Replace
Only ONE document-mcp instance should listen on 8003 or you may hit stale code.
"@
    }
    & (Join-Path $PSScriptRoot "stop_document_mcp.ps1") -Quiet
}

& (Join-Path $PSScriptRoot "start_postgres_podman.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $env:DOCUMENT_MCP_BUILD_ID) {
    try {
        $sha = git -C $LegalAi rev-parse --short HEAD 2>$null
        if ($sha) { $env:DOCUMENT_MCP_BUILD_ID = $sha.Trim() }
    } catch { }
}
if (-not $env:DOCUMENT_MCP_BUILD_ID) {
    $env:DOCUMENT_MCP_BUILD_ID = "dev-$(Get-Date -Format 'yyyyMMddHHmmss')"
}

Set-Location $LegalAi
Write-Host "document-mcp -> http://localhost:8003"
Write-Host "DATABASE_URL=$env:DATABASE_URL"
Write-Host "DOCUMENT_MCP_BUILD_ID=$env:DOCUMENT_MCP_BUILD_ID"
uvicorn mcp.document_server.main:app --host 0.0.0.0 --port 8003
