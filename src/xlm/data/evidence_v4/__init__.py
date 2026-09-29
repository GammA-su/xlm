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
  ``docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PROTOCOL.md``);
- :mod:`phase_d_plan`, :mod:`phase_d_decode`, :mod:`phase_d` — the frozen v4.1
  Phase-D acquisition (8 M projected ranges, 47 T dictionary-inclusive
  pieces) on this engine, bound to the COMPLETE v4.1 Phase-P parent (protocol
  ``docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V4.1-PHASE-D-PROTOCOL.md``).

Nothing here accepts a URL, file, range, ETag, host, locator, operation kind,
root or output path from a caller.
"""

from __future__ import annotations
