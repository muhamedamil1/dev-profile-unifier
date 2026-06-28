from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_project_root() -> None:
    """
    Ensure scripts can import the app package when executed as:

        python scripts/verify_candidate_discovery.py

    without requiring PYTHONPATH configuration.
    """
    project_root = Path(__file__).resolve().parents[1]

    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
