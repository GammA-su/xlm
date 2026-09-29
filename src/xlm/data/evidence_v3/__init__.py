"""Essential-Web evidence v3.0: the single trusted Phase-P execution boundary.

v3.0 is a NEW EXECUTION ACCOUNTING EPOCH, not a v2 budget reset. v2.0-v2.2
are permanently CLOSED_NON_EXECUTABLE / HISTORICALLY_UNCERTIFIABLE.

The supported entry points live in :mod:`executor` and accept only file
paths (plus a synthetic harness for disposable roots). They construct every
trusted object themselves: :mod:`authorization` (bytes -> minted validated
authorization / operator approval), :mod:`plan` (the only source of
operations), :mod:`genesis` (exclusive epoch start), :mod:`journal`
(authoritative hash-chained state), :mod:`fsroot` (containment and the only
write layer), :mod:`memory` (during-work supervision), :mod:`transport`
and :mod:`netpolicy` (single-hop range transport, exact redirect/identity
policy). Phase D is not executable here. No real genesis is created by
tests or builders; synthetic epochs use disposable roots only.
"""

from __future__ import annotations
