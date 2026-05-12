"""
Make the `originn_level1` package importable regardless of where pytest is
invoked from (repo root, package dir, or anywhere with the venv active).
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
