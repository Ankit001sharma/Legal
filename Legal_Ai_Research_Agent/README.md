# Legal_Ai_Research_Agent

Multi-agent Indian legal research system with production-grade validation pipeline.

## Requirements

- **Python 3.11+** (see `pyproject.toml`; Docker images use 3.11)
- Windows PowerShell **or** WSL/Ubuntu with Python 3.11 installed

## Quick start (Windows)

```powershell
cd "c:\Users\miniOrange\Projects\Ai Project\Legal_AI_FULL\Legal_Ai_Research_Agent"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
python eval/run_eval.py
python -m pytest tests/validation tests/test_report_verification.py tests/test_graph_wiring.py -q
```

## Quick start (WSL / Ubuntu)

Default WSL Python is often **3.10**, which is too old. Install 3.11 first:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev
```

Then from the repo root:

```bash
cd "/mnt/c/Users/miniOrange/Projects/Ai Project/Legal_AI_FULL/Legal_Ai_Research_Agent"
# Fix Windows line endings if the script fails with "pipefail: invalid option"
sed -i 's/\r$//' scripts/setup-wsl.sh
bash scripts/setup-wsl.sh
source .venv/bin/activate
python eval/run_eval.py
python -m pytest tests/validation tests/test_report_verification.py tests/test_graph_wiring.py -q
```

## Common errors

| Error | Fix |
|-------|-----|
| `python: command not found` | Use `python3` or activate `.venv` |
| `requires a different Python: 3.10.x not in '>=3.11'` | Install Python 3.11 (see above) |
| `set: pipefail: invalid option` | Windows CRLF in script — run `sed -i 's/\r$//' scripts/setup-wsl.sh` |
| Wrong directory | Must be `Legal_Ai_Research_Agent`, not `Legal ai` |
