"""``python -m xlm.data.quality`` entry point."""

from __future__ import annotations

import sys

from xlm.data.quality.cli import main

if __name__ == "__main__":
    sys.exit(main())
