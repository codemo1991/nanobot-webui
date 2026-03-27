"""pytest configuration — add the worktree root to the Python path."""
from __future__ import annotations

import sys
from pathlib import Path

# Root of the worktree (one level up from tests/)
_WORKTREE_ROOT = Path(__file__).resolve().parent.parent
if str(_WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_ROOT))
