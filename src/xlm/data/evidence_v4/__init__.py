"""Essential-Web evidence v4.0: one dedicated Phase-P structural fetcher.

v4.0 adopts the exact frozen v2.0 scientific membership and replaces only the
execution mechanism with a deliberately small program (protocol
``docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.0-PROTOCOL.md``):

- :mod:`frozen` — compiled freeze constants and the committed plan loader
  (the only source of operations);
- :mod:`transport` — exact URL/redirect policy, response identity checks and
  the single-hop live HTTPS transport;
- :mod:`state` — the SQLite receipt store;
- :mod:`layout` — metadata-only Parquet footer checks (never statistics);
- :mod:`phase_p` — the engine, restart reconciliation and output export;
- :mod:`v41` — the frozen v4.1 transport-host amendment (exact expanded
  Hugging Face storage/CDN host set, fresh version and root) reusing this
  engine unchanged (protocol
  ``docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PROTOCOL.md``).

Nothing here accepts a URL, file, range, ETag, operation kind or output path
from a caller. Phase D is not implemented.
"""

from __future__ import annotations
