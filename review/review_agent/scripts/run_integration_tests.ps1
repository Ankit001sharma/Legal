$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not $env:DATABASE_URL) {
  $env:DATABASE_URL = "postgresql://legalai:legalai@localhost:5435/legalai"
}
$env:DOCUMENT_STORE_BACKEND = "pgvector"
$env:PYTHONPATH = "../../document_core;.;../../Legal ai"
$env:GUARD_PASS_ENABLED = "false"
$env:FINAL_GAP_VERIFY_ENABLED = "false"
python -m pytest tests/ -m integration -v @args
