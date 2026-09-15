#!/usr/bin/env python3
"""Museum convenience wrapper for the generic PLATEAU review renderer."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from render_plateau_review import main


if __name__ == "__main__":
    raise SystemExit(main())
