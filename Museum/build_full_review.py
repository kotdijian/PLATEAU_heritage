#!/usr/bin/env python3
"""Museum convenience wrapper for the repository-level PLATEAU review tool."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plateau_review import main as generic_main


def main() -> int:
    arguments = sys.argv[1:]
    if "--profile" not in arguments:
        arguments = [*arguments, "--profile", "museum"]
    return generic_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
