"""Phase-A global quality audit: read-only, streaming, content-free measurement.

This package MEASURES low-quality material that survived the source adapters
(markup, boilerplate, repetition, encoding/control junk, OCR artefacts, extreme
composition, inherited language evidence). It never modifies, drops or
transforms a corpus document, never chooses a cleaning threshold, and never
writes document text into an aggregate artifact. Candidate threshold bands are
distribution-derived and labelled ``PROPOSAL_ONLY``; review text is materialized
only by the explicit operator command ``materialize-review``.

Phase B v1 (``cleaning_policy``, ``cleaning``, ``cleaning_report``,
``cleaning_runner``) is a READ-ONLY policy dry run: it evaluates a frozen KEEP / DROP /
REVIEW policy on every document and writes content-free reports; it never writes a
cleaned corpus (``docs/runbooks/quality-cleaning-dry-run.md``).

Any later content-transforming cleaning makes every existing C05 dedup,
contamination, lineage, split and membership artifact stale (see
``docs/runbooks/quality-audit.md``).
"""
