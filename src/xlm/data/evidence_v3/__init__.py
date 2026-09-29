"""Essential-Web evidence v3.0 offline mechanisms (clean execution epoch).

v3.0 is a NEW EXECUTION ACCOUNTING EPOCH, not a v2 budget reset. v2.0-v2.2
are permanently CLOSED_NON_EXECUTABLE / HISTORICALLY_UNCERTIFIABLE and are
adopted only as planning observations or closed provenance. No v3 network
may occur before a valid epoch_start.json exists; this package implements
and synthetic-tests the mechanism but never creates a real genesis.
"""

from __future__ import annotations
