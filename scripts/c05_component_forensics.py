"""Read-only, content-free C05 component forensics (fast path with live progress).

The implementation is :mod:`xlm.data.exclusion.forensics`; the 858eb9e tool is kept as
``scripts/c05_component_forensics_reference.py`` (the equivalence oracle). stdout carries
only the final JSON report; progress and refusals go to stderr. Exit 0 = report
written; 1 = refused; 130 = interrupted. Check the exit code and
``target_component.reconstruction_complete`` before reading a redirected report.
"""

from __future__ import annotations

from xlm.data.exclusion.forensics import main

if __name__ == "__main__":
    raise SystemExit(main())
