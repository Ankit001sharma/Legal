#!/usr/bin/env bash
# Set up Legal_Ai_Research_Agent on Ubuntu/WSL (requires Python 3.11+).
set -eu

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MIN_PYTHON="3.11"

pick_python() {
  for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
      ver="$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
      if [[ "$(printf '%s\n' "$MIN_PYTHON" "$ver" | sort -V | head -n1)" == "$MIN_PYTHON" ]]; then
        echo "$cmd"
        return 0
      fi
    fi
  done
  return 1
}

if ! PYTHON="$(pick_python)"; then
  echo "ERROR: Python >= ${MIN_PYTHON} is required."
  echo "Current default: $(python3 --version 2>&1 || echo 'python3 not found')"
  echo ""
  echo "Install Python 3.11 on Ubuntu/WSL:"
  echo "  sudo add-apt-repository -y ppa:deadsnakes/ppa"
  echo "  sudo apt update"
  echo "  sudo apt install -y python3.11 python3.11-venv python3.11-dev"
  echo ""
  echo "Then re-run:"
  echo "  bash scripts/setup-wsl.sh"
  exit 1
fi

echo "Using $PYTHON ($($PYTHON --version))"

"$PYTHON" -m venv .venv
# shellcheck source=/dev/null
. .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"

echo ""
echo "Setup complete."
echo "  source .venv/bin/activate"
echo "  python eval/run_eval.py"
echo "  python -m pytest tests/validation tests/test_report_verification.py tests/test_graph_wiring.py -q"
