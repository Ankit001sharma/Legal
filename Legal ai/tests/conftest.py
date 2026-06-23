"""Pytest setup for Streamlit-side modules under Legal ai/."""

from __future__ import annotations

import sys
from pathlib import Path

_LEGAL_AI_ROOT = Path(__file__).resolve().parents[1]
_root = str(_LEGAL_AI_ROOT)
if _root not in sys.path:
    sys.path.insert(0, _root)
