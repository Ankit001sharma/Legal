# Youngser P0: one-shot install for review stack (document_core + review_agent + langchain)
$ErrorActionPreference = "Stop"

$ReviewAgentDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$LegalRoot = Resolve-Path (Join-Path $ReviewAgentDir "..\..")
$DocumentCore = Join-Path $LegalRoot "document_core"

Write-Host "Youngser P0: installing document_core from $DocumentCore"
pip install -e $DocumentCore

Write-Host "Youngser P0: installing review_agent from $ReviewAgentDir"
Push-Location $ReviewAgentDir
pip install -e ".[dev]"
Pop-Location

Write-Host "Youngser P0: verifying langchain import"
python -c "from langchain.chat_models import init_chat_model; print('langchain OK')"

Write-Host "Youngser P0: verifying review_agent config"
python -c "from review_agent.config import get_settings; s=get_settings(); assert s.review_plan_llm_max_tokens == 1024; print('review_agent OK')"

Write-Host "Done. Restart document-mcp if it was already running."
