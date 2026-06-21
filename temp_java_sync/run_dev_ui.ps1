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
    Write-Host "Created .env - set LLM_API_KEY for review."
}

$ReviewAgent = Join-Path $Root "..\review\review_agent"
python -c "import langchain" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Youngser P0: installing review stack dependencies..."
    & (Join-Path $ReviewAgent "scripts\install_deps.ps1")
}

Write-Host "Open in browser: http://localhost:8090"
Write-Host "Ensure document-mcp is running on port 8003"
python dev_ui_server.py
