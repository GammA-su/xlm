"""Pattern-based secret and PII detection and redaction transform.

Adheres to C03 and Amendment 7.
"""

from __future__ import annotations

import re
import time

from pydantic import Field

from xlm.config.schemas import StrictConfigModel
from xlm.core.contracts import CanonicalDocument
from xlm.data.cleaning.base import BaseTransform
from xlm.data.cleaning.features import TextFeatures
from xlm.data.cleaning.types import QualityMetrics, TransformAction, TransformResult

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("huggingface_token", re.compile(r"hf_[A-Za-z0-9]{34,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"ghp_[A-Za-z0-9]{36,}")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.]{20,}\b")),
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    ),
    ("canary_credential", re.compile(r"CANARY_SECRET_[A-Za-z0-9_]+")),
    (
        "email_address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]

#: Reserved documentation domains (RFC 2606 section 3, RFC 6761 section 6.5)
#: whose addresses are placeholders, never personal destinations. Matching is
#: exact-host-or-dot-suffix on the lowercased domain, so subdomains such as
#: ``mail.example.com`` are placeholders while ``notreallyexample.com``,
#: ``my-example.com``, and ``example.org.attacker.com`` are not.
RESERVED_EXAMPLE_DOMAINS: frozenset[str] = frozenset(
    {"example.com", "example.net", "example.org", "example"}
)

# Necessary literal substrings only: a gate never replaces the frozen regex.
# Detection still follows the original order; Unicode SSNs use the original
# Unicode digit pattern. Bearer has no Unicode-only IGNORECASE letter variants.
_REQUIRED_LITERAL = {
    "huggingface_token": "hf_",
    "aws_access_key": "AKIA",
    "github_token": "ghp_",
    "private_key": "-----BEGIN ",
    "canary_credential": "CANARY_SECRET_",
    "ssn": "-",
}

#: P27B-K: single-pass presence hint over the seven non-email secret patterns.
#: Each alternative is byte-for-byte the corresponding SECRET_PATTERNS member
#: with inline flags rewritten to scoped form (``(?i:...)``), so the hint
#: matches a text if and only if at least one detailed pattern matches it.
#: A miss skips all seven detailed scans; a hit runs the unchanged detailed
#: path, preserving detected-type order, reason strings, and overlap
#: semantics exactly.
_NON_EMAIL_HINT_RE: re.Pattern[str] = re.compile(
    "|".join(
        [
            r"hf_[A-Za-z0-9]{34,}",
            r"AKIA[0-9A-Z]{16}",
            r"ghp_[A-Za-z0-9]{36,}",
            r"(?i:\bbearer\s+[A-Za-z0-9_\-\.]{20,}\b)",
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
            r"CANARY_SECRET_[A-Za-z0-9_]+",
            r"\b\d{3}-\d{2}-\d{4}\b",
        ]
    )
)


def is_reserved_example_email(matched_email: str) -> bool:
    """True when a regex-matched address sits on a reserved example domain."""
    _, separator, domain = matched_email.rpartition("@")
    if not separator:
        return False
    domain = domain.lower()
    if domain in RESERVED_EXAMPLE_DOMAINS:
        return True
    return any(domain.endswith(f".{reserved}") for reserved in RESERVED_EXAMPLE_DOMAINS)


def _redact_email_match(match: re.Match[str]) -> str:
    """Redact genuine addresses; leave reserved placeholders byte-identical."""
    if is_reserved_example_email(match.group(0)):
        return match.group(0)
    return "[REDACTED_EMAIL_ADDRESS]"


def redact_sensitive_text(text: str) -> str:
    """Sanitize text by replacing detected secrets and PII with inert placeholder tokens.

    Ensures protected values never reach quarantine, logs, quality reports, or review displays.
    """
    if not text:
        return ""
    sanitized = text
    for sec_type, pattern in SECRET_PATTERNS:
        if sec_type == "email_address":
            sanitized = pattern.sub(_redact_email_match, sanitized)
        else:
            sanitized = pattern.sub(f"[REDACTED_{sec_type.upper()}]", sanitized)
    return sanitized


class PiiConfig(StrictConfigModel):
    """Configuration for secret and PII detection."""

    action: str = Field(
        default="reject",
        description=(
            "Action to take on match: 'reject' (drop and quarantine) or 'redact' (replace inline)."
        ),
    )
    scan_for_secrets: bool = Field(default=True, description="Scan for API keys and credentials.")
    scan_for_pii: bool = Field(default=True, description="Scan for emails and SSNs.")


class PiiSecretFilter(BaseTransform):
    """Detects and mitigates exposed credentials, private keys, and high-risk PII patterns.

    Adheres strictly to Amendment 7:
    - Rejects or redacts secrets (Hugging Face tokens, AWS keys, GitHub tokens, canary credentials).
    - Secret-bearing rejections are flagged so quarantine omits raw secret text.
    - Explicitly documented: basic pattern detection is an operational filter, not a legal
      guarantee that a corpus is completely free of personal information (Contract C03).
    """

    transform_id = "pii_secret_filter"
    version = "1"
    mutates_text = False  # False in reject mode, True in redact mode

    def __init__(self, config: PiiConfig | None = None) -> None:
        super().__init__(config or PiiConfig())
        self.cfg: PiiConfig = self.config  # type: ignore
        self.mutates_text = self.cfg.action == "redact"

    def apply(
        self, doc: CanonicalDocument, features: TextFeatures | None = None
    ) -> TransformResult:
        start_t = time.monotonic()
        text = doc.text
        if not text:
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                metrics=QualityMetrics(),
                duration_ms=0.0,
            )

        detected_types: list[str] = []
        reserved_placeholders_ignored = 0
        # C-backed literal gates avoid the combined regex traversal on ordinary
        # prose. Detailed matching and detector order remain unchanged.
        lower = text.lower()
        for sec_type, pattern in SECRET_PATTERNS:
            if sec_type == "email_address":
                genuine_found = False
                if "@" in text:
                    for match in pattern.finditer(text):
                        if is_reserved_example_email(match.group(0)):
                            reserved_placeholders_ignored += 1
                        else:
                            genuine_found = True
                if genuine_found:
                    detected_types.append(sec_type)
            else:
                may_match = (
                    "bearer" in lower
                    if sec_type == "bearer_token"
                    else _REQUIRED_LITERAL[sec_type] in text
                )
                if may_match and pattern.search(text):
                    detected_types.append(sec_type)

        duration = (time.monotonic() - start_t) * 1000.0
        metrics = QualityMetrics(
            utf8_byte_count=doc.utf8_byte_count,
            word_count=len(features.words(text)) if features is not None else len(text.split()),
            detected_secrets=detected_types,
            reserved_email_placeholders_ignored=reserved_placeholders_ignored,
        )

        if not detected_types:
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=doc,
                reasons=[],
                metrics=metrics,
                duration_ms=duration,
            )

        # Secrets/PII detected!
        if self.cfg.action == "reject":
            reasons = [f"detected_secret:{t}" for t in detected_types]
            return TransformResult(
                action=TransformAction.REJECT,
                document=None,
                reasons=reasons,
                metrics=metrics,
                duration_ms=duration,
            )
        elif self.cfg.action == "redact":
            redacted_text = redact_sensitive_text(text)
            new_doc = self.create_transformed_copy(
                doc=doc,
                new_text=redacted_text,
                stage_name=self.transform_id,
                reasons=[f"redacted_secret:{t}" for t in detected_types],
                metadata_updates={"secrets_redacted": detected_types},
            )
            metrics.utf8_byte_count = new_doc.utf8_byte_count
            return TransformResult(
                action=TransformAction.ACCEPT,
                document=new_doc,
                reasons=[f"redacted_secret:{t}" for t in detected_types],
                metrics=metrics,
                duration_ms=duration,
            )
        else:
            raise ValueError(f"Unknown PII action: '{self.cfg.action}'")
