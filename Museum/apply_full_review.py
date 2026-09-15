#!/usr/bin/env python3
"""Museum compatibility entry point for the repository-level review applier."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apply_plateau_review import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
