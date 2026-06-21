param(
    [ValidateSet("sync", "review", "full")]
    [string]$Mode = "full"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$env:PYTHONPATH = @(
    (Join-Path $Root "..\document_core"),
    (Join-Path $Root "..\review\review_agent"),
    (Join-Path $Root "..\Legal ai")
) -join ";"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example - set LLM_API_KEY before review."
}

switch ($Mode) {
    "sync"   { python run_sync_only.py; exit $LASTEXITCODE }
    "review" { python run_review_only.py; exit $LASTEXITCODE }
    "full"   { python run_full_e2e.py; exit $LASTEXITCODE }
}
