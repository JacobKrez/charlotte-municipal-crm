"""Shared test configuration.

The repository root goes on ``sys.path`` so that ``import src.roi`` works
from a fresh checkout with no installation step. The tests are pure and
offline: no network, no credentials, no model provider.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
