"""Essential-Web evidence-v2.0 offline mechanisms.

Implements ONLY the frozen protocol in
``docs/implementation/reports/ESSENTIAL-WEB-EVIDENCE-V2-PROTOCOL.md``
(bound by ``docs/implementation/evidence/ESSENTIAL-WEB-EVIDENCE-V2/freeze.json``).
No network, no fetch, no text inspection, no acquisition execution here:
this package provides deterministic selection/ranking, shared budget
ledgers, sparse-retention planning, blinding, rubric validation, and
canonical digest/provenance machinery, all testable on synthetic data.
"""

from xlm.data.evidence_v2.frozen import PROTOCOL_VERSION

__all__ = ["PROTOCOL_VERSION"]
